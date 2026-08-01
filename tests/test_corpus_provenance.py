"""run_promote must carry provenance from staging into training_corpus,
and CORPUS_ANONYMIZE must decide whether ingest anonymizes at all."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services import corpus_pipeline
from app.services.corpus_pipeline import AnonymizationResult

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


# ─── CORPUS_ANONYMIZE ─────────────────────────────────────────────────────────


async def test_ingest_keeps_raw_text_when_anonymize_disabled(
    db_pool, seed_agent, test_post, monkeypatch
):
    """CORPUS_ANONYMIZE=false stages the team's real text and never calls the
    anonymizer at all. Also pins the quality_score sentinel — the column is
    NOT NULL on both staging and training_corpus, and with anonymization off
    there is no AnonymizationResult to take a score from."""
    from tests.conftest import _make_answer
    from app.config import settings

    monkeypatch.setattr(settings, "corpus_anonymize", False)
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    await _make_answer(db_pool, test_post["id"], seed_agent["id"], upvote_count=5)

    async def _boom(*a, **kw):
        raise AssertionError("anonymize_qa_pair must not be called when disabled")

    monkeypatch.setattr(corpus_pipeline, "anonymize_qa_pair", _boom)

    count = await corpus_pipeline.run_ingest(db_pool)
    assert count == 1

    row = await db_pool.fetchrow(
        "SELECT question_text, answer_text, quality_score FROM corpus_staging"
    )
    title = await db_pool.fetchval(
        "SELECT title FROM posts WHERE id = $1", test_post["id"]
    )
    assert title in row["question_text"]
    assert row["quality_score"] == pytest.approx(1.0)


async def test_ingest_anonymizes_when_enabled(
    db_pool, seed_agent, test_post, monkeypatch
):
    """CORPUS_ANONYMIZE=true keeps the old behaviour end to end: the
    anonymizer's text AND its quality_score are what get staged."""
    from tests.conftest import _make_answer
    from app.config import settings

    monkeypatch.setattr(settings, "corpus_anonymize", True)
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    await _make_answer(db_pool, test_post["id"], seed_agent["id"], upvote_count=5)

    mock_result = AnonymizationResult(
        question_text="generic q", answer_text="generic a", quality_score=0.77
    )
    with patch(
        "app.services.corpus_pipeline.anonymize_qa_pair",
        new=AsyncMock(return_value=mock_result),
    ):
        count = await corpus_pipeline.run_ingest(db_pool)

    assert count == 1
    row = await db_pool.fetchrow(
        "SELECT question_text, answer_text, quality_score FROM corpus_staging"
    )
    assert row["question_text"] == "generic q"
    assert row["answer_text"] == "generic a"
    assert row["quality_score"] == pytest.approx(0.77)


async def test_ingest_still_skips_entirely_without_ollama(monkeypatch):
    """Regression guard for spec §1: with anonymization off but no Ollama,
    ingest must still skip. run_promote needs Ollama for BOTH signals, so
    staging would mark answers consumed, hold them, then permanently reject
    them — unrecoverable even after Ollama is installed later."""
    from app.config import settings

    monkeypatch.setattr(settings, "corpus_anonymize", False)
    monkeypatch.setattr(settings, "ollama_base_url", "")

    class _Boom:
        async def fetch(self, *a, **kw):
            raise AssertionError("run_ingest must not query when Ollama is absent")

    assert await corpus_pipeline.run_ingest(_Boom()) == 0


# ─── Boot floors ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field", ["corpus_quarantine_days", "corpus_upvote_threshold"]
)
def test_zero_is_rejected_at_boot(field):
    """0 reads as "disabled" to a human but is destructive to the code: it
    bypasses the quarantine / qualifies every answer on the network.

    NOTE: asserts on the raised error, never on settings.<attr>. Asserting
    directly on a Settings attribute makes pytest's assertion rewriting print
    the entire Settings repr — API key and DB password included — into test
    output and CI logs. That happened on 2026-07-31 and forced a key rotation.
    """
    from pydantic import ValidationError
    from app.config import Settings

    with pytest.raises(ValidationError) as exc:
        Settings(**{field: 0})
    assert "must be >= 1" in str(exc.value)


@pytest.mark.parametrize(
    "field", ["corpus_quarantine_days", "corpus_upvote_threshold"]
)
def test_one_is_accepted_at_boot(field):
    """The floor is 1, not 2 — don't over-reject."""
    from app.config import Settings

    value = getattr(Settings(**{field: 1}), field)
    assert value == 1
