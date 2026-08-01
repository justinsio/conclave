"""Tests for the 90-day closed-post expiry worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.post_expiry import run_expiry

pytestmark = pytest.mark.usefixtures("clean_db")


async def _insert_post(pool, agent_id, status: str, age_days: int, closed: bool = True) -> str:
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    closed_at = created if (closed and status == "resolved") else None
    row = await pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget, status,
                              created_at, closed_at)
           VALUES ($1, 'coding', 'Expiry test', 'body', 100, $2, $3, $4)
           RETURNING id""",
        agent_id, status, created, closed_at,
    )
    return str(row["id"])


async def _insert_answer(pool, post_id, agent_id) -> str:
    row = await pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count, intent_match)
           VALUES ($1, $2, 'answer', 0.8, 5, 'full') RETURNING id""",
        post_id, agent_id,
    )
    return str(row["id"])


# ─── Core expiry logic ────────────────────────────────────────────────────────

async def test_resolved_post_over_ttl_is_deleted(db_pool, seed_agent):
    post_id = await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=91)
    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 1
    row = await db_pool.fetchrow("SELECT id FROM posts WHERE id = $1", post_id)
    assert row is None


async def test_resolved_post_under_ttl_is_kept(db_pool, seed_agent):
    post_id = await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=89)
    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 0
    row = await db_pool.fetchrow("SELECT id FROM posts WHERE id = $1", post_id)
    assert row is not None


async def test_admin_deleted_post_over_ttl_is_deleted(db_pool, seed_agent):
    """Admin-deleted posts have no closed_at — expiry uses created_at."""
    post_id = await _insert_post(db_pool, seed_agent["id"], "deleted", age_days=91, closed=False)
    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 1
    row = await db_pool.fetchrow("SELECT id FROM posts WHERE id = $1", post_id)
    assert row is None


async def test_open_post_over_ttl_is_never_deleted(db_pool, seed_agent):
    """Open posts are not subject to 90-day expiry — only closed ones are."""
    await _insert_post(db_pool, seed_agent["id"], "open", age_days=200, closed=False)
    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 0


async def test_expiry_returns_count(db_pool, seed_agent):
    await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=91)
    await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=120)
    await _insert_post(db_pool, seed_agent["id"], "deleted", age_days=95, closed=False)
    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 3


async def test_expiry_respects_custom_ttl(db_pool, seed_agent):
    await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=31)
    deleted = await run_expiry(db_pool, ttl_days=30)
    assert deleted == 1


async def test_no_expired_posts_returns_zero(db_pool, seed_agent):
    await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=10)
    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 0


# ─── Cascade ──────────────────────────────────────────────────────────────────

async def test_answers_cascade_deleted_with_post(db_pool, seed_agent):
    post_id = await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=91)
    answer_id = await _insert_answer(db_pool, post_id, seed_agent["id"])
    await run_expiry(db_pool, ttl_days=90)
    row = await db_pool.fetchrow("SELECT id FROM answers WHERE id = $1", answer_id)
    assert row is None


async def test_young_post_answers_survive(db_pool, seed_agent):
    post_id = await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=10)
    answer_id = await _insert_answer(db_pool, post_id, seed_agent["id"])
    await run_expiry(db_pool, ttl_days=90)
    row = await db_pool.fetchrow("SELECT id FROM answers WHERE id = $1", answer_id)
    assert row is not None


# ─── Mixed batch ──────────────────────────────────────────────────────────────

async def test_only_expired_posts_deleted_in_mixed_batch(db_pool, seed_agent):
    old_id = await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=100)
    young_id = await _insert_post(db_pool, seed_agent["id"], "resolved", age_days=30)
    open_id = await _insert_post(db_pool, seed_agent["id"], "open", age_days=200, closed=False)

    deleted = await run_expiry(db_pool, ttl_days=90)
    assert deleted == 1

    assert await db_pool.fetchrow("SELECT id FROM posts WHERE id = $1", old_id) is None
    assert await db_pool.fetchrow("SELECT id FROM posts WHERE id = $1", young_id) is not None
    assert await db_pool.fetchrow("SELECT id FROM posts WHERE id = $1", open_id) is not None


# ─── 2.7b: off by default, per-category TTLs, corpus exemption ────────────────

import pytest as _pytest  # noqa: E402

from app.services.post_expiry import (  # noqa: E402
    parse_ttl_overrides,
    start_post_expiry_worker,
    stop_post_expiry_worker,
)
import app.services.post_expiry as _pe  # noqa: E402


@_pytest.mark.parametrize("raw,expected", [
    ("", {}),
    ("coding=30", {"coding": 30}),
    ("coding=30,research=never", {"coding": 30, "research": "never"}),
    ("  coding = 30 , research = never  ", {"coding": 30, "research": "never"}),
    ("coding=30,,", {"coding": 30}),
])
def test_parse_ttl_overrides_accepts_valid_input(raw, expected):
    assert parse_ttl_overrides(raw) == expected


@_pytest.mark.parametrize("raw", [
    "coding=0",          # the trap: "delete everything closed more than 0 days ago"
    "coding=-5",
    "coding",            # no '='
    "coding=soon",       # not an int and not 'never'
    "Coding=never",      # capitalised - would silently match zero rows
    "security=30",       # not a real category
])
def test_parse_ttl_overrides_rejects_bad_input(raw):
    with _pytest.raises(ValueError):
        parse_ttl_overrides(raw)


def test_zero_default_ttl_is_rejected_at_boot():
    """POST_EXPIRY_TTL_DAYS=0 wipes the whole resolved history on the next
    sweep. Asserts on the raised error, never on a Settings attribute - that
    would print the entire Settings repr, API key included, into test output."""
    from pydantic import ValidationError
    from app.config import Settings

    with _pytest.raises(ValidationError) as exc:
        Settings(post_expiry_ttl_days=0)
    assert "must be >= 1" in str(exc.value)


async def test_disabled_worker_never_starts(db_pool):
    """The default. Nothing can be deleted by accident."""
    await start_post_expiry_worker(db_pool, interval=1, ttl_days=1, enabled=False)
    assert _pe._worker_task is None


async def test_enabled_worker_does_start(db_pool):
    """The path no test covered: an inverted condition would pass every
    disabled-path test and silently kill expiry for everyone who turns it on."""
    await start_post_expiry_worker(db_pool, interval=3600, ttl_days=90, enabled=True)
    try:
        assert _pe._worker_task is not None
        assert not _pe._worker_task.done()
    finally:
        await stop_post_expiry_worker()
    assert _pe._worker_task is None


async def _closed_post(pool, agent_id, category="coding", days_old=200):
    return await pool.fetchval(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              status, closed_at)
           VALUES ($1, $2, 't', 'b', 100, 'resolved',
                   NOW() - ($3 || ' days')::INTERVAL)
           RETURNING id""",
        agent_id, category, str(days_old),
    )


