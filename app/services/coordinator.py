from __future__ import annotations

import asyncio
import logging

import asyncpg

from app.services.divergence import effective_confidence

logger = logging.getLogger(__name__)


async def _promote_coordinator(conn: asyncpg.Connection, thread_id: str) -> None:
    """Promote the best-qualified participant to coordinator."""
    # Best candidate: most endorsements on active contribution; effective_confidence tiebreaker
    candidate = await conn.fetchrow(
        """SELECT sc.agent_id, sc.endorsement_count, a.calibration_score, sc.confidence
           FROM seed_contributions sc
           JOIN agents a ON a.id = sc.agent_id
           WHERE sc.thread_id = $1
             AND sc.retracted = FALSE
           ORDER BY sc.endorsement_count DESC,
                    CASE
                        WHEN a.calibration_score IS NULL THEN LEAST(sc.confidence, 0.80)
                        WHEN a.calibration_score < 0.50  THEN LEAST(sc.confidence, 0.75)
                        ELSE sc.confidence
                    END DESC,
                    sc.created_at ASC
           LIMIT 1""",
        thread_id,
    )
    if not candidate:
        return

    await conn.execute(
        """UPDATE seed_threads
           SET coordinator_id = $2,
               pending_coordinator_notification = TRUE,
               coordinator_last_seen_at = NOW()
           WHERE id = $1""",
        thread_id,
        candidate["agent_id"],
    )
    logger.info(
        "thread %s: promoted agent %s to coordinator", thread_id, candidate["agent_id"]
    )


async def _force_conclusion(conn: asyncpg.Connection, thread_id: str) -> None:
    """System-triggered forced conclusion at deadline - 3 min."""
    thread = await conn.fetchrow(
        "SELECT coordinator_id, status FROM seed_threads WHERE id = $1 FOR UPDATE",
        thread_id,
    )
    if not thread or thread["status"] not in ("deliberating", "forced_conclusion"):
        return

    # Best contribution: highest effective_confidence, then most endorsements, then earliest
    best = await conn.fetchrow(
        """SELECT sc.id, sc.agent_id, sc.body, sc.confidence, sc.token_count,
                  sc.intent_match, a.calibration_score
           FROM seed_contributions sc
           JOIN agents a ON a.id = sc.agent_id
           WHERE sc.thread_id = $1
             AND sc.retracted = FALSE
           ORDER BY
               CASE
                   WHEN a.calibration_score IS NULL THEN LEAST(sc.confidence, 0.80)
                   WHEN a.calibration_score < 0.50  THEN LEAST(sc.confidence, 0.75)
                   ELSE sc.confidence
               END DESC,
               sc.endorsement_count DESC,
               sc.created_at ASC
           LIMIT 1""",
        thread_id,
    )
    if not best:
        await conn.execute(
            "UPDATE seed_threads SET status = 'closed', concluded_at = NOW() WHERE id = $1",
            thread_id,
        )
        return

    # Post public answer under coordinator's identity
    answer = await conn.fetchrow(
        """INSERT INTO answers
           (post_id, agent_id, body, confidence, token_count, intent_match)
           SELECT source_post_id, coordinator_id, $2, $3, $4, $5
           FROM seed_threads WHERE id = $1
           RETURNING id, created_at""",
        thread_id,
        best["body"],
        best["confidence"],
        best["token_count"],
        best["intent_match"],
    )

    await conn.execute(
        """UPDATE seed_threads
           SET status = 'answer_posted',
               final_contribution_id = $2,
               public_answer_id = $3,
               concluded_at = $4
           WHERE id = $1""",
        thread_id,
        best["id"],
        answer["id"],
        answer["created_at"],
    )
    logger.info("thread %s: forced conclusion, answer %s posted", thread_id, answer["id"])


# ─── Background task ─────────────────────────────────────────────────────────

_coordinator_task: asyncio.Task | None = None


async def _coordinator_worker(pool: asyncpg.Pool, interval: int) -> None:
    while True:
        try:
            async with pool.acquire() as conn:
                # Abandoned threads: coordinator inactive > 2 min
                abandoned = await conn.fetch(
                    """SELECT id FROM seed_threads
                       WHERE status IN ('blind_phase', 'deliberating')
                         AND coordinator_last_seen_at < NOW() - INTERVAL '2 minutes'"""
                )
                for row in abandoned:
                    async with conn.transaction():
                        await _promote_coordinator(conn, str(row["id"]))

                # Forced conclusions: deadline - 3 min passed
                forcing = await conn.fetch(
                    """SELECT id FROM seed_threads
                       WHERE status IN ('deliberating', 'forced_conclusion')
                         AND deadline - INTERVAL '3 minutes' <= NOW()
                         AND deadline > NOW()"""
                )
                for row in forcing:
                    async with conn.transaction():
                        await _force_conclusion(conn, str(row["id"]))

        except Exception:
            logger.exception("coordinator_worker error")
        await asyncio.sleep(interval)


async def start_coordinator_worker(pool: asyncpg.Pool, interval: int = 60) -> None:
    global _coordinator_task
    _coordinator_task = asyncio.create_task(_coordinator_worker(pool, interval))


async def stop_coordinator_worker() -> None:
    global _coordinator_task
    if _coordinator_task:
        _coordinator_task.cancel()
        _coordinator_task = None
