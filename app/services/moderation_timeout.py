"""Fail-closed auto-block worker: unreviewed ESCALATE items past the timeout are
resolved as 'blocked' and kept suppressed. Mirrors app/services/post_expiry.py.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.services.notifications import notify_auto_block

logger = logging.getLogger(__name__)


async def run_moderation_timeout(pool: asyncpg.Pool, timeout_hours: int = 8) -> int:
    """Auto-block unreviewed queue items older than timeout_hours. The content is
    already suppressed (held at ESCALATE time); this resolves the queue row and
    re-asserts suppression defensively. Returns the number auto-blocked."""
    rows = await pool.fetch(
        """UPDATE moderation_queue
              SET resolved = TRUE, resolved_at = NOW(),
                  resolved_by = 'auto_timeout', action_taken = 'blocked',
                  notes = COALESCE(notes, '') || ' [auto-blocked: unreviewed past timeout]'
            WHERE resolved = FALSE
              AND flagged_at < NOW() - ($1 || ' hours')::INTERVAL
            RETURNING target_id, target_type""",
        str(timeout_hours),
    )
    for r in rows:
        if r["target_type"] == "post":
            await pool.execute("UPDATE posts SET suppressed = TRUE WHERE id = $1", r["target_id"])
        elif r["target_type"] == "answer":
            await pool.execute("UPDATE answers SET suppressed = TRUE WHERE id = $1", r["target_id"])
    if rows:
        logger.info("moderation_timeout: auto-blocked %d unreviewed items", len(rows))
        await notify_auto_block(count=len(rows))
    return len(rows)


# ─── Background worker ────────────────────────────────────────────────────────

_worker_task: asyncio.Task | None = None


async def _worker(pool: asyncpg.Pool, interval: int, timeout_hours: int) -> None:
    while True:
        try:
            await run_moderation_timeout(pool, timeout_hours=timeout_hours)
        except Exception:
            logger.exception("moderation_timeout: worker error")
        await asyncio.sleep(interval)


async def start_moderation_timeout_worker(
    pool: asyncpg.Pool, interval: int = 900, timeout_hours: int = 8
) -> None:
    global _worker_task
    _worker_task = asyncio.create_task(_worker(pool, interval, timeout_hours))


async def stop_moderation_timeout_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
