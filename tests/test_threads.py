"""Integration tests for /internal/threads endpoints.

Requires: TEST_DATABASE_URL pointing to a local Postgres instance with
migrations applied (conftest.py handles this).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("clean_db")


# ─── Auth ─────────────────────────────────────────────────────────────────────

async def test_non_seed_rejected(client, non_seed_agent):
    headers = {"Authorization": f"Bearer {non_seed_agent['api_key']}"}
    r = await client.post("/internal/threads", json={"source_post_id": "00000000-0000-0000-0000-000000000001"}, headers=headers)
    assert r.status_code == 403
    assert "Seed agents only" in r.text


async def test_invalid_key_rejected(client):
    headers = {"Authorization": "Bearer bad-key"}
    r = await client.post("/internal/threads", json={"source_post_id": "00000000-0000-0000-0000-000000000001"}, headers=headers)
    assert r.status_code == 403


# ─── Create thread ────────────────────────────────────────────────────────────

async def test_create_thread(client, seed_agent, test_post):
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    r = await client.post("/internal/threads", json={"source_post_id": str(test_post["id"])}, headers=headers)
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "blind_phase"
    assert data["source_post_id"] == str(test_post["id"])
    assert data["coordinator_id"] == str(seed_agent["id"])
    assert "blind_phase_ends_at" in data
    assert "deadline" in data


async def test_create_thread_duplicate_rejected(client, seed_agent, test_post):
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    body = {"source_post_id": str(test_post["id"])}
    await client.post("/internal/threads", json=body, headers=headers)
    r = await client.post("/internal/threads", json=body, headers=headers)
    assert r.status_code == 409


async def test_create_thread_missing_post(client, seed_agent):
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    r = await client.post(
        "/internal/threads",
        json={"source_post_id": "00000000-0000-0000-0000-000000000099"},
        headers=headers,
    )
    assert r.status_code == 404


# ─── Register ─────────────────────────────────────────────────────────────────

async def _create_thread(client, agent, post_id):
    headers = {"Authorization": f"Bearer {agent['api_key']}"}
    r = await client.post("/internal/threads", json={"source_post_id": str(post_id)}, headers=headers)
    assert r.status_code == 201
    return r.json()["thread_id"]


async def test_register(client, seed_agent, seed_agent2, test_post):
    thread_id = await _create_thread(client, seed_agent, test_post["id"])
    headers = {"Authorization": f"Bearer {seed_agent2['api_key']}"}
    r = await client.post(f"/internal/threads/{thread_id}/register", headers=headers)
    assert r.status_code == 200
    assert r.json()["registered"] is True


async def test_register_duplicate_rejected(client, seed_agent, seed_agent2, test_post):
    thread_id = await _create_thread(client, seed_agent, test_post["id"])
    headers = {"Authorization": f"Bearer {seed_agent2['api_key']}"}
    await client.post(f"/internal/threads/{thread_id}/register", headers=headers)
    r = await client.post(f"/internal/threads/{thread_id}/register", headers=headers)
    assert r.status_code == 409


# ─── Draft ────────────────────────────────────────────────────────────────────

DRAFT_BODY = {
    "body": "Use a bloom filter pre-pass then dict.fromkeys(). Stays under 400MB at 10M items.",
    "confidence": 0.78,
    "approach": "bloom filter + dict.fromkeys",
    "token_count": 20,
    "intent_match": "full",
}


async def test_submit_draft(client, seed_agent, test_post):
    thread_id = await _create_thread(client, seed_agent, test_post["id"])
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    await client.post(f"/internal/threads/{thread_id}/register", headers=headers)
    r = await client.post(f"/internal/threads/{thread_id}/draft", json=DRAFT_BODY, headers=headers)
    assert r.status_code == 201
    assert r.json()["status"] == "committed"


async def test_submit_draft_duplicate_rejected(client, seed_agent, seed_agent2, test_post):
    thread_id = await _create_thread(client, seed_agent, test_post["id"])
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    headers2 = {"Authorization": f"Bearer {seed_agent2['api_key']}"}
    # Register two agents so thread stays in blind_phase after seed_agent's first draft
    await client.post(f"/internal/threads/{thread_id}/register", headers=headers)
    await client.post(f"/internal/threads/{thread_id}/register", headers=headers2)
    await client.post(f"/internal/threads/{thread_id}/draft", json=DRAFT_BODY, headers=headers)
    r = await client.post(f"/internal/threads/{thread_id}/draft", json=DRAFT_BODY, headers=headers)
    assert r.status_code == 409


# ─── Thread list ──────────────────────────────────────────────────────────────

async def test_list_threads_returns_blind_phase(client, seed_agent, test_post):
    await _create_thread(client, seed_agent, test_post["id"])
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    r = await client.get("/internal/threads?status=blind_phase", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    assert any(t["status"] == "blind_phase" for t in data["data"])


# ─── Full deliberation flow ───────────────────────────────────────────────────

async def _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post):
    """Force a thread from blind_phase to deliberating by advancing via background call."""
    thread_id = await _create_thread(client, seed_agent, test_post["id"])

    h1 = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    h2 = {"Authorization": f"Bearer {seed_agent2['api_key']}"}

    # Both register
    await client.post(f"/internal/threads/{thread_id}/register", headers=h1)
    await client.post(f"/internal/threads/{thread_id}/register", headers=h2)

    # Both submit drafts
    draft1 = {**DRAFT_BODY, "body": "Bloom filter approach: fast, 400MB peak."}
    draft2 = {**DRAFT_BODY, "body": "Chunked hash set approach: memory-safe, 380MB peak.", "confidence": 0.86}

    await client.post(f"/internal/threads/{thread_id}/draft", json=draft1, headers=h1)
    await client.post(f"/internal/threads/{thread_id}/draft", json=draft2, headers=h2)

    # All registered seeds submitted — thread should have advanced automatically
    return thread_id


async def test_contribute_after_blind_phase(client, db_pool, seed_agent, seed_agent2, seed_agent3, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    # Third seed joins as participant and contributes
    h3 = {"Authorization": f"Bearer {seed_agent3['api_key']}"}
    contrib_body = {
        "body": "Sorted approach preserves order via index map. Slightly slower but simpler.",
        "confidence": 0.72,
        "approach": "index-preserving sort",
        "token_count": 15,
        "intent_match": "full",
    }
    r = await client.post(f"/internal/threads/{thread_id}/contribute", json=contrib_body, headers=h3)
    assert r.status_code == 201
    data = r.json()
    assert data["agent_id"] == str(seed_agent3["id"])


async def test_endorse_contribution(client, db_pool, seed_agent, seed_agent2, seed_agent3, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    # Get contributions
    h1 = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    thread_r = await client.get(f"/internal/threads/{thread_id}", headers=h1)
    contributions = thread_r.json()["contributions"]
    assert len(contributions) >= 2

    # seed_agent3 endorses seed_agent2's contribution
    h3 = {"Authorization": f"Bearer {seed_agent3['api_key']}"}
    contrib_id = next(c["id"] for c in contributions if c["agent_id"] == str(seed_agent2["id"]))
    r = await client.post(
        f"/internal/threads/{thread_id}/endorse",
        json={"target_contribution_id": contrib_id, "note": "Chunked approach is safer."},
        headers=h3,
    )
    assert r.status_code == 201
    assert r.json()["signal_type"] == "endorse"


async def test_conclude_thread(client, db_pool, seed_agent, seed_agent2, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    # Coordinator gets contributions
    h1 = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    thread_r = await client.get(f"/internal/threads/{thread_id}", headers=h1)
    contributions = thread_r.json()["contributions"]
    winning_id = contributions[0]["id"]

    r = await client.post(
        f"/internal/threads/{thread_id}/conclude",
        json={
            "winning_contribution_id": winning_id,
            "conclusion_type": "consensus",
            "coordinator_note": "Endorsed, no open challenges.",
        },
        headers=h1,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "answer_posted"
    assert "public_answer_id" in data
    assert data["conclusion_type"] == "consensus"


async def test_non_coordinator_cannot_conclude(client, db_pool, seed_agent, seed_agent2, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    h2 = {"Authorization": f"Bearer {seed_agent2['api_key']}"}
    thread_r = await client.get(f"/internal/threads/{thread_id}", headers=h2)
    contributions = thread_r.json()["contributions"]

    r = await client.post(
        f"/internal/threads/{thread_id}/conclude",
        json={"winning_contribution_id": contributions[0]["id"], "conclusion_type": "consensus"},
        headers=h2,
    )
    assert r.status_code == 403


async def test_challenge_contribution(client, db_pool, seed_agent, seed_agent2, seed_agent3, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    h3 = {"Authorization": f"Bearer {seed_agent3['api_key']}"}
    # seed3 contributes first
    contrib_r = await client.post(
        f"/internal/threads/{thread_id}/contribute",
        json={
            "body": "Chunked hash set — no bounds assumption needed.",
            "confidence": 0.88,
            "approach": "chunked hash set",
            "token_count": 12,
            "intent_match": "full",
        },
        headers=h3,
    )
    contrib_id = contrib_r.json()["id"]

    # Get seed1's contribution
    h1 = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    thread_r = await client.get(f"/internal/threads/{thread_id}", headers=h1)
    seed1_contrib = next(c for c in thread_r.json()["contributions"] if c["agent_id"] == str(seed_agent["id"]))

    # seed3 challenges seed1's contribution with seed3's own as counter
    r = await client.post(
        f"/internal/threads/{thread_id}/challenge",
        json={
            "target_contribution_id": seed1_contrib["id"],
            "reasoning": "Bloom filter assumes bounded integers — question doesn't specify.",
            "counter_contribution_id": contrib_id,
        },
        headers=h3,
    )
    assert r.status_code == 201
    assert r.json()["signal_type"] == "challenge"


async def test_retract_contribution(client, db_pool, seed_agent, seed_agent2, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    h1 = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    thread_r = await client.get(f"/internal/threads/{thread_id}", headers=h1)
    my_contrib = next(c for c in thread_r.json()["contributions"] if c["agent_id"] == str(seed_agent["id"]))

    r = await client.post(f"/internal/threads/{thread_id}/retract/{my_contrib['id']}", headers=h1)
    assert r.status_code == 200
    assert r.json()["retracted"] is True


async def test_confidence_hidden_from_peers(client, db_pool, seed_agent, seed_agent2, test_post):
    thread_id = await _advance_to_deliberating(client, db_pool, seed_agent, seed_agent2, test_post)

    # seed_agent2 reads thread — seed_agent's contribution should have null confidence
    h2 = {"Authorization": f"Bearer {seed_agent2['api_key']}"}
    r = await client.get(f"/internal/threads/{thread_id}", headers=h2)
    contribs = r.json()["contributions"]

    seed1_contrib = next(c for c in contribs if c["agent_id"] == str(seed_agent["id"]))
    assert seed1_contrib["confidence"] is None  # hidden from peer

    seed2_contrib = next(c for c in contribs if c["agent_id"] == str(seed_agent2["id"]))
    assert seed2_contrib["confidence"] is not None  # own contribution visible
