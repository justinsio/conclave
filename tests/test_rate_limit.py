"""Tests for the per-agent rate limiter (Part 3)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import settings
from app.services.rate_limit import enforce_rate_limit

pytestmark = pytest.mark.usefixtures("clean_db")


class _State:
    """Minimal stand-in for request.state."""


class _Req:
    def __init__(self):
        self.state = _State()


async def _seed(db_pool, is_seed=False, plan="reader"):
    row = await db_pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan, rules_version_acknowledged)
           VALUES (md5(random()::text), $1, $2, '1.0') RETURNING id""",
        is_seed, plan,
    )
    return row["id"]


async def test_disabled_is_noop(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    agent_id = await _seed(db_pool)
    req = _Req()
    for _ in range(200):
        await enforce_rate_limit(req, agent_id, "reader", db_pool)  # never raises
    assert req.state.rate_limit_remaining == settings.rate_limits["reader"]


async def test_under_limit_passes(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    agent_id = await _seed(db_pool, plan="trial")  # tier 10
    req = _Req()
    for _ in range(10):
        await enforce_rate_limit(req, agent_id, "trial", db_pool)
    assert req.state.rate_limit_remaining == 0


async def test_over_limit_raises_429(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    agent_id = await _seed(db_pool, plan="trial")  # tier 10
    req = _Req()
    for _ in range(10):
        await enforce_rate_limit(req, agent_id, "trial", db_pool)
    with pytest.raises(HTTPException) as exc:
        await enforce_rate_limit(req, agent_id, "trial", db_pool)  # 11th
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


async def test_separate_agents_independent(db_pool, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    a = await _seed(db_pool, plan="trial")
    b = await _seed(db_pool, plan="trial")
    req = _Req()
    for _ in range(10):
        await enforce_rate_limit(req, a, "trial", db_pool)
    # b is unaffected by a's window
    for _ in range(10):
        await enforce_rate_limit(req, b, "trial", db_pool)
