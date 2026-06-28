"""R14 — privileged admin mutations are logged to audit_log (severity='admin_action').

OWASP A09: a solo operator behind a single static admin key needs to notice admin
abuse (key minting, bans, cost-cap changes) if that key is ever compromised. These
mutations previously wrote nothing scannable; each now appends an audit_log row,
which the dashboard's recent-audit tail already surfaces.

(Injection attempts are logged separately to moderation_log; moderation-queue
resolves are recorded in moderation_queue.resolved_by/action_taken.)
"""
from __future__ import annotations

import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")

ADMIN = {"Authorization": f"Admin {settings.admin_api_key}"}


async def _audit(db_pool, action):
    return await db_pool.fetchrow(
        "SELECT agent_id, action, severity, metadata FROM audit_log WHERE action = $1",
        action,
    )


async def test_ban_is_audited(client, standard_agent, db_pool):
    r = await client.post(
        f"/v1/admin/agents/{standard_agent['id']}/ban",
        json={"reason": "spam", "duration_hours": 24}, headers=ADMIN,
    )
    assert r.status_code == 200
    row = await _audit(db_pool, "admin_ban")
    assert row is not None
    assert row["severity"] == "admin_action"
    assert str(row["agent_id"]) == str(standard_agent["id"])


async def test_restore_is_audited(client, standard_agent, db_pool):
    r = await client.post(
        f"/v1/admin/agents/{standard_agent['id']}/restore", headers=ADMIN
    )
    assert r.status_code == 200
    row = await _audit(db_pool, "admin_restore")
    assert row is not None
    assert str(row["agent_id"]) == str(standard_agent["id"])


async def test_beta_user_create_is_audited(client, db_pool):
    r = await client.post(
        "/internal/admin/beta-users",
        json={"email": "audit-create@test.io", "agent_name": "A", "category": "coding"},
        headers=ADMIN,
    )
    assert r.status_code == 200
    row = await _audit(db_pool, "admin_beta_user_create")
    assert row is not None
    assert row["severity"] == "admin_action"
    assert str(row["agent_id"]) == r.json()["agent_id"]


async def test_beta_user_extend_is_audited(client, db_pool):
    created = await client.post(
        "/internal/admin/beta-users",
        json={"email": "audit-extend@test.io", "agent_name": "B", "category": "coding"},
        headers=ADMIN,
    )
    user_id = created.json()["user_id"]
    r = await client.post(f"/internal/admin/beta-users/{user_id}/extend", headers=ADMIN)
    assert r.status_code == 200
    assert await _audit(db_pool, "admin_beta_user_extend") is not None


async def test_cost_cap_override_is_audited(client, db_pool):
    r = await client.post(
        "/internal/admin/cost/cap", json={"cap_usd": 2.5}, headers=ADMIN
    )
    assert r.status_code == 200
    row = await _audit(db_pool, "admin_cost_cap_override")
    assert row is not None
    assert row["metadata"]["cap_usd"] == 2.5


async def test_cost_cap_clear_is_audited(client, db_pool):
    r = await client.delete("/internal/admin/cost/cap", headers=ADMIN)
    assert r.status_code == 200
    assert await _audit(db_pool, "admin_cost_cap_clear") is not None
