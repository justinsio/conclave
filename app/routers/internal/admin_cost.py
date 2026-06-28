"""Admin cost visibility + runtime cap override (Part 3)."""
from __future__ import annotations

from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import require_admin
from app.database import get_pool
from app.services.audit import log_admin_action
from app.services.cost_breaker import effective_cap, global_spend_today

router = APIRouter(prefix="/internal/admin/cost", tags=["internal-admin"])


class AgentSpend(BaseModel):
    agent_id: str
    cost_usd: float
    call_count: int


class CostStatus(BaseModel):
    day: str
    global_spend_usd: float
    effective_cap_usd: float
    tripped: bool
    per_agent: list[AgentSpend]


class CapOverride(BaseModel):
    cap_usd: float


async def _status(pool: asyncpg.Pool) -> CostStatus:
    cap = await effective_cap(pool)
    spend = await global_spend_today(pool)
    rows = await pool.fetch(
        """SELECT agent_id, cost_usd, call_count
             FROM moderation_spend_daily
            WHERE day = CURRENT_DATE
            ORDER BY cost_usd DESC"""
    )
    day = await pool.fetchval("SELECT CURRENT_DATE")
    return CostStatus(
        day=str(day),
        global_spend_usd=spend,
        effective_cap_usd=cap,
        tripped=spend >= cap,
        per_agent=[
            AgentSpend(agent_id=str(r["agent_id"]),
                       cost_usd=float(r["cost_usd"]),
                       call_count=r["call_count"])
            for r in rows
        ],
    )


@router.get("", response_model=CostStatus, dependencies=[Depends(require_admin)])
async def get_cost_status(pool: asyncpg.Pool = Depends(get_pool)):
    return await _status(pool)


@router.post("/cap", response_model=CostStatus, dependencies=[Depends(require_admin)])
async def set_cap_override(body: CapOverride, pool: asyncpg.Pool = Depends(get_pool)):
    await pool.execute(
        "UPDATE circuit_breaker_state SET daily_cost_cap_override_usd = $1 WHERE id = 1",
        Decimal(str(body.cap_usd)),
    )
    await log_admin_action(
        pool, "admin_cost_cap_override", metadata={"cap_usd": body.cap_usd}
    )
    return await _status(pool)


@router.delete("/cap", response_model=CostStatus, dependencies=[Depends(require_admin)])
async def clear_cap_override(pool: asyncpg.Pool = Depends(get_pool)):
    await pool.execute(
        "UPDATE circuit_breaker_state SET daily_cost_cap_override_usd = NULL WHERE id = 1"
    )
    await log_admin_action(pool, "admin_cost_cap_clear")
    return await _status(pool)
