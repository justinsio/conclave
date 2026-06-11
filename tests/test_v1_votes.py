"""Tests for /v1/votes endpoints."""
import pytest
from tests.conftest import _make_answer

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding", "intent": "solution",
    "title": "Test", "body": "Test body.", "token_budget": 100,
}


async def _setup(client, poster, answerer, db_pool):
    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {poster['api_key']}"})
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, answerer["id"])
    return post_id, str(answer["id"])


async def test_upvote_answer(client, standard_agent, standard_agent2, db_pool):
    _, answer_id = await _setup(client, standard_agent, standard_agent2, db_pool)
    r = await client.post(
        "/v1/votes",
        json={"answer_id": answer_id},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 201
    assert r.json()["new_upvote_count"] == 1


async def test_trial_agent_cannot_vote(client, standard_agent, standard_agent2, trial_agent, db_pool):
    _, answer_id = await _setup(client, standard_agent, standard_agent2, db_pool)
    r = await client.post(
        "/v1/votes",
        json={"answer_id": answer_id},
        headers={"Authorization": f"Bearer {trial_agent['api_key']}"},
    )
    assert r.status_code == 403


async def test_cannot_vote_own_answer(client, standard_agent, standard_agent2, db_pool):
    _, answer_id = await _setup(client, standard_agent, standard_agent2, db_pool)
    r = await client.post(
        "/v1/votes",
        json={"answer_id": answer_id},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 403


async def test_duplicate_vote_rejected(client, standard_agent, standard_agent2, db_pool):
    _, answer_id = await _setup(client, standard_agent, standard_agent2, db_pool)
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    await client.post("/v1/votes", json={"answer_id": answer_id}, headers=headers)
    r = await client.post("/v1/votes", json={"answer_id": answer_id}, headers=headers)
    assert r.status_code == 409


async def test_remove_vote(client, standard_agent, standard_agent2, db_pool):
    _, answer_id = await _setup(client, standard_agent, standard_agent2, db_pool)
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    await client.post("/v1/votes", json={"answer_id": answer_id}, headers=headers)
    r = await client.delete(f"/v1/votes/{answer_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["new_upvote_count"] == 0
