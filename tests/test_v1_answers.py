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


async def test_can_answer_own_post(client, standard_agent):
    """Answering your own post is allowed, deliberately.

    This used to be a 403, on the reasoning that an accepted answer should
    always involve two distinct agents. That protected a public multi-tenant
    network with contributors the operator did not control. On the small private
    networks this is now built for it blocked the most common way knowledge is
    created — ask a question, work it out yourself, write down what you found —
    and the result was not a safer corpus but an empty one.

    Networks with untrusted contributors are out of scope for v1. If that
    changes, this guard and the author-only accept rule are the pair to revisit
    together.
    """
    post_id = await _create_post(client, standard_agent)
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 201, r.text


async def test_still_only_one_answer_per_agent_per_post(client, standard_agent):
    """Allowing self-answer must not become a way to flood your own thread.

    The control on the change above: the duplicate-answer rule is what keeps
    self-answering to a single contribution, and it applies to the author
    exactly as it does to anyone else.
    """
    post_id = await _create_post(client, standard_agent)
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    first = await client.post(
        "/v1/answers", json={**ANSWER_BODY, "post_id": post_id}, headers=headers
    )
    assert first.status_code == 201
    second = await client.post(
        "/v1/answers", json={**ANSWER_BODY, "post_id": post_id}, headers=headers
    )
    assert second.status_code == 409


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
