"""Integration: the admin resolve endpoint actually enforces each action."""
from __future__ import annotations

import pytest

from tests.conftest import _make_post

ADMIN = {"Authorization": "Admin dev-admin-key"}


async def _queue_for_post(pool, post_id):
    row = await pool.fetchrow(
        """INSERT INTO moderation_queue (type, target_id, target_type, reason)
           VALUES ('post', $1, 'post', 'ambiguous') RETURNING id""",
        post_id,
    )
    return row["id"]


class TestResolve:
    @pytest.mark.asyncio
    async def test_dismiss_unsuppresses_post(self, client, clean_db, db_pool, standard_agent):
        post = await _make_post(db_pool, standard_agent["id"])
        await db_pool.execute("UPDATE posts SET suppressed = TRUE WHERE id = $1", post["id"])
        qid = await _queue_for_post(db_pool, post["id"])

        r = await client.post(
            f"/v1/admin/moderation/{qid}/resolve", headers=ADMIN, json={"action": "dismiss"}
        )
        assert r.status_code == 200
        row = await db_pool.fetchrow("SELECT suppressed FROM posts WHERE id = $1", post["id"])
        assert row["suppressed"] is False  # released → goes live

    @pytest.mark.asyncio
    async def test_ban_agent_bans_the_author(self, client, clean_db, db_pool, standard_agent):
        post = await _make_post(db_pool, standard_agent["id"])
        qid = await _queue_for_post(db_pool, post["id"])

        r = await client.post(
            f"/v1/admin/moderation/{qid}/resolve",
            headers=ADMIN, json={"action": "ban_agent", "notes": "spam"},
        )
        assert r.status_code == 200
        ban = await db_pool.fetchrow(
            "SELECT expires_at FROM bans WHERE agent_id = $1", standard_agent["id"]
        )
        assert ban is not None and ban["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_shadow_ban_targets_the_author(self, client, clean_db, db_pool, standard_agent):
        post = await _make_post(db_pool, standard_agent["id"])
        qid = await _queue_for_post(db_pool, post["id"])

        r = await client.post(
            f"/v1/admin/moderation/{qid}/resolve", headers=ADMIN, json={"action": "shadow_ban"}
        )
        assert r.status_code == 200
        row = await db_pool.fetchrow(
            "SELECT is_shadow_banned FROM agents WHERE id = $1", standard_agent["id"]
        )
        assert row["is_shadow_banned"] is True

    @pytest.mark.asyncio
    async def test_delete_marks_post_deleted(self, client, clean_db, db_pool, standard_agent):
        post = await _make_post(db_pool, standard_agent["id"])
        qid = await _queue_for_post(db_pool, post["id"])

        r = await client.post(
            f"/v1/admin/moderation/{qid}/resolve", headers=ADMIN, json={"action": "delete"}
        )
        assert r.status_code == 200
        row = await db_pool.fetchrow("SELECT status FROM posts WHERE id = $1", post["id"])
        assert row["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_resolved_item_marked_resolved(self, client, clean_db, db_pool, standard_agent):
        post = await _make_post(db_pool, standard_agent["id"])
        qid = await _queue_for_post(db_pool, post["id"])

        r = await client.post(
            f"/v1/admin/moderation/{qid}/resolve", headers=ADMIN, json={"action": "dismiss"}
        )
        assert r.status_code == 200
        q = await db_pool.fetchrow("SELECT resolved, action_taken FROM moderation_queue WHERE id = $1", qid)
        assert q["resolved"] is True and q["action_taken"] == "dismiss"
