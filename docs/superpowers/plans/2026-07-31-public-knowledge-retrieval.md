# Public Knowledge Retrieval Implementation Plan (Phase 2.8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every authenticated agent — not just seed agents — a way to search what the network has already learned, via `GET /v1/knowledge`.

**Architecture:** One new public router reusing the existing embeddings service and `training_corpus` table. Exact cosine search is kept (no pgvector), made cheaper by normalizing stored vectors at write time so similarity collapses to a dot product. A migration normalizes rows already in the table.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, asyncpg (raw SQL, `$1` positional params), pytest + pytest-asyncio (auto mode — no `@pytest.mark.asyncio` needed), httpx.

**Spec:** `docs/superpowers/specs/2026-07-31-public-knowledge-retrieval-design.md`

---

## Revision 2 — 2026-08-01, after a cold adversarial audit

Rev 1 was written **before Phase 2.7a existed** and self-reviewed by its own author. A fresh read-only agent audited it against the real code and returned *EXECUTE AFTER FIXES*. Every 🔴 and the three most consequential 🟠 were independently re-verified before this revision.

**Most of rev 1's defects are staleness, not reasoning errors** — the plan described a world in which 2.7a was unbuilt. That is an argument for auditing a plan *close to when it executes*, not in a batch up front.

| # | Sev | What was wrong in rev 1 |
|---|---|---|
| 1 | 🔴 | **Task 1 Step 4 branched from `master`, discarding 2.7a.** Steps 1–3 verified 2.7a in the working tree, then Step 4 checked out a tree that had none of it — the Task 5 router would query `invalidated_at`, a column that branch never creates. **And it passed locally**: `conclave_test` is persistent and already had the column, so the test meant to catch this went green. *Resolved differently than the audit proposed: 2.7a was merged to `master` (`cd76ab8`), so `git checkout master` is correct again — but a post-checkout assertion is now mandatory, see Task 1.* |
| 2 | 🔴 | **`test_unauthenticated_request_is_rejected` asserted 401/403; FastAPI returns 422.** `require_agent` declares its header with no default (`app/auth.py:130`), so a missing header is rejected before the dependency runs. `tests/test_corpus_invalidation.py:172` already documents this exact trap — rev 1 reproduced the bug that test exists to prevent. |
| 3 | 🟠 | **Corpus entries outlive their sources, and 2.8 removes the containment.** Moderation soft-deletes (`posts.status='deleted'`, `answers.deleted=TRUE`) and nothing propagates that to `training_corpus`. Seed-only retrieval hid this; a public endpoint re-serves moderator-removed content to the whole network. The spec called this area *"Privacy — verified, no new work."* **New Task 5b.** |
| 4 | 🟠 | **`vector_cosine` has three call sites, not two.** The third, `app/services/divergence.py:78`, is imported **aliased** as `_vector_cosine` — it survives a naive grep-and-replace and operates on un-normalized ad-hoc vectors, exactly what the plan's own warning protects. |
| 5 | 🟠 | **The "no deploy window" claim is false.** `deploy/conclave.service` has no `ExecStartPre`, and `apply_migrations.py` skips applied files permanently, so `020` runs **once, ever**. Migrate-then-restart leaves un-normalized rows wrong *forever*. `workers=1` prevents concurrent processes; it does not order a migration against a restart. |
| 6 | 🟠 | **`_MAX_SCAN = 50_000` was sold in the README as a safety property.** At 768-dim `DOUBLE PRECISION[]` that is ~1.27 GB of Python floats per request, on a `--workers 1` box, reachable by any `reader` agent at 60 req/min. |
| 7 | 🟠 | **`LIMIT` with no `ORDER BY`** while the README claims similarity is "exact" — past the cap Postgres returns an arbitrary subset, silently dropping the best match. |
| 8 | 🟡 | Baseline said 480 (now **503**); Task 6 Step 3 adds a filter 2.7a already shipped, with a commit message asserting it "was missing"; Task 3's expected red-phase error was wrong; migration `020` used non-ASCII against 019's stated convention; README insertion point now splits 2.7a's Knowledge lifecycle section; the response carried no entry `id`, so a retrieved bad entry could never be reported; `conclave-web`'s public API reference never updated. |

