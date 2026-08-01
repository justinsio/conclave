"""Embeddings written to training_corpus must be unit length."""
import math

import pytest

from app.services import corpus_pipeline

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_run_promote_stores_a_normalized_embedding(db_pool, monkeypatch):
    """run_promote must normalize before INSERT — vector_dot at query time is
    only correct if every stored vector is unit length.

    This drives the REAL ingest path. The dual-signal gate is stubbed so the
    test pins normalization rather than the promotion matrix, and
    get_embeddings returns a deliberately un-normalized vector (magnitude 5) so
    a missing normalize_vector call fails the assertion.
    """
    async def _fake_embeddings(texts):
        return [[3.0, 4.0]]                       # magnitude 5, NOT unit length

    async def _no_seed_check(question, answer):
        return None

    async def _no_critique(question, answer):
        return None

    monkeypatch.setattr(corpus_pipeline, "get_embeddings", _fake_embeddings)
    monkeypatch.setattr(corpus_pipeline, "_seed_cross_check", _no_seed_check)
    monkeypatch.setattr(corpus_pipeline, "_critique_answer", _no_critique)
    monkeypatch.setattr(corpus_pipeline, "_promotion_decision", lambda *a, **kw: "promote")

    await db_pool.execute(
        """INSERT INTO corpus_staging
           (question_text, answer_text, category, quality_score,
            source_provider_type, promotion_status, promote_after,
            ring_check_clean, retry_count)
           VALUES ('q', 'a', 'coding', 1.0, 'test', 'pending',
                   NOW() - INTERVAL '1 day', TRUE, 0)"""
    )

    promoted = await corpus_pipeline.run_promote(db_pool)
    assert promoted == 1, "staging row did not promote — check the stubs above"

    stored = await db_pool.fetchval(
        "SELECT embedding FROM training_corpus WHERE question_text = 'q'"
    )
    magnitude = math.sqrt(sum(x * x for x in stored))
    assert math.isclose(magnitude, 1.0, rel_tol=1e-9)
