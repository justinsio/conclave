"""GET /v1/knowledge — public corpus retrieval for any authenticated agent."""
import pytest

from app.routers.v1 import knowledge

pytestmark = pytest.mark.usefixtures("clean_db")


async def _seed_corpus(pool, question, answer, embedding, category="coding",
                       invalidated=False):
    await pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, invalidated_at)
           VALUES ($1, $2, $3, $4, 1.0, 'test',
                   CASE WHEN $5 THEN NOW() ELSE NULL END)""",
        question, answer, embedding, category, invalidated,
    )


def _fixed_embedding(monkeypatch, vector):
    async def _fake(texts):
        return [vector]
    monkeypatch.setattr(knowledge, "get_embeddings", _fake)


async def test_a_non_seed_agent_can_retrieve(client, db_pool, standard_agent, monkeypatch):
    """THE regression test for this phase. Retrieval used to be seed-only, so a
    team could contribute to a knowledge base it could never read."""
    await _seed_corpus(db_pool, "how to dedupe", "use a set", [1.0, 0.0])
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=dedupe",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["data"][0]["answer_text"] == "use a set"


async def test_response_carries_the_entry_id(client, db_pool, standard_agent, monkeypatch):
    """Without an id a caller who retrieves a wrong answer has no handle on it
    and nothing to report. Adding it later is a contract change."""
    await _seed_corpus(db_pool, "q", "a", [1.0, 0.0])
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=q",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    expected = await db_pool.fetchval("SELECT id FROM training_corpus LIMIT 1")
    assert r.json()["data"][0]["id"] == str(expected)


async def test_missing_auth_header_is_422(client):
    """NOT 401/403. require_agent declares `authorization: Annotated[str, Header()]`
    with no default (app/auth.py:130), so FastAPI rejects the request as a missing
    required parameter BEFORE the dependency body runs. Mirrors
    tests/test_corpus_invalidation.py, which exists for this exact trap."""
    r = await client.get("/v1/knowledge?q=anything")
    assert r.status_code == 422


async def test_wrong_api_key_is_rejected(client):
    """The door that actually proves auth."""
    r = await client.get(
        "/v1/knowledge?q=anything",
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert r.status_code == 403


async def test_invalidated_entries_are_excluded(client, db_pool, standard_agent, monkeypatch):
    """Load-bearing: without this filter, Phase 2.7a's invalidation does nothing
    on the surface that agents actually read."""
    await _seed_corpus(db_pool, "stale", "wrong answer", [1.0, 0.0], invalidated=True)
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=stale",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0


async def test_moderator_deleted_answer_is_not_re_served(
    client, db_pool, standard_agent, seed_agent, monkeypatch
):
    """A moderator removed this answer for cause. Nothing propagates that to
    training_corpus, so without the delete-join the public endpoint hands it
    back to the whole network — content the moderation path already took down."""
    post = await db_pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget)
           VALUES ($1, 'coding', 't', 'b', 100) RETURNING id""",
        standard_agent["id"],
    )
    answer = await db_pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match)
           VALUES ($1, $2, 'bad', 0.9, 3, 'full') RETURNING id""",
        post["id"], seed_agent["id"],
    )
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_post_id, source_answer_id)
           VALUES ('q', 'bad', $1, 'coding', 1.0, 'test', $2, $3)""",
        [1.0, 0.0], post["id"], answer["id"],
    )
    _fixed_embedding(monkeypatch, [1.0, 0.0])
    auth = {"Authorization": f"Bearer {standard_agent['api_key']}"}

    # Retrievable while the answer stands.
    r = await client.get("/v1/knowledge?q=q", headers=auth)
    assert r.json()["count"] == 1

    # Moderation soft-deletes it (app/routers/v1/admin.py).
    await db_pool.execute("UPDATE answers SET deleted = TRUE WHERE id = $1", answer["id"])

    r = await client.get("/v1/knowledge?q=q", headers=auth)
    assert r.json()["count"] == 0


async def test_moderator_deleted_post_is_not_re_served(
    client, db_pool, standard_agent, seed_agent, monkeypatch
):
    """Same guard on the post side."""
    post = await db_pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget)
           VALUES ($1, 'coding', 't', 'b', 100) RETURNING id""",
        standard_agent["id"],
    )
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_post_id)
           VALUES ('q', 'a', $1, 'coding', 1.0, 'test', $2)""",
        [1.0, 0.0], post["id"],
    )
    _fixed_embedding(monkeypatch, [1.0, 0.0])
    auth = {"Authorization": f"Bearer {standard_agent['api_key']}"}

    assert (await client.get("/v1/knowledge?q=q", headers=auth)).json()["count"] == 1

    await db_pool.execute("UPDATE posts SET status = 'deleted' WHERE id = $1", post["id"])

    assert (await client.get("/v1/knowledge?q=q", headers=auth)).json()["count"] == 0


async def test_entry_with_null_provenance_is_still_served(
    client, db_pool, standard_agent, monkeypatch
):
    """The honest limit, pinned so nobody later mistakes it for a bug: entries
    promoted before 2.7a have NULL provenance permanently — there is no
    backfill — so the delete-join cannot check them and they stay retrievable.
    Removing one is an operator action: POST /internal/admin/corpus/{id}/invalidate.
    """
    await _seed_corpus(db_pool, "legacy", "old answer", [1.0, 0.0])
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=legacy",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.json()["count"] == 1


async def test_category_filter_narrows_results(client, db_pool, standard_agent, monkeypatch):
    await _seed_corpus(db_pool, "c", "coding answer", [1.0, 0.0], category="coding")
    await _seed_corpus(db_pool, "r", "research answer", [1.0, 0.0], category="research")
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=x&category=research",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.json()["count"] == 1
    assert r.json()["data"][0]["answer_text"] == "research answer"


async def test_empty_corpus_returns_empty_not_an_error(client, standard_agent, monkeypatch):
    _fixed_embedding(monkeypatch, [1.0, 0.0])
    r = await client.get(
        "/v1/knowledge?q=nothing here",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json() == {"data": [], "count": 0, "truncated": False}


async def test_missing_embeddings_degrade_gracefully(client, standard_agent, monkeypatch):
    """No Ollama must not fail an agent's turn."""
    async def _none(texts):
        return None
    monkeypatch.setattr(knowledge, "get_embeddings", _none)

    r = await client.get(
        "/v1/knowledge?q=anything",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["reason"] == "embeddings_unavailable"
    assert r.json()["count"] == 0


@pytest.mark.parametrize("bad", ["/v1/knowledge?q=x&k=0", "/v1/knowledge?q=x&k=11",
                                 "/v1/knowledge?q="])
async def test_out_of_range_parameters_are_rejected(client, standard_agent, bad):
    """FastAPI's ge/le REJECT with 422 — they do not clamp."""
    r = await client.get(bad, headers={"Authorization": f"Bearer {standard_agent['api_key']}"})
    assert r.status_code == 422


async def test_results_are_ordered_by_similarity(client, db_pool, standard_agent, monkeypatch):
    await _seed_corpus(db_pool, "far", "far answer", [0.0, 1.0])
    await _seed_corpus(db_pool, "near", "near answer", [1.0, 0.0])
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=x&k=2",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    data = r.json()["data"]
    assert data[0]["answer_text"] == "near answer"
    assert data[0]["similarity"] >= data[1]["similarity"]


async def test_not_truncated_on_a_small_corpus(client, db_pool, standard_agent, monkeypatch):
    """truncated must mean something — it is False well under the scan cap."""
    await _seed_corpus(db_pool, "q", "a", [1.0, 0.0])
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=q",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.json()["truncated"] is False
