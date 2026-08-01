"""POST /internal/corpus/{id}/flag and threshold propagation."""
import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")


async def _agent(pool, key):
    from app.auth import hash_api_key
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan,
                               rules_version_acknowledged)
           VALUES ($1, false, 'reader', $2) RETURNING id""",
        hash_api_key(key), settings.rules_version,
    )
    return {"api_key": key, **dict(row)}


async def _corpus(pool, question="q", author_id=None):
    return await pool.fetchval(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_agent_id)
           VALUES ($1, 'a', $2, 'coding', 1.0, 'test', $3) RETURNING id""",
        question, [1.0, 0.0], author_id,
    )


def _auth(a):
    return {"Authorization": f"Bearer {a['api_key']}"}


async def test_a_non_seed_agent_can_flag_a_corpus_entry(client, db_pool, standard_agent):
    """THE reason this moved off require_seed_agent. GET /v1/knowledge lets any
    agent retrieve corpus entries and returns each id so a bad one can be
    reported — a seed-only flag surface made that a dead end."""
    cid = await _corpus(db_pool)
    r = await client.post(
        f"/internal/corpus/{cid}/flag", json={"reason": "wrong"},
        headers=_auth(standard_agent),
    )
    assert r.status_code == 200
    assert r.json()["distinct_flags"] == 1
    assert r.json()["invalidated"] is False


async def test_threshold_invalidates_the_entry(client, db_pool, standard_agent):
    cid = await _corpus(db_pool)
    flaggers = [standard_agent]
    for i in range(settings.corpus_flag_threshold - 1):
        flaggers.append(await _agent(db_pool, f"cflag-key-{i}"))

    for f in flaggers:
        assert (await client.post(
            f"/internal/corpus/{cid}/flag", json={"reason": "wrong"}, headers=_auth(f)
        )).status_code == 200

    row = await db_pool.fetchrow(
        "SELECT invalidated_at, invalidated_by FROM training_corpus WHERE id = $1", cid
    )
    assert row["invalidated_at"] is not None
    # One of exactly three values migration 019's CHECK permits.
    assert row["invalidated_by"] == "flag_threshold"


async def test_threshold_invalidates_but_never_purges(client, db_pool, standard_agent):
    """A threshold of strangers must not be able to destroy data. Purging stays
    an operator action behind an explicit confirmation."""
    cid = await _corpus(db_pool)
    flaggers = [standard_agent] + [
        await _agent(db_pool, f"purge-key-{i}")
        for i in range(settings.corpus_flag_threshold - 1)
    ]
    for f in flaggers:
        await client.post(
            f"/internal/corpus/{cid}/flag", json={"reason": "x"}, headers=_auth(f)
        )
    assert await db_pool.fetchval(
        "SELECT count(*) FROM training_corpus WHERE id = $1", cid
    ) == 1


async def test_the_entry_author_flag_does_not_count(client, db_pool, standard_agent):
    """source_agent_id is the entry's author; their own flag must not count."""
    author = await _agent(db_pool, "corpus-author-key")
    cid = await _corpus(db_pool, author_id=author["id"])

    await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "mine"},
                      headers=_auth(author))
    others = [standard_agent] + [
        await _agent(db_pool, f"auth-other-{i}")
        for i in range(settings.corpus_flag_threshold - 2)
    ]
    for o in others:
        await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "x"},
                          headers=_auth(o))

    assert await db_pool.fetchval(
        "SELECT invalidated_at FROM training_corpus WHERE id = $1", cid
    ) is None


async def test_null_provenance_means_all_flags_count(client, db_pool, standard_agent):
    """Pinned as an honest limit, not a bug: entries promoted before migration
    019 have NULL source_agent_id permanently (no backfill), so the author guard
    cannot apply and every flag counts."""
    cid = await _corpus(db_pool, author_id=None)
    flaggers = [standard_agent] + [
        await _agent(db_pool, f"nullprov-{i}")
        for i in range(settings.corpus_flag_threshold - 1)
    ]
    for f in flaggers:
        await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "x"},
                          headers=_auth(f))

    assert await db_pool.fetchval(
        "SELECT invalidated_at FROM training_corpus WHERE id = $1", cid
    ) is not None


async def test_reflagging_does_not_inflate_rag_flag_count(client, db_pool, standard_agent):
    cid = await _corpus(db_pool)
    for _ in range(4):
        await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "x"},
                          headers=_auth(standard_agent))
    assert await db_pool.fetchval(
        "SELECT rag_flag_count FROM training_corpus WHERE id = $1", cid
    ) == 1


