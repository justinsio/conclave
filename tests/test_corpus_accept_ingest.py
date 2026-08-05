"""Accept qualifies an answer for corpus staging (spec §3c).

Without this, run_ingest stages only at 3 distinct upvotes, which a four-agent
team effectively cannot reach — so training_corpus stays empty and Phase 2.8's
retrieval endpoint returns nothing forever.
"""
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.services import corpus_pipeline

pytestmark = pytest.mark.usefixtures("clean_db")


async def _post_and_answer(pool, asker, answerer, *, upvotes=0, accepted=False,
                           flagged=False):
    post = await pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status)
           VALUES ($1, 'coding', 't', 'b', 100, 'public', 'resolved')
           RETURNING id""",
        asker["id"],
    )
    answer = await pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match, upvote_count, human_accepted, flagged)
           VALUES ($1, $2, 'ans', 0.9, 3, 'full', $3, $4, $5)
           RETURNING id""",
        post["id"], answerer["id"], upvotes, accepted, flagged,
    )
    return post, answer


async def test_accepted_answer_with_no_upvotes_is_staged(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    """The whole point of §3c."""
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    await _post_and_answer(db_pool, standard_agent, seed_agent, upvotes=0, accepted=True)

    staged = await corpus_pipeline.run_ingest(db_pool)
    assert staged == 1


async def test_unaccepted_answer_below_threshold_is_not_staged(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    await _post_and_answer(db_pool, standard_agent, seed_agent, upvotes=0, accepted=False)

    assert await corpus_pipeline.run_ingest(db_pool) == 0


async def test_upvote_threshold_still_qualifies_independently(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    """Accept is an ADDITIONAL valve, not a replacement."""
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    await _post_and_answer(
        db_pool, standard_agent, seed_agent,
        upvotes=settings.corpus_upvote_threshold, accepted=False,
    )

    assert await corpus_pipeline.run_ingest(db_pool) == 1


async def test_accepted_but_flagged_answer_is_still_excluded(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    """Accept must not bypass the flag guard."""
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    await _post_and_answer(
        db_pool, standard_agent, seed_agent, upvotes=0, accepted=True, flagged=True,
    )

    assert await corpus_pipeline.run_ingest(db_pool) == 0


async def test_accept_stages_the_answer_with_a_promote_after(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    """Accept routes an answer into corpus_staging, stamped with a promote_after.

    Replaces test_accept_does_not_skip_quarantine, which asserted that
    run_promote returns 0 because "promote_after is still in the future". That
    stopped being true when CORPUS_QUARANTINE_DAYS defaulted to 0 — and the test
    kept passing anyway, for a reason unrelated to what it claimed:
    ollama_base_url pointed at an unreachable host, so the gate could not be
    consulted and run_promote declined to act. It was green while documenting
    behaviour the system no longer had.

    What is actually invariant is that accept STAGES rather than promoting
    directly, so the pipeline still owns promotion. The quarantine mechanism
    itself is tested below, with an explicit positive value instead of a
    default that has since changed.
    """
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    await _post_and_answer(db_pool, standard_agent, seed_agent, upvotes=0, accepted=True)

    assert await corpus_pipeline.run_ingest(db_pool) == 1

    row = await db_pool.fetchrow(
        "SELECT promote_after, promotion_status FROM corpus_staging"
    )
    assert row is not None, "accept must stage the answer"
    assert row["promote_after"] is not None
    assert row["promotion_status"] == "pending"

    corpus_count = await db_pool.fetchval("SELECT COUNT(*) FROM training_corpus")
    assert corpus_count == 0, "accept must not write straight to the corpus"


async def test_a_positive_quarantine_defers_promotion(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    """The quarantine still works when an operator asks for one.

    Pinned with an explicit value rather than the shipped default, so that
    changing the default cannot silently turn this into a test of nothing —
    which is exactly what happened to its predecessor.
    """
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    monkeypatch.setattr(settings, "corpus_quarantine_days", 7)
    await _post_and_answer(db_pool, standard_agent, seed_agent, upvotes=0, accepted=True)
    await corpus_pipeline.run_ingest(db_pool)

    promote_after = await db_pool.fetchval("SELECT promote_after FROM corpus_staging")
    assert promote_after > datetime.now(timezone.utc), "promote_after must be in the future"
    assert await corpus_pipeline.run_promote(db_pool) == 0
