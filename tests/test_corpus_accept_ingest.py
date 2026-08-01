"""Accept qualifies an answer for corpus staging (spec §3c).

Without this, run_ingest stages only at 3 distinct upvotes, which a four-agent
team effectively cannot reach — so training_corpus stays empty and Phase 2.8's
retrieval endpoint returns nothing forever.
"""
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


async def test_accept_does_not_skip_quarantine(
    db_pool, standard_agent, seed_agent, monkeypatch
):
    """An accepted answer is STAGED, not promoted. promote_after is still in the
    future, so run_promote must not pick it up yet."""
    monkeypatch.setattr(settings, "ollama_base_url", "http://fake")
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    await _post_and_answer(db_pool, standard_agent, seed_agent, upvotes=0, accepted=True)
    await corpus_pipeline.run_ingest(db_pool)

    promote_after = await db_pool.fetchval("SELECT promote_after FROM corpus_staging")
    assert promote_after is not None
    assert await corpus_pipeline.run_promote(db_pool) == 0
