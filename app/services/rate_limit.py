"""Per-agent fixed-window rate limiter (Part 3).

Postgres-backed; no Redis. Enforced from the auth dependencies so the agent is
already resolved. No-op when settings.rate_limit_enabled is False (the default;
beta/prod .env sets it True).
"""
from __future__ import annotations

import time
from uuid import UUID

import asyncpg
from fastapi import HTTPException, Request

from app.config import settings


_MAX_TIER_NAME = 20  # agents.plan is VARCHAR(20)


def parse_rate_limit_tiers(raw: str) -> dict[str, int]:
    """Parse "name=perminute" pairs. Raises ValueError on a malformed entry —
    a silently-ignored limit is worse than a failed boot."""
    tiers: dict[str, int] = {}
    for chunk in (raw or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        name, sep, value = entry.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or not value:
            raise ValueError(f"{entry!r}: expected 'name=perminute', e.g. 'contractor=20'")
        if len(name) > _MAX_TIER_NAME:
            raise ValueError(
                f"{name!r}: tier names are limited to {_MAX_TIER_NAME} characters "
                "(agents.plan is VARCHAR(20))"
            )
        try:
            limit = int(value)
        except ValueError:
            raise ValueError(f"{entry!r}: {value!r} is not a whole number") from None
        if limit < 1:
            raise ValueError(f"{entry!r}: limit must be at least 1")
        tiers[name] = limit
    return tiers


def get_rate_limits() -> dict[str, int]:
    """Built-in defaults with the operator's overrides merged on top."""
    return {**settings.rate_limits, **parse_rate_limit_tiers(settings.rate_limit_tiers)}


async def enforce_rate_limit(
    request: Request, agent_id: UUID, plan: str, pool: asyncpg.Pool
) -> None:
    limit = get_rate_limits().get(plan, 60)
    request.state.agent_plan = plan

    if not settings.rate_limit_enabled:
        request.state.rate_limit_remaining = limit
        return

    count = await pool.fetchval(
        """INSERT INTO rate_limit_counters (agent_id, window_start, request_count)
           VALUES ($1, date_trunc('minute', now()), 1)
           ON CONFLICT (agent_id, window_start)
           DO UPDATE SET request_count = rate_limit_counters.request_count + 1
           RETURNING request_count""",
        agent_id,
    )
    request.state.rate_limit_remaining = max(limit - count, 0)

    # Cheap self-contained prune: only on the first hit of a new window per agent.
    if count == 1:
        await pool.execute(
            "DELETE FROM rate_limit_counters WHERE window_start < now() - interval '10 minutes'"
        )

    if count > limit:
        window = settings.rate_limit_window_seconds
        retry_after = window - int(time.time()) % window
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
