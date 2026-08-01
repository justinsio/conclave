# app/routers/internal/corpus.py
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import require_seed_agent
from app.database import get_pool
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
