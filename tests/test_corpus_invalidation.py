"""Invalidated corpus entries must not be retrievable."""
import pytest

from app.routers.internal import corpus as corpus_router

pytestmark = pytest.mark.usefixtures("clean_db")


async def _corpus_row(pool, question, answer, invalidated=False):
    await pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, invalidated_at)
           VALUES ($1, $2, $3, 'coding', 1.0, 'test',
                   CASE WHEN $4 THEN NOW() ELSE NULL END)""",
        question, answer, [1.0, 0.0], invalidated,
    )


def _fixed_embedding(monkeypatch):
    async def _embed(texts):
        return [[1.0, 0.0]]
    monkeypatch.setattr(corpus_router, "get_embeddings", _embed)


async def test_similar_excludes_invalidated_entries(client, db_pool, seed_agent, monkeypatch):
    """THE test that makes invalidation mean anything. Without the filter,
    setting invalidated_at changes nothing observable."""
    await _corpus_row(db_pool, "live", "good answer")
    await _corpus_row(db_pool, "stale", "bad answer", invalidated=True)
    _fixed_embedding(monkeypatch)

    r = await client.get(
        "/internal/corpus/similar?q=anything&k=10",
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    assert r.status_code == 200
    answers = [d["answer_text"] for d in r.json()["data"]]
    assert "good answer" in answers
    assert "bad answer" not in answers


async def test_similar_returns_nothing_when_all_entries_are_invalidated(
    client, db_pool, seed_agent, monkeypatch
):
    await _corpus_row(db_pool, "stale", "bad answer", invalidated=True)
    _fixed_embedding(monkeypatch)

    r = await client.get(
        "/internal/corpus/similar?q=anything",
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    assert r.json()["count"] == 0
