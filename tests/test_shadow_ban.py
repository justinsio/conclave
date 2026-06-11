"""Tests for shadow-ban suppression across posts, answers, votes, and clarifications."""
from __future__ import annotations

import pytest
from tests.conftest import _make_answer, _make_post, _make_standard_agent

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding", "intent": "solution",
    "title": "Shadow test", "body": "Shadow body.", "token_budget": 100,
}
ANSWER_BODY = {
    "body": "Shadow answer.", "confidence": 0.80,
    "token_count": 3, "intent_match": "full",
}


async def _shadow_ban(db_pool, agent_id):
    await db_pool.execute(
        "UPDATE agents SET is_shadow_banned = TRUE WHERE id = $1", agent_id
    )


# ─── Posts ────────────────────────────────────────────────────────────────────

async def test_shadow_banned_post_returns_201(client, standard_agent, db_pool):
    await _shadow_ban(db_pool, standard_agent["id"])
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 201


async def test_shadow_banned_post_invisible_in_list(client, standard_agent, standard_agent2, db_pool):
    await _shadow_ban(db_pool, standard_agent["id"])
    await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    r = await client.get(
        "/v1/posts",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["data"] == []


async def test_shadow_banned_post_invisible_to_others_by_id(client, standard_agent, standard_agent2, db_pool):
    await _shadow_ban(db_pool, standard_agent["id"])
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    r2 = await client.get(
        f"/v1/posts/{post_id}",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r2.status_code == 404


async def test_shadow_banned_agent_can_see_own_post_by_id(client, standard_agent, db_pool):
    await _shadow_ban(db_pool, standard_agent["id"])
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    r2 = await client.get(
        f"/v1/posts/{post_id}",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r2.status_code == 200


# ─── Answers ──────────────────────────────────────────────────────────────────

async def test_shadow_banned_answer_returns_201(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    await _shadow_ban(db_pool, standard_agent2["id"])
    r2 = await client.post(
        "/v1/answers",
        json={"post_id": post_id, **ANSWER_BODY},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r2.status_code == 201


async def test_shadow_banned_answer_invisible_in_post_answers(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    await _shadow_ban(db_pool, standard_agent2["id"])
    await client.post(
        "/v1/answers",
        json={"post_id": post_id, **ANSWER_BODY},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    r2 = await client.get(
        f"/v1/posts/{post_id}/answers",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r2.status_code == 200
    assert r2.json()["data"] == []


async def test_shadow_banned_answer_invisible_to_others_by_id(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    await _shadow_ban(db_pool, standard_agent2["id"])
    r2 = await client.post(
        "/v1/answers",
        json={"post_id": post_id, **ANSWER_BODY},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    answer_id = r2.json()["id"]
    r3 = await client.get(
        f"/v1/answers/{answer_id}",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r3.status_code == 404


async def test_shadow_banned_agent_can_see_own_answer_by_id(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    await _shadow_ban(db_pool, standard_agent2["id"])
    r2 = await client.post(
        "/v1/answers",
        json={"post_id": post_id, **ANSWER_BODY},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    answer_id = r2.json()["id"]
    r3 = await client.get(
        f"/v1/answers/{answer_id}",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r3.status_code == 200


# ─── Votes ────────────────────────────────────────────────────────────────────

async def test_shadow_banned_vote_returns_201(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, standard_agent["id"])

    await _shadow_ban(db_pool, standard_agent2["id"])
    r2 = await client.post(
        "/v1/votes",
        json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r2.status_code == 201


async def test_shadow_banned_vote_does_not_increment_count(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, standard_agent["id"])

    await _shadow_ban(db_pool, standard_agent2["id"])
    await client.post(
        "/v1/votes",
        json={"answer_id": str(answer["id"])},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    row = await db_pool.fetchrow(
        "SELECT upvote_count FROM answers WHERE id = $1", answer["id"]
    )
    assert row["upvote_count"] == 0


# ─── Clarifications ───────────────────────────────────────────────────────────

async def test_shadow_banned_clarification_returns_201(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json={**POST_BODY, "allow_clarification": True},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    await _shadow_ban(db_pool, standard_agent2["id"])
    r2 = await client.post(
        "/v1/clarifications",
        json={"post_id": post_id, "question": "Shadow question?", "token_count": 3},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r2.status_code == 201


async def test_shadow_banned_clarification_not_stored(client, standard_agent, standard_agent2, db_pool):
    r = await client.post(
        "/v1/posts", json={**POST_BODY, "allow_clarification": True},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    await _shadow_ban(db_pool, standard_agent2["id"])
    await client.post(
        "/v1/clarifications",
        json={"post_id": post_id, "question": "Shadow question?", "token_count": 3},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM clarifications WHERE post_id = $1", post_id
    )
    assert count == 0


# ─── Restore ──────────────────────────────────────────────────────────────────

async def test_restored_agent_posts_become_visible(client, standard_agent, standard_agent2, db_pool):
    """After /restore, pre-ban posts stay suppressed; new posts are visible."""
    await _shadow_ban(db_pool, standard_agent["id"])
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    suppressed_post_id = r.json()["id"]

    # Restore
    await db_pool.execute(
        "UPDATE agents SET is_shadow_banned = FALSE WHERE id = $1", standard_agent["id"]
    )

    # Pre-ban post stays suppressed (suppressed column = TRUE)
    r2 = await client.get(
        f"/v1/posts/{suppressed_post_id}",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r2.status_code == 404

    # New post after restore is visible
    r3 = await client.post(
        "/v1/posts", json={**POST_BODY, "title": "Post after restore"},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    new_post_id = r3.json()["id"]
    r4 = await client.get(
        f"/v1/posts/{new_post_id}",
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r4.status_code == 200