async def test_never_category_survives_the_default_sweep(db_pool, standard_agent):
    """The trap the spec names: if 'never' keys are dropped from the overridden
    list, every never-category post falls into the default DELETE - the exact
    inverse of what the operator asked for."""
    keep = await _closed_post(db_pool, standard_agent["id"], category="research")
    drop = await _closed_post(db_pool, standard_agent["id"], category="coding")

    await _pe.run_expiry(db_pool, ttl_days=90, overrides={"research": "never"})

    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", keep) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", drop) == 0


async def test_per_category_ttl_is_applied(db_pool, standard_agent):
    young = await _closed_post(db_pool, standard_agent["id"], category="coding", days_old=40)
    old = await _closed_post(db_pool, standard_agent["id"], category="coding", days_old=200)

    await _pe.run_expiry(db_pool, ttl_days=365, overrides={"coding": 100})

    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", young) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", old) == 0


async def test_a_post_with_a_corpus_descendant_never_expires(db_pool, standard_agent):
    """Deleting it would strand the provenance that answers 'what did this bad
    entry contaminate?'."""
    protected = await _closed_post(db_pool, standard_agent["id"])
    plain = await _closed_post(db_pool, standard_agent["id"])
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, category, quality_score,
            source_provider_type, source_post_id)
           VALUES ('q', 'a', 'coding', 1.0, 'test', $1)""",
        protected,
    )

    await _pe.run_expiry(db_pool, ttl_days=90)

    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", protected) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", plain) == 0


async def test_a_null_category_post_is_not_silently_exempt(db_pool, standard_agent):
    """`category <> ALL(...)` is NULL for a NULL category, which would skip the
    row. The IS NULL branch exists for exactly this."""
    pid = await db_pool.fetchval(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              status, closed_at)
           VALUES ($1, NULL, 't', 'b', 100, 'resolved', NOW() - INTERVAL '200 days')
           RETURNING id""",
        standard_agent["id"],
    )
    await _pe.run_expiry(db_pool, ttl_days=90, overrides={"coding": 30})
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", pid) == 0
