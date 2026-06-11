"""Tests for app/services/calibration.py."""
from __future__ import annotations

import pytest

from app.services.calibration import (
    _MIN_SAMPLE,
    _ROLLING_WINDOW,
    _compute_calibration_score,
    _process_thread,
)

pytestmark = pytest.mark.usefixtures("clean_db")


# ─── _compute_calibration_score (pure) ───────────────────────────────────────

class TestComputeCalibrationScore:
    def test_none_below_min_sample(self):
        score, sample = _compute_calibration_score([True, True, True])
        assert score is None
        assert sample == 3

    def test_none_at_zero(self):
        score, sample = _compute_calibration_score([])
        assert score is None
        assert sample == 0

    def test_none_at_min_sample_minus_one(self):
        score, sample = _compute_calibration_score([True] * (_MIN_SAMPLE - 1))
        assert score is None

    def test_computed_at_min_sample(self):
        # exactly 10 events, all correct
        score, sample = _compute_calibration_score([True] * _MIN_SAMPLE)
        assert score == pytest.approx(1.0)
        assert sample == _MIN_SAMPLE

    def test_partial_accuracy(self):
        # 8 correct, 2 incorrect → 0.8
        values = [True] * 8 + [False] * 2
        score, sample = _compute_calibration_score(values)
        assert score == pytest.approx(0.8)
        assert sample == 10

    def test_all_incorrect(self):
        values = [False] * _MIN_SAMPLE
        score, sample = _compute_calibration_score(values)
        assert score == pytest.approx(0.0)

    def test_rolling_window_limit(self):
        # Exactly _ROLLING_WINDOW events all correct
        values = [True] * _ROLLING_WINDOW
        score, sample = _compute_calibration_score(values)
        assert score == pytest.approx(1.0)
        assert sample == _ROLLING_WINDOW


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _make_concluded_thread(
    pool,
    coordinator_id,
    post_id,
    answer_id,
    contributions: list[dict],  # [{"agent_id": ..., "confidence": ...}]
) -> str:
    row = await pool.fetchrow(
        """INSERT INTO seed_threads
           (source_post_id, coordinator_id, status,
            deadline, blind_phase_ends_at, public_answer_id)
           VALUES ($1, $2, 'answer_posted',
                   NOW() + INTERVAL '12 minutes',
                   NOW() - INTERVAL '1 minute',
                   $3)
           RETURNING id""",
        post_id, coordinator_id, answer_id,
    )
    thread_id = row["id"]
    for c in contributions:
        await pool.execute(
            """INSERT INTO seed_contributions
               (thread_id, agent_id, body, confidence, token_count, intent_match)
               VALUES ($1, $2, 'Test contribution.', $3, 5, 'full')""",
            thread_id, c["agent_id"], c["confidence"],
        )
    return str(thread_id)


async def _make_answer_with_upvotes(pool, post_id, agent_id, upvote_count: int = 0) -> str:
    row = await pool.fetchrow(
        """INSERT INTO answers
           (post_id, agent_id, body, confidence, token_count, intent_match, upvote_count)
           VALUES ($1, $2, 'Answer body.', 0.9, 5, 'full', $3)
           RETURNING id""",
        post_id, agent_id, upvote_count,
    )
    return str(row["id"])


# ─── _process_thread integration ─────────────────────────────────────────────

async def test_high_confidence_event_recorded_when_upvoted(db_pool, seed_agent, test_post):
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=3)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [{"agent_id": seed_agent["id"], "confidence": 0.90}],
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    event = await db_pool.fetchrow(
        "SELECT * FROM seed_calibration_events WHERE agent_id = $1 AND thread_id = $2",
        seed_agent["id"], thread_id,
    )
    assert event is not None
    assert event["was_upvoted"] is True
    assert event["is_correct"] is True
    assert event["claimed_confidence"] == pytest.approx(0.90)


async def test_high_confidence_event_recorded_when_not_upvoted(db_pool, seed_agent, test_post):
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=0)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [{"agent_id": seed_agent["id"], "confidence": 0.90}],
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    event = await db_pool.fetchrow(
        "SELECT * FROM seed_calibration_events WHERE agent_id = $1",
        seed_agent["id"],
    )
    assert event is not None
    assert event["was_upvoted"] is False
    assert event["is_correct"] is False


async def test_low_confidence_contribution_skipped(db_pool, seed_agent, test_post):
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=2)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [{"agent_id": seed_agent["id"], "confidence": 0.70}],  # below 0.85 threshold
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM seed_calibration_events WHERE agent_id = $1",
        seed_agent["id"],
    )
    assert count == 0


async def test_thread_closed_after_processing(db_pool, seed_agent, test_post):
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=1)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [{"agent_id": seed_agent["id"], "confidence": 0.90}],
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    row = await db_pool.fetchrow(
        "SELECT status, calibration_processed_at FROM seed_threads WHERE id = $1",
        thread_id,
    )
    assert row["status"] == "closed"
    assert row["calibration_processed_at"] is not None


async def test_calibration_score_null_below_min_sample(db_pool, seed_agent, test_post):
    # One event is below _MIN_SAMPLE (10) — score must be NULL
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=1)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [{"agent_id": seed_agent["id"], "confidence": 0.90}],
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    agent = await db_pool.fetchrow(
        "SELECT calibration_score, calibration_sample_size FROM agents WHERE id = $1",
        seed_agent["id"],
    )
    assert agent["calibration_score"] is None
    assert agent["calibration_sample_size"] == 1


async def test_idempotent_double_processing(db_pool, seed_agent, test_post):
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=2)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [{"agent_id": seed_agent["id"], "confidence": 0.90}],
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)
        # Thread is now 'closed' — process again should be a no-op on events
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM seed_calibration_events WHERE agent_id = $1 AND thread_id = $2",
        seed_agent["id"], thread_id,
    )
    assert count == 1  # not 2


async def test_multiple_seeds_in_same_thread(db_pool, seed_agent, seed_agent2, test_post):
    answer_id = await _make_answer_with_upvotes(db_pool, test_post["id"], seed_agent["id"], upvote_count=1)
    thread_id = await _make_concluded_thread(
        db_pool, seed_agent["id"], test_post["id"], answer_id,
        [
            {"agent_id": seed_agent["id"], "confidence": 0.90},
            {"agent_id": seed_agent2["id"], "confidence": 0.88},
        ],
    )

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await _process_thread(conn, thread_id, answer_id)

    count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM seed_calibration_events WHERE thread_id = $1",
        thread_id,
    )
    assert count == 2