⚠️ **Still unaudited:** this revision itself.

---

## Environment setup (read before Task 1)

Run tests with the repo venv (system Python lacks `asyncpg`):

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest
```

**Baseline before starting:** **503 passed** (conclave `master` at merge commit `cd76ab8`, 2026-08-01, after Phase 2.7a merged). Rev 1 said 480, which predated 2.7a. Record the real number you observe. If it differs, stop and report rather than proceeding.

**Conventions:**
- DB-touching test modules put `pytestmark = pytest.mark.usefixtures("clean_db")` at module level. Pure-logic tests (Task 2) need no DB and must not use it.
- Commits are **local only**. Do not push to Gitea — Justin confirms every push.
- Work on a branch, not `master`.

---

## 🚦 HARD PRECONDITION — read before writing any code

This plan **depends on Phase 2.7a**, because `training_corpus.invalidated_at` is created by its migration `019`. ✅ **2.7a is BUILT and MERGED to `master` as of 2026-08-01 (`cd76ab8`, 8 commits, suite 503 green)** — rev 1's statement that 2.7 had "no implementation plan and no code" is obsolete. The dependency is satisfied; Task 1 Step 5 verifies it in the branch rather than assuming it.

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
| `app/services/corpus_pipeline.py` — the `INSERT INTO training_corpus` inside `run_promote` | Normalize the embedding before INSERT. **Anchor on the INSERT statement, not a line number.** Rev 1 cited `:339-351`; the audit corrected that to `:369-371`; it is actually at **`:384`** on `cd76ab8` — the audit's own correction was already stale, because 2.7a's provenance block shifted it again. Three different numbers for one statement is the argument for not citing numbers at all. |
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

Expected: **503 passed** on `master` at or after merge commit `cd76ab8`. (Rev 1 said 480 — that predated 2.7a.) Record whatever you observe. Any failure means the tree was already red; stop and report.

- [ ] **Step 4: Create the branch**

```bash
cd /f/ObsidianAI/conclave && git checkout master && git checkout -b feat/public-knowledge-retrieval
```

- [ ] **Step 5: Assert 2.7a is in the branch you just created — not merely in the tree you checked from**

> 🔴 **Rev 2 — this step exists because rev 1 shipped without it and the omission was invisible.** Steps 1–3 verify 2.7a *in the working tree*. When 2.7a lived only on a feature branch, `git checkout master` silently discarded all of it, and the Task 5 router then queried `invalidated_at` — a column that branch never creates. **It passed locally anyway**: `conclave_test` is persistent and `tests/conftest.py` re-applies migrations without ever dropping, so the column lingered from the previous session and the test written to catch exactly this went green. Only a fresh database would have failed.
>
> 2.7a is now merged to `master` (`cd76ab8`), so the checkout above is correct today. **Verify it rather than trusting it** — this is cheap, and the failure mode is silent.

```bash
cd /f/ObsidianAI/conclave && git branch --show-current && \
  git show --stat HEAD --oneline -- migrations/019_knowledge_lifecycle.sql | head -3 && \
  git grep -c "invalidated_at IS NULL" HEAD -- app/routers/internal/corpus.py
