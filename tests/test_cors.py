"""CORS is OFF by default, and still works when an operator turns it on.

The one browser-facing endpoint CORS existed for — the marketing site's waitlist
form — was deleted 2026-08-03 along with the rest of that product. The mechanism
was kept because a self-hoster who builds their own browser UI against this API
will need it; the default was emptied because shipping an allowlist naming a
domain the operator does not control is a trust relationship nobody asked for.

main.py only installs the middleware when at least one origin is configured, so
these tests rely on conftest setting CORS_ALLOW_ORIGINS before app.main imports.
"""
from __future__ import annotations

from app.config import Settings
from tests.conftest import CORS_TEST_ORIGIN


def test_the_shipped_default_is_off():
    """Asserted off the FIELD default, not a live Settings() — conftest exports
    CORS_ALLOW_ORIGINS for the tests below, and pydantic-settings would read that
    back, making this pass for the wrong reason."""
    assert Settings.model_fields["cors_allow_origins"].default == "", (
        "CORS_ALLOW_ORIGINS must ship empty. It previously defaulted to the "
        "marketing site's domain, so every self-hoster inherited a cross-origin "
        "trust relationship with a domain they do not own."
    )


async def test_preflight_allows_a_configured_origin(client):
    resp = await client.options(
        "/v1/posts",
        headers={
            "Origin": CORS_TEST_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == CORS_TEST_ORIGIN


async def test_actual_response_carries_cors_header_for_a_configured_origin(client):
    # A simple request bearing an allowed Origin must be echoed an allow-origin
    # so the browser exposes the response body to that page's JavaScript.
    resp = await client.get("/health", headers={"Origin": CORS_TEST_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == CORS_TEST_ORIGIN


async def test_untrusted_origin_is_not_allowed(client):
    resp = await client.options(
        "/v1/posts",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette rejects a disallowed preflight and never echoes the attacker origin.
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"


async def test_the_dead_marketing_origin_is_no_longer_trusted(client):
    """The old default. A self-hoster must not be trusting it implicitly."""
    resp = await client.get("/health", headers={"Origin": "https://conclaveai.co"})
    assert resp.headers.get("access-control-allow-origin") != "https://conclaveai.co"
