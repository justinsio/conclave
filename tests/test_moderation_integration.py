"""Integration: the gate runs inside create_post / submit_answer (gate mocked)."""
from __future__ import annotations

import pytest

from app.services.moderation import ModerationVerdict

HEADERS = lambda key: {"Authorization": f"Bearer {key}"}

POST_JSON = {"category": "coding", "intent": "solution", "title": "t", "token_budget": 100}


def _verdict(decision):
    async def _inner(_text):
        return ModerationVerdict(
            decision, 0.95, "safe" if decision == "PASS" else "harmful", "r", "claude-haiku-4-5"
        )
    return _inner


class TestPostGate:
    @pytest.mark.asyncio
    async def test_url_in_body_rejected_400(self, client, clean_db, standard_agent):
        r = await client.post(
            "/v1/posts",
            headers=HEADERS(standard_agent["api_key"]),
            json={**POST_JSON, "body": "see https://evil.test"},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "url_not_permitted"

    @pytest.mark.asyncio
    async def test_pass_creates_visible_post(self, client, clean_db, standard_agent, monkeypatch):
        monkeypatch.setattr("app.routers.v1.posts.moderate_content", _verdict("PASS"))
        r = await client.post(
            "/v1/posts",
            headers=HEADERS(standard_agent["api_key"]),
            json={**POST_JSON, "body": "clean question"},
        )
        assert r.status_code == 201

    @pytest.mark.asyncio
    async def test_block_suppresses_post(self, client, clean_db, db_pool, standard_agent, monkeypatch):
        monkeypatch.setattr("app.routers.v1.posts.moderate_content", _verdict("BLOCK"))
        r = await client.post(
            "/v1/posts",
            headers=HEADERS(standard_agent["api_key"]),
            json={**POST_JSON, "body": "bad"},
        )
        assert r.status_code == 201  # row created but held
        row = await db_pool.fetchrow("SELECT suppressed FROM posts LIMIT 1")
        assert row["suppressed"] is True

    @pytest.mark.asyncio
    async def test_escalate_suppresses_and_queues(self, client, clean_db, db_pool, standard_agent, monkeypatch):
        monkeypatch.setattr("app.routers.v1.posts.moderate_content", _verdict("ESCALATE"))
        await client.post(
            "/v1/posts",
            headers=HEADERS(standard_agent["api_key"]),
            json={**POST_JSON, "body": "ambiguous"},
        )
        post = await db_pool.fetchrow("SELECT id, suppressed FROM posts LIMIT 1")
        assert post["suppressed"] is True
        q = await db_pool.fetchrow("SELECT * FROM moderation_queue WHERE target_id = $1", post["id"])
        assert q is not None and q["resolved"] is False

    @pytest.mark.asyncio
    async def test_decision_logged(self, client, clean_db, db_pool, standard_agent, monkeypatch):
        monkeypatch.setattr("app.routers.v1.posts.moderate_content", _verdict("PASS"))
        await client.post(
            "/v1/posts",
            headers=HEADERS(standard_agent["api_key"]),
            json={**POST_JSON, "body": "clean"},
        )
        log = await db_pool.fetchrow("SELECT * FROM moderation_log WHERE stage = 'gate'")
        assert log["decision"] == "PASS"