```

Expected: branch `feat/public-knowledge-retrieval`; migration `019` reachable from `HEAD`; and **1** hit for the seed-path filter. A zero or an empty result means you branched off a commit without 2.7a — **STOP**, do not proceed to Task 2.

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

Expected: FAIL on the **magnitude assertion** — `AssertionError` from `math.isclose(5.0, 1.0)`, because the staged vector is not yet normalized. That is the correct red phase.

> 🟡 **Rev 2 — rev 1 predicted `AttributeError: module 'app.services.corpus_pipeline' has no attribute 'normalize_vector'`.** The test never monkeypatches `normalize_vector`, so no `AttributeError` occurs. An executor who sees the assertion failure instead of the promised error may conclude the test is wrong and start "fixing" a test that is behaving correctly.

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

> 🔴 **Ordering hazard — rev 1's reassurance here was false.** `vector_dot` is only correct if *every* stored vector is normalized, so this migration and the Task 3 ingest change must ship in the same deploy. Rev 1 then claimed *"the shipped topology is systemd with `workers=1` … so there is no window where old code writes un-normalized rows into a migrated table."* **`workers=1` prevents concurrent old/new processes. It does not order the migration against the restart.** Verified:
>
> - `deploy/conclave.service` has `ExecStart` only — **no `ExecStartPre`**, no migration hook.
> - `scripts/apply_migrations.py` records applied filenames in `schema_migrations` and skips them permanently, so **`020` runs exactly once, ever**.
> - `README.md` documents `apply_migrations.py` as a standalone manual step with no stated ordering relative to `systemctl restart`.
>
> Both orderings therefore have a hole, and neither raises:
> - **restart → migrate:** new code runs `vector_dot` against un-normalized rows. Every similarity is wrong by the row's magnitude.
> - **migrate → restart:** old code keeps writing un-normalized rows into a normalized table until the restart. Those rows are wrong **forever**, because 020 will never run again.

- [ ] **Close the ordering hole before shipping this migration**

Add to `deploy/conclave.service`, above `ExecStart`:

```ini
ExecStartPre=/opt/conclave/.venv/bin/python /opt/conclave/scripts/apply_migrations.py
```

and document the deploy order in `README.md` as **stop → migrate → start**. This makes the unit self-migrating, so the hazard cannot recur for any future migration either.

⚠️ If you would rather not change the deploy unit in this phase, take the spec §7 fallback instead: keep `vector_cosine` on the query path (correct for normalized *and* un-normalized input) and ship only the query-magnitude hoist. That forfeits most of the win — the audit measured **2.61×** on the scoring loop at 10k×768 — so prefer the `ExecStartPre`.

- [ ] **Step 1: Write the migration**

> 🟡 **Rev 2 — keep it ASCII.** `migrations/019_knowledge_lifecycle.sql:7-10` records why: `tests/conftest.py:61` and `scripts/apply_migrations.py:60` both call `read_text()` with **no `encoding=`**, so migration files decode with the locale codec (cp1252 on Windows). Inside `--` comments non-ASCII degrades to mojibake rather than raising — you can see it in `apply_migrations.py`'s own `done —` output — but a non-ASCII **string literal** would corrupt silently. Rev 1's comment block used em dashes; replace them with `-`.

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


async def test_missing_auth_header_is_422(client):
    """NOT 401/403. require_agent declares `authorization: Annotated[str, Header()]`
    with no default (app/auth.py:130), so FastAPI rejects the request as a missing
    required parameter BEFORE the dependency body runs. Mirrors
    tests/test_corpus_invalidation.py:172, which exists for this exact trap."""
    r = await client.get("/v1/knowledge?q=anything")
    assert r.status_code == 422


async def test_wrong_api_key_is_rejected(client):
    """The door that actually proves auth — rev 1 had no coverage for it."""
    r = await client.get(
        "/v1/knowledge?q=anything",
        headers={"Authorization": "Bearer not-a-real-key"},
    )
    assert r.status_code == 403


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


async def test_moderator_deleted_answer_is_not_re_served(
    client, db_pool, standard_agent, seed_agent, monkeypatch
):
    """A moderator removed this answer for cause. Nothing propagates that to
    training_corpus, so before rev 2 the public endpoint handed it back to the
    whole network — content the moderation path had already taken down."""
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

    # Moderation soft-deletes it (app/routers/v1/admin.py:76).
    await db_pool.execute("UPDATE answers SET deleted = TRUE WHERE id = $1", answer["id"])

    r = await client.get("/v1/knowledge?q=q", headers=auth)
    assert r.json()["count"] == 0


async def test_entry_with_null_provenance_is_still_served(
    client, db_pool, standard_agent, monkeypatch
):
    """The honest limit, pinned so nobody later mistakes it for a bug: entries
    promoted before 2.7a have NULL provenance permanently — there is no
    backfill — so the delete-join cannot check them and they remain retrievable.
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

# Ceiling on rows scanned per query.
#
# Rev 2 dropped this from 50_000. Embeddings are 768-dim DOUBLE PRECISION[]
# decoded into Python lists: ~25 KB per row, so 50k rows is ~1.27 GB of float
# objects and ~307 MB over the wire, per request, on a --workers 1 box that any
# `reader` agent may hit 60 times a minute. Rev 1 described that ceiling in the
# README as a safety property. It was the opposite.
#
# 5_000 rows is ~127 MB peak — survivable, and past that the honest move is to
# tell the caller the result set was truncated rather than silently return a
# worse match. If the corpus outgrows this, replace the cap with a server-side
# cursor and a running top-k heap (peak memory O(k), not O(n)); do not just
# raise the number.
_MAX_SCAN = 5_000


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
        """SELECT tc.id, tc.question_text, tc.answer_text, tc.category, tc.embedding
             FROM training_corpus tc
             LEFT JOIN answers a ON a.id = tc.source_answer_id
             LEFT JOIN posts   p ON p.id = tc.source_post_id
            WHERE tc.embedding IS NOT NULL
              AND tc.invalidated_at IS NULL
              AND (a.id IS NULL OR a.deleted = FALSE)
              AND (p.id IS NULL OR p.status <> 'deleted')
              AND ($1::text IS NULL OR tc.category = $1)
            ORDER BY tc.created_at DESC
            LIMIT $2""",
        category, _MAX_SCAN,
    )

    scored = [
        {
            # Return the entry id. Without it a caller who retrieves a wrong
            # answer has no handle on it and nothing to report — and adding it
            # later is an API contract change.
            "id": str(row["id"]),
            "question_text": row["question_text"],
            "answer_text": row["answer_text"],
            "category": row["category"],
            "similarity": vector_dot(query_vec, list(row["embedding"])),
        }
        for row in rows
    ]
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top_k = scored[:k]

    # Tell the caller when the scan hit the ceiling, rather than silently
    # returning a worse match than the corpus actually holds.
    return {"data": top_k, "count": len(top_k), "truncated": len(rows) >= _MAX_SCAN}
```

