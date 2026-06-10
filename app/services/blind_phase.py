from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.services.divergence import DivergenceResult, check_divergence

logger = logging.getLogger(__name__)


async def advance_blind_phase(pool: asyncpg.Pool, thread_id: str) -> None:
    """
    Called when: all registered seeds submitted, or the 90s window expired.
    Copies drafts → contributions, runs divergence gate, advances thread status.
    Uses FOR UPDATE on the thread row to prevent concurrent advances.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            thread = await conn.fetchrow(
                """SELECT id, status, registered_agent_ids, divergence_round,
                          source_post_id, elevated_risk
                   FROM seed_threads
                   WHERE id = $1
                   FOR UPDATE""",
                thread_id,
            )
            if not thread or thread["status"] != "blind_phase":
                return  # already advanced or thread gone

            drafts = await conn.fetch(
                """SELECT id, agent_id, body, confidence, approach, token_count, intent_match
                   FROM seed_drafts
                   WHERE thread_id = $1""",
                thread_id,
            )

            if not drafts:
                # No one submitted — close silently
                await conn.execute(
                    "UPDATE seed_threads SET status = 'closed', concluded_at = NOW() WHERE id = $1",
                    thread_id,
                )
                logger.info("thread %s closed: no drafts submitted", thread_id)
                return

            # Determine if post is tagged 'complex' for divergence check
            post_is_complex = False
            if thread["source_post_id"]:
                post = await conn.fetchrow(
                    "SELECT tags FROM posts WHERE id = $1",
                    thread["source_post_id"],
                )
                if post and post["tags"]:
                    post_is_complex = "complex" in (post["tags"] or [])

            draft_list = [dict(d) for d in drafts]

            # Run divergence gate (requires ≥ 3 drafts)
            result = DivergenceResult("proceed")
            if len(draft_list) >= 3:
                result = check_divergence(draft_list, post_is_complex)

            if result.action == "divergence_hold":
                new_round = (thread["divergence_round"] or 0) + 1
                if new_round >= 2:
                    # Second suspicious round — close silently, flag post
                    await conn.execute(
                        """UPDATE seed_threads
                           SET status = 'closed', concluded_at = NOW()
                           WHERE id = $1""",
                        thread_id,
                    )
                    if thread["source_post_id"]:
                        await conn.execute(
                            "UPDATE posts SET internally_flagged = TRUE WHERE id = $1",
                            thread["source_post_id"],
                        )
                    logger.warning(
                        "thread %s closed after repeated divergence_hold", thread_id
                    )
                else:
                    # First hold — reset for re-draft
                    await conn.execute(
                        """UPDATE seed_threads
                           SET status = 'divergence_hold',
                               divergence_round = $2,
                               blind_phase_ends_at = NOW() + INTERVAL '90 seconds',
                               registered_agent_ids = '{}'
                           WHERE id = $1""",
                        thread_id,
                        new_round,
                    )
                    # Clear previous drafts (mark with round so training data preserved)
                    await conn.execute(
                        "DELETE FROM seed_drafts WHERE thread_id = $1",
                        thread_id,
                    )
                    logger.info("thread %s divergence_hold round %d", thread_id, new_round)
                return

            # Copy drafts → contributions
            for draft in draft_list:
                await conn.execute(
                    """INSERT INTO seed_contributions
                       (thread_id, agent_id, body, confidence, approach, token_count,
                        intent_match, from_draft, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, NOW())
                       ON CONFLICT DO NOTHING""",
                    thread_id,
                    draft["agent_id"],
                    draft["body"],
                    draft["confidence"],
                    draft["approach"],
                    draft["token_count"],
                    draft["intent_match"],
                )

            # Flag outlier contribution if detected
            if result.flagged_agent_id:
                await conn.execute(
                    """UPDATE seed_contributions
                       SET divergence_flagged = TRUE
                       WHERE thread_id = $1 AND agent_id = $2""",
                    thread_id,
                    str(result.flagged_agent_id),
                )

            await conn.execute(
                "UPDATE seed_threads SET status = 'deliberating' WHERE id = $1",
                thread_id,
            )
            logger.info(
                "thread %s advanced to deliberating (%d drafts)", thread_id, len(draft_list)
            )


# ─── Background task ─────────────────────────────────────────────────────────

_blind_phase_task: asyncio.Task | None = None


async def _blind_phase_worker(pool: asyncpg.Pool, interval: int) -> None:
    while True:
        try:
            expired = await pool.fetch(
                """SELECT id FROM seed_threads
                   WHERE status IN ('blind_phase', 'divergence_hold')
                     AND blind_phase_ends_at <= NOW()"""
            )
            for row in expired:
                await advance_blind_phase(pool, str(row["id"]))
        except Exception:
            logger.exception("blind_phase_worker error")
        await asyncio.sleep(interval)


async def start_blind_phase_worker(pool: asyncpg.Pool, interval: int = 5) -> None:
    global _blind_phase_task
    _blind_phase_task = asyncio.create_task(_blind_phase_worker(pool, interval))


async def stop_blind_phase_worker() -> None:
    global _blind_phase_task
    if _blind_phase_task:
        _blind_phase_task.cancel()
        _blind_phase_task = None
