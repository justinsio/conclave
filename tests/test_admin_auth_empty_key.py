"""SEC-1 — an empty ADMIN_API_KEY must never authorise anyone.

`require_admin` compares the supplied key against `settings.admin_api_key`
with `secrets.compare_digest`. When the configured key is empty, that compare
succeeds against an empty supplied key, so the header `Authorization: Admin `
(trailing space, no key) grants full admin: key minting, bans, kill switches.

The production preflight does not save you — it returns immediately unless
ENVIRONMENT=production, and .env.example ships `dev`. So the guard has to live
in `require_admin` itself, unconditionally, in every environment.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth import require_admin


@pytest.mark.asyncio
async def test_empty_configured_key_rejects_empty_credential(monkeypatch):
    """The core hole: compare_digest('', '') is True, so this once authorised."""
    from app import auth

    monkeypatch.setattr(auth.settings, "admin_api_key", "")

    with pytest.raises(HTTPException) as exc:
        await require_admin("Admin ")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_empty_configured_key_rejects_any_credential(monkeypatch):
    """A misconfigured instance must refuse everyone, not just the empty key."""
    from app import auth

    monkeypatch.setattr(auth.settings, "admin_api_key", "")

    for header in ("Admin ", "Admin anything", "Admin dev-admin-key"):
        with pytest.raises(HTTPException) as exc:
            await require_admin(header)
        assert exc.value.status_code == 403, header


@pytest.mark.asyncio
async def test_whitespace_only_configured_key_is_also_rejected(monkeypatch):
    """`ADMIN_API_KEY=" "` is a misconfiguration, not a secret."""
    from app import auth

    monkeypatch.setattr(auth.settings, "admin_api_key", "   ")

    with pytest.raises(HTTPException) as exc:
        await require_admin("Admin    ")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_real_configured_key_still_works(monkeypatch):
    """The guard must not break the normal path."""
    from app import auth

    monkeypatch.setattr(auth.settings, "admin_api_key", "a-real-strong-secret")

    await require_admin("Admin a-real-strong-secret")  # must not raise

    with pytest.raises(HTTPException) as exc:
        await require_admin("Admin wrong-key")
    assert exc.value.status_code == 403