> 🟠 **Rev 2 — three changes to this query, all from the audit:**
> 1. **`ORDER BY tc.created_at DESC`.** Rev 1 had `LIMIT` with no ordering while the README claimed similarity is *"exact"*. Past the cap Postgres returns an arbitrary subset, so the best match could vanish with no error and no signal — the fail-in-the-safe-direction class again. Ordering makes truncation deterministic and recency-biased; `truncated` makes it visible.
> 2. **The two `LEFT JOIN`s are a privacy fix, not a tidy-up.** Corpus entries outlive their sources. Moderation soft-deletes (`app/routers/v1/admin.py:74-76` sets `posts.status='deleted'` / `answers.deleted=TRUE`) and **nothing propagates that to `training_corpus`**. While retrieval was seed-only that was contained; a public endpoint would re-serve moderator-removed content to every authenticated agent. Because the deletes are *soft*, the rows persist and a join can see them.
>    🔑 **Honest limits, both deliberate:** entries with NULL provenance — everything promoted before 2.7a, permanently, since there is no backfill — cannot be checked and are still served. And a post hard-deleted by `run_expiry` leaves no row, so `p.id IS NULL` lets its corpus entry through; that is correct, not a leak — the corpus entry is the knowledge the network chose to retain, and 2.7b exempts corpus-descended posts from expiry anyway.
> 3. **`id` in the response** — see the comment above.

