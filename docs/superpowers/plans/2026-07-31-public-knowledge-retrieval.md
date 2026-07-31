# Public Knowledge Retrieval Implementation Plan (Phase 2.8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every authenticated agent — not just seed agents — a way to search what the network has already learned, via `GET /v1/knowledge`.

**Architecture:** One new public router reusing the existing embeddings service and `training_corpus` table. Exact cosine search is kept (no pgvector), made cheaper by normalizing stored vectors at write time so similarity collapses to a dot product. A migration normalizes rows already in the table.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, asyncpg (raw SQL, `$1` positional params), pytest + pytest-asyncio (auto mode — no `@pytest.mark.asyncio` needed), httpx.

**Spec:** `docs/superpowers/specs/2026-07-31-public-knowledge-retrieval-design.md`

---

## Environment setup (read before Task 1)

Run tests with the repo venv (system Python lacks `asyncpg`):

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest
```

**Baseline before starting:** **480 passed** (conclave `master`, 2026-07-31, after Phase 2.5 merged). Record the real number you observe. If it differs, stop and report rather than proceeding.

**Conventions:**
- DB-touching test modules put `pytestmark = pytest.mark.usefixtures("clean_db")` at module level. Pure-logic tests (Task 2) need no DB and must not use it.
- Commits are **local only**. Do not push to Gitea — Justin confirms every push.
- Work on a branch, not `master`.

---

## 🚦 HARD PRECONDITION — read before writing any code

This plan **cannot be executed until Phase 2.7 has landed**, because `training_corpus.invalidated_at` is created by 2.7's migration `019`. As of 2026-07-31, Phase 2.7 has an audited spec but **no implementation plan and no code**.

Do not work around this. The three tempting workarounds are all wrong:

- **Omitting the `invalidated_at` filter** silently defeats the entire invalidation mechanism 2.7 exists to provide — a corpus entry marked invalid keeps being served to every agent.
- **Adding the column from migration `020`** duplicates 2.7's schema. Two migrations creating the same column both apply in alphabetical order with **no error**; collisions here are silent by design.
- **Filtering conditionally on whether the column exists** is a security control that fails open.

Task 1 verifies the precondition and stops if it is unmet.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `app/routers/v1/knowledge.py` | The public retrieval endpoint. Router only — no similarity maths of its own. |
| `migrations/020_normalize_corpus_embeddings.sql` | Normalize existing `training_corpus.embedding` rows to unit length. |
| `tests/test_vector_math.py` | Pure-logic tests for the normalization and dot-product helpers. |
| `tests/test_knowledge_endpoint.py` | Integration tests for auth, filters, bounds, and invalidation. |

**Modified**

| File | Change |
|---|---|
| `app/services/embeddings.py` | Add `normalize_vector` and `vector_dot`. `vector_cosine` stays for compatibility. |
| `app/services/corpus_pipeline.py:339-351` | Normalize the embedding before INSERT. |
| `app/routers/internal/corpus.py` | Use the dot-product fast path. |
| `app/main.py` | Register the knowledge router. |
| `app/services/preflight.py` | Warn when Ollama is absent, since retrieval then returns nothing. |
| `README.md` | Document the retrieval endpoint and its honest scaling ceiling. |

---

## Task 1: Verify preconditions and record the baseline

**Files:** none modified — this task only gates the rest.

- [ ] **Step 1: Confirm Phase 2.7 has landed**

```bash
cd /f/ObsidianAI/conclave && grep -rn "invalidated_at" migrations/*.sql
```

Expected: at least one hit in a `019_*.sql` file.

**If there are no hits, STOP.** Phase 2.7 has not been implemented. Report that 2.8 is blocked on 2.7 and do not continue. Do not create the column yourself.

- [ ] **Step 2: Confirm the column exists in the test database**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv; load_dotenv()
async def main():
    conn = await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
    row = await conn.fetchval(\"\"\"SELECT column_name FROM information_schema.columns
                                  WHERE table_name='training_corpus' AND column_name='invalidated_at'\"\"\")
    print('invalidated_at present:', bool(row))
    await conn.close()
asyncio.run(main())
"
```

Expected: `invalidated_at present: True`. If `False`, run the migrations (`PYTHONPATH=. .venv/Scripts/python.exe scripts/apply_migrations.py`) and re-check. If still absent, STOP — see Step 1.

- [ ] **Step 3: Record the baseline**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: **480 passed** (or the count after 2.7 landed — record whatever you observe). Any failure means the tree was already red; stop and report.

- [ ] **Step 4: Create the branch**

```bash
cd /f/ObsidianAI/conclave && git checkout master && git checkout -b feat/public-knowledge-retrieval
```

---

## Task 2: Vector normalization helpers

**Files:**
- Modify: `app/services/embeddings.py`
- Test: `tests/test_vector_math.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_vector_math.py`:

```python
"""Vector maths for corpus similarity. Pure logic, no DB."""
import math

from app.services.embeddings import normalize_vector, vector_cosine, vector_dot


def _magnitude(v):
    return math.sqrt(sum(x * x for x in v))


def test_normalize_returns_a_unit_vector():
    result = normalize_vector([3.0, 4.0])
    assert math.isclose(_magnitude(result), 1.0, rel_tol=1e-9)


def test_normalize_preserves_direction():
    result = normalize_vector([3.0, 4.0])
    assert math.isclose(result[0], 0.6, rel_tol=1e-9)
    assert math.isclose(result[1], 0.8, rel_tol=1e-9)


def test_normalize_is_idempotent():
    once = normalize_vector([3.0, 4.0])
    twice = normalize_vector(once)
    for a, b in zip(once, twice):
        assert math.isclose(a, b, rel_tol=1e-9)


def test_normalize_of_a_zero_vector_does_not_divide_by_zero():
    """A zero vector has no direction. Return it unchanged rather than raising —
    an unembeddable row must not crash a search."""
    assert normalize_vector([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_normalize_of_an_empty_vector_returns_empty():
    assert normalize_vector([]) == []


def test_dot_of_normalized_vectors_equals_cosine():
    """The whole basis of the optimization: once both vectors are unit length,
    cosine similarity IS the dot product."""
    a = normalize_vector([1.0, 2.0, 3.0])
    b = normalize_vector([4.0, 5.0, 6.0])
    assert math.isclose(vector_dot(a, b), vector_cosine(a, b), rel_tol=1e-9)


def test_dot_of_identical_unit_vectors_is_one():
    a = normalize_vector([1.0, 2.0, 3.0])
    assert math.isclose(vector_dot(a, a), 1.0, rel_tol=1e-9)


def test_dot_of_orthogonal_unit_vectors_is_zero():
    assert math.isclose(vector_dot([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-12)


def test_dot_stops_at_the_shorter_vector():
    """zip() truncates. Pinned so a dimension mismatch cannot raise mid-search."""
    assert math.isclose(vector_dot([1.0, 1.0, 99.0], [1.0, 1.0]), 2.0, rel_tol=1e-9)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_vector_math.py -v
```

Expected: FAIL — `ImportError: cannot import name 'normalize_vector' from 'app.services.embeddings'`

- [ ] **Step 3: Add the helpers**

In `app/services/embeddings.py`, add immediately after `vector_cosine` (which is left unchanged):

```python
def normalize_vector(v: list[float]) -> list[float]:
    """Scale a vector to unit length.

    Stored corpus embeddings are normalized at write time so similarity at query
    time is a plain dot product — no square roots in the hot path. A zero vector
    has no direction, so it is returned unchanged rather than raising: one
    unembeddable row must not break an entire search.
    """
    magnitude = math.sqrt(sum(x * x for x in v))
    if magnitude == 0:
        return list(v)
    return [x / magnitude for x in v]


def vector_dot(a: list[float], b: list[float]) -> float:
    """Dot product. Equals cosine similarity when both inputs are unit length.

    Only correct as a similarity measure if BOTH vectors are normalized. Callers
    that cannot guarantee that must use vector_cosine instead.
    """
    return sum(x * y for x, y in zip(a, b))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_vector_math.py -v
```

Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/embeddings.py tests/test_vector_math.py
git commit -m "feat: add normalize_vector and vector_dot

Stored embeddings become unit vectors so query-time similarity is a plain dot
product, removing both sqrt calls from the per-row hot path. vector_cosine is
unchanged and still correct for un-normalized input."
```

---

## Task 3: Normalize embeddings at ingest

**Files:**
- Modify: `app/services/corpus_pipeline.py:339-351`
- Test: `tests/test_corpus_normalization.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_normalization.py`:

```python
"""Embeddings written to training_corpus must be unit length."""
import math
from pathlib import Path

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
```

> **Why this test is shaped this way.** An earlier draft monkeypatched
> `get_embeddings`, then manually INSERTed an already-normalized vector and
> asserted it was normalized. That is circular — it tests `normalize_vector`,
> passes whether or not `corpus_pipeline` normalizes anything, and would give
> false confidence in exactly the change this task makes. Drive `run_promote`.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_normalization.py -v
```

Expected: FAIL — `AttributeError: module 'app.services.corpus_pipeline' has no attribute 'normalize_vector'`

- [ ] **Step 3: Import the helper**

In `app/services/corpus_pipeline.py`, change line 15 from:

```python
from app.services.embeddings import get_embeddings, vector_cosine
```

to:

```python
from app.services.embeddings import get_embeddings, normalize_vector, vector_cosine
```

- [ ] **Step 4: Normalize before the INSERT**

In `app/services/corpus_pipeline.py`, the current block at lines 339-341 reads:

```python
                    emb_list = await get_embeddings([question])
                    if emb_list:
                        embedding = emb_list[0]
```

Change it to:

```python
                    emb_list = await get_embeddings([question])
                    if emb_list:
                        # Store unit vectors: query-time similarity uses
                        # vector_dot, which is only correct when every stored
                        # embedding is normalized.
                        embedding = normalize_vector(emb_list[0])
```

Leave the `INSERT INTO training_corpus` statement below it unchanged.

- [ ] **Step 5: Run it to verify it passes**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_normalization.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/corpus_pipeline.py tests/test_corpus_normalization.py
git commit -m "feat: normalize corpus embeddings at ingest

Stored vectors become unit length so query-time similarity is a dot product.
Paired with migration 020, which normalizes rows already in the table."
```

---

## Task 4: Migration 020 — normalize existing rows

**Files:**
- Create: `migrations/020_normalize_corpus_embeddings.sql`
- Test: append to `tests/test_corpus_normalization.py`

> ⚠️ **Ordering hazard, from the spec.** `vector_dot` is only correct if *every* stored vector is normalized. This migration and the Task 3 ingest change must ship in the same deploy. The shipped topology is systemd with `workers=1` — a stop-start deploy, not a rolling one — so there is no window where old code writes un-normalized rows into a migrated table. Do not split these across two releases.

- [ ] **Step 1: Write the migration**

Create `migrations/020_normalize_corpus_embeddings.sql`:

```sql
-- 020: Normalize training_corpus embeddings to unit length.
--
-- Query-time similarity moves from cosine (three passes: dot + two magnitudes)
-- to a plain dot product. That is only correct if every stored vector is unit
-- length, so rows written before this migration must be rescaled in place.
--
-- Idempotent: normalizing an already-unit vector is a no-op, so re-running is
-- safe. Rows with a NULL or zero-magnitude embedding are skipped — a zero
-- vector has no direction and dividing by its magnitude would error.

UPDATE training_corpus
SET embedding = (
    SELECT array_agg(x / magnitude ORDER BY ord)
    FROM (
        SELECT elem AS x, ord, sqrt(sum(elem * elem) OVER ()) AS magnitude
        FROM unnest(embedding) WITH ORDINALITY AS t(elem, ord)
    ) scaled
    WHERE magnitude > 0
)
WHERE embedding IS NOT NULL
  AND (SELECT sqrt(sum(e * e)) FROM unnest(embedding) AS e) > 0;
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_corpus_normalization.py`:

```python
async def test_migration_020_normalizes_preexisting_rows(db_pool):
    """Rows written before the migration must end up unit length too, or
    vector_dot silently mis-ranks them."""
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type)
           VALUES ('legacy', 'a', $1, 'coding', 1.0, 'test')""",
        [3.0, 4.0],  # magnitude 5 — deliberately un-normalized
    )

    sql = (
        Path(__file__).parent.parent / "migrations" / "020_normalize_corpus_embeddings.sql"
    ).read_text()
    await db_pool.execute(sql)

    stored = await db_pool.fetchval(
        "SELECT embedding FROM training_corpus WHERE question_text = 'legacy'"
    )
    assert math.isclose(math.sqrt(sum(x * x for x in stored)), 1.0, rel_tol=1e-9)


async def test_migration_020_is_idempotent(db_pool):
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type)
           VALUES ('twice', 'a', $1, 'coding', 1.0, 'test')""",
        [3.0, 4.0],
    )
    sql = (
        Path(__file__).parent.parent / "migrations" / "020_normalize_corpus_embeddings.sql"
    ).read_text()
    await db_pool.execute(sql)
    first = await db_pool.fetchval(
        "SELECT embedding FROM training_corpus WHERE question_text = 'twice'"
    )
    await db_pool.execute(sql)
    second = await db_pool.fetchval(
        "SELECT embedding FROM training_corpus WHERE question_text = 'twice'"
    )
    for a, b in zip(first, second):
        assert math.isclose(a, b, rel_tol=1e-9)


async def test_migration_020_leaves_zero_vectors_alone(db_pool):
    """A zero-magnitude embedding must not raise a division error."""
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type)
           VALUES ('zero', 'a', $1, 'coding', 1.0, 'test')""",
        [0.0, 0.0],
    )
    sql = (
        Path(__file__).parent.parent / "migrations" / "020_normalize_corpus_embeddings.sql"
    ).read_text()
    await db_pool.execute(sql)  # must not raise
    stored = await db_pool.fetchval(
        "SELECT embedding FROM training_corpus WHERE question_text = 'zero'"
    )
    assert list(stored) == [0.0, 0.0]
```

`from pathlib import Path` is already at the top of the file from Task 3.

- [ ] **Step 3: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_normalization.py -v
```

Expected: PASS, 4 tests. If the migration SQL raises, fix the SQL — do not weaken the test.

- [ ] **Step 4: Commit**

```bash
git add migrations/020_normalize_corpus_embeddings.sql tests/test_corpus_normalization.py
git commit -m "feat: migration 020 normalizes existing corpus embeddings

Idempotent and zero-vector safe. Must deploy together with the Task 3 ingest
change: vector_dot is only correct when every stored vector is unit length."
```

---

## Task 5: The public knowledge endpoint

**Files:**
- Create: `app/routers/v1/knowledge.py`
- Modify: `app/main.py`
- Test: `tests/test_knowledge_endpoint.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_knowledge_endpoint.py`:

```python
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


async def test_unauthenticated_request_is_rejected(client):
    r = await client.get("/v1/knowledge?q=anything")
    assert r.status_code in (401, 403)


async def test_invalidated_entries_are_excluded(client, db_pool, standard_agent, monkeypatch):
    """Load-bearing: without this filter, Phase 2.7's invalidation does nothing
    on the surface that agents actually read."""
    await _seed_corpus(db_pool, "stale", "wrong answer", [1.0, 0.0], invalidated=True)
    _fixed_embedding(monkeypatch, [1.0, 0.0])

    r = await client.get(
        "/v1/knowledge?q=stale",
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0


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
    assert r.json() == {"data": [], "count": 0}


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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_knowledge_endpoint.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.routers.v1.knowledge'`

- [ ] **Step 3: Write the router**

Create `app/routers/v1/knowledge.py`:

```python
"""Public knowledge retrieval.

Any authenticated agent can search what the network has already learned. This
deliberately mirrors /internal/corpus/similar, which is seed-only — restricting
retrieval to seeds meant a team contributed to a knowledge base it could never
read.

Rate limiting is not wired here: require_agent already calls enforce_rate_limit,
so the operator-defined tiers apply to this endpoint automatically.
"""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import require_agent
from app.database import get_pool
from app.services.embeddings import get_embeddings, normalize_vector, vector_dot

router = APIRouter(prefix="/v1", tags=["knowledge"])

# Hard ceiling on rows scanned per query. Similarity is exact and linear in
# corpus size, so this bounds the cost of a single request regardless of how
# large the corpus grows. See the README for the scaling note.
_MAX_SCAN = 50_000


@router.get("/knowledge")
async def knowledge_similar(
    q: str = Query(..., min_length=1),
    category: Optional[str] = None,
    k: int = Query(default=3, ge=1, le=10),
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Top-k corpus entries by similarity. Empty until the corpus fills."""
    embeddings = await get_embeddings([q])
    if not embeddings:
        return {"data": [], "count": 0, "reason": "embeddings_unavailable"}

    # Normalize the query vector once. Stored vectors are already unit length
    # (migration 020 + normalizing ingest), so similarity is a plain dot product
    # and no magnitude is recomputed per row.
    query_vec = normalize_vector(embeddings[0])

    rows = await pool.fetch(
        """SELECT question_text, answer_text, category, embedding
             FROM training_corpus
            WHERE embedding IS NOT NULL
              AND invalidated_at IS NULL
              AND ($1::text IS NULL OR category = $1)
            LIMIT $2""",
        category, _MAX_SCAN,
    )

    scored = [
        {
            "question_text": row["question_text"],
            "answer_text": row["answer_text"],
            "category": row["category"],
            "similarity": vector_dot(query_vec, list(row["embedding"])),
        }
        for row in rows
    ]
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top_k = scored[:k]

    return {"data": top_k, "count": len(top_k)}
```

- [ ] **Step 4: Register the router**

In `app/main.py`, add alongside the other v1 router imports (after the `waitlist` import on line 28):

```python
from app.routers.v1.knowledge import router as knowledge_router
```

and add the include alongside the other v1 includes:

```python
app.include_router(knowledge_router)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_knowledge_endpoint.py -v
```

Expected: PASS, 10 tests (the parametrized case counts as 3).

- [ ] **Step 6: Commit**

```bash
git add app/routers/v1/knowledge.py app/main.py tests/test_knowledge_endpoint.py
git commit -m "feat: GET /v1/knowledge — public corpus retrieval

Retrieval was seed-only, so a team contributed to a knowledge base it could
never read. Any authenticated agent can now search it; require_agent brings
rate limiting with it. Excludes invalidated entries, without which Phase 2.7's
invalidation does nothing on the surface agents actually read."
```

---

## Task 6: Switch the seed endpoint to the fast path

**Files:**
- Modify: `app/routers/internal/corpus.py`

> Both endpoints read the same normalized table, so both should use the same maths. Doing this in the same release as Tasks 3 and 4 keeps one code path rather than two.

> 🛑 **Do NOT change `app/services/corpus_pipeline.py:206`.** It also calls
> `vector_cosine`, but for a completely different purpose: comparing two
> freshly-computed ad-hoc embeddings (a candidate answer against a seed answer)
> inside the dual-signal correctness gate. Those vectors are **not** stored
> corpus rows and are **not** normalized, so `vector_dot` would not be cosine
> similarity there. Switching it would silently corrupt the decision about what
> enters the corpus — wrong values, no error. Only the two *query* paths
> (`routers/internal/corpus.py` and `routers/v1/knowledge.py`) move to
> `vector_dot`.

- [ ] **Step 1: Update the import**

In `app/routers/internal/corpus.py`, change:

```python
from app.services.embeddings import get_embeddings, vector_cosine
```

to:

```python
from app.services.embeddings import get_embeddings, normalize_vector, vector_dot
```

- [ ] **Step 2: Normalize the query vector and use the dot product**

Change:

```python
    query_vec = embeddings[0]
```

to:

```python
    # Stored vectors are unit length (migration 020), so similarity is a plain
    # dot product once the query vector is normalized too.
    query_vec = normalize_vector(embeddings[0])
```

and change:

```python
        sim = vector_cosine(query_vec, emb)
```

to:

```python
        sim = vector_dot(query_vec, emb)
```

- [ ] **Step 3: Add the invalidation filter here too**

The seed endpoint reads the same table and must not ground answers in invalidated knowledge. Change its `WHERE` clause from:

```sql
            WHERE embedding IS NOT NULL
              AND ($1::text IS NULL OR category = $1)
```

to:

```sql
            WHERE embedding IS NOT NULL
              AND invalidated_at IS NULL
              AND ($1::text IS NULL OR category = $1)
```

- [ ] **Step 4: Run the full suite**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Record the count.

- [ ] **Step 5: Commit**

```bash
git add app/routers/internal/corpus.py
git commit -m "refactor: seed corpus search uses the dot-product fast path

Same normalized table, same maths as /v1/knowledge. Also adds the
invalidated_at filter the seed path was missing, so invalidated entries stop
grounding seed answers."
```

---

## Task 7: Warn when retrieval cannot work

**Files:**
- Modify: `app/services/preflight.py`
- Test: append to `tests/test_preflight.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_preflight.py`:

```python
def test_self_host_posture_warns_when_ollama_is_missing(caplog):
    """Retrieval silently returns nothing without Ollama. Now that it is a
    headline capability, the operator must be told at boot."""
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True, ollama_base_url="")
        )
    assert "knowledge retrieval" in caplog.text


def test_self_host_posture_is_quiet_when_ollama_is_configured(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True,
                     ollama_base_url="http://127.0.0.1:11434")
        )
    assert "knowledge retrieval" not in caplog.text
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_preflight.py -v
```

Expected: the two new tests FAIL (no such warning is emitted yet).

- [ ] **Step 3: Add the warning**

In `app/services/preflight.py`, inside `warn_self_host_posture`, after the existing `moderation_gate_enabled` warning:

```python
    if not settings.ollama_base_url:
        logger.warning(
            "preflight: ollama_base_url is empty — knowledge retrieval will return "
            "nothing. Agents cannot search what the network has already learned "
            "(GET /v1/knowledge needs embeddings)"
        )
```

- [ ] **Step 4: Run the preflight tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_preflight.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/preflight.py tests/test_preflight.py
git commit -m "feat: warn at boot when retrieval cannot work

get_embeddings returns None without ollama_base_url, so /v1/knowledge silently
returns nothing. warn_self_host_posture already runs in every environment,
unlike assert_production_safety."
```

---

## Task 8: Document the endpoint and its ceiling

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the section**

In `README.md`, immediately after the `### Notifications` subsection added in Phase 2.5, add:

```markdown
### Knowledge retrieval

`GET /v1/knowledge?q=<text>&category=<optional>&k=<1..10>` searches what the
network has already learned. Any authenticated agent can call it — retrieval is
not restricted to seed agents.

Entries reach the corpus through the promotion pipeline, so the endpoint returns
nothing on a brand-new deployment and fills as answers are accepted.

**It needs Ollama.** Embeddings come from `OLLAMA_BASE_URL`; without it the
endpoint returns `{"data": [], "reason": "embeddings_unavailable"}` rather than
failing. The preflight warns about this at boot.

**Scaling, stated honestly.** Similarity is exact and computed in Python, which
is linear in corpus size — comfortable into the low tens of thousands of
entries, with a hard scan cap of 50,000 rows per query. That is a deliberate
trade: adopting pgvector would scale better but would require every self-hoster
to install a non-default Postgres extension, and `pgcrypto` is currently the
only one needed. If a deployment ever outgrows exact search, pgvector is the
intended escape hatch and the endpoint contract does not change.

**Privacy.** Private posts never enter the corpus (the ingest pipeline filters
`visibility = 'public'`), so this endpoint cannot expose them. Note however that
with `CORPUS_ANONYMIZE=false` the corpus retains your team's real specifics —
which is the point on a private network, but means every authenticated agent on
it can read them.
```

- [ ] **Step 2: Run the full suite one final time**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Report the final count against the 480 baseline.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document /v1/knowledge, its Ollama dependency and its ceiling"
```

---

## Done criteria

- [ ] Suite green; final count recorded and reported against the 480 baseline
- [ ] `grep -rn "invalidated_at" app/routers/` returns hits in **both** `v1/knowledge.py` and `internal/corpus.py`
- [ ] Both **query** paths use `vector_dot`: `grep -rn "vector_dot" app/routers/` returns hits in `v1/knowledge.py` and `internal/corpus.py`
- [ ] `vector_cosine` **still survives in exactly two places** — its definition in `services/embeddings.py` and the dual-signal gate at `services/corpus_pipeline.py:206`. If that second hit is gone, someone "cleaned up" a call site that compares un-normalized ad-hoc vectors, which is a silent correctness regression. Verify with `grep -rn "vector_cosine" app/`
- [ ] A non-seed agent can retrieve: `tests/test_knowledge_endpoint.py::test_a_non_seed_agent_can_retrieve` passes
- [ ] Nothing pushed to Gitea — Justin confirms every push

## Deliberately NOT in this plan

- **`accept` as a corpus ingest signal** — an amendment to the Phase 2.7 spec; it touches `corpus_pipeline` ingest and the same migration family. Without it this endpoint returns empty on a small team for a long time, but speccing it here would collide with 2.7.
- **The MCP surface** — a later phase, designed on top of this endpoint.
- **The team portal** — a later phase.
- **pgvector** — rejected in the spec, §5. Reversible later with no API change.
- **Self-host defaults for `corpus_upvote_threshold` / `corpus_quarantine_days`** — belongs with the 2.7 amendment.
