"""Tests for the admin cost endpoints (Part 3)."""
from __future__ import annotations

import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")

_ADMIN = {"Authorization": f"Admin {settings.admin_api_key}"}


async def test_cost_status_shape(client, standard_agent, db_pool):
    await db_pool.execute(
        """INSERT INTO moderation_spend_daily (day, agent_id, cost_usd, call_count)
           VALUES (CURRENT_DATE, $1, 0.25, 3)""",
        standard_agent["id"],
    )
    r = await client.get("/internal/admin/cost", headers=_ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["global_spend_usd"] == pytest.approx(0.25)
    assert body["effective_cap_usd"] == pytest.approx(settings.moderation_daily_cost_cap_usd)
    assert body["tripped"] is False
    assert len(body["per_agent"]) == 1


async def test_cap_override_then_clear(client, db_pool):
    r = await client.post("/internal/admin/cost/cap", json={"cap_usd": 9.0}, headers=_ADMIN)
    assert r.status_code == 200
    assert r.json()["effective_cap_usd"] == pytest.approx(9.0)
    r2 = await client.delete("/internal/admin/cost/cap", headers=_ADMIN)
    assert r2.json()["effective_cap_usd"] == pytest.approx(settings.moderation_daily_cost_cap_usd)


async def test_requires_admin(client):
    r = await client.get("/internal/admin/cost")
    assert r.status_code in (401, 403, 422)
