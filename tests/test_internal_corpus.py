"""Tests for GET /internal/corpus/similar endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("clean_db")

_ZERO_VEC = [0.0] * 768
# Unit vector: 1.0 in first dimension, 0.0 elsewhere (cosine sim = 1 against itself)
_ONE_VEC = [1.0] + [0.0] * 767


# ─── Auth ─────────────────────────────────────────────────────────────────────

async def test_corpus_similar_requires_seed(client, non_seed_agent):
    """A non-seed agent must receive HTTP 403."""
    with patch(
        "app.routers.internal.corpus.get_embeddings",
        new=AsyncMock(return_value=[_ZERO_VEC]),
    ):
        headers = {"Authorization": f"Bearer {non_seed_agent['api_key']}"}
        r = await client.get("/internal/corpus/similar", params={"q": "hello"}, headers=headers)
    assert r.status_code == 403
    assert "Seed agents only" in r.text


# ─── Empty corpus ─────────────────────────────────────────────────────────────

async def test_corpus_similar_empty_corpus_returns_empty(client, seed_agent):
    """Seed agent + empty training_corpus → 200 with count 0 and empty data."""
    with patch(
        "app.routers.internal.corpus.get_embeddings",
        new=AsyncMock(return_value=[_ZERO_VEC]),
    ):
        headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
        r = await client.get("/internal/corpus/similar", params={"q": "test query"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["data"] == []


# ─── Row returned ─────────────────────────────────────────────────────────────

async def test_corpus_similar_returns_match(client, seed_agent, db_pool):
    """Insert one corpus row, query with the same embedding → count 1 and answer_text returned."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO training_corpus
               (question_text, answer_text, category, quality_score, embedding)
               VALUES ('What is 2+2?', 'Four.', 'math', 0.95, $1)""",
            _ONE_VEC,  # asyncpg maps list[float] → DOUBLE PRECISION[]
        )

    with patch(
        "app.routers.internal.corpus.get_embeddings",
        new=AsyncMock(return_value=[_ONE_VEC]),
    ):
        headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
        r = await client.get("/internal/corpus/similar", params={"q": "arithmetic", "k": "1"}, headers=headers)

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["answer_text"] == "Four."
    assert body["data"][0]["question_text"] == "What is 2+2?"
    assert body["data"][0]["category"] == "math"
    # similarity should be 1.0 (identical unit vectors)
    assert body["data"][0]["similarity"] > 0.99
