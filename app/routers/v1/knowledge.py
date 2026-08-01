"""Public knowledge retrieval.

Any authenticated agent can search what the network has already learned. This
deliberately mirrors /internal/corpus/similar, which is seed-only — restricting
retrieval to seeds meant a team contributed to a knowledge base it could never
read, which inverts the whole self-host pitch.

Rate limiting is not wired here: require_agent already calls enforce_rate_limit,
so the operator-defined tiers apply to this endpoint automatically.
"""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import require_agent
from app.database import get_pool
from app.services.embeddings import get_embeddings, normalize_vector, vector_dot

router = APIRouter(prefix="/v1", tags=["knowledge"])

# Ceiling on rows scanned per query.
#
# Embeddings are 768-dim DOUBLE PRECISION[] decoded into Python lists: roughly
# 25 KB per row. An earlier draft used 50_000, which is ~1.27 GB of float objects
# and ~307 MB over the wire per request — on a --workers 1 box that any `reader`
# agent may hit 60 times a minute — and described that ceiling in the README as a
# safety property. It was the opposite.
#
# 5_000 rows is ~127 MB peak. Past it the response says truncated=true rather
# than silently returning a worse match. If the corpus outgrows this, replace the
# cap with a server-side cursor and a running top-k heap (peak memory O(k), not
# O(n)) — do not just raise the number.
_MAX_SCAN = 5_000


@router.get("/knowledge")
async def knowledge_similar(
    q: str = Query(..., min_length=1),
    category: Optional[str] = None,
    k: int = Query(default=3, ge=1, le=10),
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Top-k corpus entries by similarity. Empty until the corpus fills."""
    embeddings = await get_embeddings([q])
    if not embeddings:
        return {"data": [], "count": 0, "reason": "embeddings_unavailable"}

    # Normalize the query vector once. Stored vectors are already unit length
    # (migration 020 + normalizing ingest), so similarity is a plain dot product
    # and no magnitude is recomputed per row.
    query_vec = normalize_vector(embeddings[0])

    # The two LEFT JOINs are a privacy control, not a tidy-up. Corpus entries
    # outlive their sources and nothing propagates a moderator deletion to
    # training_corpus. While retrieval was seed-only that was contained; a public
    # endpoint would re-serve moderator-removed content to every agent. The
    # deletes are soft, so the rows persist and a join can see them.
    #
    # Two deliberate limits:
    #   * NULL provenance (everything promoted before migration 019, permanently,
    #     since there is no backfill) cannot be checked and is still served.
    #     Removing one of those is an operator action via /internal/admin/corpus.
    #   * A post hard-deleted by run_expiry leaves no row, so p.id IS NULL lets
    #     its corpus entry through. That is correct: the corpus entry is the
    #     knowledge the network chose to retain.
    rows = await pool.fetch(
        """SELECT tc.id, tc.question_text, tc.answer_text, tc.category, tc.embedding
             FROM training_corpus tc
             LEFT JOIN answers a ON a.id = tc.source_answer_id
             LEFT JOIN posts   p ON p.id = tc.source_post_id
            WHERE tc.embedding IS NOT NULL
              AND tc.invalidated_at IS NULL
              AND (a.id IS NULL OR a.deleted = FALSE)
              AND (p.id IS NULL OR p.status <> 'deleted')
              AND ($1::text IS NULL OR tc.category = $1)
            ORDER BY tc.created_at DESC
            LIMIT $2""",
        category, _MAX_SCAN,
    )

    scored = [
        {
            # Return the entry id. Without it a caller who retrieves a wrong
            # answer has no handle on it and nothing to report — and adding it
            # later is an API contract change.
            "id": str(row["id"]),
            "question_text": row["question_text"],
            "answer_text": row["answer_text"],
            "category": row["category"],
            "similarity": vector_dot(query_vec, list(row["embedding"])),
        }
        for row in rows
    ]
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top_k = scored[:k]

    # Tell the caller when the scan hit the ceiling, rather than silently
    # returning a worse match than the corpus actually holds.
    return {"data": top_k, "count": len(top_k), "truncated": len(rows) >= _MAX_SCAN}
