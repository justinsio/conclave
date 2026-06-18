"""Tests for the daily cost circuit breaker (Part 3)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services.cost_breaker import (
    _cost_usd,
    assert_cost_budget,
    effective_cap,
    global_spend_today,
    record_gate_cost,
)

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_spend_table_exists_and_empty(db_pool):
    total = await db_pool.fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM moderation_spend_daily WHERE day = CURRENT_DATE"
    )
    assert float(total) == 0.0


async def _agent(db_pool):
    row = await db_pool.fetchrow(
        "INSERT INTO agents (api_key_hash, is_seed) VALUES (md5(random()::text), false) RETURNING id"
    )
    return row["id"]


def test_cost_math():
    assert _cost_usd(1_000_000, 1_000_000) == pytest.approx(6.0)
    assert _cost_usd(2000, 500) == pytest.approx(0.0045)


async def test_record_accumulates_and_global_sum(db_pool):
    agent_id = await _agent(db_pool)
    await record_gate_cost(db_pool, agent_id, 1_000_000, 0)  # $1.00
    await record_gate_cost(db_pool, agent_id, 1_000_000, 0)  # +$1.00
    assert await global_spend_today(db_pool) == pytest.approx(2.0)
    row = await db_pool.fetchrow(
        "SELECT call_count FROM moderation_spend_daily WHERE day = CURRENT_DATE AND agent_id = $1",
        agent_id,
    )
    assert row["call_count"] == 2


async def test_zero_tokens_not_recorded(db_pool):
    agent_id = await _agent(db_pool)
    await record_gate_cost(db_pool, agent_id, 0, 0)
    assert await global_spend_today(db_pool) == pytest.approx(0.0)


async def test_assert_budget_trips_at_cap(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "moderation_gate_enabled", True)
    monkeypatch.setattr(settings, "moderation_daily_cost_cap_usd", 1.0)
    agent_id = await _agent(db_pool)
    await record_gate_cost(db_pool, agent_id, 1_000_000, 0)  # spend == cap
    with pytest.raises(HTTPException) as exc:
        await assert_cost_budget(db_pool)
    assert exc.value.status_code == 503


async def test_assert_budget_noop_when_gate_disabled(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "moderation_gate_enabled", False)
    agent_id = await _agent(db_pool)
    await record_gate_cost(db_pool, agent_id, 5_000_000, 0)  # way over default cap
    await assert_cost_budget(db_pool)  # must not raise


async def test_override_raises_cap(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "moderation_gate_enabled", True)
    monkeypatch.setattr(settings, "moderation_daily_cost_cap_usd", 1.0)
    agent_id = await _agent(db_pool)
    await record_gate_cost(db_pool, agent_id, 1_000_000, 0)  # $1.00 == default cap
    await db_pool.execute(
        "UPDATE circuit_breaker_state SET daily_cost_cap_override_usd = 5.0 WHERE id = 1"
    )
    assert await effective_cap(db_pool) == pytest.approx(5.0)
    await assert_cost_budget(db_pool)  # under raised cap → no raise


async def test_crossing_alerts_once(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "moderation_gate_enabled", True)
    await db_pool.execute(
        "UPDATE circuit_breaker_state SET daily_cost_cap_override_usd = 0.001 WHERE id = 1"
    )
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.cost_breaker.notify_cost_breaker", mock)
    agent_id = await _agent(db_pool)
    await record_gate_cost(db_pool, agent_id, 1_000_000, 0)  # crosses 0.001
    await record_gate_cost(db_pool, agent_id, 1_000_000, 0)  # already tripped
    assert mock.await_count == 1
