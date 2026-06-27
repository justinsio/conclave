"""CR-01 / R9 — POST /v1/admin/agents/{id}/restore must lift hard bans.

`restore` previously only cleared `is_shadow_banned`; auth blocks on the `bans`
table, so a "restored" agent stayed locked out (a false-success on the operator's
only un-ban lever). These tests assert the end-to-end un-ban actually works.
"""
from __future__ import annotations

import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")

ADMIN = {"Authorization": f"Admin {settings.admin_api_key}"}


def _bearer(agent: dict) -> dict:
    return {"Authorization": f"Bearer {agent['api_key']}"}


async def _hard_ban(db_pool, agent_id, expires_at=None) -> None:
    await db_pool.execute(
        "INSERT INTO bans (agent_id, reason, expires_at, issued_by) "
        "VALUES ($1, 'test ban', $2, 'admin')",
        agent_id, expires_at,
    )


async def test_restore_lifts_hard_ban_so_agent_can_authenticate(client, standard_agent, db_pool):
    await _hard_ban(db_pool, standard_agent["id"])  # permanent ban

    # Sanity: a banned agent is rejected at auth.
    pre = await client.get("/v1/agents/me", headers=_bearer(standard_agent))
    assert pre.status_code == 403

    r = await client.post(
        f"/v1/admin/agents/{standard_agent['id']}/restore", headers=ADMIN
    )
    assert r.status_code == 200

    # The whole point: the agent can authenticate again after restore.
    post = await client.get("/v1/agents/me", headers=_bearer(standard_agent))
    assert post.status_code == 200


async def test_restore_response_reports_hard_ban_lifted(client, standard_agent, db_pool):
    await _hard_ban(db_pool, standard_agent["id"])

    r = await client.post(
        f"/v1/admin/agents/{standard_agent['id']}/restore", headers=ADMIN
    )
    assert r.status_code == 200
    body = r.json()
    assert body["is_shadow_banned"] is False
    assert body["hard_ban_lifted"] is True


async def test_restore_reports_no_hard_ban_when_none_active(client, standard_agent, db_pool):
    # Only shadow-banned, no row in `bans`.
    await db_pool.execute(
        "UPDATE agents SET is_shadow_banned = TRUE WHERE id = $1", standard_agent["id"]
    )
    r = await client.post(
        f"/v1/admin/agents/{standard_agent['id']}/restore", headers=ADMIN
    )
    assert r.status_code == 200
    assert r.json()["hard_ban_lifted"] is False
