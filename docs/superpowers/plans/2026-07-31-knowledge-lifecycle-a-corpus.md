# Knowledge Lifecycle A — Corpus Lifecycle & Retrieval Integrity (Phase 2.7a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stored knowledge removable, traceable, and reachable — soft invalidation that actually filters retrieval, provenance carried through promotion, and an ingest valve a small team can reach.

**Architecture:** One migration adds invalidation and provenance columns plus the two flag tables (used by plan 2.7b). `run_promote` starts carrying provenance. The seed retrieval path gains the `invalidated_at IS NULL` filter that makes invalidation mean anything. `CORPUS_ANONYMIZE` becomes a setting, and `accept` joins the upvote threshold as a qualifying ingest signal.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, asyncpg (raw SQL, `$1` positional params), pytest + pytest-asyncio (auto mode — no `@pytest.mark.asyncio` needed).

**Spec:** `docs/superpowers/specs/2026-07-30-knowledge-lifecycle-design.md` (revision 2, plus the §3c amendment of 2026-07-31)

---

## Revision 2 — 2026-08-01, after a cold adversarial audit

Rev 1 was written, self-reviewed, and believed correct by one author. A fresh read-only agent with no session context audited it against the real code and returned **EXECUTE AFTER FIXES**. Every 🔴 below was independently re-verified against source before this revision was written.

| # | Sev | What was wrong in rev 1 |
|---|---|---|
| 1 | 🔴 | **Baseline was false.** 479 passed / 1 failed — `test_reset_track_a_writes_audit_log` rotted on 2026-08-01. Task 1 Step 1 says "stop if red", so an executor **halted on the first step**. Fixed on `master` in `a497cb2`. |
| 2 | 🔴 | **Task 6 Step 4 quoted source that does not exist.** Applying it verbatim raised `NameError` on every ingest tick under the *new default*, swallowed by a bare `except` — corpus never fills, worker looks healthy. Rewritten from `corpus_pipeline.py:262-264`. |
| 3 | 🔴 | **The `quality_score = 1.0` sentinel was dropped**, and it is `NOT NULL` on both tables. Folded into the Task 6 rewrite. |
| 4 | 🔴 | **The Ollama guard breaks 5 existing tests; the draft named 1.** Two of the five keep passing *for the wrong reason*, silently losing the private-post and threshold filter coverage. All five now named with the fix. |
| 5 | 🔴 | **Task 8's missing-key test asserted 401/403; it is 422.** `require_admin` has no header default, so FastAPI rejects before the dependency runs. Both doors now covered. |
| 6 | 🔴 | **Migration 019's provenance backfill recovered nothing and reported `UPDATE 1`.** `run_promote` nulls the staging FKs in the same transaction as the corpus INSERT, so no joinable row ever has provenance. Backfill deleted; the real limit documented. |
| 7 | 🟠 | **Task 4 Step 5 was built on a false premise** — the test it described asserts only on `corpus_staging` and passes unchanged. Following it literally deleted live GDPR regression coverage. Now additive. |
| 8 | 🟠 | **Task 6's only `CORPUS_ANONYMIZE` test was a tautology** — it never called `run_ingest` and passed against a no-op. Replaced with two tests that exercise both settings. |
| 9 | 🟠 | **`CORPUS_QUARANTINE_DAYS=0` / `CORPUS_UPVOTE_THRESHOLD=0` reopened the zero-value trap** for the third time in this project. Floors now enforced at boot, not in a comment. |
| 10 | 🟡 | Migration applied only to the test DB; purge tests checked neither cascade nor audit; no CHECK on `invalidated_by`; index comment overstated its own value; `answers.py` citations off by one **inside a committed SQL comment**; README migration range stale. All fixed. |

🔑 **The pattern across all three audits to date: the failures cluster in code quoted from memory rather than opened.** Every snippet in this revision that claims to show existing source was pasted from the file.

⚠️ **Still unaudited:** this revision itself. The changed sections have not been cold-read by anyone.

---

## Scope: this is plan A of two

**In this plan (2.7a):** spec §1, §2, §3c, §4 (corpus endpoints only), §5.

**In plan 2.7b, NOT here:** spec §3 (the two flag surfaces, threshold logic, propagation, `GET /internal/admin/flag-events`) and §3b (post expiry rework).

The migration here creates `answer_flags` and `corpus_flags` **and nothing reads or writes them until 2.7b.** That is deliberate: one migration for one phase, so 2.7b needs no schema change of its own and cannot collide on a migration number.

🔑 **Phase 2.8 unblocks at the end of this plan.** It needs `invalidated_at` (Task 2) and the retrieval filter (Task 5). It does not need 2.7b.

---

