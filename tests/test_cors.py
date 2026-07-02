"""CORS behaviour for the browser-facing endpoints (the marketing-site waitlist).

The pre-launch site (https://conclaveai.co) submits the "Notify me" form by
POSTing `application/json` to the API (https://api.conclaveai.co/v1/waitlist).
Because the content-type is application/json, the browser first sends a CORS
preflight OPTIONS request. Without CORS middleware the API never answers it and
the browser blocks the sign-up. These tests pin that the configured site
origin receives CORS headers and other origins do not.
"""
from __future__ import annotations

SITE_ORIGIN = "https://conclaveai.co"


async def test_waitlist_preflight_allows_site_origin(client):
    resp = await client.options(
        "/v1/waitlist",
        headers={
            "Origin": SITE_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == SITE_ORIGIN


async def test_actual_response_carries_cors_header_for_site_origin(client):
    # A simple request bearing the site Origin must be echoed an allow-origin so
    # the browser exposes the response body to the site's JavaScript.
    resp = await client.get("/health", headers={"Origin": SITE_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == SITE_ORIGIN


async def test_untrusted_origin_is_not_allowed(client):
    resp = await client.options(
        "/v1/waitlist",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette rejects a disallowed preflight and never echoes the attacker origin.
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"
