# Knowledge Lifecycle A — Corpus Lifecycle & Retrieval Integrity (Phase 2.7a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stored knowledge removable, traceable, and reachable — soft invalidation that actually filters retrieval, provenance carried through promotion, and an ingest valve a small team can reach.

**Architecture:** One migration adds invalidation and provenance columns plus the two flag tables (used by plan 2.7b). `run_promote` starts carrying provenance. The seed retrieval path gains the `invalidated_at IS NULL` filter that makes invalidation mean anything. `CORPUS_ANONYMIZE` becomes a setting, and `accept` joins the upvote threshold as a qualifying ingest signal.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, asyncpg (raw SQL, `$1` positional params), pytest + pytest-asyncio (auto mode — no `@pytest.mark.asyncio` needed).

**Spec:** `docs/superpowers/specs/2026-07-30-knowledge-lifecycle-design.md` (revision 2, plus the §3c amendment of 2026-07-31)

---

## Scope: this is plan A of two

**In this plan (2.7a):** spec §1, §2, §3c, §4 (corpus endpoints only), §5.

**In plan 2.7b, NOT here:** spec §3 (the two flag surfaces, threshold logic, propagation, `GET /internal/admin/flags`) and §3b (post expiry rework).

The migration here creates `answer_flags` and `corpus_flags` **and nothing reads or writes them until 2.7b.** That is deliberate: one migration for one phase, so 2.7b needs no schema change of its own and cannot collide on a migration number.

🔑 **Phase 2.8 unblocks at the end of this plan.** It needs `invalidated_at` (Task 2) and the retrieval filter (Task 5). It does not need 2.7b.

---

## Environment setup (read before Task 1)

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest
```

**Baseline:** **480 passed** (conclave `master`, 2026-07-31, Phase 2.5 merged). Record what you actually observe; if it differs, stop and report.

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

-- ── Provenance ───────────────────────────────────────────────────────────────
-- Deliberately NO foreign keys. Posts expire; an FK would either block that
-- expiry or null the link out. A dangling UUID is an acceptable breadcrumb, and
-- with CORPUS_ANONYMIZE=false the entry holds the full original text anyway, so
-- the content survives regardless of whether the source row does.
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_post_id   UUID;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_answer_id UUID;
ALTER TABLE training_corpus ADD COLUMN IF NOT EXISTS source_agent_id  UUID;

-- Backfill provenance from corpus_staging where its own FKs are still intact.
--
-- HONEST LIMIT: the only available join is on the text itself. When an entry was
-- ingested with anonymization ON, training_corpus.question_text differs from the
-- staging text and the join simply will not match. Those rows keep NULL
-- provenance, which the flag-author guard in 2.7b treats as "all flags count".
-- That is stated in the docs rather than papered over.
UPDATE training_corpus tc
SET source_post_id   = cs.source_post_id,
    source_answer_id = cs.source_answer_id
FROM corpus_staging cs
WHERE tc.source_post_id IS NULL
  AND tc.question_text = cs.question_text
  AND tc.answer_text   = cs.answer_text;

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

-- Supports the retrieval path's invalidated_at IS NULL filter.
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

- [ ] **Step 3: Commit**

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

- [ ] **Step 5: Update the test that asserts the OLD behaviour**

`tests/test_corpus_pipeline.py::test_promote_nulls_fk_after_promotion` asserts that provenance is discarded at promotion. That was the behaviour this task deliberately reverses. **Update it, do not delete it** — it still has a job: proving the *staging* row's FKs behave as expected after promotion.

Find the assertion that the promoted `training_corpus` row has no source link and replace it with the opposite expectation:

```python
    # 2.7a reverses this: provenance is now CARRIED to training_corpus, because
    # invalidation-by-propagation needs something to join on. The staging row's
    # own lifecycle is unchanged.
    promoted = await db_pool.fetchrow(
        "SELECT source_post_id, source_answer_id FROM training_corpus LIMIT 1"
    )
    assert promoted["source_post_id"] is not None
    assert promoted["source_answer_id"] is not None
