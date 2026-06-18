"""Integration: rate limit enforced through a real authenticated endpoint."""
from __future__ import annotations

import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_reader_429_after_tier(client, standard_agent, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setitem(settings.rate_limits, "reader", 3)  # tighten for the test
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    statuses = [(await client.get("/v1/posts", headers=headers)).status_code
                for _ in range(4)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429


async def test_headers_reflect_remaining(client, standard_agent, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    r = await client.get("/v1/posts", headers=headers)
    assert "X-RateLimit-Remaining" in r.headers
    assert int(r.headers["X-RateLimit-Remaining"]) == settings.rate_limits.get("reader", 60) - 1


async def test_disabled_no_429(client, standard_agent, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setitem(settings.rate_limits, "reader", 1)
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    for _ in range(5):
        r = await client.get("/v1/posts", headers=headers)
        assert r.status_code != 429
