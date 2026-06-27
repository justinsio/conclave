"""HR-03 / R12 — concurrent duplicate votes must not 500, and must count once.

The upvote path was a check-then-act TOCTOU on the bare pool: the `existing`
SELECT and the INSERT were not atomic, so two simultaneous identical votes both
passed the check, both INSERTed, and the DB's UNIQUE(agent_id, answer_id) raised
an unhandled asyncpg.UniqueViolationError → raw 500 instead of the intended 409.
All existing duplicate-vote tests are strictly sequential, so the green suite
never exercised the concurrent path.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import _make_answer

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding", "intent": "solution",
    "title": "Concurrency test", "body": "Body.", "token_budget": 100,
}


async def test_concurrent_duplicate_votes_never_500_and_count_once(
    client, standard_agent, standard_agent2, db_pool
):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]
    answer = await _make_answer(db_pool, post_id, standard_agent["id"])

    voter = {"Authorization": f"Bearer {standard_agent2['api_key']}"}
    results = await asyncio.gather(*[
        client.post("/v1/votes", json={"answer_id": str(answer["id"])}, headers=voter)
        for _ in range(8)
    ])
    statuses = [resp.status_code for resp in results]

    # The bug: at least one request returns a raw 500 (unhandled UniqueViolation).
    assert all(s in (201, 409) for s in statuses), f"got unexpected statuses: {statuses}"
    # Exactly one vote is actually recorded; the rest are clean 409s.
    assert statuses.count(201) == 1, f"expected exactly one 201, got {statuses}"

    # No counter drift: the denormalized count matches the single real vote.
    upvotes = await db_pool.fetchval(
        "SELECT upvote_count FROM answers WHERE id = $1", answer["id"]
    )
    assert upvotes == 1
    vote_rows = await db_pool.fetchval(
        "SELECT COUNT(*) FROM votes WHERE answer_id = $1", answer["id"]
    )
    assert vote_rows == 1
