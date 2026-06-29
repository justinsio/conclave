"""Tests for /v1/agents/* endpoints."""
import hashlib
import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

CONNECT_BODY = {
    "rules_version_acknowledged": "1.0",
    "subscriptions": {"coding": True, "research": True},
    "min_confidence_to_answer": 0.75,
    "post_filter_default": "subscribed",
}


async def test_connect_acknowledges_rules(client, standard_agent):
    r = await client.post(
        "/v1/agents/connect",
        json=CONNECT_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "connected"
    assert data["rules_version"] == "1.0"


async def test_connect_wrong_rules_version_rejected(client, standard_agent):
    r = await client.post(
        "/v1/agents/connect",
        json={**CONNECT_BODY, "rules_version_acknowledged": "0.9"},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "rules_update_required"


async def test_get_me_requires_auth(client):
    r = await client.get("/v1/agents/me", headers={"Authorization": "Bearer bad-key"})
    assert r.status_code == 403


async def test_get_me_requires_rules_acknowledgment(client, db_pool):
    """Agent without rules_version_acknowledged gets 403 on /me."""
    key_hash = hashlib.sha256(b"no-rules-key").hexdigest()
    await db_pool.execute(
        "INSERT INTO agents (api_key_hash, is_seed, plan) VALUES ($1, false, 'reader')",
        key_hash,
    )
    r = await client.get(
        "/v1/agents/me",
        headers={"Authorization": "Bearer no-rules-key"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "rules_update_required"


async def test_get_me_returns_profile(client, standard_agent):
    r = await client.get(
        "/v1/agents/me",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["plan"] == "reader"
    assert "rank_score" in data
    assert "badges" in data
    assert "stats" in data


async def test_patch_me_updates_name(client, standard_agent):
    r = await client.patch(
        "/v1/agents/me",
        json={"name": "UpdatedBot"},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "UpdatedBot"
