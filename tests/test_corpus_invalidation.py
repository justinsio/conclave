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


# ─── Operator corpus surface (Task 8) ─────────────────────────────────────────
# require_admin reads `Authorization: Admin <key>` — NOT an X-Admin-Key header.
# Matches the existing convention in tests/test_beta_accounts.py:14.
from app.config import settings as _settings

ADMIN = {"Authorization": f"Admin {_settings.admin_api_key}"}


async def _one_corpus_id(pool):
    return await pool.fetchval("SELECT id FROM training_corpus LIMIT 1")


async def test_admin_can_invalidate_and_restore(client, db_pool):
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)

    r = await client.post(
        f"/internal/admin/corpus/{cid}/invalidate",
        json={"reason": "superseded"}, headers=ADMIN,
    )
    assert r.status_code == 200
    row = await db_pool.fetchrow(
        "SELECT invalidated_at, invalidated_reason, invalidated_by FROM training_corpus WHERE id = $1",
        cid,
    )
    assert row["invalidated_at"] is not None
    assert row["invalidated_reason"] == "superseded"
    assert row["invalidated_by"] == "operator"

    r = await client.post(f"/internal/admin/corpus/{cid}/restore", headers=ADMIN)
    assert r.status_code == 200
    assert await db_pool.fetchval(
        "SELECT invalidated_at FROM training_corpus WHERE id = $1", cid
    ) is None


async def test_purge_requires_explicit_confirmation(client, db_pool):
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)

    r = await client.request(
        "DELETE", f"/internal/admin/corpus/{cid}", json={"confirm": False}, headers=ADMIN,
    )
    assert r.status_code == 400
    assert await db_pool.fetchval(
        "SELECT count(*) FROM training_corpus WHERE id = $1", cid
    ) == 1


async def test_purge_removes_the_row(client, db_pool):
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)

    r = await client.request(
        "DELETE", f"/internal/admin/corpus/{cid}", json={"confirm": True}, headers=ADMIN,
    )
    assert r.status_code == 200
    assert await db_pool.fetchval(
        "SELECT count(*) FROM training_corpus WHERE id = $1", cid
    ) == 0


async def test_purge_cascades_flags_and_writes_audit(client, db_pool, standard_agent):
    """Spec §Testing requires all three, not just the row count: the row goes,
    its corpus_flags go with it (ON DELETE CASCADE, migration 019), and the
    action is auditable."""
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)
    await db_pool.execute(
        "INSERT INTO corpus_flags (corpus_id, agent_id, reason) VALUES ($1, $2, 'wrong')",
        cid, standard_agent["id"],
    )
    assert await db_pool.fetchval(
        "SELECT count(*) FROM corpus_flags WHERE corpus_id = $1", cid
    ) == 1

    r = await client.request(
        "DELETE", f"/internal/admin/corpus/{cid}", json={"confirm": True}, headers=ADMIN,
    )
    assert r.status_code == 200

    assert await db_pool.fetchval(
        "SELECT count(*) FROM corpus_flags WHERE corpus_id = $1", cid
    ) == 0
    # Read through the partitioned PARENT — never by partition name. Naming
    # audit_log_2026_07 is what rotted test_reset_track_a_writes_audit_log on
    # 2026-08-01 (fixed in a497cb2).
    audit = await db_pool.fetchrow(
        "SELECT metadata FROM audit_log WHERE action = 'admin_corpus_purge'"
    )
    assert audit is not None
    assert audit["metadata"]["corpus_id"] == str(cid)


async def test_corpus_list_filters_by_invalidation_state(client, db_pool):
    await _corpus_row(db_pool, "live", "a")
    await _corpus_row(db_pool, "dead", "b", invalidated=True)

    r = await client.get("/internal/admin/corpus?invalidated=false", headers=ADMIN)
    questions = [e["question_text"] for e in r.json()["data"]]
    assert "live" in questions and "dead" not in questions


async def test_admin_endpoints_reject_a_wrong_key(client, db_pool):
    """A wrong key is the door that matters — require_admin raises 403 for both
    a bad prefix and a bad key."""
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)
    r = await client.post(
        f"/internal/admin/corpus/{cid}/invalidate",
        json={"reason": "x"},
        headers={"Authorization": "Admin not-an-admin-key"},
    )
    assert r.status_code == 403


async def test_admin_endpoints_reject_a_missing_key(client, db_pool):
    """No Authorization header at all is a 422, not a 401/403: require_admin
    declares `authorization: Annotated[str, Header()]` with no default, so
    FastAPI rejects the request as a missing required parameter BEFORE the
    dependency body runs."""
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)
    r = await client.post(f"/internal/admin/corpus/{cid}/invalidate", json={"reason": "x"})
    assert r.status_code == 422
