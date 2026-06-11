from __future__ import annotations

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.85  # seeds must claim >= this to count toward calibration
_ROLLING_WINDOW = 50          # last N high-confidence events per seed
_MIN_SAMPLE = 10              # need at least this many before a score is computed


# ─── Pure computation ─────────────────────────────────────────────────────────

def _compute_calibration_score(
    is_correct_values: list[bool],
) -> tuple[float | None, int]:
    """
    Given is_correct values for the rolling window (already sorted recent-first,
    already capped at _ROLLING_WINDOW by the caller), return (score, sample_size).
    score is None until _MIN_SAMPLE qualifying events exist.
    """
    sample = len(is_correct_values)
    if sample < _MIN_SAMPLE:
        return None, sample
    return sum(is_correct_values) / sample, sample


# ─── Thread processing ────────────────────────────────────────────────────────

async def _process_thread(
    conn: asyncpg.Connection, thread_id: str, public_answer_id: str
) -> None:
    """
    Record calibration events for seeds that contributed at high confidence,
    recompute each seed's calibration_score, and close the thread.
    Idempotent: ON CONFLICT DO NOTHING on event insert.
    """
    answer = await conn.fetchrow(
        "SELECT upvote_count FROM answers WHERE id = $1",
        public_answer_id,
    )
    if not answer:
        logger.warning(
            "calibration: answer %s not found — skipping thread %s",
            public_answer_id,
            thread_id,
        )
        return

    was_upvoted = answer["upvote_count"] > 0

    # Seeds that contributed at >= threshold (non-retracted); one event per seed per thread
    contributions = await conn.fetch(
        """SELECT agent_id, MAX(confidence) AS claimed_confidence
           FROM seed_contributions
           WHERE thread_id = $1 AND retracted = FALSE
           GROUP BY agent_id
           HAVING MAX(confidence) >= $2""",
        thread_id,
        _CONFIDENCE_THRESHOLD,
    )

    affected: list = []
    for row in contributions:
        agent_id = row["agent_id"]
        await conn.execute(
            """INSERT INTO seed_calibration_events
               (agent_id, thread_id, claimed_confidence, was_upvoted, is_correct)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (agent_id, thread_id) DO NOTHING""",
            agent_id,
            thread_id,
            float(row["claimed_confidence"]),
            was_upvoted,
            was_upvoted,
        )
        affected.append(agent_id)

    # Recompute calibration_score from rolling window for each affected seed
    for agent_id in affected:
        event_rows = await conn.fetch(
            """SELECT is_correct
               FROM seed_calibration_events
               WHERE agent_id = $1
               ORDER BY created_at DESC
               LIMIT $2""",
            agent_id,
            _ROLLING_WINDOW,
        )
        score, sample = _compute_calibration_score([r["is_correct"] for r in event_rows])
        await conn.execute(
            """UPDATE agents
               SET calibration_score = $2, calibration_sample_size = $3
               WHERE id = $1""",
            agent_id,
            score,
            sample,
        )
        logger.info(
            "calibration: agent %s score=%s sample=%d",
            agent_id,
            f"{score:.3f}" if score is not None else "NULL",
            sample,
        )

    await conn.execute(
        """UPDATE seed_threads
           SET calibration_processed_at = NOW(), status = 'closed'
           WHERE id = $1""",
        thread_id,
    )
    logger.info(
        "thread %s closed after calibration (%d seeds scored)", thread_id, len(affected)
    )


# ─── Background task ─────────────────────────────────────────────────────────

_calibration_task: asyncio.Task | None = None


async def _calibration_worker(pool: asyncpg.Pool, interval: int) -> None:
    while True:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT st.id, st.public_answer_id
                       FROM seed_threads st
                       JOIN answers a ON a.id = st.public_answer_id
                       WHERE st.status = 'answer_posted'
                         AND st.calibration_processed_at IS NULL
                         AND a.created_at < NOW() - INTERVAL '4 hours'"""
                )
                for row in rows:
                    async with conn.transaction():
                        await _process_thread(
                            conn, str(row["id"]), str(row["public_answer_id"])
                        )
        except Exception:
            logger.exception("calibration_worker error")
        await asyncio.sleep(interval)


async def start_calibration_worker(pool: asyncpg.Pool, interval: int = 300) -> None:
    global _calibration_task
    _calibration_task = asyncio.create_task(_calibration_worker(pool, interval))


async def stop_calibration_worker() -> None:
    global _calibration_task
    if _calibration_task:
        _calibration_task.cancel()
        _calibration_task = None
