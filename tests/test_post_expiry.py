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