## Environment setup (read before Task 1)

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest
```

**Baseline:** **480 passed** (conclave `master` at commit `a497cb2`, re-verified 2026-08-01). Record what you actually observe; if it differs, stop and report.

> ⚠️ **Rev 2 — this baseline was briefly false and would have halted an executor at Task 1 Step 1.** On 2026-08-01 the suite reported **479 passed, 1 failed**: `tests/test_circuit_breaker.py::test_reset_track_a_writes_audit_log` queried `audit_log_2026_06` then `audit_log_2026_07` **by partition name**, and from 2026-08-01 the row lands in the DEFAULT partition that migration `016` added. The test rotted on a date, not on a code change. **Fixed in `a497cb2`** by reading through the partitioned parent — the same lesson `tests/conftest.py:75-77` already recorded when `016` landed, at the one call site that was missed. It was the last hardcoded partition reference in test code.

**Conventions:**
- DB-touching test modules put `pytestmark = pytest.mark.usefixtures("clean_db")` at module level.
- Commits are **local only**. Do not push to Gitea — Justin confirms every push.
- Work on a branch, not `master`.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `migrations/019_knowledge_lifecycle.sql` | Invalidation + provenance columns, both flag tables, index corrections, provenance backfill. |
| `app/routers/internal/admin_corpus.py` | Operator corpus surface: list, invalidate, restore, purge. |
| `tests/test_corpus_invalidation.py` | Invalidation excluded from retrieval; round-trip; purge. |
| `tests/test_corpus_provenance.py` | `run_promote` carries provenance; anonymize on/off. |
| `tests/test_corpus_accept_ingest.py` | §3c — accept qualifies an answer for staging. |

**Modified**

| File | Change |
|---|---|
| `app/config.py` | Add `corpus_anonymize`. |
| `app/services/corpus_pipeline.py` | Provenance in `run_promote`; `CORPUS_ANONYMIZE` in `run_ingest`; accept as a qualifying signal. |
| `app/routers/internal/corpus.py` | Add `AND invalidated_at IS NULL`. |
| `app/main.py` | Register the admin corpus router. |
| `tests/conftest.py` | Add `answer_flags`, `corpus_flags` to `_truncate_tables`. |
| `tests/test_corpus_pipeline.py` | Update `test_promote_nulls_fk_after_promotion` (asserts the OLD behaviour). |
| `.env.example`, `README.md` | Document `CORPUS_ANONYMIZE` and the corpus lifecycle. |

---

## Task 1: Baseline and branch

- [ ] **Step 1: Confirm the tree is green**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: **480 passed**. Any failure means the tree was already red — stop and report.

- [ ] **Step 2: Confirm Phase 2.5 landed (this plan builds on its conventions)**

```bash
cd /f/ObsidianAI/conclave && ls migrations/017_drop_notification_prefs.sql && grep -n "warn_self_host_posture" app/services/preflight.py
```

Expected: the file exists and the function is present. If not, Phase 2.5 has not merged — stop.

- [ ] **Step 3: Create the branch**

```bash
cd /f/ObsidianAI/conclave && git checkout master && git checkout -b feat/knowledge-lifecycle-corpus
```

---

## Task 2: Migration 019 — schema

**Files:**
- Create: `migrations/019_knowledge_lifecycle.sql`

> **Numbering:** `016` audit_log partitions (merged), `017` Phase 2.5 (merged), `018` Phase 2.6 (not yet built), `019` this plan, `020` Phase 2.8. Two files sharing a number both apply in alphabetical order **with no error** — collisions here are silent, so do not renumber.

- [ ] **Step 1: Write the migration**

Create `migrations/019_knowledge_lifecycle.sql`:

```sql
-- 019: Knowledge lifecycle — invalidation, provenance, and flag storage.
--
-- Before this, training_corpus had exactly one INSERT and no UPDATE or DELETE
-- anywhere: nothing could remove, correct, or invalidate knowledge once stored.
-- Everything built decided what got IN; nothing decided what came OUT.

-- ── Invalidation (soft removal) ──────────────────────────────────────────────
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS invalidated_at     TIMESTAMPTZ;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS invalidated_reason TEXT;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS invalidated_by     VARCHAR(20);

-- Spec §5 enumerates exactly three legal writers. corpus_staging sets the
-- precedent (migration 004:32-35 CHECKs both promotion_status and
-- critique_verdict). 2.7b writes 'flag_threshold' and 'propagation'; without a
-- constraint a typo there is silently stored and silently un-queryable.
-- DO block because ADD CONSTRAINT has no IF NOT EXISTS.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'training_corpus_invalidated_by_check'
    ) THEN
        ALTER TABLE training_corpus
            ADD CONSTRAINT training_corpus_invalidated_by_check
            CHECK (invalidated_by IS NULL
                   OR invalidated_by IN ('operator', 'flag_threshold', 'propagation'));
    END IF;
END $$;

-- ── Provenance ───────────────────────────────────────────────────────────────
-- Deliberately NO foreign keys. Posts expire; an FK would either block that
-- expiry or null the link out. A dangling UUID is an acceptable breadcrumb, and
-- with CORPUS_ANONYMIZE=false the entry holds the full original text anyway, so
-- the content survives regardless of whether the source row does.
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_post_id   UUID;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_answer_id UUID;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_agent_id  UUID;

