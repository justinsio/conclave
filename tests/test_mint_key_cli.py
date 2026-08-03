"""Tests for scripts/mint_key.py — the first command DEPLOY.md gives an operator.

The CLI wrapper had no coverage: the minting logic it calls is tested through
POST /internal/admin/agents, but the wrapper's own job is everything that route
does not do — connection setup, and turning failures into a sentence instead of
a traceback. Both of those broke on first contact with a real box.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# scripts/ is not a package (see the module docstring in mint_key.py), so load
# by path — the same reason it is invoked by path rather than with -m.
_PATH = Path(__file__).parent.parent / "scripts" / "mint_key.py"
_spec = importlib.util.spec_from_file_location("mint_key", _PATH)
mint_key = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mint_key)


@pytest.fixture
def cli_pool(db_pool, monkeypatch):
    """Point the CLI at the test pool and stop it closing the shared fixture."""
    async def _init(*a, **k):
        return db_pool

    async def _close(*a, **k):
        return None

    monkeypatch.setattr(mint_key, "init_pool", _init)
    monkeypatch.setattr(mint_key, "close_pool", _close)
    return db_pool


async def test_mints_a_key_and_synthesizes_an_unroutable_email(cli_pool, clean_db, capsys):
    assert await mint_key.mint("alice", "general", "reader", None) == 0

    out = capsys.readouterr().out
    assert "alice@local.invalid" in out          # RFC 2606 — can never resolve
    assert "API key (shown once, store it now)" in out

    row = await cli_pool.fetchrow("SELECT api_key_hash, name FROM agents WHERE name = 'alice'")
    assert row is not None
    # Only the hash is stored; the raw key exists solely in that printed line.
    assert row["api_key_hash"] not in out


async def test_never_expires_is_reported_as_never(cli_pool, clean_db, capsys):
    """AGENT_KEY_TTL_DAYS=0 maps to SQL NULL, never make_interval(days => 0),
    which evaluates to NOW() — an already-expired key."""
    monkey_ttl = mint_key.settings.agent_key_ttl_days
    mint_key.settings.agent_key_ttl_days = 0
    try:
        assert await mint_key.mint("bob", "general", "reader", None) == 0
    finally:
        mint_key.settings.agent_key_ttl_days = monkey_ttl

    assert "expires : never" in capsys.readouterr().out
    assert await cli_pool.fetchval(
        "SELECT key_expires_at FROM agents WHERE name = 'bob'"
    ) is None


async def test_a_duplicate_name_prints_a_message_not_a_traceback(cli_pool, clean_db, capsys):
    """Reusing an HTTP route in a CLI means HTTP errors surface here. A
    fastapi.exceptions.HTTPException traceback is the wrong output for
    "that name is taken" — this fired on the first real run."""
    assert await mint_key.mint("carol", "general", "reader", None) == 0

    with pytest.raises(SystemExit) as exc:
        await mint_key.mint("carol", "general", "reader", None)

    msg = str(exc.value)
    assert "could not mint a key" in msg
    assert "HTTPException" not in msg
    assert "Traceback" not in msg


async def test_reports_an_unreachable_database_without_a_traceback(monkeypatch, capsys):
    """Same defect the smoke test hit on the VM: an unguarded init_pool() answers
    a stopped database with ~40 lines of asyncpg internals."""
    async def boom(*a, **k):
        raise OSError("[Errno -2] Name or service not known")

    monkeypatch.setattr(mint_key, "init_pool", boom)
    monkeypatch.setattr(mint_key.settings, "database_url", "postgresql://db:5432/conclave")

    with pytest.raises(SystemExit) as exc:
        await mint_key.mint("dave", "general", "reader", None)

    msg = str(exc.value)
    assert "could not connect to the database" in msg
    assert "docker compose ps db" in msg
    assert "Traceback" not in msg
