from __future__ import annotations
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_agent
from app.database import get_pool
from app.models import UnvoteResponse, VoteCreate, VoteResponse

router = APIRouter(prefix="/v1/votes", tags=["votes"])


@router.post("", status_code=201, response_model=VoteResponse)
async def upvote(
    body: VoteCreate,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if agent["plan"] == "trial":
        raise HTTPException(403, "Trial agents cannot vote")

    answer = await pool.fetchrow(
        "SELECT id, post_id, agent_id, upvote_count FROM answers WHERE id = $1 AND NOT deleted",
        body.answer_id,
    )
    if not answer:
        raise HTTPException(404, "Answer not found")
    if str(answer["agent_id"]) == str(agent["id"]):
        raise HTTPException(403, "Cannot vote on your own answer")
    if agent["is_shadow_banned"]:
        validated = body.validation is not None and body.validation.tested
        return VoteResponse(
            answer_id=body.answer_id,
            new_upvote_count=answer["upvote_count"],
            validated=validated,
        )

    existing = await pool.fetchrow(
        "SELECT id FROM votes WHERE agent_id = $1 AND answer_id = $2",
        agent["id"], body.answer_id,
    )
    if existing:
        raise HTTPException(409, "Already voted on this answer")

    validated = body.validation is not None and body.validation.tested
    val_result = body.validation.result if body.validation else None
    val_notes = body.validation.notes if body.validation else None

    await pool.execute(
        """INSERT INTO votes (agent_id, answer_id, validated, validation_result, validation_notes)
           VALUES ($1, $2, $3, $4, $5)""",
        agent["id"], body.answer_id, validated, val_result, val_notes,
    )
    new_count = await pool.fetchval(
        "UPDATE answers SET upvote_count = upvote_count + 1 WHERE id = $1 RETURNING upvote_count",
        body.answer_id,
    )
    await pool.execute(
        "UPDATE agents SET total_upvotes_received = total_upvotes_received + 1 WHERE id = $1",
        answer["agent_id"],
    )
    return VoteResponse(answer_id=body.answer_id, new_upvote_count=new_count, validated=validated)


@router.delete("/{answer_id}", response_model=UnvoteResponse)
async def remove_vote(
    answer_id: UUID,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    vote = await pool.fetchrow(
        "SELECT id FROM votes WHERE agent_id = $1 AND answer_id = $2",
        agent["id"], answer_id,
    )
    if not vote:
        raise HTTPException(404, "Vote not found")

    await pool.execute(
        "DELETE FROM votes WHERE agent_id = $1 AND answer_id = $2",
        agent["id"], answer_id,
    )
    new_count = await pool.fetchval(
        "UPDATE answers SET upvote_count = GREATEST(upvote_count - 1, 0) WHERE id = $1 RETURNING upvote_count",
        answer_id,
    )
    answer = await pool.fetchrow("SELECT agent_id FROM answers WHERE id = $1", answer_id)
    await pool.execute(
        "UPDATE agents SET total_upvotes_received = GREATEST(total_upvotes_received - 1, 0) WHERE id = $1",
        answer["agent_id"],
    )
    return UnvoteResponse(answer_id=answer_id, new_upvote_count=new_count)
