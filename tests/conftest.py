"""
Test fixtures.

Requires a local Postgres instance and TEST_DATABASE_URL in .env:
  createdb conclave_test
  pytest
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Point to test DB before importing app modules that read settings at import time
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", ""))

from app.auth import hash_api_key
from app.database import close_pool, init_pool
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
        """TRUNCATE seed_signals, seed_contributions, seed_drafts,
                       seed_threads, answers, posts, agents
           RESTART IDENTITY CASCADE"""
    )


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await _apply_migrations(conn)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_pool):
    async with db_pool.acquire() as conn:
        await _truncate_tables(conn)
    # Wire the app to use the test pool
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
async def test_post(db_pool, seed_agent):
    return await _make_post(db_pool, seed_agent["id"])


@pytest_asyncio.fixture
async def complex_post(db_pool, seed_agent):
    return await _make_post(db_pool, seed_agent["id"], tags=["complex"])
