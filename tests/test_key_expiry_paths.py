"""HR-02 / R8 — key expiry must be enforced on the connect path, not just require_agent.

`key_expires_at` was checked only in `require_agent`. `POST /v1/agents/connect`
authenticates via `require_agent_no_rules_check`, which skipped the gate — so an
expired beta key could still connect and refresh `last_connected_at`, muddying
"active beta user" metrics. The check belongs AFTER the rate limiter on each path
(preserving the Part 3 DoS-bypass fix), not in `_lookup_agent` (which runs first).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import hash_api_key
from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")

CONNECT_BODY = {"rules_version_acknowledged": settings.rules_version}


async def _beta_agent(pool, api_key, key_expires_at):
    await pool.execute(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged, key_expires_at)
           VALUES ($1, FALSE, 'reader', 'BetaAgent', $2, $3)""",
        hash_api_key(api_key), settings.rules_version, key_expires_at,
    )


def _bearer(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


async def test_expired_key_blocked_on_connect(client, db_pool):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _beta_agent(db_pool, "beta-expired-connect", past)

    resp = await client.post(
        "/v1/agents/connect", json=CONNECT_BODY, headers=_bearer("beta-expired-connect")
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "key_expired"


async def test_active_key_allowed_on_connect(client, db_pool):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    await _beta_agent(db_pool, "beta-active-connect", future)

    resp = await client.post(
        "/v1/agents/connect", json=CONNECT_BODY, headers=_bearer("beta-active-connect")
    )
    assert resp.status_code == 200


async def test_null_expiry_connects(client, db_pool):
    await _beta_agent(db_pool, "beta-null-connect", None)

    resp = await client.post(
        "/v1/agents/connect", json=CONNECT_BODY, headers=_bearer("beta-null-connect")
    )
    assert resp.status_code == 200
