from __future__ import annotations

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

_CLOSED_STATUSES = ("resolved", "deleted")


async def run_expiry(pool: asyncpg.Pool, ttl_days: int = 90) -> int:
    """
    Hard-delete posts that have been closed for longer than ttl_days.

    Measures age as COALESCE(closed_at, created_at) so admin-deleted posts
    (which have no closed_at) are measured from creation date.
    Answers cascade on DELETE. seed_threads and corpus_staging FKs are SET NULL.
    Returns the number of posts deleted.
    """
    rows = await pool.fetch(
        f"""DELETE FROM posts
            WHERE status = ANY($1)
              AND COALESCE(closed_at, created_at) < NOW() - ($2 || ' days')::INTERVAL
            RETURNING id, status""",
        list(_CLOSED_STATUSES),
        str(ttl_days),
    )
    if rows:
        logger.info("post_expiry: purged %d closed posts (ttl=%d days)", len(rows), ttl_days)
    return len(rows)


# ─── Background worker ────────────────────────────────────────────────────────

_worker_task: asyncio.Task | None = None


async def _worker(pool: asyncpg.Pool, interval: int, ttl_days: int) -> None:
    while True:
        try:
            await run_expiry(pool, ttl_days=ttl_days)
        except Exception:
            logger.exception("post_expiry: worker error")
        await asyncio.sleep(interval)


async def start_post_expiry_worker(
    pool: asyncpg.Pool, interval: int = 3600, ttl_days: int = 90
) -> None:
    global _worker_task
    _worker_task = asyncio.create_task(_worker(pool, interval, ttl_days))


async def stop_post_expiry_worker() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
