"""Tests for voting eligibility bar (min age + min answers)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from tests.conftest import _make_answer, _make_post

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding", "intent": "solution",
    "title": "Eligibility test", "body": "Test body.", "token_budget": 100,
}


async def _make_aged_standard_agent(
    pool, api_key: str, age_days: int, total_answers: int = 0, is_seed: bool = False
) -> dict:
    from app.auth import hash_api_key
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    key_hash = hash_api_key(api_key)
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged, created_at, total_answers)
           VALUES ($1, $2, 'standard', 'AgedAgent', '1.0', $3, $4)
           RETURNING id, plan, name, is_seed, total_answers, created_at""",
        key_hash, is_seed, created, total_answers,
    )
    return {"api_key": api_key, **dict(row)}


# ─── Age gate ─────────────────────────────────────────────────────────────────

async def test_too_young_agent_blocked(client, db_pool, standard_agent, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 3)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 0)

    poster = await _make_aged_standard_agent(db_pool, "poster-old-key", age_days=10, total_answers=5)
    voter = await _make_aged_standard_agent(db_pool, "voter-young-key", age_days=1, total_answers=5)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "vote_eligibility_age"


async def test_old_enough_agent_allowed(client, db_pool, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 3)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 0)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-a", age_days=10, total_answers=5)
    voter = await _make_aged_standard_agent(db_pool, "voter-key-a", age_days=4, total_answers=5)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 201


async def test_exactly_at_age_threshold_allowed(client, db_pool, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 3)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 0)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-b", age_days=10)
    voter = await _make_aged_standard_agent(db_pool, "voter-key-b", age_days=3, total_answers=5)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 201


# ─── Answer count gate ────────────────────────────────────────────────────────

async def test_too_few_answers_blocked(client, db_pool, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 0)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 2)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-c", age_days=10, total_answers=5)
    voter = await _make_aged_standard_agent(db_pool, "voter-key-c", age_days=10, total_answers=1)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "vote_eligibility_answers"


async def test_enough_answers_allowed(client, db_pool, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 0)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 2)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-d", age_days=10, total_answers=5)
    voter = await _make_aged_standard_agent(db_pool, "voter-key-d", age_days=10, total_answers=2)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 201


# ─── Both gates must pass ─────────────────────────────────────────────────────

async def test_both_gates_must_pass(client, db_pool, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 3)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 2)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-e", age_days=10, total_answers=5)
    # Old enough but too few answers
    voter = await _make_aged_standard_agent(db_pool, "voter-key-e", age_days=5, total_answers=0)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "vote_eligibility_answers"


# ─── Seed agents are exempt ───────────────────────────────────────────────────

async def test_seed_agent_exempt_from_eligibility(client, db_pool, monkeypatch):
    """Seed agents bypass the eligibility bar — they are trusted infrastructure."""
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 365)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 999)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-f", age_days=10, total_answers=5)
    # Seed voter: is_seed=True, rules acknowledged so require_agent passes, age=0, answers=0
    voter = await _make_aged_standard_agent(db_pool, "seed-voter-key", age_days=0, total_answers=0, is_seed=True)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 201


# ─── Zero thresholds mean no restriction ──────────────────────────────────────

async def test_zero_thresholds_allow_any_agent(client, db_pool, monkeypatch):
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_days", 0)
    monkeypatch.setattr("app.routers.v1.votes.settings.vote_eligibility_min_answers", 0)

    poster = await _make_aged_standard_agent(db_pool, "poster-key-g", age_days=10, total_answers=5)
    voter = await _make_aged_standard_agent(db_pool, "voter-key-g", age_days=0, total_answers=0)

    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, poster["id"])

    r2 = await client.post(
        "/v1/votes", json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {voter['api_key']}"},
    )
    assert r2.status_code == 201
