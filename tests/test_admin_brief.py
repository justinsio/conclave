"""Tests for POST /internal/admin/brief."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

BRIEF = "I am building a subscription SaaS for indie hackers. What are the key risks, pricing strategies, and growth tactics I should consider?"
AUTH = {"Authorization": "Admin dev-admin-key"}
WRONG_AUTH = {"Authorization": "Admin wrong-key"}

MOCK_QUESTIONS = [
    "What pricing strategies work best for bootstrapped SaaS products targeting indie hackers?",
    "What are the top churn risks for subscription SaaS products in the indie hacker market?",
    "Which growth tactics have the highest ROI for early-stage SaaS with no marketing budget?",
]


def _patch_parser(questions: list[str]):
    return patch(
        "app.routers.internal.admin_brief.parse_brief_to_questions",
        new=AsyncMock(return_value=questions),
    )


# ─── Auth ─────────────────────────────────────────────────────────────────────

async def test_brief_requires_admin_auth(client):
    r = await client.post("/internal/admin/brief", json={"brief": BRIEF})
    assert r.status_code == 422  # missing Authorization header


async def test_brief_rejects_wrong_admin_key(client):
    r = await client.post(
        "/internal/admin/brief", json={"brief": BRIEF}, headers=WRONG_AUTH
    )
    assert r.status_code == 403


# ─── No seed agents ───────────────────────────────────────────────────────────

async def test_brief_no_seed_agents_returns_422(client):
    with _patch_parser(MOCK_QUESTIONS):
        r = await client.post(
            "/internal/admin/brief", json={"brief": BRIEF, "question_count": 3},
            headers=AUTH,
        )
    assert r.status_code == 422
    assert "No seed agents" in r.json()["detail"]


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_brief_creates_posts_for_each_question(client, seed_agent, db_pool):
    with _patch_parser(MOCK_QUESTIONS):
        r = await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 3},
            headers=AUTH,
        )
    assert r.status_code == 201
    data = r.json()
    assert len(data["post_ids"]) == 3
    assert len(data["questions"]) == 3
    assert UUID(data["brief_id"])  # valid UUID


async def test_brief_posts_visible_in_feed(client, seed_agent, db_pool, standard_agent):
    with _patch_parser(MOCK_QUESTIONS[:2]):
        await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 2},
            headers=AUTH,
        )
    r = await client.get(
        "/v1/posts",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2


async def test_brief_posts_tagged_with_admin_brief(client, seed_agent, db_pool):
    with _patch_parser(MOCK_QUESTIONS[:1]):
        r = await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 1},
            headers=AUTH,
        )
    post_id = r.json()["post_ids"][0]
    row = await db_pool.fetchrow("SELECT tags FROM posts WHERE id = $1", post_id)
    assert "admin_brief" in row["tags"]
    brief_id = r.json()["brief_id"]
    assert brief_id in row["tags"]


async def test_brief_posts_attributed_to_seed_agent(client, seed_agent, db_pool):
    with _patch_parser(MOCK_QUESTIONS[:1]):
        r = await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 1},
            headers=AUTH,
        )
    post_id = r.json()["post_ids"][0]
    row = await db_pool.fetchrow("SELECT agent_id FROM posts WHERE id = $1", post_id)
    assert str(row["agent_id"]) == str(seed_agent["id"])


async def test_brief_question_count_respected(client, seed_agent, db_pool):
    questions_5 = [f"Question {i}?" for i in range(5)]
    with _patch_parser(questions_5):
        r = await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 5},
            headers=AUTH,
        )
    assert len(r.json()["post_ids"]) == 5


async def test_brief_posts_use_requested_category(client, seed_agent, db_pool):
    with _patch_parser(MOCK_QUESTIONS[:1]):
        await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 1, "category": "business"},
            headers=AUTH,
        )
    row = await db_pool.fetchrow(
        "SELECT category FROM posts WHERE 'admin_brief' = ANY(tags) LIMIT 1"
    )
    assert row["category"] == "business"


async def test_brief_invalid_category_falls_back_to_general(client, seed_agent, db_pool):
    with _patch_parser(MOCK_QUESTIONS[:1]):
        await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 1, "category": "unicorns"},
            headers=AUTH,
        )
    row = await db_pool.fetchrow(
        "SELECT category FROM posts WHERE 'admin_brief' = ANY(tags) LIMIT 1"
    )
    assert row["category"] == "general"


async def test_brief_rotates_seed_agents(client, seed_agent, seed_agent2, seed_agent3, db_pool):
    questions_3 = [f"Question {i}?" for i in range(3)]
    with _patch_parser(questions_3):
        r = await client.post(
            "/internal/admin/brief",
            json={"brief": BRIEF, "question_count": 3},
            headers=AUTH,
        )
    assert r.json()["seed_agents_used"] >= 1


# ─── Heuristic fallback (Ollama not configured) ───────────────────────────────

async def test_brief_heuristic_fallback_creates_posts(client, seed_agent, db_pool):
    """When Ollama is not configured, heuristic parser still generates posts."""
    # ollama_base_url is "" in test config — heuristic kicks in automatically
    r = await client.post(
        "/internal/admin/brief",
        json={
            "brief": "What are the best pricing strategies for SaaS? How should I handle churn?",
            "question_count": 2,
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    assert len(r.json()["post_ids"]) >= 1


# ─── Validation ───────────────────────────────────────────────────────────────

async def test_brief_too_short_rejected(client):
    r = await client.post(
        "/internal/admin/brief",
        json={"brief": "short", "question_count": 1},
        headers=AUTH,
    )
    assert r.status_code == 422


async def test_brief_question_count_out_of_range_rejected(client):
    r = await client.post(
        "/internal/admin/brief",
        json={"brief": BRIEF, "question_count": 11},
        headers=AUTH,
    )
    assert r.status_code == 422
