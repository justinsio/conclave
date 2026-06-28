"""Beta-account enablement (Billing/Signup Phase 1).

Hand-issue beta testers a working API key with a 30-day expiry. No login,
no passwords, no Stripe — these admin endpoints are the entire surface.
Design: 02 Areas/Business/ai-agent-network-billing-signup.md (Phase 1).
"""
from __future__ import annotations

import secrets
from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import hash_api_key, require_admin
from app.database import get_pool
from app.services.audit import log_admin_action

router = APIRouter(prefix="/internal/admin/beta-users", tags=["internal-admin"])

BETA_KEY_DAYS = 30


class BetaUserCreate(BaseModel):
    email: str
    agent_name: str
    category: str


class BetaUserCreated(BaseModel):
    user_id: str
    agent_id: str
    email: str
    agent_name: str
    category: str
    plan: str
    api_key: str
    key_expires_at: datetime


class BetaUserRow(BaseModel):
    user_id: str
    email: str
    agent_id: str
    agent_name: str | None
    key_expires_at: datetime | None
    post_count: int
    answer_count: int
    created_at: datetime


class ExtendResponse(BaseModel):
    user_id: str
    key_expires_at: datetime


@router.post("", response_model=BetaUserCreated, dependencies=[Depends(require_admin)])
async def create_beta_user(body: BetaUserCreate, pool: asyncpg.Pool = Depends(get_pool)):
    """Create a beta user + their reader agent, return the raw key once.

    The category is echoed back for the operator's hand-written invite; wiring
    it into agent subscriptions is Phase 2 (real onboarding) work.
    """
    email = body.email.strip().lower()
    raw_key = secrets.token_urlsafe(32)

    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT 1 FROM users WHERE email = $1", email):
                raise HTTPException(
                    409,
                    detail={"code": "email_exists",
                            "message": "A user with that email already exists."},
                )
            user_id = await conn.fetchval(
                "INSERT INTO users (email, is_beta) VALUES ($1, TRUE) RETURNING id",
                email,
            )
            agent = await conn.fetchrow(
                """INSERT INTO agents (api_key_hash, is_seed, plan, name, user_id,
                                       key_expires_at)
                   VALUES ($1, FALSE, 'reader', $2, $3,
                           NOW() + make_interval(days => $4))
                   RETURNING id, key_expires_at""",
                hash_api_key(raw_key), body.agent_name, user_id, BETA_KEY_DAYS,
            )

    # Key minting is the highest-value action a compromised admin key could take.
    await log_admin_action(
        pool, "admin_beta_user_create", agent_id=agent["id"],
        metadata={"email": email, "user_id": str(user_id)},
    )

    return BetaUserCreated(
        user_id=str(user_id),
        agent_id=str(agent["id"]),
        email=email,
        agent_name=body.agent_name,
        category=body.category,
        plan="reader",
        api_key=raw_key,
        key_expires_at=agent["key_expires_at"],
    )


@router.get("", response_model=list[BetaUserRow], dependencies=[Depends(require_admin)])
async def list_beta_users(pool: asyncpg.Pool = Depends(get_pool)):
    """Beta roster with per-agent post/answer counts — feeds beta success
    metrics and the cost-per-active-user calculation that sets real prices."""
    rows = await pool.fetch(
        """SELECT u.id AS user_id, u.email, u.created_at,
                  a.id AS agent_id, a.name AS agent_name, a.key_expires_at,
                  (SELECT COUNT(*) FROM posts WHERE agent_id = a.id)   AS post_count,
                  (SELECT COUNT(*) FROM answers WHERE agent_id = a.id) AS answer_count
           FROM users u
           JOIN agents a ON a.user_id = u.id
           WHERE u.is_beta = TRUE
           ORDER BY u.created_at DESC"""
    )
    return [
        BetaUserRow(
            user_id=str(r["user_id"]),
            email=r["email"],
            agent_id=str(r["agent_id"]),
            agent_name=r["agent_name"],
            key_expires_at=r["key_expires_at"],
            post_count=r["post_count"],
            answer_count=r["answer_count"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/{user_id}/extend", response_model=ExtendResponse,
             dependencies=[Depends(require_admin)])
async def extend_beta_user(user_id: UUID, pool: asyncpg.Pool = Depends(get_pool)):
    """Add 30 days to the user's key expiry — preferred over minting new keys."""
    new_expiry = await pool.fetchval(
        """UPDATE agents
           SET key_expires_at = COALESCE(key_expires_at, NOW())
                                + make_interval(days => $2)
           WHERE user_id = $1
           RETURNING key_expires_at""",
        user_id, BETA_KEY_DAYS,
    )
    if new_expiry is None:
        raise HTTPException(
            404,
            detail={"code": "user_not_found",
                    "message": "No beta agent for that user."},
        )
    await log_admin_action(
        pool, "admin_beta_user_extend",
        metadata={"user_id": str(user_id), "key_expires_at": new_expiry.isoformat()},
    )
    return ExtendResponse(user_id=str(user_id), key_expires_at=new_expiry)
