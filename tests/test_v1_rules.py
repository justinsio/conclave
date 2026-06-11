"""Tests for GET /v1/rules."""
import pytest

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_get_rules_no_auth(client):
    r = await client.get("/v1/rules")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert "rules" in data
    assert isinstance(data["rules"], list)
    assert len(data["rules"]) > 0
    assert "changelog" in data


async def test_get_rules_returns_rate_limit_headers(client):
    r = await client.get("/v1/rules")
    assert "x-ratelimit-limit" in r.headers
