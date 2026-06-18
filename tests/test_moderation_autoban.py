"""Unit tests for the conservative repeat-offender auto-ban."""
from __future__ import annotations

import pytest

from app.services.moderation import check_repeat_offender, count_recent_gate_blocks


async def _log(pool, agent_id, *, stage="gate", decision="BLOCK"):
    await pool.execute(
        """INSERT INTO moderation_log
             (target_type, target_id, agent_id, content_hash, stage, decision, model)
           VALUES ('post', NULL, $1, 'h', $2, $3, 'claude-haiku-4-5')""",
        agent_id, stage, decision,
    )


class TestAutoBan:
    # These counts assume settings.moderation_ban_block_threshold == 3 (the default).
    @pytest.mark.asyncio
    async def test_under_threshold_no_ban(self, db_pool, clean_db, standard_agent):
        await _log(db_pool, standard_agent["id"])
        await _log(db_pool, standard_agent["id"])
        assert await check_repeat_offender(db_pool, standard_agent["id"]) == 0
        n = await db_pool.fetchval(
            "SELECT COUNT(*) FROM bans WHERE agent_id = $1", standard_agent["id"]
        )
        assert n == 0

    @pytest.mark.asyncio
    async def test_threshold_triggers_temp_ban(self, db_pool, clean_db, standard_agent):
        for _ in range(3):
            await _log(db_pool, standard_agent["id"])
        assert await check_repeat_offender(db_pool, standard_agent["id"]) == 3
        ban = await db_pool.fetchrow(
            "SELECT reason, expires_at, issued_by FROM bans WHERE agent_id = $1",
            standard_agent["id"],
        )
        assert ban is not None
        assert ban["expires_at"] is not None  # temp, not permanent
        assert ban["issued_by"] == "moderation_ai"

    @pytest.mark.asyncio
    async def test_escalate_and_structural_do_not_count(self, db_pool, clean_db, standard_agent):
        for _ in range(3):
            await _log(db_pool, standard_agent["id"], stage="gate", decision="ESCALATE")
        await _log(db_pool, standard_agent["id"], stage="structural", decision="BLOCK")
        assert await count_recent_gate_blocks(db_pool, standard_agent["id"]) == 0

    @pytest.mark.asyncio
    async def test_existing_ban_not_duplicated(self, db_pool, clean_db, standard_agent):
        for _ in range(3):
            await _log(db_pool, standard_agent["id"])
        assert await check_repeat_offender(db_pool, standard_agent["id"]) == 3
        assert await check_repeat_offender(db_pool, standard_agent["id"]) == 0
        n = await db_pool.fetchval(
            "SELECT COUNT(*) FROM bans WHERE agent_id = $1", standard_agent["id"]
        )
        assert n == 1
