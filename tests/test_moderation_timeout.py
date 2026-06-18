"""Unit tests for the 8h fail-closed auto-block worker."""
from __future__ import annotations

import pytest

from app.services import moderation_timeout
from app.services.moderation_timeout import run_moderation_timeout
from tests.conftest import _make_post


async def _queue(pool, target_id, *, hours_ago):
    await pool.execute(
        """INSERT INTO moderation_queue
             (type, target_id, target_type, reason, flagged_at, resolved)
           VALUES ('post', $1, 'post', 'ambiguous',
                   NOW() - ($2 || ' hours')::INTERVAL, FALSE)""",
        target_id, str(hours_ago),
    )


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    async def _noop(**_kwargs):
        return False
    monkeypatch.setattr(moderation_timeout, "notify_auto_block", _noop)


class TestTimeout:
    @pytest.mark.asyncio
    async def test_stale_unreviewed_item_auto_blocked(self, db_pool, clean_db, seed_agent):
        post = await _make_post(db_pool, seed_agent["id"])
        await db_pool.execute("UPDATE posts SET suppressed = TRUE WHERE id = $1", post["id"])
        await _queue(db_pool, post["id"], hours_ago=9)

        n = await run_moderation_timeout(db_pool, timeout_hours=8)
        assert n == 1

        q = await db_pool.fetchrow(
            "SELECT resolved, resolved_by, action_taken FROM moderation_queue LIMIT 1"
        )
        assert q["resolved"] is True
        assert q["resolved_by"] == "auto_timeout"
        assert q["action_taken"] == "blocked"
        p = await db_pool.fetchrow("SELECT suppressed FROM posts WHERE id = $1", post["id"])
        assert p["suppressed"] is True

    @pytest.mark.asyncio
    async def test_fresh_item_left_untouched(self, db_pool, clean_db, seed_agent):
        post = await _make_post(db_pool, seed_agent["id"])
        await _queue(db_pool, post["id"], hours_ago=1)

        n = await run_moderation_timeout(db_pool, timeout_hours=8)
        assert n == 0

        q = await db_pool.fetchrow("SELECT resolved FROM moderation_queue LIMIT 1")
        assert q["resolved"] is False
