"""The FastAPI lifespan must run the production preflight before serving."""
from __future__ import annotations

import pytest

from app.config import settings
from app.main import app, lifespan


@pytest.mark.asyncio
async def test_lifespan_refuses_unsafe_production(monkeypatch):
    # Force production with the dev-default admin key + gate off → preflight must
    # raise before init_pool/workers run.
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_api_key", "dev-admin-key")
    monkeypatch.setattr(settings, "moderation_gate_enabled", False)
    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass  # should never get here
