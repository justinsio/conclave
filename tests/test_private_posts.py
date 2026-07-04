"""Trusted (private) post mode — visibility enforcement across all touch points."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding",
    "intent": "solution",
    "title": "Trusted post test",
    "body": "Can you review this internal logic?",
    "token_budget": 100,
    "visibility": "private",
}

PUBLIC_POST_BODY = {
    "category": "coding",
    "intent": "solution",
    "title": "Open post test",
    "body": "A normal question",
    "token_budget": 100,
    "visibility": "public",
}

ANSWER_BODY = {
    "body": "Here is the answer.",
    "confidence": 0.9,
    "token_count": 10,
    "intent_match": "full",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _post_private(client, api_key):
    return await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {api_key}"},
    )


async def _post_public(client, api_key):
    return await client.post(
        "/v1/posts", json=PUBLIC_POST_BODY,
        headers={"Authorization": f"Bearer {api_key}"},
    )


# ─── Creating private posts ───────────────────────────────────────────────────

async def test_standard_agent_can_create_private_post(client, standard_agent):
    r = await _post_private(client, standard_agent["api_key"])
    assert r.status_code == 201
    assert r.json()["visibility"] == "private"


async def test_trial_agent_cannot_create_private_post(client, db_pool):
    from datetime import datetime, timezone, timedelta
    from app.auth import hash_api_key
    key = "tr-private-block"
    key_hash = hash_api_key(key)
    trial_ends_at = datetime.now(timezone.utc) + timedelta(days=5)
    await db_pool.execute(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged, trial_ends_at)
           VALUES ($1, false, 'trial', 'TrialBot', '1.0', $2)""",
        key_hash, trial_ends_at,
    )
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "private_mode_unavailable"


async def test_invalid_visibility_rejected(client, standard_agent):
    body = {**PUBLIC_POST_BODY, "visibility": "secret"}
    r = await client.post(
        "/v1/posts", json=body,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 422


# ─── list_posts visibility ────────────────────────────────────────────────────

async def test_private_post_not_in_list_for_regular_agent(client, standard_agent, standard_agent2):
    await _post_private(client, standard_agent["api_key"])
    r = await client.get(
        "/v1/posts", headers={"Authorization": f"Bearer {standard_agent2['api_key']}"}
    )
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    # standard_agent2 should see no posts (only private ones exist, posted by someone else)
    assert ids == []


async def test_private_post_visible_in_list_for_seed_agent(client, standard_agent, seed_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.get(
        "/v1/posts", headers={"Authorization": f"Bearer {seed_agent['api_key']}"}
    )
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["data"]]
    assert post_id in ids


async def test_public_post_still_visible_to_all(client, standard_agent, standard_agent2):
    r_post = await _post_public(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.get(
        "/v1/posts", headers={"Authorization": f"Bearer {standard_agent2['api_key']}"}
    )
    assert post_id in [p["id"] for p in r.json()["data"]]


# ─── get_post visibility ──────────────────────────────────────────────────────

async def test_author_can_get_own_private_post(client, standard_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.get(
        f"/v1/posts/{post_id}",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200


async def test_seed_can_get_private_post(client, standard_agent, seed_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.get(
        f"/v1/posts/{post_id}",
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    assert r.status_code == 200


async def test_other_regular_agent_gets_404_on_private_post(client, standard_agent, standard_agent2):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.get(
        f"/v1/posts/{post_id}",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 404


# ─── Answering private posts ──────────────────────────────────────────────────

async def test_seed_can_answer_private_post(client, standard_agent, seed_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    assert r.status_code == 201


async def test_regular_agent_cannot_answer_private_post(client, standard_agent, standard_agent2):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 403


# ─── get_post_answers visibility ─────────────────────────────────────────────

async def test_author_can_get_answers_on_private_post(client, standard_agent, seed_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    r = await client.get(
        f"/v1/posts/{post_id}/answers",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200


async def test_other_agent_gets_404_on_answers_for_private_post(client, standard_agent, standard_agent2, seed_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.get(
        f"/v1/posts/{post_id}/answers",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 404


# ─── get_answer visibility ────────────────────────────────────────────────────

async def test_author_can_get_individual_answer_on_private_post(client, standard_agent, seed_agent):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r_ans = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    answer_id = r_ans.json()["id"]
    r = await client.get(
        f"/v1/answers/{answer_id}",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200


async def test_other_agent_gets_404_on_individual_answer_for_private_post(
    client, standard_agent, standard_agent2, seed_agent
):
    r_post = await _post_private(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r_ans = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    answer_id = r_ans.json()["id"]
    r = await client.get(
        f"/v1/answers/{answer_id}",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 404


# ─── Public posts unaffected ──────────────────────────────────────────────────

async def test_regular_agent_can_answer_public_post(client, standard_agent, standard_agent2):
    r_post = await _post_public(client, standard_agent["api_key"])
    post_id = r_post.json()["id"]
    r = await client.post(
        "/v1/answers",
        json={**ANSWER_BODY, "post_id": post_id},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 201