- [ ] **Step 4: Register the router**

In `app/main.py`, add alongside the other v1 router imports (rev 1 said "line 28"; it is **line 29** on `master` at `cd76ab8`, and 2.7a added an import above it — match on the `waitlist` line, not a number):

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

- [ ] **Step 3: Confirm the invalidation filter is already here — do NOT add it**

> 🟠 **Rev 2 — rev 1 told you to add a filter that 2.7a already shipped.** `app/routers/internal/corpus.py:35` has carried `AND invalidated_at IS NULL` since commit `d9abed9`. Rev 1's literal find/replace will not match (which fails loudly, fine) — but its **commit message asserted the seed path "was missing" the filter**, which would have entered git history as a false claim. `tests/test_corpus_invalidation.py:26-53` already pins this behaviour; rev 1 never named those two tests.

```bash
cd /f/ObsidianAI/conclave && grep -n "invalidated_at IS NULL" app/routers/internal/corpus.py
```

Expected: exactly **1** hit. If zero, 2.7a is not in this branch — stop and re-check Task 1 Step 5.

- [ ] **Step 4: Run the full suite**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Record the count.

- [ ] **Step 5: Commit**

```bash
git add app/routers/internal/corpus.py
git commit -m "refactor: seed corpus search uses the dot-product fast path

Same normalized table, same maths as /v1/knowledge. The invalidated_at
filter was already added by Phase 2.7a (d9abed9) and is unchanged here."
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

In `README.md`, immediately after the **`### Knowledge lifecycle`** subsection added in Phase 2.7a (not after `### Notifications` — 2.7a inserted between them), add:

```markdown
### Knowledge retrieval

`GET /v1/knowledge?q=<text>&category=<optional>&k=<1..10>` searches what the
network has already learned. Any authenticated agent can call it — retrieval is
not restricted to seed agents.

Entries reach the corpus through the promotion pipeline, so the endpoint returns
nothing on a brand-new deployment and fills as answers are accepted.

**It needs Ollama**, for the same reason ingest does — see *Ingest requires
Ollama* above. Without `OLLAMA_BASE_URL` the endpoint returns
`{"data": [], "reason": "embeddings_unavailable"}` rather than failing, and the
preflight warns at boot.

**Scaling, stated honestly.** Similarity is computed in Python and is linear in
corpus size, so a query scans at most 5,000 live entries. Past that the response
carries `"truncated": true` and the result is the best match *among the newest
5,000* — not necessarily the best in the corpus. That is a real limit, not a
safety feature: raising the cap raises memory per request roughly 25 KB per
entry. Adopting pgvector would scale better but would require every self-hoster
to install a non-default Postgres extension, and `pgcrypto` is currently the
only one needed; pgvector is the intended escape hatch and the endpoint contract
does not change.

**Privacy.** Private posts never enter the corpus (ingest filters
`visibility = 'public'`), and answers a moderator has deleted are excluded from
results. Two limits worth knowing:

- With `CORPUS_ANONYMIZE=false` the corpus retains your team's real specifics —
  the point on a private network, but every authenticated agent can read them.
- Entries promoted **before** the provenance columns existed cannot be linked
  back to a source answer, so a later moderator deletion cannot exclude them.
  Use `POST /internal/admin/corpus/{id}/invalidate` to remove those by hand.
```

> 🟡 **Rev 2 — two fixes here.** The insertion point moved: rev 1 said *"immediately after `### Notifications`"*, but 2.7a added `### Knowledge lifecycle` directly below it, so that would split the knowledge docs in half. And the Ollama paragraph now cross-references rather than restating what `### Knowledge lifecycle` already says.
>
> The scaling paragraph was rewritten because rev 1 claimed similarity is *"exact"* **and** advertised a 50,000-row cap as protection. Both could not be true: past the cap an unordered `LIMIT` silently drops the best match, and 50k×768 floats is ~1.27 GB per request.

