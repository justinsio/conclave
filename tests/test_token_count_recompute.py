"""R7 / Pass-4 H4 — public answer + clarification paths must recompute token_count.

The models comment "hint only — server recomputes", and the internal/seed path
(threads.py) does recompute via compute_token_count(). But the public POST
/v1/answers and POST /v1/clarifications stored the self-reported body.token_count
verbatim — so a stranger could under-report tokens (cost misattribution) or
over-report. Server now recomputes from the actual text, matching the internal
path.
"""
from __future__ import annotations

import pytest

from app.services.token_count import compute_token_count
from tests.conftest import _make_post

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding", "intent": "solution",
    "title": "Token recompute test", "body": "Body.", "token_budget": 100,
}


async def test_answer_token_count_is_recomputed_not_self_reported(
    client, standard_agent, standard_agent2, db_pool
):
    r = await client.post(
        "/v1/posts", json=POST_BODY,
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    post_id = r.json()["id"]

    answer_body = "x" * 400  # ~100 tokens; self-report a dishonest 1
    r2 = await client.post(
        "/v1/answers",
        json={
            "post_id": post_id, "body": answer_body,
            "confidence": 0.9, "token_count": 1, "intent_match": "full",
        },
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r2.status_code == 201
    assert r2.json()["token_count"] == compute_token_count(answer_body)
    assert r2.json()["token_count"] != 1


async def test_clarification_token_count_is_recomputed_not_self_reported(
    client, standard_agent, standard_agent2, db_pool
):
    post = await _make_post(db_pool, standard_agent["id"])  # allow_clarification default TRUE

    question = "y" * 80  # ~20 tokens; self-report a dishonest 1
    r = await client.post(
        "/v1/clarifications",
        json={"post_id": str(post["id"]), "question": question, "token_count": 1},
        headers={"Authorization": f"Bearer {standard_agent2['api_key']}"},
    )
    assert r.status_code == 201
    stored = await db_pool.fetchval(
        "SELECT token_count FROM clarifications WHERE id = $1", r.json()["id"]
    )
    assert stored == compute_token_count(question)
    assert stored != 1
