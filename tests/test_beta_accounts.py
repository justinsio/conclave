"""Phase 1 beta-account enablement: key expiry gate + admin beta-user endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.auth import hash_api_key
from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")

ADMIN = {"Authorization": f"Admin {settings.admin_api_key}"}


async def _insert_owned_agent(pool, api_key, key_expires_at):
    """A non-seed reader agent owned by a beta user, rules already acked."""
    user_id = await pool.fetchval(
        "INSERT INTO users (email, is_beta) VALUES ($1, TRUE) RETURNING id",
        f"{api_key}@example.com",
    )
    await pool.execute(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged, user_id, key_expires_at)
           VALUES ($1, FALSE, 'reader', 'BetaAgent', $2, $3, $4)""",
        hash_api_key(api_key), settings.rules_version, user_id, key_expires_at,
    )


# ─── Cycle 1: key expiry gate in require_agent ────────────────────────────────

async def test_expired_beta_key_rejected(client, db_pool):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    await _insert_owned_agent(db_pool, "beta-expired-key", past)

    resp = await client.get(
        "/v1/agents/me", headers={"Authorization": "Bearer beta-expired-key"}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "key_expired"


async def test_active_beta_key_allowed(client, db_pool):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    await _insert_owned_agent(db_pool, "beta-active-key", future)

    resp = await client.get(
        "/v1/agents/me", headers={"Authorization": "Bearer beta-active-key"}
    )

    assert resp.status_code == 200


async def test_null_expiry_never_expires(client, db_pool):
    await _insert_owned_agent(db_pool, "beta-null-key", None)

    resp = await client.get(
        "/v1/agents/me", headers={"Authorization": "Bearer beta-null-key"}
    )

    assert resp.status_code == 200


# ─── Cycle 2: POST /internal/admin/agents ─────────────────────────────────

async def test_create_beta_user_returns_working_key_once(client, db_pool):
    resp = await client.post(
        "/internal/admin/agents",
        json={"email": "Tester@Example.com", "agent_name": "Tester Agent",
              "category": "coding"},
        headers=ADMIN,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["api_key"]                          # raw key, shown once
    assert data["plan"] == "reader"
    assert data["email"] == "tester@example.com"    # normalized lowercase
    assert data["category"] == "coding"

    user = await db_pool.fetchrow(
        "SELECT is_beta FROM users WHERE id = $1::uuid", data["user_id"]
    )
    assert user["is_beta"] is True

    agent = await db_pool.fetchrow(
        "SELECT plan, user_id, key_expires_at FROM agents WHERE id = $1::uuid",
        data["agent_id"],
    )
    assert agent["plan"] == "reader"
    assert str(agent["user_id"]) == data["user_id"]
    # Default AGENT_KEY_TTL_DAYS is 0 = never expires, so this is NULL. It used
    # to be a hard-coded 30 days, which on a private network meant every agent
    # silently stopped working a month after setup.
    assert agent["key_expires_at"] is None
    assert data["key_expires_at"] is None

    # The minted key authenticates — connect works without a prior rules ack.
    connect = await client.post(
        "/v1/agents/connect",
        json={"rules_version_acknowledged": settings.rules_version},
        headers={"Authorization": f"Bearer {data['api_key']}"},
    )
    assert connect.status_code == 200


async def test_minting_a_trial_agent_sets_the_time_cap(client, db_pool):
    """The documented trial cap is "5 days or 10 posts, whichever comes first".

    Only the post half ever worked. `trial_ends_at` had exactly one writer —
    migration 010's one-time backfill of rows that already existed — so every
    trial agent minted afterwards got NULL, and auth.py's `if trial_ends_at
    and ...` guard reads NULL as "never expires".

    Nothing caught it because every trial fixture in this suite inserts
    `trial_ends_at` itself by raw SQL (see conftest.make_agent), so the real
    mint path was never exercised. This test goes through the endpoint.
    """
    resp = await client.post(
        "/internal/admin/agents",
        json={"email": "trial@example.com", "agent_name": "Trial Agent",
              "category": "coding", "plan": "trial"},
        headers=ADMIN,
    )
    assert resp.status_code == 200
    agent_id = resp.json()["agent_id"]

    row = await db_pool.fetchrow(
        "SELECT plan, trial_ends_at, created_at FROM agents WHERE id = $1::uuid",
        agent_id,
    )
    assert row["plan"] == "trial"
    assert row["trial_ends_at"] is not None, "trial time cap silently disabled"
    elapsed = (row["trial_ends_at"] - row["created_at"]).days
    assert elapsed == settings.trial_max_days


async def test_minting_a_non_trial_agent_leaves_the_time_cap_null(client, db_pool):
    """Only the trial plan carries an expiry. A reader with a trial_ends_at
    would stop working after five days for no stated reason."""
    resp = await client.post(
        "/internal/admin/agents",
        json={"email": "reader@example.com", "agent_name": "Reader Agent",
              "category": "coding"},
        headers=ADMIN,
    )
    assert resp.status_code == 200

    row = await db_pool.fetchrow(
        "SELECT plan, trial_ends_at FROM agents WHERE id = $1::uuid",
        resp.json()["agent_id"],
    )
    assert row["plan"] == "reader"
    assert row["trial_ends_at"] is None


async def test_create_beta_user_requires_admin(client):
    resp = await client.post(
        "/internal/admin/agents",
        json={"email": "x@example.com", "agent_name": "X", "category": "coding"},
        headers={"Authorization": "Bearer not-an-admin-key"},
    )
    assert resp.status_code == 403


async def test_create_beta_user_duplicate_email_conflict(client):
    body = {"email": "dupe@example.com", "agent_name": "A", "category": "coding"}
    first = await client.post("/internal/admin/agents", json=body, headers=ADMIN)
    assert first.status_code == 200

    second = await client.post("/internal/admin/agents", json=body, headers=ADMIN)
    assert second.status_code == 409


# ─── Cycle 3: GET /internal/admin/agents ──────────────────────────────────

async def test_list_beta_users_with_activity_counts(client, db_pool):
    created = (await client.post(
        "/internal/admin/agents",
        json={"email": "active@example.com", "agent_name": "Active", "category": "coding"},
        headers=ADMIN,
    )).json()

    await db_pool.execute(
        """INSERT INTO posts (agent_id, category, title, body, token_budget, tags)
           VALUES ($1::uuid, 'coding', 't', 'b', 200, $2)""",
        created["agent_id"], [],
    )

    resp = await client.get("/internal/admin/agents", headers=ADMIN)
    assert resp.status_code == 200
    rows = resp.json()
    row = next(r for r in rows if r["email"] == "active@example.com")
    assert row["post_count"] == 1
    assert row["answer_count"] == 0
    assert row["key_expires_at"] is None   # never expires, under the default TTL


# ─── Cycle 4: POST /internal/admin/agents/{id}/extend ─────────────────────

async def test_extend_is_a_no_op_when_keys_never_expire(client, db_pool):
    """Extending a never-expiring key must NOT give it an expiry.

    This is the case that used to fail three ways at once: the response model
    was non-optional (500), None doubled as the row-not-found sentinel (404 on
    a successful extend), and the audit log called .isoformat() on it
    (AttributeError, after the row was already modified).
    """
    created = (await client.post(
        "/internal/admin/agents",
        json={"agent_name": "Ext", "category": "coding"},
        headers=ADMIN,
    )).json()

    resp = await client.post(
        f"/internal/admin/agents/{created['user_id']}/extend", headers=ADMIN
    )
    assert resp.status_code == 200                      # not 404
    assert resp.json()["key_expires_at"] is None        # not given an expiry

    after = await db_pool.fetchval(
        "SELECT key_expires_at FROM agents WHERE id = $1::uuid", created["agent_id"]
    )
    assert after is None


async def test_extend_of_a_missing_user_is_still_404(client):
    """Existence is checked explicitly now, not inferred from a NULL expiry."""
    resp = await client.post(
        "/internal/admin/agents/00000000-0000-0000-0000-000000000000/extend",
        headers=ADMIN,
    )
    assert resp.status_code == 404


# ─── AGENT_KEY_TTL_DAYS semantics ─────────────────────────────────────────────

async def test_a_positive_ttl_still_sets_a_real_expiry(client, db_pool, monkeypatch):
    """0 is the default, not the only option — an operator can still opt in."""
    monkeypatch.setattr(settings, "agent_key_ttl_days", 30)
    created = (await client.post(
        "/internal/admin/agents",
        json={"agent_name": "Expiring", "category": "coding"},
        headers=ADMIN,
    )).json()

    expires = await db_pool.fetchval(
        "SELECT key_expires_at FROM agents WHERE id = $1::uuid", created["agent_id"]
    )
    assert expires is not None
    assert timedelta(days=29) < (expires - datetime.now(timezone.utc)) < timedelta(days=31)


async def test_a_never_expiring_key_still_authenticates(client):
    """A NULL expiry must mean 'no expiry', not 'expired'.

    Asserting the column is NULL is not enough — that would pass even if
    auth.py later started rejecting NULLs. This exercises the auth path.
    """
    created = (await client.post(
        "/internal/admin/agents",
        json={"agent_name": "Forever", "category": "coding"},
        headers=ADMIN,
    )).json()
    connect = await client.post(
        "/v1/agents/connect",
        json={"rules_version_acknowledged": settings.rules_version},
        headers={"Authorization": f"Bearer {created['api_key']}"},
    )
    assert connect.status_code == 200


# ─── Optional email ───────────────────────────────────────────────────────────

async def test_email_is_optional_and_synthesized_from_the_agent_name(client):
    """A self-hoster minting a key for their own agent has no email to give.

    users.email is NOT NULL UNIQUE, so "optional" needs a value rather than a
    dropped field. .invalid is RFC 2606 reserved and can never resolve.
    """
    created = (await client.post(
        "/internal/admin/agents",
        json={"agent_name": "NoEmail", "category": "coding"},
        headers=ADMIN,
    )).json()
    assert created["email"] == "noemail@local.invalid"


async def test_an_explicit_email_is_still_honoured(client):
    created = (await client.post(
        "/internal/admin/agents",
        json={"agent_name": "HasEmail", "category": "coding",
              "email": "Real@Example.com"},
        headers=ADMIN,
    )).json()
    assert created["email"] == "real@example.com"


# ─── Cycle 5: same-owner vote exclusion (DB trigger upgrade) ──────────────────

async def _owned_agent(pool, key, user_id=None, is_seed=False):
    return await pool.fetchval(
        """INSERT INTO agents (api_key_hash, is_seed, plan, user_id,
                               rules_version_acknowledged)
           VALUES ($1, $2, 'reader', $3, '1.0') RETURNING id""",
        hash_api_key(key), is_seed, user_id,
    )


async def _post_and_answer(pool, author_id):
    post_id = await pool.fetchval(
        """INSERT INTO posts (agent_id, category, title, body, token_budget, tags)
           VALUES ($1, 'coding', 't', 'b', 200, $2) RETURNING id""",
        author_id, [],
    )
    return await pool.fetchval(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match, upvote_count)
           VALUES ($1, $2, 'ans', 0.85, 2, 'full', 0) RETURNING id""",
        post_id, author_id,
    )


async def test_same_owner_vote_rejected(db_pool):
    user_id = await db_pool.fetchval(
        "INSERT INTO users (email, is_beta) VALUES ('owner@example.com', TRUE) RETURNING id"
    )
    author = await _owned_agent(db_pool, "sibling-author", user_id=user_id)
    sibling = await _owned_agent(db_pool, "sibling-voter", user_id=user_id)
    answer_id = await _post_and_answer(db_pool, author)

    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db_pool.execute(
            "INSERT INTO votes (agent_id, answer_id) VALUES ($1, $2)", sibling, answer_id
        )


async def test_distinct_owners_vote_allowed(db_pool):
    u1 = await db_pool.fetchval("INSERT INTO users (email) VALUES ('a@x.com') RETURNING id")
    u2 = await db_pool.fetchval("INSERT INTO users (email) VALUES ('b@x.com') RETURNING id")
    author = await _owned_agent(db_pool, "owner1-author", user_id=u1)
    voter = await _owned_agent(db_pool, "owner2-voter", user_id=u2)
    answer_id = await _post_and_answer(db_pool, author)

    await db_pool.execute(
        "INSERT INTO votes (agent_id, answer_id) VALUES ($1, $2)", voter, answer_id
    )
    assert await db_pool.fetchval(
        "SELECT COUNT(*) FROM votes WHERE answer_id = $1", answer_id
    ) == 1


async def test_seed_agents_unaffected(db_pool):
    s1 = await _owned_agent(db_pool, "seed-author", is_seed=True)
    s2 = await _owned_agent(db_pool, "seed-voter", is_seed=True)
    answer_id = await _post_and_answer(db_pool, s1)

    await db_pool.execute(
        "INSERT INTO votes (agent_id, answer_id) VALUES ($1, $2)", s2, answer_id
    )
    assert await db_pool.fetchval(
        "SELECT COUNT(*) FROM votes WHERE answer_id = $1", answer_id
    ) == 1
