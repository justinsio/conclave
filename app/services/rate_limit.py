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


async def enforce_rate_limit(
    request: Request, agent_id: UUID, plan: str, pool: asyncpg.Pool
) -> None:
    limit = settings.rate_limits.get(plan, 60)
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
