"""run_promote must carry provenance from staging into training_corpus."""
import pytest

from app.services import corpus_pipeline

pytestmark = pytest.mark.usefixtures("clean_db")


async def _stage(pool, post_id=None, answer_id=None, question="q", answer="a"):
    await pool.execute(
        """INSERT INTO corpus_staging
           (source_post_id, source_answer_id, question_text, answer_text,
            category, quality_score, source_provider_type, promotion_status,
            promote_after, ring_check_clean, retry_count)
           VALUES ($1, $2, $3, $4, 'coding', 1.0, 'test', 'pending',
                   NOW() - INTERVAL '1 day', TRUE, 0)""",
        post_id, answer_id, question, answer,
    )


def _force_promote(monkeypatch):
    async def _none(question, answer):
        return None
    monkeypatch.setattr(corpus_pipeline, "_seed_cross_check", _none)
    monkeypatch.setattr(corpus_pipeline, "_critique_answer", _none)
    monkeypatch.setattr(corpus_pipeline, "_promotion_decision", lambda *a, **kw: "promote")

    async def _embed(texts):
        return [[1.0, 0.0]]
    monkeypatch.setattr(corpus_pipeline, "get_embeddings", _embed)


async def test_promote_carries_source_ids(db_pool, seed_agent, standard_agent, monkeypatch):
    """Without this, propagation in 2.7b has nothing to join on."""
    post = await db_pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget)
           VALUES ($1, 'coding', 't', 'b', 100) RETURNING id""",
        standard_agent["id"],
    )
    answer = await db_pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match)
           VALUES ($1, $2, 'ans', 0.9, 3, 'full') RETURNING id""",
        post["id"], seed_agent["id"],
    )
    await _stage(db_pool, post_id=post["id"], answer_id=answer["id"])
    _force_promote(monkeypatch)

    assert await corpus_pipeline.run_promote(db_pool) == 1

    row = await db_pool.fetchrow(
        """SELECT source_post_id, source_answer_id, source_agent_id
             FROM training_corpus WHERE question_text = 'q'"""
    )
    assert row["source_post_id"] == post["id"]
    assert row["source_answer_id"] == answer["id"]
    assert row["source_agent_id"] == seed_agent["id"]


async def test_promote_tolerates_missing_provenance(db_pool, monkeypatch):
    """A staged row whose source rows are gone must still promote — a missing
    link is a no-op, not an error."""
    await _stage(db_pool, post_id=None, answer_id=None, question="orphan")
    _force_promote(monkeypatch)

    assert await corpus_pipeline.run_promote(db_pool) == 1
    row = await db_pool.fetchrow(
        "SELECT source_post_id, source_agent_id FROM training_corpus WHERE question_text = 'orphan'"
    )
    assert row["source_post_id"] is None
    assert row["source_agent_id"] is None
