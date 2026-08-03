"""A malformed ?cursor= must be 400 on every paginated endpoint, never 500.

Verified against the live stack before the fix: all four shapes below returned
500 on GET /v1/posts. That is the server reporting its own failure for a client's
typo — and since any authenticated agent can trigger it at will, it buries real
incidents in noise that looks identical to them.

Driven over HTTP rather than through build_cursor_clause directly: the unit tests
pin the helper, these pin what a caller actually receives, which is the thing
that was broken.
"""
from __future__ import annotations

import base64
import json

import pytest

CASES = [
    pytest.param("not-base64!!!", id="not-base64"),
    pytest.param(base64.urlsafe_b64encode(b"hello").decode(), id="valid-b64-not-json"),
    pytest.param(base64.urlsafe_b64encode(b"null").decode(), id="json-null"),
    pytest.param(base64.urlsafe_b64encode(b"[1,2]").decode(), id="json-list"),
    pytest.param(
        base64.urlsafe_b64encode(json.dumps({"foo": "bar"}).encode()).decode(),
        id="right-type-wrong-keys",
    ),
]


@pytest.mark.parametrize("cursor", CASES)
async def test_list_posts_rejects_a_malformed_cursor(client, clean_db, standard_agent, cursor):
    r = await client.get(
        "/v1/posts",
        params={"cursor": cursor},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize("cursor", CASES)
async def test_agent_history_rejects_a_malformed_cursor(client, clean_db, standard_agent, cursor):
    r = await client.get(
        "/v1/agents/me/history",
        params={"cursor": cursor},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize("cursor", CASES)
async def test_post_answers_rejects_a_malformed_cursor(
    client, clean_db, standard_agent, seed_agent, test_post, cursor
):
    r = await client.get(
        f"/v1/posts/{test_post['id']}/answers",
        params={"cursor": cursor},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"


async def test_a_real_cursor_from_the_api_still_paginates(client, clean_db, db_pool, standard_agent):
    """The guard must reject junk without rejecting the API's own cursors — the
    obvious way to 'fix' a 500 is to reject more than you should."""
    from tests.conftest import _make_post

    for _ in range(3):
        await _make_post(db_pool, standard_agent["id"], category="coding")

    first = await client.get(
        "/v1/posts",
        params={"category": "coding", "limit": 2, "sort": "recent"},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert first.status_code == 200
    cursor = first.json()["pagination"]["next_cursor"]
    assert cursor, "expected a next_cursor with 3 posts and limit=2"

    second = await client.get(
        "/v1/posts",
        params={"category": "coding", "limit": 2, "sort": "recent", "cursor": cursor},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert second.status_code == 200

    first_ids = {p["id"] for p in first.json()["data"]}
    second_ids = {p["id"] for p in second.json()["data"]}
    assert not (first_ids & second_ids), "pages must not overlap"


async def test_answers_pagination_round_trips_an_int_cursor(
    client, clean_db, db_pool, standard_agent, seed_agent, test_post
):
    """The answers endpoint sorts on upvote_count (INTEGER), a different code
    path from the timestamp cursors above and a different binding failure."""
    from tests.conftest import _make_answer

    for i in range(3):
        await _make_answer(db_pool, test_post["id"], seed_agent["id"],
                           body=f"Answer number {i}.", upvote_count=i)

    hdrs = {"Authorization": f"Bearer {standard_agent['api_key']}"}
    first = await client.get(
        f"/v1/posts/{test_post['id']}/answers",
        params={"limit": 2, "sort": "upvotes"}, headers=hdrs,
    )
    assert first.status_code == 200
    cursor = first.json()["pagination"]["next_cursor"]
    assert cursor, "expected a next_cursor with 3 answers and limit=2"

    second = await client.get(
        f"/v1/posts/{test_post['id']}/answers",
        params={"limit": 2, "sort": "upvotes", "cursor": cursor}, headers=hdrs,
    )
    assert second.status_code == 200, f"following our own cursor 500'd: {second.text[:200]}"
    assert not (
        {a["id"] for a in first.json()["data"]} & {a["id"] for a in second.json()["data"]}
    ), "pages must not overlap"


async def test_no_cursor_is_still_fine(client, clean_db, standard_agent):
    r = await client.get(
        "/v1/posts", headers={"Authorization": f"Bearer {standard_agent['api_key']}"}
    )
    assert r.status_code == 200
