# app/routers/internal/corpus.py
from __future__ import annotations

from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_agent, require_seed_agent
from app.config import settings
from app.database import get_pool
from app.models import FlagRequest
from app.services.flag_threshold import (
    apply_corpus_flag_threshold,
    count_distinct_corpus_flags,
)
from app.services.embeddings import get_embeddings, normalize_vector, vector_dot

router = APIRouter(prefix="/internal/corpus", tags=["internal-corpus"])


@router.get("/similar")
async def corpus_similar(
    q: str = Query(..., min_length=1),
    category: Optional[str] = None,
    k: int = Query(default=3, ge=1, le=10),
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Top-k anonymized training_corpus pairs by cosine similarity. Empty until the corpus fills."""
    embeddings = await get_embeddings([q])
    if not embeddings:
        return {"data": [], "count": 0, "reason": "embeddings_unavailable"}

    # Normalize once. Stored vectors are unit length (migration 020 + normalizing
    # ingest), so similarity below is a plain dot product — same maths as
    # /v1/knowledge, no sqrt per row.
    query_vec = normalize_vector(embeddings[0])

    rows = await pool.fetch(
        """SELECT question_text, answer_text, category, embedding
             FROM training_corpus
            WHERE embedding IS NOT NULL
              AND invalidated_at IS NULL
              AND ($1::text IS NULL OR category = $1)""",
        category,
    )

    scored = []
    for row in rows:
        emb = list(row["embedding"])
        sim = vector_dot(query_vec, emb)
        scored.append({
            "question_text": row["question_text"],
            "answer_text": row["answer_text"],
            "category": row["category"],
            "similarity": sim,
        })

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top_k = scored[:k]

    return {"data": top_k, "count": len(top_k)}


@router.post("/{corpus_id}/flag")
async def flag_corpus_entry(
    corpus_id: UUID,
    body: FlagRequest,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Report a corpus entry as wrong.

    require_agent, NOT require_seed_agent: GET /v1/knowledge lets any
    authenticated agent retrieve corpus entries and returns each entry's id
    precisely so a bad one can be reported. A seed-only flag surface left that
    a dead end — the agent could find bad knowledge and had no way to say so.
    The distinct-agent threshold plus the one-flag-per-agent constraint are the
    abuse control.

    At threshold the entry is INVALIDATED, never purged. Purging stays an
    operator action behind an explicit confirmation.
    """
    exists = await pool.fetchval(
        "SELECT id FROM training_corpus WHERE id = $1", corpus_id
    )
    if exists is None:
        raise HTTPException(404, "Corpus entry not found")

    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchval(
                """INSERT INTO corpus_flags (corpus_id, agent_id, reason)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (corpus_id, agent_id) DO NOTHING
                   RETURNING id""",
                corpus_id, agent["id"], body.reason,
            )
            if inserted is not None:
                # Only bump the raw counter on a NEW flag, so re-posting cannot
                # inflate it. Note rag_flag_count is "raw flags received" and
                # deliberately differs from the threshold count, which excludes
                # the author — a self-flag bumps this and contributes nothing to
                # suppression.
                await conn.execute(
                    "UPDATE training_corpus SET rag_flag_count = rag_flag_count + 1 WHERE id = $1",
                    corpus_id,
                )
            invalidated = await apply_corpus_flag_threshold(conn, corpus_id)
            count = await count_distinct_corpus_flags(conn, corpus_id)

    return {
        "corpus_id": str(corpus_id),
        "flag_recorded": True,
        "distinct_flags": count,
        "threshold": settings.corpus_flag_threshold,
        "invalidated": invalidated,
    }
