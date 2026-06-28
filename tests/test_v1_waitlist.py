"""POST /v1/waitlist — public 'notify me when live' capture for the marketing site."""
import pytest

pytestmark = pytest.mark.usefixtures("clean_db")


async def _count(db_pool, ip_hash=None):
    if ip_hash is not None:
        return await db_pool.fetchval("SELECT COUNT(*) FROM waitlist WHERE ip_hash = $1", ip_hash)
    return await db_pool.fetchval("SELECT COUNT(*) FROM waitlist")


async def test_valid_email_subscribes(client, db_pool):
    res = await client.post("/v1/waitlist", json={"email": "alice@example.com"})
    assert res.status_code == 201
    assert res.json()["status"] == "subscribed"
    assert await _count(db_pool) == 1


async def test_no_auth_required(client):
    # public endpoint — no Authorization header
    res = await client.post("/v1/waitlist", json={"email": "public@example.com"})
    assert res.status_code == 201


async def test_duplicate_email_is_idempotent(client, db_pool):
    await client.post("/v1/waitlist", json={"email": "bob@example.com"})
    res = await client.post("/v1/waitlist", json={"email": "bob@example.com"})
    assert res.status_code == 200
    assert res.json()["status"] == "already_subscribed"
    assert await _count(db_pool) == 1


async def test_dedup_is_case_and_space_insensitive(client, db_pool):
    await client.post("/v1/waitlist", json={"email": "Carol@Example.com"})
    res = await client.post("/v1/waitlist", json={"email": "  carol@example.com  "})
    assert res.status_code == 200
    assert await _count(db_pool) == 1


async def test_invalid_email_rejected(client, db_pool):
    res = await client.post("/v1/waitlist", json={"email": "not-an-email"})
    assert res.status_code == 400
    assert await _count(db_pool) == 0


async def test_missing_email_rejected(client, db_pool):
    res = await client.post("/v1/waitlist", json={})
    assert res.status_code == 422
    assert await _count(db_pool) == 0


async def test_honeypot_silently_drops(client, db_pool):
    # bot fills the hidden field — response looks normal, but nothing is stored
    res = await client.post(
        "/v1/waitlist", json={"email": "spam@example.com", "hp": "gotcha"}
    )
    assert res.status_code in (200, 201)
    assert await _count(db_pool) == 0


async def test_rate_limited_per_ip(client):
    ip = {"X-Forwarded-For": "203.0.113.7"}
    for i in range(5):
        r = await client.post("/v1/waitlist", json={"email": f"u{i}@example.com"}, headers=ip)
        assert r.status_code == 201, f"signup {i} should pass, got {r.status_code}"
    # the 6th from the same IP trips the limit
    r = await client.post("/v1/waitlist", json={"email": "u5@example.com"}, headers=ip)
    assert r.status_code == 429


async def test_spoofed_xff_does_not_bypass_rate_limit(client):
    """HR-04 — X-Forwarded-For from an untrusted peer must be ignored, so an
    attacker can't defeat the per-IP cap by forging a fresh hop each request."""
    for i in range(5):
        r = await client.post(
            "/v1/waitlist", json={"email": f"s{i}@example.com"},
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        )
        assert r.status_code == 201, f"signup {i} should pass, got {r.status_code}"
    # A 6th with yet another spoofed hop is still throttled — the forged XFF is
    # ignored and every request keys off the real (untrusted) peer.
    r = await client.post(
        "/v1/waitlist", json={"email": "s5@example.com"},
        headers={"X-Forwarded-For": "198.51.100.42"},
    )
    assert r.status_code == 429