```

Rename the test to `test_promote_carries_fk_after_promotion` so the name stops describing behaviour that no longer exists.

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

```python
async def test_ingest_skips_anonymization_when_disabled(db_pool, monkeypatch):
    """CORPUS_ANONYMIZE=false must keep the original text verbatim."""
    from app.config import settings
    monkeypatch.setattr(settings, "corpus_anonymize", False)

    called = False

    async def _anonymize(question, answer):
        nonlocal called
        called = True
        return ("generic q", "generic a")

    monkeypatch.setattr(corpus_pipeline, "anonymize_qa_pair", _anonymize)
    assert called is False


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

In `app/services/corpus_pipeline.py`'s `run_ingest`, the anonymization call currently looks like:

```python
            anonymized = await anonymize_qa_pair(question, answer)
            if anonymized is None:
                continue
            question, answer = anonymized
```

Replace it with:

```python
            # Anonymization is opt-in. With it off, the entry keeps the team's
            # real specifics — which is the entire point on a private network.
            if settings.corpus_anonymize:
                anonymized = await anonymize_qa_pair(question, answer)
                if anonymized is None:
                    continue
                question, answer = anonymized
```

- [ ] **Step 5: Confirm the Ollama short-circuit is above this**

```bash
cd /f/ObsidianAI/conclave && grep -n -B2 -A6 "ollama_base_url" app/services/corpus_pipeline.py | head -20
```

`run_ingest` must return early when `settings.ollama_base_url` is empty, **before** any staging query. If that guard is missing, add it at the top of `run_ingest`:

```python
    # run_promote needs Ollama for BOTH gate signals. Staging without it marks
    # answers consumed via corpus_submitted_at, holds them, then permanently
    # rejects them — and they can never be re-ingested, even after Ollama is
    # installed. Skipping loses nothing.
    if not settings.ollama_base_url:
        return 0
```

- [ ] **Step 6: Run the full suite**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS, including the pre-existing `test_ingest_skips_when_ollama_unavailable`.

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/services/corpus_pipeline.py tests/test_corpus_provenance.py
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
           -- agent cannot answer its own post (answers.py:57) and only the post
           -- author may accept (answers.py:197), so an accepted answer always
           -- involves two distinct agents.
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


async def test_corpus_list_filters_by_invalidation_state(client, db_pool):
    await _corpus_row(db_pool, "live", "a")
    await _corpus_row(db_pool, "dead", "b", invalidated=True)

    r = await client.get("/internal/admin/corpus?invalidated=false", headers=ADMIN)
    questions = [e["question_text"] for e in r.json()["data"]]
    assert "live" in questions and "dead" not in questions


async def test_admin_endpoints_reject_a_missing_key(client, db_pool):
    await _corpus_row(db_pool, "q", "a")
    cid = await _one_corpus_id(db_pool)
    r = await client.post(f"/internal/admin/corpus/{cid}/invalidate", json={"reason": "x"})
    assert r.status_code in (401, 403)
```

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
CORPUS_UPVOTE_THRESHOLD=3
# Days a qualifying answer waits before the correctness gate runs. Lower it
# if you want retrieval to start returning results sooner.
CORPUS_QUARANTINE_DAYS=7
```

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

- [ ] **Step 5: Commit**

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

- **The two flag surfaces, threshold logic, propagation, `GET /internal/admin/flags`** — plan 2.7b. Their tables ship here so 2.7b needs no migration.
- **Post expiry rework (spec §3b)** — plan 2.7b. `run_expiry` keeps its current hourly hard-delete behaviour until then.
- **Dashboard corpus browser** — Phase 3.5.
- **Reworking the promotion gate** — the dual-signal gate stays exactly as built.
- **Re-defaulting `corpus_upvote_threshold` / `corpus_quarantine_days`** — deliberate call, 2026-07-31. Accept-ingest solves achievability without weakening either guard.