- [ ] **Step 2: Update the published API reference in `conclave-web`**

> 🟡 **Rev 2 — rev 1 never left this repo.** `conclave-web/src/content/docs/docs/api-reference.md` is the canonical published `/v1` surface (Rules / Agents / Posts / Answers / Clarifications / Votes / Network). This phase adds a public `/v1` endpoint; shipping it undocumented there means the published reference is wrong the day it lands.

Add a `Knowledge` section documenting `GET /v1/knowledge` — the query parameters, the `id`/`question_text`/`answer_text`/`category`/`similarity` response shape, `truncated`, and the `embeddings_unavailable` case.

⚠️ **While you are in that file:** it still documents `GET`/`PATCH /agents/me/notifications`, which **Phase 2.5 deleted** (migration `017` dropped the columns and the routes are gone). Verify and remove:

```bash
grep -n "agents/me/notifications" /f/ObsidianAI/conclave-web/src/content/docs/docs/api-reference.md
grep -rn "notifications" /f/ObsidianAI/conclave/app/routers/v1/agents.py || echo "  confirmed: no such route in the backend"
```

This is a separate commit in a separate repo — do not mix it with the `conclave` commit below.

- [ ] **Step 3: Run the full suite one final time**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Report the final count against the **503** baseline (rev 1 said 480; that predated 2.7a).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document /v1/knowledge, its Ollama dependency and its ceiling"
```

---

## Done criteria

- [ ] Suite green; final count recorded and reported against the **503** baseline
- [ ] `grep -rn "invalidated_at" app/routers/` returns hits in **both** `v1/knowledge.py` and `internal/corpus.py`
- [ ] Both **query** paths use `vector_dot`: `grep -rn "vector_dot" app/routers/` returns hits in `v1/knowledge.py` and `internal/corpus.py`
- [ ] `vector_cosine` **still survives in exactly three modules** — verify with `grep -rn "vector_cosine" app/`:
  - `services/embeddings.py:13` — the definition
  - `services/corpus_pipeline.py:206` — the dual-signal gate
  - `services/divergence.py:78` — seed draft divergence, **imported aliased as `_vector_cosine`** (`divergence.py:10`)

  > 🟠 **Rev 2 — rev 1 said "exactly two places" and named two. There are three.** `divergence.py` compares fresh seed-draft embeddings pairwise: **un-normalized ad-hoc vectors**, precisely the case this task's own 🛑 warning exists to protect. Because it is imported under an alias, it survives a naive grep-and-replace on `vector_cosine(` — and swapping it for `vector_dot` would silently change which seeds get flagged as divergent (the outlier threshold at `divergence.py:86`), with no error and no failing test. If any of the three hits is gone, someone "cleaned up" a correctness-critical call site.
- [ ] A non-seed agent can retrieve: `tests/test_knowledge_endpoint.py::test_a_non_seed_agent_can_retrieve` passes
- [ ] Nothing pushed to Gitea — Justin confirms every push

## Deliberately NOT in this plan

- **`accept` as a corpus ingest signal** — an amendment to the Phase 2.7 spec; it touches `corpus_pipeline` ingest and the same migration family. Without it this endpoint returns empty on a small team for a long time, but speccing it here would collide with 2.7.
- **The MCP surface** — a later phase, designed on top of this endpoint.
- **The team portal** — a later phase.
- **pgvector** — rejected in the spec, §5. Reversible later with no API change.
- ~~**Self-host defaults for `corpus_upvote_threshold` / `corpus_quarantine_days`**~~ — **partly delivered by 2.7a**: both now reject `0` at boot (`app/config.py`), though the values themselves are unchanged at 3 / 7.
