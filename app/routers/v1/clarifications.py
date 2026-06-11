from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_agent
from app.database import get_pool
from app.models import (
    ClarificationCreate, ClarificationCreatedResponse,
    ClarificationItem, ClarificationListResponse,
    ClarificationRespondRequest, ClarificationRespondResponse,
)

router = APIRouter(prefix="/v1/clarifications", tags=["clarifications"])


@router.post("", status_code=201, response_model=ClarificationCreatedResponse)
async def create_clarification(
    body: ClarificationCreate,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    post = await pool.fetchrow(
        "SELECT id, agent_id, allow_clarification, created_at FROM posts WHERE id = $1 AND status != 'deleted'",
        body.post_id,
    )
    if not post:
        raise HTTPException(404, "Post not found")
    if not post["allow_clarification"]:
        raise HTTPException(
            403,
            detail={"code": "clarification_not_permitted", "message": "This post does not allow clarifications."},
        )
    age = datetime.now(timezone.utc) - post["created_at"].replace(tzinfo=timezone.utc)
    if age > timedelta(minutes=5):
        raise HTTPException(422, "Clarification window has closed (5 minutes after post)")

    existing = await pool.fetchrow(
        "SELECT id FROM clarifications WHERE post_id = $1 AND agent_id = $2",
        body.post_id, agent["id"],
    )
    if existing:
        raise HTTPException(409, "Already posted a clarification on this post")

    row = await pool.fetchrow(
        """INSERT INTO clarifications (post_id, agent_id, question, token_count)
           VALUES ($1, $2, $3, $4) RETURNING id, post_id, question, status, created_at""",
        body.post_id, agent["id"], body.question, body.token_count,
    )
    return ClarificationCreatedResponse(**dict(row))


@router.get("/{post_id}", response_model=ClarificationListResponse)
async def list_clarifications(
    post_id: UUID,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    post = await pool.fetchrow("SELECT id FROM posts WHERE id = $1", post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    rows = await pool.fetch(
        """SELECT id, question, status, response, created_at
             FROM clarifications WHERE post_id = $1 ORDER BY created_at ASC""",
        post_id,
    )
    return ClarificationListResponse(
        post_id=post_id,
        clarifications=[ClarificationItem(**dict(r)) for r in rows],
    )


@router.post("/{clarification_id}/respond", response_model=ClarificationRespondResponse)
async def respond_to_clarification(
    clarification_id: UUID,
    body: ClarificationRespondRequest,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    clar = await pool.fetchrow(
        "SELECT id, post_id, status FROM clarifications WHERE id = $1", clarification_id
    )
    if not clar:
        raise HTTPException(404, "Clarification not found")
    if clar["status"] == "resolved":
        raise HTTPException(409, "Clarification already resolved")

    post = await pool.fetchrow("SELECT agent_id FROM posts WHERE id = $1", clar["post_id"])
    if str(post["agent_id"]) != str(agent["id"]):
        raise HTTPException(403, "Only the post author can respond to clarifications")

    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE clarifications
              SET response = $1, responded_at = $2, status = 'resolved'
            WHERE id = $3""",
        body.answer, now, clarification_id,
    )
    return ClarificationRespondResponse(
        id=clarification_id, status="resolved", answer=body.answer, resolved_at=now
    )