-- NO BACKFILL. Provenance for pre-2.7a corpus entries is permanently
-- unrecoverable, and it is worth being exact about why.
--
-- run_promote INSERTs the training_corpus row and, in the SAME transaction, sets
-- corpus_staging.source_post_id = NULL and source_answer_id = NULL
-- (corpus_pipeline.py:358-359). A training_corpus row exists only because its
-- staging row was promoted. Therefore every staging row that could be joined has
-- already had its provenance nulled — for every corpus entry, always, regardless
-- of anonymization.
--
-- Consequences, stated rather than papered over:
--   * Pre-2.7a entries keep NULL provenance forever. The 2.7b flag-author guard
--     treats those as "all flags count".
--   * The spec §3b expiry exemption ("posts with a corpus descendant never
--     expire") does not protect the source posts of pre-2.7a entries.
-- Only entries promoted AFTER this migration carry provenance.

-- ── Flag storage (populated by plan 2.7b; created here so one phase = one migration) ──
CREATE TABLE IF NOT EXISTS answer_flags (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id  UUID NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    agent_id   UUID NOT NULL REFERENCES agents(id)  ON DELETE CASCADE,
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT answer_flags_one_per_agent UNIQUE (answer_id, agent_id)
);

-- corpus_flags.corpus_id CASCADEs so a purge takes its flags with it. The
-- "no foreign keys on training_corpus" rule concerns FKs pointing OUTWARD from
-- that table; inbound references are fine.
CREATE TABLE IF NOT EXISTS corpus_flags (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corpus_id  UUID NOT NULL REFERENCES training_corpus(id) ON DELETE CASCADE,
    agent_id   UUID NOT NULL REFERENCES agents(id)          ON DELETE CASCADE,
    reason     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT corpus_flags_one_per_agent UNIQUE (corpus_id, agent_id)
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
-- DROP first. CREATE INDEX IF NOT EXISTS with the same name silently keeps the
-- OLD predicate, so the migration reports success while changing nothing.
DROP INDEX IF EXISTS idx_training_corpus_finetune_eligible;
CREATE INDEX idx_training_corpus_finetune_eligible
    ON training_corpus (created_at)
    WHERE finetuned_at IS NULL AND rag_flag_count = 0 AND invalidated_at IS NULL;

-- Narrows the retrieval scan to live, embedded rows. Be honest about the size of
-- that win: corpus.py:31-37 issues an unbounded SELECT with no ORDER BY and no
-- LIMIT, then scores every row in Python, so the (category) key accelerates
-- nothing here — only the partial predicate does any work. Kept because 2.8 adds
-- a category-filtered public retrieval path that will use the key.
CREATE INDEX IF NOT EXISTS idx_training_corpus_active
    ON training_corpus (category)
    WHERE invalidated_at IS NULL AND embedding IS NOT NULL;
```

- [ ] **Step 2: Apply it and confirm the columns exist**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv; load_dotenv()
async def main():
    conn = await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
    await conn.execute(open('migrations/019_knowledge_lifecycle.sql', encoding='utf-8').read())
    cols = await conn.fetch(\"\"\"SELECT column_name FROM information_schema.columns
                                WHERE table_name='training_corpus'
                                  AND column_name IN ('invalidated_at','source_post_id','source_agent_id')\"\"\")
    print('new columns:', sorted(r['column_name'] for r in cols))
    idx = await conn.fetchval(\"\"\"SELECT indexdef FROM pg_indexes
                                  WHERE indexname='idx_training_corpus_finetune_eligible'\"\"\")
    print('predicate has invalidated_at:', 'invalidated_at' in (idx or ''))
    await conn.close()
asyncio.run(main())
"
```

Expected:
```
new columns: ['invalidated_at', 'source_agent_id', 'source_post_id']
predicate has invalidated_at: True
```

If `predicate has invalidated_at` is `False`, the `DROP INDEX` did not take effect — fix it before continuing. That is the exact silent failure the DROP exists to prevent.

Also confirm the CHECK constraint landed and actually rejects:

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -c "
import asyncio, asyncpg, os
from dotenv import load_dotenv; load_dotenv()
async def main():
    conn = await asyncpg.connect(os.environ['TEST_DATABASE_URL'])
    tr = conn.transaction(); await tr.start()
    await conn.execute(\"INSERT INTO training_corpus (question_text, answer_text, category, quality_score, source_provider_type, invalidated_by) VALUES ('q','a','coding',1.0,'seed','operator')\")
    print('legal value accepted: True')
    try:
        await conn.execute(\"INSERT INTO training_corpus (question_text, answer_text, category, quality_score, source_provider_type, invalidated_by) VALUES ('q','a','coding',1.0,'seed','typo')\")
        print('ILLEGAL VALUE ACCEPTED — constraint missing')
    except asyncpg.exceptions.CheckViolationError:
        print('illegal value rejected: True')
    await tr.rollback(); await conn.close()
asyncio.run(main())
"
```

- [ ] **Step 3: Apply to the dev database too**

> 🟡 **Rev 2 — the draft only ever touched `TEST_DATABASE_URL`.** `scripts/apply_migrations.py` exists (it tracks applied filenames in `schema_migrations` and wraps each file in a transaction, `:59-65`) and the draft never ran it. Anyone who starts the app locally against `DATABASE_URL` after Task 4 gets `column "source_post_id" of relation "training_corpus" does not exist` from `run_promote`.

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe scripts/apply_migrations.py
```

- [ ] **Step 4: Commit**

```bash
git add migrations/019_knowledge_lifecycle.sql
git commit -m "feat: migration 019 — corpus invalidation, provenance, flag tables

Adds soft-invalidation and provenance columns to training_corpus, creates
answer_flags and corpus_flags for plan 2.7b, and recreates the finetune-eligible
index with DROP first — CREATE INDEX IF NOT EXISTS silently keeps the old
predicate. Provenance is backfilled from corpus_staging where the text still
matches; anonymized rows cannot be matched and keep NULL provenance."
```

---

## Task 3: Keep the truncate list honest

**Files:**
- Modify: `tests/conftest.py`

> `_truncate_tables` is a hand-maintained list. Rows leaking between tests make threshold tests in 2.7b order-dependent — the exact class of bug that made this suite non-reproducible before.

- [ ] **Step 1: Add the two new tables**

In `tests/conftest.py`, the `TRUNCATE` statement currently begins:

```python
        """TRUNCATE seed_signals, seed_contributions, seed_drafts, seed_threads,
                       votes, clarifications, bans, agent_category_scores,
```

Change that opening to include both flag tables:

```python
        """TRUNCATE answer_flags, corpus_flags,
                       seed_signals, seed_contributions, seed_drafts, seed_threads,
                       votes, clarifications, bans, agent_category_scores,
```

- [ ] **Step 2: Verify the suite still passes**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: **480 passed**. A failure here means a table name is wrong — `TRUNCATE` on a missing table aborts the whole statement, so every DB test breaks at once.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: truncate answer_flags and corpus_flags between tests

Hand-maintained list; leaked flag rows would make 2.7b's distinct-agent
threshold tests order-dependent."
```

---

## Task 4: `run_promote` carries provenance

**Files:**
- Modify: `app/services/corpus_pipeline.py`
- Modify: `tests/test_corpus_pipeline.py`
- Test: `tests/test_corpus_provenance.py` (create)

> Spec §5: *"provenance must be added to both that SELECT and the INSERT, or the columns stay NULL and every feature built on them is inert."* Propagation in 2.7b depends entirely on this task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_provenance.py`:

```python
"""run_promote must carry provenance from staging into training_corpus."""
import pytest

from app.services import corpus_pipeline

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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_provenance.py -v
```

Expected: FAIL — `source_post_id` is `None` where the test expects the post's id.

- [ ] **Step 3: Select provenance in the candidate query**

In `app/services/corpus_pipeline.py`, `run_promote`'s candidate query currently reads:

```python
        """SELECT id, question_text, answer_text, category, quality_score,
                  source_provider_type, retry_count
           FROM corpus_staging
```

Change the select list to carry the source ids through:

```python
        """SELECT id, question_text, answer_text, category, quality_score,
                  source_provider_type, retry_count,
                  source_post_id, source_answer_id
           FROM corpus_staging
```

- [ ] **Step 4: Resolve the answering agent and write provenance**

Still in `run_promote`, the promote branch currently reads:

```python
                    await conn.execute(
                        """INSERT INTO training_corpus
                           (question_text, answer_text, embedding, category,
                            quality_score, source_provider_type)
                           VALUES ($1, $2, $3, $4, $5, $6)""",
                        question, answer, embedding,
                        row["category"], row["quality_score"],
                        row["source_provider_type"],
                    )
```

Replace it with:

```python
                    # The answering agent is the corpus entry's author. Resolved
                    # from the answer rather than stored on staging, and NULL
                    # when the answer row is gone — a missing link is a no-op.
                    source_agent_id = None
                    if row["source_answer_id"] is not None:
                        source_agent_id = await conn.fetchval(
                            "SELECT agent_id FROM answers WHERE id = $1",
                            row["source_answer_id"],
                        )

                    await conn.execute(
                        """INSERT INTO training_corpus
                           (question_text, answer_text, embedding, category,
                            quality_score, source_provider_type,
                            source_post_id, source_answer_id, source_agent_id)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                        question, answer, embedding,
                        row["category"], row["quality_score"],
                        row["source_provider_type"],
                        row["source_post_id"], row["source_answer_id"],
                        source_agent_id,
                    )
```

- [ ] **Step 5: EXTEND `test_promote_nulls_fk_after_promotion` — do not rewrite or rename it**

> 🟠 **Rev 2 — the draft was built on a false premise.** It said the test *"asserts that provenance is discarded at promotion"* and instructed: *"find the assertion that the promoted `training_corpus` row has no source link and replace it with the opposite expectation."* **There is no such assertion.** `tests/test_corpus_pipeline.py:390-395` asserts only on **`corpus_staging`**:
> ```python
> staging = await db_pool.fetchrow(
>     "SELECT source_post_id, source_answer_id FROM corpus_staging WHERE id = $1", entry_id)
> assert staging["source_post_id"] is None
> assert staging["source_answer_id"] is None
> ```
> This task does **not** change staging-nulling (`corpus_pipeline.py:358-359` is untouched), so **the test passes unchanged after Task 4.** Following the draft literally would have deleted live regression coverage of the GDPR staging behaviour and renamed the test to misdescribe what it still primarily checks. Spec §Testing repeats the same false claim — strike it there too.

**Append** the new assertions; leave the existing two and the test name alone:

```python
    # 2.7a additionally carries provenance FORWARD to training_corpus, because
    # invalidation-by-propagation needs something to join on. The staging row's
    # own nulling above is unchanged and still covered.
    promoted = await db_pool.fetchrow(
        "SELECT source_post_id, source_answer_id FROM training_corpus LIMIT 1"
    )
    assert promoted["source_post_id"] is not None
    assert promoted["source_answer_id"] is not None
```

The name `test_promote_nulls_fk_after_promotion` stays accurate — staging FKs are still nulled. If the mixed concern bothers you, put the `training_corpus` half in `tests/test_corpus_provenance.py` instead and leave this test completely untouched.

- [ ] **Step 6: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_provenance.py tests/test_corpus_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/corpus_pipeline.py tests/test_corpus_provenance.py tests/test_corpus_pipeline.py
git commit -m "feat: run_promote carries provenance into training_corpus

source_post_id, source_answer_id and the resolved source_agent_id now survive
promotion. Without this the columns stay NULL and everything built on them —
propagation, the flag-author guard, the expiry exemption — is inert.

test_promote_nulls_fk_after_promotion asserted the old discard behaviour and is
updated and renamed rather than deleted."
```

---

## Task 5: The retrieval filter — the change that makes invalidation mean anything

**Files:**
- Modify: `app/routers/internal/corpus.py`
- Test: `tests/test_corpus_invalidation.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_invalidation.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_invalidation.py -v
```

Expected: FAIL — `"bad answer"` is returned, because nothing filters it yet.

- [ ] **Step 3: Add the filter**

In `app/routers/internal/corpus.py`, change the query's `WHERE` clause from:

```python
            WHERE embedding IS NOT NULL
              AND ($1::text IS NULL OR category = $1)""",
```

to:

```python
            WHERE embedding IS NOT NULL
              AND invalidated_at IS NULL
              AND ($1::text IS NULL OR category = $1)""",
```

- [ ] **Step 4: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_invalidation.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/internal/corpus.py tests/test_corpus_invalidation.py
git commit -m "feat: seed retrieval excludes invalidated corpus entries

The load-bearing change. Without this filter, invalidation sets a column and
changes nothing observable — a wrong entry keeps grounding seed answers forever.

Phase 2.8's public /v1/knowledge endpoint carries the identical requirement."
```

---

## Task 6: `CORPUS_ANONYMIZE`

**Files:**
- Modify: `app/config.py`, `app/services/corpus_pipeline.py`
- Test: append to `tests/test_corpus_provenance.py`

> Spec §1: anonymization was built for a public multi-tenant fine-tuning corpus. On a private team network it genericizes the team's own specifics into uselessness and severs provenance. Default **off**; `true` retains the old posture for the local-distillation idea.

- [ ] **Step 1: Add the setting**

In `app/config.py`, directly beneath `corpus_promote_interval` (currently line 14), add:

```python
    # Anonymization was built for a PUBLIC multi-tenant fine-tuning corpus. On a
    # private team network it replaces "our payment system" with "a payment
    # processing system" — deleting exactly the specifics that made the entry
    # worth keeping — and it is the same pass that severs provenance.
    # Set true to retain the GDPR-exempt posture for local distillation.
    corpus_anonymize: bool = False
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_corpus_provenance.py`:

> ⚠️ **Rev 2 — the draft's first test here was a tautology.** It set `called = False`, monkeypatched the anonymizer, then asserted `called is False` **without ever calling `run_ingest`**. It passed against any implementation, including a no-op, and it monkeypatched the function to return a *tuple* where the real one returns an `AnonymizationResult`. Both tests below actually invoke `run_ingest`.
>
> 🔑 **Both must set `ollama_base_url`.** It is `''` in the test process (verified: `python -c "from app.config import settings; print(repr(settings.ollama_base_url))"` → `''`), so the Step 5 guard would otherwise return 0 and every assertion below would pass or fail for the wrong reason.

```python
async def test_ingest_keeps_raw_text_when_anonymize_disabled(
    db_pool, seed_agent, test_post, monkeypatch
):
    """CORPUS_ANONYMIZE=false stages the team's real text and never calls the
    anonymizer at all. Also pins the quality_score sentinel — the column is
    NOT NULL (migrations/004_corpus_pipeline.sql:19) and with anonymization off
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
    """Regression guard for the spec's §1 finding: with anonymization off but no
    Ollama, ingest must still skip. run_promote needs Ollama for BOTH signals, so
    staging would mark answers consumed, hold them, then permanently reject them
    — unrecoverable even after Ollama is installed later."""
    from app.config import settings
    monkeypatch.setattr(settings, "corpus_anonymize", False)
    monkeypatch.setattr(settings, "ollama_base_url", "")

    class _Boom:
        async def fetch(self, *a, **kw):
            raise AssertionError("run_ingest must not query when Ollama is absent")

    assert await corpus_pipeline.run_ingest(_Boom()) == 0
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_provenance.py -v
```

Expected: the Ollama-absent test FAILS if `run_ingest` does not short-circuit, or the setting test fails with `AttributeError` on `corpus_anonymize`.

- [ ] **Step 4: Gate the anonymization call**

> 🔴 **Rev 2 — the draft quoted source that does not exist.** It claimed the call was `anonymized = await anonymize_qa_pair(...)` / `question, answer = anonymized`. It is not, and there is no local `answer` variable. **Applying the draft's replacement produced `NameError: name 'result' is not defined` on every ingest tick whenever `corpus_anonymize` is False — the new default** — swallowed by `_ingest_worker`'s bare `except Exception` (`corpus_pipeline.py:436-437`) into a log line. The corpus would never fill and the worker would look healthy. Read the real block before editing it.

The real code, **`app/services/corpus_pipeline.py:262-264`**:

```python
        result = await anonymize_qa_pair(question, row["answer_body"])
        if result is None:
            continue
```

`anonymize_qa_pair` returns an **`AnonymizationResult` dataclass** (`corpus_pipeline.py:87-91`), not a tuple. `result` is then consumed four times in the `corpus_staging` INSERT and the log line — **`corpus_pipeline.py:283, :284, :286, :297`** (`result.question_text`, `result.answer_text`, `result.quality_score`, and `result.quality_score` again in `logger.info`).

Replace the block with:

```python
        # Anonymization is opt-in. With it off, the entry keeps the team's real
        # specifics — which is the entire point on a private network. quality_score
        # is NOT NULL on both corpus_staging and training_corpus, so the un-anonymized
        # path needs the 1.0 sentinel (spec §1); there is no AnonymizationResult to
        # take a score from.
        question_text, answer_text, quality = question, row["answer_body"], 1.0
        if settings.corpus_anonymize:
            result = await anonymize_qa_pair(question, row["answer_body"])
            if result is None:
                continue
            question_text = result.question_text
            answer_text = result.answer_text
            quality = result.quality_score
```

Then update the four consumers so nothing still reads `result`:

- `:283` `result.question_text` → `question_text`
- `:284` `result.answer_text` → `answer_text`
- `:286` `result.quality_score` → `quality`
- `:297` `result.quality_score` → `quality` (inside `logger.info`)

Confirm none survive:

```bash
cd /f/ObsidianAI/conclave && grep -n "result\." app/services/corpus_pipeline.py
```

Expected: **no hits inside `run_ingest`.** A surviving `result.` is the `NameError` above, and it will not show up until an ingest tick runs with the setting off.

- [ ] **Step 5: Add the Ollama short-circuit — it does NOT exist yet**

> 🔴 **Rev 2 — the draft's detection command found the wrong thing.** `grep -n -B2 -A6 "ollama_base_url" app/services/corpus_pipeline.py` matches **`corpus_pipeline.py:148`, inside `_ollama_chat`** — not `run_ingest`. An implementer skimming that output sees a hit and concludes the guard already exists. It does not.

Scope the grep to the function that matters:

```bash
cd /f/ObsidianAI/conclave && grep -n -A12 "^async def run_ingest" app/services/corpus_pipeline.py
```

**Expected: no `ollama_base_url` in the output.** The guard is genuinely required now — today `run_ingest` skips without Ollama only *implicitly*, because `anonymize_qa_pair` → `_ollama_chat` returns `None` and the loop `continue`s. Step 4 removes that call on the default path, so the implicit skip disappears and the "burns answers permanently" scenario in spec §1 becomes live. Add at the top of `run_ingest`:

```python
    # run_promote needs Ollama for BOTH gate signals. Staging without it marks
    # answers consumed via corpus_submitted_at, holds them, then permanently
    # rejects them — and they can never be re-ingested, even after Ollama is
    # installed. Skipping loses nothing.
    if not settings.ollama_base_url:
        return 0
```

- [ ] **Step 6: Fix the five existing ingest tests this breaks**

> 🔴 **Rev 2 — the draft named one test and there are five.** It said *"Expected: PASS, including the pre-existing `test_ingest_skips_when_ollama_unavailable`"* — the one ingest test that survives untouched. `settings.ollama_base_url` is `''` in the test process, so the new guard makes `run_ingest` return 0 unconditionally under pytest.

**Three break loudly** — each needs `monkeypatch.setattr(settings, "ollama_base_url", "http://fake")`, and because they assert on anonymized output, also `monkeypatch.setattr(settings, "corpus_anonymize", True)`:

| Test | Line | Breaks on |
|---|---|---|
| `test_ingest_stages_eligible_answer` | `tests/test_corpus_pipeline.py:110` | `assert count == 1` |
| `test_ingest_marks_answer_submitted` | `:136` | `corpus_submitted_at is not None` |
| `test_ingest_idempotent` | `:154` | `assert total == 1` |

⚠️ `test_ingest_stages_eligible_answer:129-130` breaks on the **anonymize gate alone**, guard or no guard: it asserts `row["question_text"] == mock_result.question_text`, and with `corpus_anonymize` off the mock is never consulted.

**Two break silently — the dangerous pair.** Both assert `count == 0` and will keep passing *for the wrong reason*, losing all coverage while the suite stays green:

| Test | Line | Coverage silently lost |
|---|---|---|
| `test_ingest_skips_private_posts` | `:171` | the private-post filter |
| `test_ingest_skips_below_threshold` | `:189` | the upvote-threshold filter |

Give both `monkeypatch.setattr(settings, "ollama_base_url", "http://fake")` and leave `corpus_anonymize` at its new `False` default — then no Ollama call happens at all and the *filter* is what makes the count 0, which is what the tests are for.

- [ ] **Step 7: Run the full suite**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: green, with **three more tests than the Task 5 total** (Step 2 adds `test_ingest_keeps_raw_text_when_anonymize_disabled`, `test_ingest_anonymizes_when_enabled`, and `test_ingest_still_skips_entirely_without_ollama`). **Record the number you actually observe** — do not carry a predicted count forward. The `master` baseline is **480 passed** as of commit `a497cb2`.

- [ ] **Step 8: Commit**

```bash
git add app/config.py app/services/corpus_pipeline.py tests/test_corpus_provenance.py tests/test_corpus_pipeline.py
git commit -m "feat: CORPUS_ANONYMIZE, default off

Anonymization was built for a public multi-tenant fine-tuning corpus. On a
private team network it genericizes the team's own specifics into uselessness
and severs provenance. Off by default; true retains the old posture.

Ingest still skips entirely without Ollama under both settings — promotion needs
it for both gate signals, and staging without it burns answers permanently."
```

---

## Task 7: Accept as the primary ingest valve (spec §3c)

**Files:**
- Modify: `app/services/corpus_pipeline.py`
- Test: `tests/test_corpus_accept_ingest.py` (create)

> **No migration.** `answers.human_accepted` exists since migration `000`; `human_accepted_note` / `human_accepted_at` since `002`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_corpus_accept_ingest.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_accept_ingest.py -v
```

Expected: `test_accepted_answer_with_no_upvotes_is_staged` FAILS (staged == 0), because only the upvote threshold qualifies today.

- [ ] **Step 3: Add accept to the qualifying condition**

In `app/services/corpus_pipeline.py`'s `run_ingest`, the candidate query currently filters on the upvote threshold:

```python
           WHERE a.upvote_count >= $1
```

Change it to accept either signal:

```python
           -- Accept is the primary valve on a small team: 3 DISTINCT upvotes is
           -- effectively unreachable with four agents, so the corpus would stay
           -- empty forever. Safe by construction rather than by policy — an
           -- agent cannot answer its own post ("Cannot answer your own post")
           -- and only the asker may accept ("Only the post author can accept an
           -- answer"), so an accepted answer always involves two distinct
           -- agents. Quoting the guard strings, not line numbers: the draft's
           -- answers.py:57/:197 were already off by one (:58/:198) before this
           -- comment was even committed.
           WHERE (a.upvote_count >= $1 OR a.human_accepted = TRUE)
```

Leave every other condition — `a.flagged = FALSE`, `p.visibility = 'public'`, the `corpus_submitted_at` guard — exactly as they are.

- [ ] **Step 4: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_accept_ingest.py -v
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/corpus_pipeline.py tests/test_corpus_accept_ingest.py
git commit -m "feat: accept qualifies an answer for corpus staging (spec 3c)

run_ingest staged only at 3 distinct upvotes, unreachable on a four-agent team,
so training_corpus stayed empty and Phase 2.8's retrieval would return nothing
forever. answers.human_accepted has existed since migration 000 and was never
read.

Two-party by construction: an agent cannot answer its own post and only the post
author may accept. Accept does not bypass the flag guard or quarantine."
```

---

## Task 8: Operator corpus surface

**Files:**
- Create: `app/routers/internal/admin_corpus.py`
- Modify: `app/main.py`
- Test: append to `tests/test_corpus_invalidation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_corpus_invalidation.py`:

```python
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
    a bad prefix and a bad key (app/auth.py:175, :179)."""
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
    declares `authorization: Annotated[str, Header()]` with no default
    (app/auth.py:172-174), so FastAPI rejects the request as a missing required
    parameter BEFORE the dependency body runs."""
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)
    r = await client.post(f"/internal/admin/corpus/{cid}/invalidate", json={"reason": "x"})
    assert r.status_code == 422
```

> 🔴 **Rev 2 — the draft asserted `in (401, 403)` for the missing-header case and would have failed.** Verified by executing the plan's own router shape against the real `require_admin`: no-auth → **422**, correct auth → 200. The existing convention at `tests/test_beta_accounts.py:109-111` sends a *wrong* key precisely because of this. Both doors are now covered.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_invalidation.py -v
```

Expected: FAIL — 404s, the routes do not exist.

- [ ] **Step 3: Write the router**

Create `app/routers/internal/admin_corpus.py`:

```python
"""Operator surface for the training corpus.

Invalidation is soft and reversible; purge is not. Restore is POST /restore
rather than DELETE /invalidate deliberately — two DELETEs on paths differing
only by a suffix, one restorative and one destructive, is a trap.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_admin
from app.database import get_pool
from app.services.audit import log_admin_action

router = APIRouter(prefix="/internal/admin/corpus", tags=["internal-admin"])


class InvalidateRequest(BaseModel):
    reason: str


class PurgeRequest(BaseModel):
    confirm: bool = False


@router.get("")
async def list_corpus(
    category: Optional[str] = None,
    invalidated: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    rows = await pool.fetch(
        """SELECT id, question_text, answer_text, category, quality_score,
                  rag_flag_count, invalidated_at, invalidated_reason,
                  invalidated_by, source_post_id, source_answer_id,
                  source_agent_id, created_at
             FROM training_corpus
            WHERE ($1::text IS NULL OR category = $1)
              AND ($2::bool IS NULL
                   OR ($2 IS TRUE AND invalidated_at IS NOT NULL)
                   OR ($2 IS FALSE AND invalidated_at IS NULL))
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4""",
        category, invalidated, limit, offset,
    )
    return {"data": [dict(r) for r in rows], "count": len(rows)}


@router.post("/{corpus_id}/invalidate")
async def invalidate_entry(
    corpus_id: UUID,
    body: InvalidateRequest,
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    updated = await pool.fetchval(
        """UPDATE training_corpus
              SET invalidated_at = NOW(),
                  invalidated_reason = $2,
                  invalidated_by = 'operator'
            WHERE id = $1 AND invalidated_at IS NULL
            RETURNING id""",
        corpus_id, body.reason,
    )
    if updated is None:
        raise HTTPException(404, "Corpus entry not found or already invalidated")
    await log_admin_action(
        pool, "admin_corpus_invalidate",
        metadata={"corpus_id": str(corpus_id), "reason": body.reason},
    )
    return {"id": str(corpus_id), "invalidated": True, "reason": body.reason}


@router.post("/{corpus_id}/restore")
async def restore_entry(
    corpus_id: UUID,
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    updated = await pool.fetchval(
        """UPDATE training_corpus
              SET invalidated_at = NULL,
                  invalidated_reason = NULL,
                  invalidated_by = NULL
            WHERE id = $1 AND invalidated_at IS NOT NULL
            RETURNING id""",
        corpus_id,
    )
    if updated is None:
        raise HTTPException(404, "Corpus entry not found or not invalidated")
    await log_admin_action(
        pool, "admin_corpus_restore", metadata={"corpus_id": str(corpus_id)},
    )
    return {"id": str(corpus_id), "invalidated": False}


@router.delete("/{corpus_id}")
async def purge_entry(
    corpus_id: UUID,
    body: PurgeRequest = Body(default_factory=PurgeRequest),
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Delete the row outright. For content that must genuinely not persist —
    a credential or hostname that survived anonymization. 'Excluded from
    retrieval' is not 'gone' while the row is still readable in Postgres."""
    if not body.confirm:
        raise HTTPException(400, "Purge is irreversible — resend with {\"confirm\": true}")
    deleted = await pool.fetchval(
        "DELETE FROM training_corpus WHERE id = $1 RETURNING id", corpus_id
    )
    if deleted is None:
        raise HTTPException(404, "Corpus entry not found")
    await log_admin_action(
        pool, "admin_corpus_purge", metadata={"corpus_id": str(corpus_id)},
    )
    return {"id": str(corpus_id), "purged": True}
```

- [ ] **Step 4: Confirm the shared signatures (verified 2026-07-31 — re-check, do not assume)**

```bash
cd /f/ObsidianAI/conclave && grep -n "def require_admin" -A4 app/auth.py && grep -n "async def log_admin_action" -A6 app/services/audit.py
```

Expected, as of 2026-07-31:

- `require_admin(authorization: Annotated[str, Header()]) -> None` — it reads
  **`Authorization: Admin <key>`**, returns `None`, and takes no pool. There is
  no `X-Admin-Key` header anywhere in this codebase.
- `log_admin_action(pool, action, *, agent_id=None, metadata=None)` — matches
  the calls above.

If either differs, **match the existing convention** (see
`app/routers/internal/admin_beta_users.py`) rather than changing the shared
function.

- [ ] **Step 5: Register the router**

In `app/main.py`, alongside the other internal admin imports:

```python
from app.routers.internal.admin_corpus import router as admin_corpus_router
```

and with the other includes:

```python
app.include_router(admin_corpus_router)
```

- [ ] **Step 6: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_invalidation.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/internal/admin_corpus.py app/main.py tests/test_corpus_invalidation.py
git commit -m "feat: operator corpus surface — list, invalidate, restore, purge

Restore is POST /restore, not DELETE /invalidate: two DELETEs differing only by
a suffix, one restorative and one destructive, is a trap. Purge requires
explicit confirmation and is audit-logged."
```

---

## Task 9: Documentation

**Files:**
- Modify: `.env.example`, `README.md`

- [ ] **Step 1: Add the setting to `.env.example`**

Beneath the Moderation block written in Phase 2.5, add:

```bash
# ── Knowledge corpus ────────────────────────────────────────────────────
# Anonymization rewrites "our payment system" as "a payment processing
# system" and strips internal names. That was built for a PUBLIC shared
# corpus; on a private team network it deletes the specifics that made the
# entry useful, and severs provenance. Leave false unless you plan to
# distil the corpus into a local model.
CORPUS_ANONYMIZE=false
# An answer qualifies for the corpus at this many upvotes OR when the asker
# accepts it. Accept is the reachable valve on a small team.
# Minimum 1. At 0 every answer on the network qualifies immediately.
CORPUS_UPVOTE_THRESHOLD=3
# Days a qualifying answer waits before the correctness gate runs. Lower it
# if you want retrieval to start returning results sooner.
# Minimum 1. At 0 promote_after is already in the past when it is written,
# so the quarantine is skipped entirely.
CORPUS_QUARANTINE_DAYS=7
```

- [ ] **Step 1b: Enforce the floors at boot — do not rely on the comment**

> 🟠 **Rev 2 — the draft reopened the zero-value trap for the third time in this project.** Spec §3b rejects `0` at parse time for `POST_EXPIRY_TTL_DAYS` for exactly this reason, and rev 2 of that spec records rev 1 closing the trap on one setting and reopening it on the next. The draft then invited the operator to *"lower it"* with no floor stated. Both settings are bare ints today with no validator (`app/config.py:11-12`).
>
> At `CORPUS_QUARANTINE_DAYS=0`: `promote_after = now + timedelta(days=0)` (`corpus_pipeline.py:271`) is instantly `<= NOW()` at `corpus_pipeline.py:317`, so **the correctness quarantine is bypassed with no error.** At `CORPUS_UPVOTE_THRESHOLD=0`: `a.upvote_count >= 0` (`corpus_pipeline.py:248`) is true for **every answer on the network**.

In `app/config.py`, add a validator beside the two fields:

```python
    @field_validator("corpus_quarantine_days", "corpus_upvote_threshold")
    @classmethod
    def _reject_zero(cls, v: int, info) -> int:
        # 0 reads as "disabled" to a human and means something destructive to the
        # code: it bypasses the quarantine / qualifies every answer. Fail at boot
        # rather than silently degrade. Same class of trap as POST_EXPIRY_TTL_DAYS.
        if v < 1:
            raise ValueError(f"{info.field_name} must be >= 1 (got {v})")
        return v
```

Add a test in `tests/test_corpus_provenance.py` that constructs `Settings(corpus_quarantine_days=0)` and asserts it raises. ⚠️ **Assert on the raised error, never on `settings.<attr>` directly** — pytest's assertion rewriting prints the whole `Settings` repr, API key and DB password included, into the output. That already forced a key rotation on 2026-07-31.

- [ ] **Step 2: Document the lifecycle in `README.md`**

After the `### Knowledge retrieval` section (added by Phase 2.8) — or after `### Notifications` if 2.8 has not landed yet — add:

```markdown
### Knowledge lifecycle

An answer enters the corpus when it is **accepted by the asker** or reaches
`CORPUS_UPVOTE_THRESHOLD` upvotes, then waits `CORPUS_QUARANTINE_DAYS` before a
two-signal correctness gate decides whether to promote it.

**Expect an empty corpus at first.** With the default 7-day quarantine, nothing
is retrievable for about a week after a fresh install even on an active team.
That is the quarantine working, not a fault — lower `CORPUS_QUARANTINE_DAYS` if
you want results sooner.

Accept is safe by construction rather than by policy: an agent cannot answer its
own post, and only the post author can accept, so an accepted answer always
involves two distinct agents. Note that an operator running two agent identities
can still inject an entry — on a self-hosted network that operator owns the
database anyway, so treat accept as a mechanism for *achievability*, not trust.

**Removing knowledge.** Operators can invalidate an entry (soft, reversible, and
immediately excluded from retrieval) or purge it (irreversible, for content that
must genuinely not persist — a credential or hostname that survived
anonymization). Invalidation is the default; "excluded from retrieval" is not the
same as "gone" while the row is still readable in Postgres.
```

- [ ] **Step 3: Run the full suite one final time**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Report the final count against the 480 baseline.

- [ ] **Step 4: Confirm every `.env.example` key is a real setting**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -c "
from app.config import Settings
import re, pathlib
declared=set(Settings.model_fields)
keys={m.group(1).lower() for m in re.finditer(r'^([A-Z][A-Z0-9_]*)=', pathlib.Path('.env.example').read_text(encoding='utf-8'), re.M)}
print('unknown keys:', sorted(keys-declared) or 'none')
"
```

Expected: `unknown keys: none`. `Settings` is `extra='forbid'` and reads `.env`, so a stale key in the public template is a hard boot failure for whoever copies it.

- [ ] **Step 5: Fix the stale migration range in `README.md` while you are in there**

> 🟡 **Rev 2 — the draft edits `README.md` and walks straight past a line it makes worse.** `README.md:23` reads `# 2. Test database (fixtures apply migrations 000→015 themselves)`. That was already wrong before this plan (`016` and `017` are merged), and `019` makes it wronger.

Replace the hardcoded range with something that cannot rot:

```markdown
# 2. Test database (fixtures apply every migration in migrations/ themselves)
```

```bash
cd /f/ObsidianAI/conclave && grep -n "migrations 000" README.md
```

Expected: no hits.

- [ ] **Step 6: Commit**

```bash
git add .env.example README.md
git commit -m "docs: corpus lifecycle — anonymization, the accept valve, removal"
```

---

## Done criteria

- [ ] Suite green; final count recorded against the 480 baseline
- [ ] `grep -n "invalidated_at IS NULL" app/routers/internal/corpus.py` returns a hit — without it invalidation is inert
- [ ] `grep -n "human_accepted" app/services/corpus_pipeline.py` returns a hit — the §3c valve
- [ ] A promoted entry has non-NULL `source_post_id`, `source_answer_id`, `source_agent_id`
- [ ] `answer_flags` and `corpus_flags` exist and are in `conftest.py::_truncate_tables`, though **nothing reads them until plan 2.7b**
- [ ] Nothing pushed to Gitea — Justin confirms every push

## Deliberately NOT in this plan

- **The two flag surfaces, threshold logic, propagation, `GET /internal/admin/flag-events`** — plan 2.7b. Their tables ship here so 2.7b needs no migration.
- **Post expiry rework (spec §3b)** — plan 2.7b. `run_expiry` keeps its current hourly hard-delete behaviour until then.
- **Dashboard corpus browser** — Phase 3.5.
- **Reworking the promotion gate** — the dual-signal gate stays exactly as built.
- **Re-defaulting `corpus_upvote_threshold` / `corpus_quarantine_days`** — deliberate call, 2026-07-31. Accept-ingest solves achievability without weakening either guard.
