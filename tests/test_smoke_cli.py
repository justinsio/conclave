"""Tests for scripts/smoke.py — the end-to-end deployment check.

These drive the real `run_smoke` coroutine in-process over ASGITransport against
the test database, so they exercise the same code the container runs rather than
a re-implementation of it.

🔒 The load-bearing test here is `test_passes_with_zero_answers`: the default
stack runs no seeds, so a smoke test that required an answer would fail on every
correct deployment. That is the one property most likely to be "helpfully" broken
by a later edit.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx

from app.routers.internal.admin_agents import synthesize_email

# scripts/ is not a package (see scripts/mint_key.py's module docstring), so the
# module is loaded by path rather than imported. Same reason apply_migrations.py
# and mint_key.py are invoked by path and not with -m.
_SMOKE_PATH = Path(__file__).parent.parent / "scripts" / "smoke.py"
_spec = importlib.util.spec_from_file_location("smoke", _SMOKE_PATH)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


async def _counts(pool) -> dict[str, int]:
    """Every table the smoke run touches, including the two whose FKs have no
    ON DELETE CASCADE (moderation_log, audit_log) and would otherwise block the
    agent delete."""
    out = {}
    for table in ("users", "agents", "posts", "moderation_log", "audit_log"):
        out[table] = await pool.fetchval(f"SELECT COUNT(*) FROM {table}")
    return out


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://api:8000"
    )


# ─── Happy path ───────────────────────────────────────────────────────────────

async def test_returns_zero_on_a_healthy_stack(client, db_pool, clean_db):
    assert await smoke.run_smoke(client, db_pool) == 0


async def test_passes_with_zero_answers(client, db_pool, clean_db):
    """The default stack has no seeds, so nothing answers. A smoke test that
    demanded an answer would report every correct zero-seed deployment as broken."""
    assert await smoke.run_smoke(client, db_pool) == 0
    assert await db_pool.fetchval("SELECT COUNT(*) FROM answers") == 0


async def test_leaves_no_rows_behind(client, db_pool, clean_db):
    before = await _counts(db_pool)
    assert await smoke.run_smoke(client, db_pool) == 0
    assert await _counts(db_pool) == before


async def test_can_run_twice_without_a_name_collision(client, db_pool, clean_db):
    """The agent name is unique per run; a fixed name would 409 on the second
    invocation and turn a working stack into a red smoke test."""
    assert await smoke.run_smoke(client, db_pool) == 0
    assert await smoke.run_smoke(client, db_pool) == 0


# ─── Failure paths — a smoke test that cannot fail has not been tested ────────

async def test_returns_nonzero_when_the_api_is_unreachable(db_pool, clean_db):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with _mock_client(refuse) as c:
        assert await smoke.run_smoke(c, db_pool) != 0


async def test_returns_nonzero_when_health_is_not_ok(db_pool, clean_db):
    def unhealthy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "degraded"})

    async with _mock_client(unhealthy) as c:
        assert await smoke.run_smoke(c, db_pool) != 0


async def test_returns_nonzero_when_the_database_is_down(client, clean_db):
    """`/health` is a static {"status":"ok"} with no DB access (main.py), so a
    dead database is caught by the mint step, not the health check."""
    class DeadPool:
        def acquire(self, *a, **k):
            raise ConnectionError("connection refused")

        async def fetchval(self, *a, **k):
            raise ConnectionError("connection refused")

        async def execute(self, *a, **k):
            raise ConnectionError("connection refused")

    assert await smoke.run_smoke(client, DeadPool()) != 0


async def test_cleans_up_after_a_midway_failure(client, db_pool, clean_db):
    """The agent is minted before the first HTTP call that can fail. If cleanup
    only ran on success, every failed run would leave an orphan agent behind and
    the next run's row-count assertions would drift."""
    before = await _counts(db_pool)

    def health_ok_then_fail(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(500, json={"detail": "boom"})

    async with _mock_client(health_ok_then_fail) as c:
        assert await smoke.run_smoke(c, db_pool) != 0

    assert await _counts(db_pool) == before


# ─── Unit-level details worth pinning ────────────────────────────────────────

def test_agent_names_are_unique_per_run():
    assert smoke.throwaway_name() != smoke.throwaway_name()


def test_throwaway_email_can_never_resolve():
    """The minted user gets an RFC 2606 .invalid address, so a smoke run can
    never write a real mailbox into the operator's roster."""
    assert synthesize_email(smoke.throwaway_name()).endswith("@local.invalid")


async def test_main_reports_an_unreachable_database_without_a_traceback(monkeypatch, capsys):
    """Found by running Step 3 on the VM: with `db` stopped, `docker compose run
    --no-deps` cannot resolve the `db` hostname, and the unguarded init_pool()
    printed ~40 lines of asyncpg internals ending in socket.gaierror. Exit 1 was
    already correct; the output was useless to a self-hoster."""
    async def boom(*a, **k):
        raise OSError("[Errno -2] Name or service not known")

    monkeypatch.setattr(smoke, "init_pool", boom)
    monkeypatch.setattr(smoke.settings, "database_url", "postgresql://db:5432/conclave")

    assert await smoke._main(smoke.DEFAULT_BASE_URL, False, 1.0) == 1
    err = capsys.readouterr().err
    assert "could not connect to the database" in err
    assert "docker compose ps db" in err
    assert "Traceback" not in err


def test_answer_timeout_clears_the_seeds_own_answer_deadline():
    """The first version of --with-answer defaulted to 120s, which could never
    have passed: seeds/loop.py filters out posts younger than DRAFT_AFTER_MINUTES
    (5) and only answers a sub-threshold draft at ANSWER_AFTER_MINUTES (15). The
    timeout is coupled to seed tuning, not to network latency — if that default
    ever drops below 900s again, --with-answer silently becomes unpassable."""
    seed_answer_deadline_seconds = 15 * 60
    assert smoke.DEFAULT_ANSWER_TIMEOUT > seed_answer_deadline_seconds, (
        f"default {smoke.DEFAULT_ANSWER_TIMEOUT}s is below the seeds' own "
        f"ANSWER_AFTER_MINUTES ({seed_answer_deadline_seconds}s) — the flag can never pass"
    )


def test_default_base_url_is_not_localhost():
    """Inside `docker compose run --rm api` this container is not the server —
    localhost is the ephemeral run container, which serves nothing."""
    assert "localhost" not in smoke.DEFAULT_BASE_URL
    assert smoke.DEFAULT_BASE_URL == "http://api:8000"
