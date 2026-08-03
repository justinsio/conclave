"""Agent provisioning — the operator's only way to issue an API key.

Renamed from "beta users" (Billing/Signup Phase 1) when Conclave became a
self-hosted product: there is no beta, no signup and no Stripe, just an
operator handing keys to their own team's agents. The `beta_users` column
`users.is_beta` and the table names are deliberately unchanged — a rename
migration is schema risk with no functional payoff.

Key lifetime is `AGENT_KEY_TTL_DAYS`, default 0 = never expires. See the
setting's comment in app/config.py for why the old hard-coded 30 was wrong
for a private network.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import hash_api_key, require_admin
from app.config import settings
from app.database import get_pool
from app.services.audit import log_admin_action

router = APIRouter(prefix="/internal/admin/agents", tags=["internal-admin"])


def synthesize_email(agent_name: str) -> str:
    """A self-hoster minting a key for their own agent has no email to give.

    `users.email` is VARCHAR NOT NULL UNIQUE (002_public_api_schema.sql), so
    "optional" needs a value rather than a dropped field, and a migration to
    relax the column is schema risk this does not warrant. `.invalid` is
    reserved by RFC 2606 and can never resolve, so the placeholder cannot
    address a real mailbox, and uniqueness per agent name is preserved.
    """
    return f"{agent_name.strip().lower()}@local.invalid"


class AgentCreate(BaseModel):
    agent_name: str
    category: str
    plan: str = Field(default="reader", max_length=20)
    # Optional: synthesized from agent_name when absent. Kept accepted so an
    # operator who wants real addresses on their roster can still supply them.
    email: str | None = None


class AgentCreated(BaseModel):
    user_id: str
    agent_id: str
    email: str
    agent_name: str
    category: str
    plan: str
    api_key: str
    # None means "never expires" — the default. A non-optional type here raised
    # an unhandled ValidationError and 500'd every mint under that default.
    key_expires_at: datetime | None


class AgentRow(BaseModel):
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
    key_expires_at: datetime | None


@router.post("", response_model=AgentCreated, dependencies=[Depends(require_admin)])
async def create_agent(body: AgentCreate, pool: asyncpg.Pool = Depends(get_pool)):
    """Create a user + their agent, and return the raw key once.

    The category is echoed back for the operator's records; wiring it into
    agent subscriptions is separate work.
    """
    email = (body.email or synthesize_email(body.agent_name)).strip().lower()
    raw_key = secrets.token_urlsafe(32)
    ttl_days = settings.agent_key_ttl_days

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
            # ttl_days = 0 must produce SQL NULL, never make_interval(days => 0):
            # that evaluates to NOW(), i.e. a key that is already expired.
            agent = await conn.fetchrow(
                """INSERT INTO agents (api_key_hash, is_seed, plan, name, user_id,
                                       key_expires_at)
                   VALUES ($1, FALSE, $2, $3, $4,
                           CASE WHEN $5::int = 0 THEN NULL
                                ELSE NOW() + make_interval(days => $5::int) END)
                   RETURNING id, key_expires_at""",
                hash_api_key(raw_key), body.plan, body.agent_name, user_id, ttl_days,
            )

    # Key minting is the highest-value action a compromised admin key could take.
    await log_admin_action(
        pool, "admin_agent_create", agent_id=agent["id"],
        metadata={"email": email, "user_id": str(user_id)},
    )

    return AgentCreated(
        user_id=str(user_id),
        agent_id=str(agent["id"]),
        email=email,
        agent_name=body.agent_name,
        category=body.category,
        plan=body.plan,
        api_key=raw_key,
        key_expires_at=agent["key_expires_at"],
    )


@router.get("", response_model=list[AgentRow], dependencies=[Depends(require_admin)])
async def list_agents(pool: asyncpg.Pool = Depends(get_pool)):
    """Roster with per-agent post/answer counts."""
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
        AgentRow(
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
async def extend_agent_key(user_id: UUID, pool: asyncpg.Pool = Depends(get_pool)):
    """Push the key expiry out by AGENT_KEY_TTL_DAYS.

    A no-op when keys never expire (TTL 0) — extending a key with no expiry
    must not give it one.
    """
    ttl_days = settings.agent_key_ttl_days

    # Existence is checked separately, NOT inferred from a NULL expiry. Using
    # None as the row-not-found sentinel meant a SUCCESSFUL extend of a
    # never-expiring key returned 404 after modifying the row.
    exists = await pool.fetchval("SELECT 1 FROM agents WHERE user_id = $1", user_id)
    if not exists:
        raise HTTPException(
            404,
            detail={"code": "user_not_found",
                    "message": "No agent for that user."},
        )

    new_expiry = await pool.fetchval(
        """UPDATE agents
           SET key_expires_at = CASE
                   WHEN $2::int = 0 THEN NULL
                   ELSE COALESCE(key_expires_at, NOW()) + make_interval(days => $2::int)
               END
           WHERE user_id = $1
           RETURNING key_expires_at""",
        user_id, ttl_days,
    )
    await log_admin_action(
        pool, "admin_agent_extend",
        metadata={
            "user_id": str(user_id),
            # Guarded: None is the normal value under the default TTL, and an
            # unguarded .isoformat() raised AttributeError -> 500 after the row
            # had already been updated.
            "key_expires_at": new_expiry.isoformat() if new_expiry else None,
        },
    )
    return ExtendResponse(user_id=str(user_id), key_expires_at=new_expiry)
