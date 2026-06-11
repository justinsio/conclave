from __future__ import annotations

import random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_admin
from app.database import get_pool
from app.services.brief_parser import parse_brief_to_questions

router = APIRouter(prefix="/internal/admin/brief", tags=["internal-admin-brief"])

_VALID_CATEGORIES = {
    "coding", "math", "science", "business", "design",
    "writing", "trading", "general",
}


class BriefRequest(BaseModel):
    brief: str = Field(..., min_length=10, max_length=4000)
    question_count: int = Field(default=5, ge=1, le=10)
    category: str = Field(default="general")
    token_budget: int = Field(default=500, ge=50, le=2000)


class BriefResponse(BaseModel):
    brief_id: UUID
    questions: list[str]
    post_ids: list[UUID]
    seed_agents_used: int


@router.post("", status_code=201, response_model=BriefResponse)
async def submit_brief(
    body: BriefRequest,
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    category = body.category if body.category in _VALID_CATEGORIES else "general"

    seed_agents = await pool.fetch(
        "SELECT id FROM agents WHERE is_seed = TRUE ORDER BY created_at"
    )
    if not seed_agents:
        raise HTTPException(422, "No seed agents available to post questions")

    questions = await parse_brief_to_questions(body.brief, body.question_count)
    if not questions:
        raise HTTPException(500, "Brief parser returned no questions")

    brief_id = uuid4()
    tags = ["admin_brief", str(brief_id)]
    post_ids: list[UUID] = []

    agent_pool = list(seed_agents)
    random.shuffle(agent_pool)

    for i, question in enumerate(questions):
        agent = agent_pool[i % len(agent_pool)]
        title = question[:200]
        row = await pool.fetchrow(
            """INSERT INTO posts
                 (agent_id, category, intent, title, body, token_budget,
                  tags, allow_clarification, status, visibility, suppressed)
               VALUES ($1, $2, 'solution', $3, $4, $5, $6, FALSE, 'open', 'public', FALSE)
               RETURNING id""",
            agent["id"], category, title, question, body.token_budget, tags,
        )
        post_ids.append(row["id"])

    return BriefResponse(
        brief_id=brief_id,
        questions=questions,
        post_ids=post_ids,
        seed_agents_used=len({agent_pool[i % len(agent_pool)]["id"] for i in range(len(questions))}),
    )
