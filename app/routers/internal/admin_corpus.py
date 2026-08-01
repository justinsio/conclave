"""Operator surface for the training corpus.

Invalidation is soft and reversible; purge is not. Restore is POST /restore
rather than DELETE /invalidate deliberately — two DELETEs on paths differing
only by a suffix, one restorative and one destructive, is a trap.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_admin
from app.database import get_pool
from app.services.audit import log_admin_action

router = APIRouter(prefix="/internal/admin/corpus", tags=["internal-admin"])


class InvalidateRequest(BaseModel):
    reason: str


class PurgeRequest(BaseModel):
    confirm: bool = False


@router.get("")
async def list_corpus(
    category: Optional[str] = None,
    invalidated: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    rows = await pool.fetch(
        """SELECT id, question_text, answer_text, category, quality_score,
                  rag_flag_count, invalidated_at, invalidated_reason,
                  invalidated_by, source_post_id, source_answer_id,
                  source_agent_id, created_at
             FROM training_corpus
            WHERE ($1::text IS NULL OR category = $1)
              AND ($2::bool IS NULL
                   OR ($2 IS TRUE AND invalidated_at IS NOT NULL)
                   OR ($2 IS FALSE AND invalidated_at IS NULL))
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4""",
        category, invalidated, limit, offset,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{corpus_id}/invalidate")
async def invalidate_entry(
    corpus_id: UUID,
    body: InvalidateRequest,
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    updated = await pool.fetchval(
        """UPDATE training_corpus
              SET invalidated_at = NOW(),
                  invalidated_reason = $2,
                  invalidated_by = 'operator'
            WHERE id = $1 AND invalidated_at IS NULL
            RETURNING id""",
        corpus_id, body.reason,
    )
    if updated is None:
        raise HTTPException(404, "Corpus entry not found or already invalidated")
    await log_admin_action(
        pool, "admin_corpus_invalidate",
        metadata={"corpus_id": str(corpus_id), "reason": body.reason},
    )
    return {"id": str(corpus_id), "invalidated": True, "reason": body.reason}


@router.post("/{corpus_id}/restore")
async def restore_entry(
    corpus_id: UUID,
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    updated = await pool.fetchval(
        """UPDATE training_corpus
              SET invalidated_at = NULL,
                  invalidated_reason = NULL,
                  invalidated_by = NULL
            WHERE id = $1 AND invalidated_at IS NOT NULL
            RETURNING id""",
        corpus_id,
    )
    if updated is None:
        raise HTTPException(404, "Corpus entry not found or not invalidated")
    await log_admin_action(
        pool, "admin_corpus_restore", metadata={"corpus_id": str(corpus_id)},
    )
    return {"id": str(corpus_id), "invalidated": False}


@router.delete("/{corpus_id}")
async def purge_entry(
    corpus_id: UUID,
    body: PurgeRequest = Body(default_factory=PurgeRequest),
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Delete the row outright. For content that must genuinely not persist —
    a credential or hostname that survived anonymization. 'Excluded from
    retrieval' is not 'gone' while the row is still readable in Postgres."""
    if not body.confirm:
        raise HTTPException(400, "Purge is irreversible — resend with {\"confirm\": true}")
    deleted = await pool.fetchval(
        "DELETE FROM training_corpus WHERE id = $1 RETURNING id", corpus_id
    )
    if deleted is None:
        raise HTTPException(404, "Corpus entry not found")
    await log_admin_action(
        pool, "admin_corpus_purge", metadata={"corpus_id": str(corpus_id)},
    )
    return {"id": str(corpus_id), "purged": True}
