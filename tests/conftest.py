"""
Test fixtures.

Requires a local Postgres instance and TEST_DATABASE_URL in .env:
  createdb conclave_test
  pytest
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

# Load .env before anything reads os.environ
load_dotenv()

# Point to test DB before importing app modules that read settings at import time
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", ""))

from app.auth import hash_api_key
from app.database import _init_connection, close_pool, init_pool
from app.main import app

MIGRATIONS = Path(__file__).parent.parent / "migrations"
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/conclave_test"
)


async def _apply_migrations(conn: asyncpg.Connection) -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text()
        await conn.execute(sql)


async def _truncate_tables(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """TRUNCATE seed_signals, seed_contributions, seed_drafts, seed_threads,
                       votes, clarifications, bans, agent_category_scores,
                       moderation_queue, answers, posts, agents, users,
                       network_stats_cache
           RESTART IDENTITY CASCADE"""
    )
    await conn.execute("DELETE FROM audit_log_2026_06")
    await conn.execute("DELETE FROM audit_log_2026_07")


@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    """Run migrations once per session using asyncio.run() — no loop conflict."""
    async def _migrate():
        conn = await asyncpg.connect(TEST_DB_URL)
        try:
            await _apply_migrations(conn)
        finally:
            await conn.close()

    asyncio.run(_migrate())


@pytest_asyncio.fixture
async def db_pool():
    """Function-scoped pool — lives on the same event loop as all other fixtures."""
    pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=5, init=_init_connection)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def clean_db(db_pool):
    async with db_pool.acquire() as conn:
        await _truncate_tables(conn)
    await init_pool(TEST_DB_URL)
    yield
    await close_pool()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ─── Test agents ──────────────────────────────────────────────────────────────

async def _make_agent(
    pool: asyncpg.Pool,
    api_key: str,
    is_seed: bool = True,
    calibration_score: float | None = None,
) -> dict:
    key_hash = hash_api_key(api_key)
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, calibration_score)
           VALUES ($1, $2, $3) RETURNING id, is_seed, calibration_score""",
        key_hash, is_seed, calibration_score,
    )
    return {"api_key": api_key, **dict(row)}


async def _make_post(
    pool: asyncpg.Pool,
    agent_id,
    category: str = "coding",
    tags: list[str] | None = None,
    token_budget: int = 200,
) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget, tags)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id, created_at""",
        agent_id,
        category,
        "Deduplicate 10M integers preserving insertion order",
        "Memory limit 512MB.",
        token_budget,
        tags or [],
    )
    return dict(row)


async def _make_standard_agent(
    pool: asyncpg.Pool,
    api_key: str,
    plan: str = "standard",
    name: str = "TestAgent",
) -> dict:
    key_hash = hash_api_key(api_key)
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged)
           VALUES ($1, false, $2, $3, '1.0') RETURNING id, plan, name""",
        key_hash, plan, name,
    )
    return {"api_key": api_key, **dict(row)}


async def _make_answer(
    pool: asyncpg.Pool,
    post_id,
    agent_id,
    body: str = "Test answer body.",
    confidence: float = 0.85,
    upvote_count: int = 0,
) -> dict:
    row = await pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match, upvote_count)
           VALUES ($1, $2, $3, $4, $5, 'full', $6) RETURNING id, created_at""",
        post_id, agent_id, body, confidence, len(body.split()), upvote_count,
    )
    return dict(row)


@pytest_asyncio.fixture
async def seed_agent(db_pool):
    return await _make_agent(db_pool, "test-seed-key-01")


@pytest_asyncio.fixture
async def seed_agent2(db_pool):
    return await _make_agent(db_pool, "test-seed-key-02")


@pytest_asyncio.fixture
async def seed_agent3(db_pool):
    return await _make_agent(db_pool, "test-seed-key-03")


@pytest_asyncio.fixture
async def non_seed_agent(db_pool):
    return await _make_agent(db_pool, "test-non-seed-key", is_seed=False)


@pytest_asyncio.fixture
async def standard_agent(db_pool):
    return await _make_standard_agent(db_pool, "test-standard-key-01")


@pytest_asyncio.fixture
async def standard_agent2(db_pool):
    return await _make_standard_agent(db_pool, "test-standard-key-02", name="TestAgent2")


@pytest_asyncio.fixture
async def trial_agent(db_pool):
    return await _make_standard_agent(db_pool, "test-trial-key-01", plan="trial", name="TrialAgent")


@pytest_asyncio.fixture
async def test_post(db_pool, seed_agent):
    return await _make_post(db_pool, seed_agent["id"])


@pytest_asyncio.fixture
async def complex_post(db_pool, seed_agent):
    return await _make_post(db_pool, seed_agent["id"], tags=["complex"])
