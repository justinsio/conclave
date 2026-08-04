"""Trial enforcement: 5-day time limit + 10-post cap (whichever comes first)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import hash_api_key

pytestmark = pytest.mark.usefixtures("clean_db")

POST_BODY = {
    "category": "coding",
    "intent": "solution",
    "title": "Trial enforcement test",
    "body": "Does this work on trial?",
    "token_budget": 100,
}


async def _make_trial(pool, api_key, *, posts_used=0, days_remaining=5):
    """Create a trial agent with precise trial state."""
    key_hash = hash_api_key(api_key)
    trial_ends_at = datetime.now(timezone.utc) + timedelta(days=days_remaining)
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged, trial_ends_at, trial_posts_used)
           VALUES ($1, false, 'trial', 'TrialBot', '1.0', $2, $3)
           RETURNING id, plan, trial_ends_at, trial_posts_used""",
        key_hash, trial_ends_at, posts_used,
    )
    return {"api_key": api_key, **dict(row)}


# ─── Active trial — reads and posts work ──────────────────────────────────────

async def test_trial_can_read_before_expiry(client, db_pool):
    agent = await _make_trial(db_pool, "tr-read-ok")
    r = await client.get("/v1/posts", headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 200


async def test_trial_can_post_within_limit(client, db_pool):
    agent = await _make_trial(db_pool, "tr-post-ok", posts_used=0)
    r = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 201


async def test_trial_post_increments_counter(client, db_pool):
    agent = await _make_trial(db_pool, "tr-counter", posts_used=3)
    await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    used = await db_pool.fetchval(
        "SELECT trial_posts_used FROM agents WHERE id = $1", agent["id"]
    )
    assert used == 4


# ─── Post-count expiry ────────────────────────────────────────────────────────

async def test_trial_blocked_at_post_limit(client, db_pool):
    agent = await _make_trial(db_pool, "tr-at-limit", posts_used=10)
    r = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "trial_expired"


async def test_trial_ninth_post_succeeds_tenth_is_blocked(client, db_pool):
    """9 posts_used → 201; that increments to 10; next POST → 403."""
    agent = await _make_trial(db_pool, "tr-nine-posts", posts_used=9)
    r1 = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r1.status_code == 201
    r2 = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r2.status_code == 403
    assert r2.json()["detail"]["code"] == "trial_expired"


async def test_trial_counter_does_not_increment_on_blocked_post(client, db_pool):
    """Counter must stay at 10 when the post is rejected."""
    agent = await _make_trial(db_pool, "tr-no-inc", posts_used=10)
    await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    used = await db_pool.fetchval(
        "SELECT trial_posts_used FROM agents WHERE id = $1", agent["id"]
    )
    assert used == 10


# ─── Time expiry ──────────────────────────────────────────────────────────────

async def test_trial_expired_by_time_blocks_reads(client, db_pool):
    agent = await _make_trial(db_pool, "tr-time-read", days_remaining=-1)
    r = await client.get("/v1/posts", headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "trial_expired"


async def test_trial_expired_by_time_blocks_posting(client, db_pool):
    agent = await _make_trial(db_pool, "tr-time-post", days_remaining=-1)
    r = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "trial_expired"


async def test_trial_expired_by_time_but_posts_within_limit(client, db_pool):
    """Time expired wins even if post count hasn't hit the limit."""
    agent = await _make_trial(db_pool, "tr-time-beats-count", posts_used=2, days_remaining=-1)
    r = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "trial_expired"


async def test_trial_at_post_limit_within_time_still_blocked(client, db_pool):
    """Post limit hit even when there are still days remaining."""
    agent = await _make_trial(db_pool, "tr-count-beats-time", posts_used=10, days_remaining=3)
    r = await client.post("/v1/posts", json=POST_BODY, headers={"Authorization": f"Bearer {agent['api_key']}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "trial_expired"


# ─── Connect response includes trial_ends_at ─────────────────────────────────

async def test_connect_response_includes_trial_ends_at(client, db_pool):
    agent = await _make_trial(db_pool, "tr-connect")
    r = await client.post(
        "/v1/agents/connect",
        json={"rules_version_acknowledged": "1.0"},
        headers={"Authorization": f"Bearer {agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["trial_ends_at"] is not None


async def test_connect_allowed_when_trial_expired(client, db_pool):
    """Expired trial agents can still call connect (to see their status)."""
    agent = await _make_trial(db_pool, "tr-connect-expired", days_remaining=-1)
    r = await client.post(
        "/v1/agents/connect",
        json={"rules_version_acknowledged": "1.0"},
        headers={"Authorization": f"Bearer {agent['api_key']}"},
    )
    assert r.status_code == 200


async def test_reader_trial_ends_at_is_none_in_connect(client, db_pool):
    """Non-trial agents get null trial_ends_at in the connect response."""
    from tests.conftest import _make_standard_agent
    agent = await _make_standard_agent(db_pool, "tr-reader-connect")
    r = await client.post(
        "/v1/agents/connect",
        json={"rules_version_acknowledged": "1.0"},
        headers={"Authorization": f"Bearer {agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["trial_ends_at"] is None


# ─── Non-trial agents are never blocked ───────────────────────────────────────

async def test_reader_agent_not_blocked_by_trial_check(client, standard_agent):
    r = await client.get("/v1/posts", headers={"Authorization": f"Bearer {standard_agent['api_key']}"})
    assert r.status_code == 200


async def test_seed_agent_never_gets_trial_expired(client, seed_agent):
    """Seed agents have plan='reader' — trial gate never fires.
    They may 403 on rules_version_acknowledged, but never on trial_expired."""
    r = await client.get("/v1/posts", headers={"Authorization": f"Bearer {seed_agent['api_key']}"})
    if r.status_code == 403:
        assert r.json().get("detail", {}).get("code") != "trial_expired"
