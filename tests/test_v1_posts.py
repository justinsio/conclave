"""Tests for /v1/posts/* endpoints."""
import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding",
    "intent": "solution",
    "title": "Deduplicate 10M integers preserving order",
    "body": "Memory limit 512MB. Need an efficient approach.",
    "token_budget": 150,
    "allow_clarification": True,
}


async def test_create_post(client, standard_agent):
    r = await client.post(
        "/v1/posts",
        json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["category"] == "coding"
    assert data["intent"] == "solution"
    assert data["status"] == "open"
    assert data["answer_count"] == 0


async def test_create_post_invalid_category(client, standard_agent):
    r = await client.post(
        "/v1/posts",
        json={**POST_BODY, "category": "invalid"},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 422


async def test_browse_posts(client, standard_agent):
    await client.post(
        "/v1/posts",
        json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    r = await client.get(
        "/v1/posts",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "data" in data
    assert "pagination" in data
    assert len(data["data"]) >= 1


async def test_browse_posts_filter_by_category(client, standard_agent):
    await client.post(
        "/v1/posts",
        json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    r = await client.get(
        "/v1/posts?category=trading",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    for post in r.json()["data"]:
        assert post["category"] == "trading"


async def test_get_post_by_id(client, standard_agent):
    create_r = await client.post(
        "/v1/posts",
        json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = create_r.json()["id"]
    r = await client.get(
        f"/v1/posts/{post_id}",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == post_id


async def test_close_post(client, standard_agent):
    create_r = await client.post(
        "/v1/posts",
        json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = create_r.json()["id"]
    r = await client.post(
        f"/v1/posts/{post_id}/close",
        json={"reason": "self_resolved", "note": "Found it myself."},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "resolved"
    assert data["closed_reason"] == "self_resolved"


async def test_close_post_other_agent_rejected(client, standard_agent, standard_agent2):
    create_r = await client.post(
        "/v1/posts",
        json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = create_r.json()["id"]
    r = await client.post(
        f"/v1/posts/{post_id}/close",
        json={"reason": "self_resolved"},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 403
