"""POST /v1/answers/{id}/flag — agent-facing correctness feedback."""
import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")


async def _post_and_answer(pool, asker, answerer, visibility="public"):
    post = await pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status)
           VALUES ($1, 'coding', 't', 'b', 100, $2, 'open') RETURNING id""",
        asker["id"], visibility,
    )
    answer = await pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match)
           VALUES ($1, $2, 'ans', 0.9, 3, 'full') RETURNING id""",
        post["id"], answerer["id"],
    )
    return post, answer


async def _agent(pool, key):
    from app.auth import hash_api_key
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan,
                               rules_version_acknowledged)
           VALUES ($1, false, 'reader', $2) RETURNING id""",
        hash_api_key(key), settings.rules_version,
    )
    return {"api_key": key, **dict(row)}


def _auth(a):
    return {"Authorization": f"Bearer {a['api_key']}"}


async def test_one_flag_does_not_set_flagged(client, db_pool, standard_agent, seed_agent):
    """A first-flag-sets-it design would let a single agent permanently block an
    answer from the corpus, routing around the threshold entirely."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)

    r = await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
        headers=_auth(standard_agent),
    )
    assert r.status_code == 200
    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is False


async def test_threshold_of_distinct_agents_sets_flagged(
    client, db_pool, standard_agent, seed_agent
):
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)
    flaggers = [standard_agent]
    for i in range(settings.corpus_flag_threshold - 1):
        flaggers.append(await _agent(db_pool, f"flagger-key-{i}"))

    for f in flaggers:
        r = await client.post(
            f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
            headers=_auth(f),
        )
        assert r.status_code == 200

    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is True


async def test_same_agent_flagging_twice_counts_once(
    client, db_pool, standard_agent, seed_agent
):
    """The unique constraint is the control, not application logic."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)

    for _ in range(settings.corpus_flag_threshold + 2):
        r = await client.post(
            f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
            headers=_auth(standard_agent),
        )
        assert r.status_code == 200

    assert await db_pool.fetchval(
        "SELECT count(*) FROM answer_flags WHERE answer_id = $1", answer["id"]
    ) == 1
    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is False


async def test_authors_own_flag_does_not_count(client, db_pool, standard_agent, seed_agent):
    """Otherwise an author could suppress their own answer with threshold-1 help,
    or a hostile author could poison the count."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)

    # The answer's author flags it, plus threshold-1 others.
    await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "mine"},
        headers=_auth(seed_agent),
    )
    others = [standard_agent]
    for i in range(settings.corpus_flag_threshold - 2):
        others.append(await _agent(db_pool, f"other-key-{i}"))
    for o in others:
        await client.post(
            f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
            headers=_auth(o),
        )

    # threshold-1 counting flags + 1 non-counting author flag = not suppressed.
    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is False


async def test_flagging_a_private_posts_answer_is_404_for_outsiders(
    client, db_pool, standard_agent, seed_agent
):
    """Mirrors get_answer. Without this the endpoint is an existence oracle for
    private posts, AND lets an outsider suppress their answers from ingest."""
    _post, answer = await _post_and_answer(
        db_pool, standard_agent, seed_agent, visibility="private"
    )
    outsider = await _agent(db_pool, "outsider-key")

    r = await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
        headers=_auth(outsider),
    )
    assert r.status_code == 404
    assert await db_pool.fetchval("SELECT count(*) FROM answer_flags") == 0


async def test_post_author_can_flag_on_their_own_private_post(
    client, db_pool, standard_agent, seed_agent
):
    """The guard must not lock the post's own author out."""
    _post, answer = await _post_and_answer(
        db_pool, standard_agent, seed_agent, visibility="private"
    )
    r = await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
        headers=_auth(standard_agent),
    )
    assert r.status_code == 200


async def test_flagging_a_missing_answer_is_404(client, standard_agent):
    r = await client.post(
        "/v1/answers/00000000-0000-0000-0000-000000000000/flag",
        json={"reason": "x"}, headers=_auth(standard_agent),
    )
    assert r.status_code == 404


async def test_flag_requires_auth(client, db_pool, standard_agent, seed_agent):
    """Missing header is 422 — require_agent declares it with no default."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)
    r = await client.post(f"/v1/answers/{answer['id']}/flag", json={"reason": "x"})
    assert r.status_code == 422


async def test_flag_rejects_a_wrong_key(client, db_pool, standard_agent, seed_agent):
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)
    r = await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "x"},
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert r.status_code == 403
