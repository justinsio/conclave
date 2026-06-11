"""Tests for /v1/answers/* endpoints."""
import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding", "intent": "solution",
    "title": "Test post", "body": "Test body.",
    "token_budget": 150,
}
ANSWER_BODY = {
    "body": "Use dict.fromkeys() for order-preserving dedup.",
    "confidence": 0.88,
    "token_count": 9,
    "intent_match": "full",
}


async def _create_post(client, agent):
    r = await client.post("/v1/posts", json=POST_BODY,
                          headers={"Authorization": f"Bearer {agent['api_key']}"})
    return r.json()["id"]


async def test_submit_answer(client, standard_agent, standard_agent2):
    post_id = await _create_post(client, standard_agent)
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["intent_match"] == "full"
    assert "agent_id" not in data


async def test_cannot_answer_own_post(client, standard_agent):
    post_id = await _create_post(client, standard_agent)
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 403


async def test_duplicate_answer_rejected(client, standard_agent, standard_agent2):
    post_id = await _create_post(client, standard_agent)
    headers = {"Authorization": f"Bearer {standard_agent2['api_key']}"}
    await client.post("/v1/answers", json={**ANSWER_BODY, "post_id": post_id}, headers=headers)
    r = await client.post("/v1/answers", json={**ANSWER_BODY, "post_id": post_id}, headers=headers)
    assert r.status_code == 409


async def test_dry_run_pass(client, standard_agent, standard_agent2):
    post_id = await _create_post(client, standard_agent)
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id, "dry_run": True},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["dry_run"] is True
    assert data["result"] == "pass"


async def test_accept_answer(client, standard_agent, standard_agent2):
    post_id = await _create_post(client, standard_agent)
    ans_r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    answer_id = ans_r.json()["id"]
    r = await client.post(
        f"/v1/answers/{answer_id}/accept",
        json={"note": "Worked perfectly."},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["human_accepted"] is True
    assert r.json()["post_status"] == "resolved"


async def test_only_post_author_can_accept(client, standard_agent, standard_agent2):
    post_id = await _create_post(client, standard_agent)
    ans_r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    answer_id = ans_r.json()["id"]
    r = await client.post(
        f"/v1/answers/{answer_id}/accept",
        json={},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 403
