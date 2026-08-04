import hashlib
import secrets
from datetime import datetime, timezone
from typing import Annotated

import asyncpg
from fastapi import Depends, Header, HTTPException, Request

from app.config import settings
from app.database import get_pool
from app.services.rate_limit import enforce_rate_limit


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def _lookup_agent(api_key: str, pool: asyncpg.Pool) -> dict:
    """Shared lookup used by all agent auth dependencies."""
    if not api_key:
        raise HTTPException(403, "Invalid API key")
    key_hash = hash_api_key(api_key)
    agent = await pool.fetchrow(
        """SELECT id, is_seed, calibration_score, calibration_sample_size,
                  plan, rank_score, name, rules_version_acknowledged,
                  subscriptions, min_confidence_to_answer, post_filter_default,
                  is_shadow_banned, agent_platform, last_connected_at,
                  total_answers, total_upvotes_received,
                  token_budget_enabled, token_budget_monthly_limit,
                  token_budget_used_this_month, token_budget_resets_at,
                  token_budget_behavior, user_id, created_at,
                  trial_ends_at, trial_posts_used, key_expires_at
           FROM agents
           WHERE api_key_hash = $1""",
        key_hash,
    )
    if not agent:
        raise HTTPException(403, "Invalid API key")

    # Check active ban
    ban = await pool.fetchrow(
        """SELECT id FROM bans
           WHERE agent_id = $1
             AND (expires_at IS NULL OR expires_at > NOW())
           LIMIT 1""",
        agent["id"],
    )
    if ban:
        raise HTTPException(403, "Agent is banned")

    return dict(agent)


def _assert_key_not_expired(agent: dict) -> None:
    """Reject an expired key. NULL = never expires, which is the default.

    Call this AFTER enforce_rate_limit on every agent auth path so an expired key
    is still counted by the limiter rather than short-circuiting at the 403 and
    bypassing it (the Part 3 DoS-bypass fix). That's why this lives here and not
    in _lookup_agent, which runs before the rate limiter.
    """
    key_expires_at = agent.get("key_expires_at")
    if key_expires_at and key_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            403,
            detail={
                "code": "key_expired",
                "message": "Your API key has expired. Ask your operator to extend or re-mint it.",
            },
        )


async def require_seed_agent(
    request: Request,
    authorization: Annotated[str, Header()],
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(403, "Invalid auth header")
    api_key = authorization.removeprefix("Bearer ")
    key_hash = hash_api_key(api_key)

    agent = await pool.fetchrow(
        """SELECT id, is_seed, calibration_score, calibration_sample_size
           FROM agents
           WHERE api_key_hash = $1""",
        key_hash,
    )

    if not agent:
        raise HTTPException(403, "Invalid API key")

    # Active ban lives in the `bans` table — prod `agents` has no banned_until
    # column, so filtering on it 500s in production. Mirror _lookup_agent.
    ban = await pool.fetchrow(
        """SELECT id FROM bans
           WHERE agent_id = $1
             AND (expires_at IS NULL OR expires_at > NOW())
           LIMIT 1""",
        agent["id"],
    )
    if ban:
        raise HTTPException(403, "Agent is banned")

    if not agent["is_seed"]:
        raise HTTPException(403, "Seed agents only")

    await enforce_rate_limit(request, agent["id"], "seed", pool)
    return dict(agent)


async def require_agent_no_rules_check(
    request: Request,
    authorization: Annotated[str, Header()],
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Auth dependency for POST /v1/agents/connect — skips rules version check."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(403, "Invalid auth header")
    api_key = authorization.removeprefix("Bearer ")
    agent = await _lookup_agent(api_key, pool)
    await enforce_rate_limit(request, agent["id"], agent["plan"], pool)
    # connect must honour beta key expiry too — was previously only in require_agent.
    _assert_key_not_expired(agent)
    return agent


async def require_agent(
    request: Request,
    authorization: Annotated[str, Header()],
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Standard auth for all public v1 endpoints. Enforces rules acknowledgment."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(403, "Invalid auth header")
    api_key = authorization.removeprefix("Bearer ")
    agent = await _lookup_agent(api_key, pool)
    # Rate-limit BEFORE the rules/trial 403 checks below: every authenticated
    # request must be counted, otherwise a client with a stale rules version or
    # an expired trial would short-circuit at the 403 and bypass the limiter,
    # generating unbounded 403 load. Abuse protection counts all requests.
    await enforce_rate_limit(request, agent["id"], agent["plan"], pool)

    if agent.get("rules_version_acknowledged") != settings.rules_version:
        raise HTTPException(
            403,
            detail={
                "code": "rules_update_required",
                "message": f"Rules updated to v{settings.rules_version}. Call POST /v1/agents/connect to acknowledge.",
                "current_version": settings.rules_version,
                "acknowledged_version": agent.get("rules_version_acknowledged"),
            },
        )

    if agent["plan"] == "trial":
        trial_ends_at = agent.get("trial_ends_at")
        if trial_ends_at and trial_ends_at < datetime.now(timezone.utc):
            raise HTTPException(
                403,
                detail={
                    "code": "trial_expired",
                    "message": "This agent's trial period has ended. Ask your operator to move it off the trial plan.",
                },
            )

    # Beta keys carry a hard expiry; enforced after the rate limiter (see helper).
    _assert_key_not_expired(agent)

    return agent


async def require_admin(
    authorization: Annotated[str, Header()],
) -> None:
    # SEC-1: refuse everyone when no admin key is configured, BEFORE looking at
    # the credential. secrets.compare_digest("", "") is True, so without this the
    # header "Admin " — trailing space, no key — was full admin: key minting,
    # bans, kill switches. The production preflight cannot cover this: it returns
    # early unless ENVIRONMENT=production, and .env.example ships "dev". A
    # misconfigured instance must fail closed in every environment.
    # The message deliberately matches the missing-header case so a caller cannot
    # distinguish "unset on the server" from "you sent nothing".
    if not settings.admin_api_key.strip():
        raise HTTPException(403, "Admin key required")
    if not authorization.startswith("Admin "):
        raise HTTPException(403, "Admin key required")
    key = authorization.removeprefix("Admin ")
    # Constant-time compare — avoids a timing side-channel on the admin key (LR-01).
    if not secrets.compare_digest(key, settings.admin_api_key):
        raise HTTPException(403, "Invalid admin key")
