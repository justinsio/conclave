"""Daily Haiku cost circuit breaker (Part 3).

Global daily spend cap. Fails closed: when today's spend reaches the effective
cap, new gate-requiring submissions are rejected (503). Per-agent spend is
recorded for visibility. One Telegram alert fires when the cap is first crossed.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import HTTPException

from app.config import settings
from app.services.notifications import notify_cost_breaker

logger = logging.getLogger(__name__)


async def effective_cap(pool: asyncpg.Pool) -> float:
    override = await pool.fetchval(
        "SELECT daily_cost_cap_override_usd FROM circuit_breaker_state WHERE id = 1"
    )
    if override is not None:
        return float(override)
    return settings.moderation_daily_cost_cap_usd


async def global_spend_today(pool: asyncpg.Pool) -> float:
    val = await pool.fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM moderation_spend_daily WHERE day = CURRENT_DATE"
    )
    return float(val)


async def assert_cost_budget(pool: asyncpg.Pool) -> None:
    """Raise 503 if today's global Haiku spend has reached the effective cap."""
    if not settings.moderation_gate_enabled:
        return
    cap = await effective_cap(pool)
    spend = await global_spend_today(pool)
    if spend >= cap:
        logger.info(
            "cost_breaker: daily cap reached (spend=%.4f >= cap=%.4f) — submissions paused (503)",
            spend, cap,
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "moderation_paused",
                    "message": "Moderation temporarily paused, retry later."},
        )


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * settings.haiku_input_price_per_mtok \
        + (output_tokens / 1_000_000) * settings.haiku_output_price_per_mtok


async def record_gate_cost(
    pool: asyncpg.Pool, agent_id: UUID, input_tokens: int, output_tokens: int
) -> None:
    """Record one paid gate call's spend; alert once when the cap is first crossed."""
    if input_tokens == 0 and output_tokens == 0:
        return
    cost = _cost_usd(input_tokens, output_tokens)
    prev_total = await global_spend_today(pool)
    # cost_usd is NUMERIC; asyncpg requires Decimal (a float raises DataError).
    await pool.execute(
        """INSERT INTO moderation_spend_daily (day, agent_id, cost_usd, call_count)
           VALUES (CURRENT_DATE, $1, $2, 1)
           ON CONFLICT (day, agent_id)
           DO UPDATE SET cost_usd = moderation_spend_daily.cost_usd + EXCLUDED.cost_usd,
                         call_count = moderation_spend_daily.call_count + 1""",
        agent_id, Decimal(str(cost)),
    )
    new_total = prev_total + cost
    cap = await effective_cap(pool)
    if prev_total < cap <= new_total:
        # Atomic once-per-day guard: only the first crosser wins the UPDATE.
        won = await pool.fetchval(
            """UPDATE circuit_breaker_state
                  SET cost_breaker_alerted_day = CURRENT_DATE
                WHERE id = 1
                  AND cost_breaker_alerted_day IS DISTINCT FROM CURRENT_DATE
              RETURNING id""",
        )
        if won is not None:
            logger.warning(
                "cost_breaker: daily cap crossed (spend=%.4f cap=%.4f) — alerting", new_total, cap
            )
            await notify_cost_breaker(spend_usd=new_total, cap_usd=cap)