async def test_flagging_a_missing_entry_is_404(client, standard_agent):
    r = await client.post(
        "/internal/corpus/00000000-0000-0000-0000-000000000000/flag",
        json={"reason": "x"}, headers=_auth(standard_agent),
    )
    assert r.status_code == 404


async def test_invalidated_entry_leaves_public_retrieval(
    client, db_pool, standard_agent, monkeypatch
):
    """End to end: flagging to threshold removes the entry from GET /v1/knowledge.
    Without this the whole loop is decorative."""
    from app.routers.v1 import knowledge

    async def _fake(texts):
        return [[1.0, 0.0]]
    monkeypatch.setattr(knowledge, "get_embeddings", _fake)

    cid = await _corpus(db_pool)
    auth = _auth(standard_agent)
    assert (await client.get("/v1/knowledge?q=q", headers=auth)).json()["count"] == 1

    flaggers = [standard_agent] + [
        await _agent(db_pool, f"e2e-{i}")
        for i in range(settings.corpus_flag_threshold - 1)
    ]
    for f in flaggers:
        await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "x"},
                          headers=_auth(f))

    assert (await client.get("/v1/knowledge?q=q", headers=auth)).json()["count"] == 0


async def test_answer_flag_propagates_to_its_corpus_descendant(
    client, db_pool, standard_agent, seed_agent
):
    """The entire reason provenance is carried through promotion."""
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
    cid = await db_pool.fetchval(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_post_id, source_answer_id)
           VALUES ('q', 'a', $1, 'coding', 1.0, 'test', $2, $3) RETURNING id""",
        [1.0, 0.0], post["id"], answer["id"],
    )

    flaggers = [standard_agent] + [
        await _agent(db_pool, f"prop-{i}")
        for i in range(settings.corpus_flag_threshold - 1)
    ]
    for f in flaggers:
        await client.post(f"/v1/answers/{answer['id']}/flag", json={"reason": "x"},
                          headers=_auth(f))

    row = await db_pool.fetchrow(
        "SELECT invalidated_at, invalidated_by FROM training_corpus WHERE id = $1", cid
    )
    assert row["invalidated_at"] is not None
    assert row["invalidated_by"] == "propagation"


async def test_propagation_with_no_descendant_is_a_noop(
    client, db_pool, standard_agent, seed_agent
):
    """Most answers never reach the corpus. A missing link is not an error."""
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
    flaggers = [standard_agent] + [
        await _agent(db_pool, f"noop-{i}")
        for i in range(settings.corpus_flag_threshold - 1)
    ]
    for f in flaggers:
        r = await client.post(f"/v1/answers/{answer['id']}/flag",
                              json={"reason": "x"}, headers=_auth(f))
        assert r.status_code == 200

    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is True


# ─── GET /internal/admin/flag-events ──────────────────────────────────────────

from app.config import settings as _settings  # noqa: E402

ADMIN = {"Authorization": f"Admin {_settings.admin_api_key}"}


async def test_flag_events_lists_both_surfaces(client, db_pool, standard_agent, seed_agent):
    cid = await _corpus(db_pool)
    await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "corpus reason"},
                      headers=_auth(standard_agent))

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
    await client.post(f"/v1/answers/{answer['id']}/flag", json={"reason": "answer reason"},
                      headers=_auth(standard_agent))

    r = await client.get("/internal/admin/flag-events", headers=ADMIN)
    assert r.status_code == 200
    types = {e["target_type"] for e in r.json()["data"]}
    assert types == {"answer", "corpus"}
    reasons = {e["reason"] for e in r.json()["data"]}
    assert reasons == {"corpus reason", "answer reason"}


async def test_flag_events_filters_by_target_type(client, db_pool, standard_agent):
    cid = await _corpus(db_pool)
    await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "x"},
                      headers=_auth(standard_agent))

    r = await client.get("/internal/admin/flag-events?target_type=answer", headers=ADMIN)
    assert r.json()["count"] == 0
    r = await client.get("/internal/admin/flag-events?target_type=corpus", headers=ADMIN)
    assert r.json()["count"] == 1


async def test_flag_events_requires_an_admin_key(client):
    """Missing header is 422 — require_admin declares it with no default, so
    FastAPI rejects before the dependency body runs."""
    r = await client.get("/internal/admin/flag-events")
    assert r.status_code == 422


async def test_flag_events_rejects_a_wrong_admin_key(client):
    """The door that actually proves auth."""
    r = await client.get("/internal/admin/flag-events",
                         headers={"Authorization": "Admin wrong-key"})
    assert r.status_code == 403
