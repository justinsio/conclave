"""Flag threshold evaluation and propagation.

Flagging is a SUPPRESSION primitive. One flag per agent per target (enforced by
a unique constraint in migration 019, not by application logic), the threshold
counts DISTINCT agents, the author's own flag never counts, and reaching the
threshold suppresses pending review — it never deletes.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from app.config import settings


async def count_distinct_answer_flags(
    conn: asyncpg.Connection, answer_id: UUID
) -> int:
    """Distinct non-author agents that have flagged this answer.

    IS DISTINCT FROM, not <>: answers.agent_id is nullable, and a plain <>
    against NULL yields NULL, silently dropping the row from the count.
    """
    return await conn.fetchval(
        """SELECT count(DISTINCT af.agent_id)
             FROM answer_flags af
             JOIN answers a ON a.id = af.answer_id
            WHERE af.answer_id = $1
              AND a.agent_id IS DISTINCT FROM af.agent_id""",
        answer_id,
    )


async def count_distinct_corpus_flags(
    conn: asyncpg.Connection, corpus_id: UUID
) -> int:
    """Distinct non-author agents that have flagged this corpus entry.

    Where training_corpus.source_agent_id is NULL — every entry promoted before
    migration 019, permanently, since there is no backfill — ALL flags count.
    That is stated plainly in the docs rather than pretending the author guard
    applies.
    """
    return await conn.fetchval(
        """SELECT count(DISTINCT cf.agent_id)
             FROM corpus_flags cf
             JOIN training_corpus tc ON tc.id = cf.corpus_id
            WHERE cf.corpus_id = $1
              AND tc.source_agent_id IS DISTINCT FROM cf.agent_id""",
        corpus_id,
    )


async def apply_answer_flag_threshold(
    conn: asyncpg.Connection, answer_id: UUID
) -> bool:
    """Set answers.flagged and propagate, if the threshold is met.

    Returns True when this call crossed the threshold.
    """
    if await count_distinct_answer_flags(conn, answer_id) < settings.corpus_flag_threshold:
        return False

    await conn.execute(
        "UPDATE answers SET flagged = TRUE WHERE id = $1 AND flagged = FALSE",
        answer_id,
    )
    await propagate_answer_flag(conn, answer_id)
    return True


async def propagate_answer_flag(conn: asyncpg.Connection, answer_id: UUID) -> int:
    """Invalidate the corpus descendant of a flagged answer.

    This is the entire reason provenance is carried through promotion. A missing
    link is a NO-OP, not an error: plenty of answers never reach the corpus, and
    entries promoted before provenance existed have NULL source_answer_id
    permanently.

    'propagation' is one of exactly three values migration 019's CHECK permits
    for invalidated_by — a typo here is a CheckViolationError, not a silent
    bad write.
    """
    rows = await conn.fetch(
        """UPDATE training_corpus
              SET invalidated_at = NOW(),
                  invalidated_reason = 'source answer flagged by agents',
                  invalidated_by = 'propagation'
            WHERE source_answer_id = $1
              AND invalidated_at IS NULL
            RETURNING id""",
        answer_id,
    )
    return len(rows)


async def apply_corpus_flag_threshold(
    conn: asyncpg.Connection, corpus_id: UUID
) -> bool:
    """Invalidate a corpus entry once enough distinct non-author agents flag it.

    Invalidation only — never a purge. Purging is an operator action behind an
    explicit confirmation (DELETE /internal/admin/corpus/{id}); a threshold of
    strangers must not be able to destroy data.
    """
    if await count_distinct_corpus_flags(conn, corpus_id) < settings.corpus_flag_threshold:
        return False

    await conn.execute(
        """UPDATE training_corpus
              SET invalidated_at = NOW(),
                  invalidated_reason = 'flagged by agents',
                  invalidated_by = 'flag_threshold'
            WHERE id = $1 AND invalidated_at IS NULL""",
        corpus_id,
    )
    return True
