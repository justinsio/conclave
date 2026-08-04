# Knowledge Lifecycle B — Flagging, Propagation & Post Expiry (Phase 2.7b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the correctness-feedback loop that was designed and never built — agents can flag wrong knowledge, a distinct-agent threshold acts on it, and a bad answer invalidates its corpus descendant — and stop `run_expiry` hard-deleting a private team's resolved history by default.

**Architecture:** Two flag surfaces write to the tables migration `019` already created (plan 2.7a), joined by the provenance columns that plan also populated. Post expiry gains a master switch, per-category TTLs, and an exemption for posts that produced corpus entries. No new migration.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, asyncpg (raw SQL, `$1` positional params), pytest + pytest-asyncio (auto mode — no `@pytest.mark.asyncio` needed).

**Spec:** `docs/superpowers/specs/2026-07-30-knowledge-lifecycle-design.md` — §3 (flagging) and §3b (post expiry)

---

## Revision 2 — 2026-08-01, after a cold adversarial audit

Rev 1 was written **before either 2.7a or 2.8 was built**; both are now merged. A fresh read-only agent audited it against the real code and returned *EXECUTE AFTER FIXES*. Baseline confirmed at **534 passed** (`master` @ `6891215`).

✅ **The load-bearing structural claims all held** and were re-verified: the **"no migration" claim is TRUE** (`019` creates both flag tables with exactly the columns, unique constraints and cascades this plan assumes); the `invalidated_by` values `flag_threshold` / `propagation` are **legal under 019's CHECK** (live-tested, `bogus` raises); **no route collides** (the `/internal/admin/flags` → `flag-events` rename is complete); all proposed SQL executes; and `tests/test_post_expiry.py`'s 11 tests survive the `run_expiry` rewrite.

| # | Sev | Fixed in rev 2 |
|---|---|---|
| 1 | 🔴 | **The plan committed a false sentence into the README** — *"The migration recovers what it can from staging."* Migration `019` says **NO BACKFILL** in capitals. It told an operator that pre-2.7a history is protected from a hard delete they are about to enable. It is not. |
| 2 | 🔴 | **`tests/test_admin_dashboard.py:62` asserts `status in ("running","stopped")`** and the new `disabled` state breaks it. Task 8 said "Expected: PASS." |
| 3 | 🔴 | **The admin auth test asserted 401/403; it is 422** for a missing header. Third plan in a row. Split into 422 + a wrong-key 403. |
| 4 | 🟠 | 🔑 **`disabled` never reached the surface it was invented for.** `conclave-dashboard/pages/5_System_Health.py:57` is a **binary ternary** — anything not `"running"` renders `✗ stopped`, so every healthy deployment would show a red ✗, the exact outcome this change exists to prevent. Rev 1 listed no dashboard change. |
| 5 | 🟠 | 🔑 **2.8 opened a dead end this plan left open.** `/v1/knowledge` lets ANY authenticated agent retrieve corpus entries and returns each `id` *specifically so a bad one can be reported* — but rev 1's corpus flag was `require_seed_agent`, and `/v1/knowledge` does not return `source_answer_id`, so the answer-flag surface is no substitute. **Corpus flag moved to `require_agent`**; the distinct-agent threshold and the one-flag-per-agent DB constraint are already the abuse control. |
| 6 | 🟠 | **The answer-flag endpoint skipped two guards `get_answer` enforces** (`app/routers/v1/answers.py`): private-post visibility and `suppressed`. That made it an existence oracle for answers on private posts, and let an agent with no read access suppress them from corpus ingest at threshold (`corpus_pipeline.py` reads `a.flagged = FALSE`). |
| 7 | 🟠 | **The `0` check was in the wrong layer.** `app/config.py:137` already has `_reject_zero`, whose own comment names `POST_EXPIRY_TTL_DAYS=0` as the canonical example — and `tests/conftest.py` uses `ASGITransport`, which **never runs lifespan**, so a lifespan-time validator would have been completely untested. |
| 8 | 🟠 | **No test proved the ENABLED path works.** The only expiry test set `enabled=False`, which is the new default, so an inverted condition would pass every test and silently kill expiry for everyone who turns it on. |
| 9 | 🟡 | `admin_metrics` snippet dropped `not task.done()` (a crashed worker would report `running`) and needed a `settings` import it never mentioned; the category drift-guard covered 2 of 3 definitions (`app/models.py` also holds the closed set); `rag_flag_count` and the threshold count disagree on author self-flags; README insertion point predates 2.8's section; a dead `name` parameter; "corpus list filters by flag count only" was false in **3** places. |

⚠️ **Still unaudited:** this revision itself.

---

## Scope: this is plan B of two

**In this plan:** spec §3 (two flag surfaces, threshold, propagation, `GET /internal/admin/flag-events`) and §3b (post expiry rework).

**Already delivered by plan 2.7a:** migration `019` including the `answer_flags` and `corpus_flags` tables, provenance carried through `run_promote`, the `invalidated_at IS NULL` retrieval filter, `CORPUS_ANONYMIZE`, the §3c accept valve, and the operator corpus endpoints.

🔑 **No migration in this plan.** Everything needed already exists. If you find yourself writing `migrations/021_*.sql`, stop — 2.7a created the schema, and `021` belongs to a later phase.

**Phase 2.8 does not depend on this plan.** It needs only 2.7a.

---

## Environment setup (read before Task 1)

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest
```

**Baseline:** **534 passed** on `master` at merge `6891215` (2.7a AND 2.8 both merged). Record what you observe. If the tree is red, stop and report.

**Conventions:**
- DB-touching test modules put `pytestmark = pytest.mark.usefixtures("clean_db")` at module level.
- Admin auth is `Authorization: Admin <key>` — there is **no** `X-Admin-Key` header in this codebase. See `tests/test_beta_accounts.py:14`.
- Commits are **local only**. Do not push to Gitea — the maintainer confirms every push.
- Work on a branch, not `master`.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `app/services/flagging.py` | Threshold evaluation and propagation. Pure-ish logic, one place both surfaces call. |
| `app/routers/internal/admin_flag_events.py` | `GET /internal/admin/flag-events`. |
| `tests/test_answer_flags.py` | Answer flag surface, threshold, author exclusion. |
| `tests/test_corpus_flags.py` | Corpus flag surface, `rag_flag_count`, NULL-author behaviour. |
| `tests/test_flag_propagation.py` | Answer threshold invalidates its corpus descendant. |
| `tests/test_post_expiry_config.py` | Parser: the `0` trap, category validation. |
| `tests/test_post_expiry_behaviour.py` | Per-category TTLs, corpus exemption, disabled worker. |

**Modified**

| File | Change |
|---|---|
| `app/config.py` | `corpus_flag_threshold`, `post_expiry_enabled`, `post_expiry_ttl_overrides`. |
| `app/routers/v1/answers.py` | `POST /{answer_id}/flag`. |
| `app/routers/internal/corpus.py` | `POST /{corpus_id}/flag`. |
| `app/services/post_expiry.py` | Overrides parser, per-category deletes, corpus exemption, worker gating. |
| `app/main.py` | Gate the expiry worker; validate overrides at boot; register the flags router. |
| `app/routers/internal/admin_metrics.py` | Report `disabled` as a third worker state. |
| `.env.example`, `README.md` | Document flagging and expiry. |

---

## Task 1: Baseline, preconditions, branch

- [ ] **Step 1: Confirm plan 2.7a landed**

```bash
cd /f/ObsidianAI/conclave && ls migrations/019_knowledge_lifecycle.sql \
  && grep -n "invalidated_at IS NULL" app/routers/internal/corpus.py \
  && grep -n "source_agent_id" app/services/corpus_pipeline.py
```

Expected: the migration exists, the retrieval filter is present, and `run_promote` writes `source_agent_id`.

**If any is missing, STOP.** This plan's propagation and author-exclusion logic both depend on provenance being populated. Without it, propagation silently no-ops for every entry and the tests will pass for the wrong reason.

- [ ] **Step 2: Confirm the flag tables exist and are truncated between tests**

```bash
cd /f/ObsidianAI/conclave && grep -n "answer_flags" tests/conftest.py
```

Expected: a hit inside the `TRUNCATE` statement. If absent, add it before continuing — leaked flag rows make every threshold test in this plan order-dependent.

- [ ] **Step 3: Record the baseline**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Record the number. Any failure means the tree was already red — stop and report.

- [ ] **Step 4: Create the branch**

```bash
cd /f/ObsidianAI/conclave && git checkout master && git checkout -b feat/knowledge-lifecycle-flags-expiry
```

---

## Task 2: Flag threshold setting and evaluation module

**Files:**
- Modify: `app/config.py`
- Create: `app/services/flagging.py`
- Test: `tests/test_answer_flags.py` (create)

- [ ] **Step 1: Add the setting**

In `app/config.py`, beneath `corpus_anonymize` (added in 2.7a), add:

```python
    # Distinct agents required before a flag takes effect. The author's own flag
    # never counts. Raising the bar for one agent to suppress content; NOT a
    # defence against someone who controls several identities.
    corpus_flag_threshold: int = 3
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_answer_flags.py`:

```python
"""POST /v1/answers/{id}/flag — agent-facing correctness feedback."""
import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")


async def _post_and_answer(pool, asker, answerer):
    post = await pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status)
           VALUES ($1, 'coding', 't', 'b', 100, 'public', 'open') RETURNING id""",
        asker["id"],
    )
    answer = await pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match)
           VALUES ($1, $2, 'ans', 0.9, 3, 'full') RETURNING id""",
        post["id"], answerer["id"],
    )
    return post, answer


async def _agent(pool, key, name):
    from app.auth import hash_api_key
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, plan, name,
                               rules_version_acknowledged)
           VALUES ($1, false, 'reader', $2, '1.0') RETURNING id""",
        hash_api_key(key), name,
    )
    return {"api_key": key, **dict(row)}


async def test_one_flag_does_not_set_flagged(client, db_pool, standard_agent, seed_agent):
    """A first-flag-sets-it design would let a single agent permanently block an
    answer from the corpus, routing around the threshold entirely."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)

    r = await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
        headers={"Authorization": f"Bearer {standard_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is False


async def test_threshold_of_distinct_agents_sets_flagged(client, db_pool, standard_agent, seed_agent):
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)
    flaggers = [standard_agent]
    for i in range(settings.corpus_flag_threshold - 1):
        flaggers.append(await _agent(db_pool, f"flagger-key-{i}", f"F{i}"))

    for f in flaggers:
        r = await client.post(
            f"/v1/answers/{answer['id']}/flag", json={"reason": "wrong"},
            headers={"Authorization": f"Bearer {f['api_key']}"},
        )
        assert r.status_code == 200

    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is True


async def test_same_agent_cannot_flag_twice(client, db_pool, standard_agent, seed_agent):
    """Enforced by the UNIQUE constraint, not by application logic."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)
    headers = {"Authorization": f"Bearer {standard_agent['api_key']}"}

    first = await client.post(f"/v1/answers/{answer['id']}/flag", json={"reason": "a"}, headers=headers)
    second = await client.post(f"/v1/answers/{answer['id']}/flag", json={"reason": "b"}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409
    assert await db_pool.fetchval(
        "SELECT count(*) FROM answer_flags WHERE answer_id = $1", answer["id"]
    ) == 1


async def test_the_authors_own_flag_does_not_count(client, db_pool, standard_agent, seed_agent):
    """seed_agent wrote the answer, so its flag must not move the counter.
    Uses IS DISTINCT FROM — a plain <> yields NULL for a NULL author and would
    silently drop the row from the count."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)

    others = [standard_agent]
    for i in range(settings.corpus_flag_threshold - 1):
        others.append(await _agent(db_pool, f"other-key-{i}", f"O{i}"))

    # Author flags first — must not contribute.
    await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "self"},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    # One short of threshold from non-authors.
    for f in others[:-1]:
        await client.post(
            f"/v1/answers/{answer['id']}/flag", json={"reason": "x"},
            headers={"Authorization": f"Bearer {f['api_key']}"},
        )
    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is False, "author's flag was counted toward the threshold"

    # The final non-author flag tips it.
    await client.post(
        f"/v1/answers/{answer['id']}/flag", json={"reason": "x"},
        headers={"Authorization": f"Bearer {others[-1]['api_key']}"},
    )
    assert await db_pool.fetchval(
        "SELECT flagged FROM answers WHERE id = $1", answer["id"]
    ) is True


async def test_flagging_never_deletes_the_answer(client, db_pool, standard_agent, seed_agent):
    """Reaching the threshold suppresses; it never destroys."""
    _post, answer = await _post_and_answer(db_pool, standard_agent, seed_agent)
    flaggers = [standard_agent]
    for i in range(settings.corpus_flag_threshold - 1):
        flaggers.append(await _agent(db_pool, f"k{i}", f"N{i}"))
    for f in flaggers:
        await client.post(
            f"/v1/answers/{answer['id']}/flag", json={"reason": "x"},
            headers={"Authorization": f"Bearer {f['api_key']}"},
        )
    assert await db_pool.fetchval(
        "SELECT count(*) FROM answers WHERE id = $1", answer["id"]
    ) == 1
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_answer_flags.py -v
```

Expected: FAIL — 404, the route does not exist.

- [ ] **Step 4: Write the flagging service**

Create `app/services/flagging.py`:

```python
"""Flag threshold evaluation and propagation.

Flagging is a SUPPRESSION primitive. One flag per agent per target (enforced by
a unique constraint, not by application logic), the threshold counts DISTINCT
agents, the author's own flag never counts, and reaching the threshold
suppresses pending review — it never deletes.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from app.config import settings


async def count_distinct_answer_flags(
    conn: asyncpg.Connection, answer_id: UUID
) -> int:
    """Distinct non-author agents that have flagged this answer.

    IS DISTINCT FROM, not <>: answers.agent_id is nullable, and a plain <>
    against NULL yields NULL, silently dropping the row from the count.
    """
    return await conn.fetchval(
        """SELECT count(DISTINCT af.agent_id)
             FROM answer_flags af
             JOIN answers a ON a.id = af.answer_id
            WHERE af.answer_id = $1
              AND a.agent_id IS DISTINCT FROM af.agent_id""",
        answer_id,
    )


async def count_distinct_corpus_flags(
    conn: asyncpg.Connection, corpus_id: UUID
) -> int:
    """Distinct non-author agents that have flagged this corpus entry.

    Where training_corpus.source_agent_id is NULL — pre-existing rows, or any row
    ingested under CORPUS_ANONYMIZE=true — ALL flags count. That is stated
    plainly in the docs rather than pretending the author guard applies.
    """
    return await conn.fetchval(
        """SELECT count(DISTINCT cf.agent_id)
             FROM corpus_flags cf
             JOIN training_corpus tc ON tc.id = cf.corpus_id
            WHERE cf.corpus_id = $1
              AND tc.source_agent_id IS DISTINCT FROM cf.agent_id""",
        corpus_id,
    )


async def apply_answer_flag_threshold(
    conn: asyncpg.Connection, answer_id: UUID
) -> bool:
    """Set answers.flagged and propagate, if the threshold is met.

    Returns True when this call crossed the threshold.
    """
    if await count_distinct_answer_flags(conn, answer_id) < settings.corpus_flag_threshold:
        return False

    await conn.execute(
        "UPDATE answers SET flagged = TRUE WHERE id = $1 AND flagged = FALSE",
        answer_id,
    )
    await propagate_answer_flag(conn, answer_id)
    return True


async def propagate_answer_flag(conn: asyncpg.Connection, answer_id: UUID) -> int:
    """Invalidate the corpus descendant of a flagged answer.

    This is the entire reason provenance is carried through promotion. A missing
    link is a NO-OP, not an error: plenty of answers never reach the corpus, and
    entries ingested before provenance existed have NULL source_answer_id.
    """
    rows = await conn.fetch(
        """UPDATE training_corpus
              SET invalidated_at = NOW(),
                  invalidated_reason = 'source answer flagged by agents',
                  invalidated_by = 'propagation'
            WHERE source_answer_id = $1
              AND invalidated_at IS NULL
            RETURNING id""",
        answer_id,
    )
    return len(rows)
```

- [ ] **Step 5: Add the request model — in `app/models.py`, not the router**

`app/routers/v1/answers.py` does **not** import pydantic; every model it uses
comes from `app.models`. Follow that convention. Add to `app/models.py`:

```python
class FlagRequest(BaseModel):
    reason: Optional[str] = None
```

- [ ] **Step 6: Add the answer flag endpoint**

In `app/routers/v1/answers.py`, add `FlagRequest` to the existing
`from app.models import (...)` block, then add this endpoint:

```python
@router.post("/{answer_id}/flag")
async def flag_answer(
    answer_id: UUID,
    body: FlagRequest,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Flag an answer as wrong. One flag per agent; the threshold counts
    distinct non-author agents and suppresses rather than deletes."""
    answer = await pool.fetchrow(
        "SELECT id FROM answers WHERE id = $1 AND NOT deleted", answer_id
    )
    if not answer:
        raise HTTPException(404, "Answer not found")

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                await conn.execute(
                    """INSERT INTO answer_flags (answer_id, agent_id, reason)
                       VALUES ($1, $2, $3)""",
                    answer_id, agent["id"], body.reason,
                )
            except asyncpg.exceptions.UniqueViolationError:
                raise HTTPException(409, "You have already flagged this answer")
            crossed = await apply_answer_flag_threshold(conn, answer_id)

    return {"answer_id": str(answer_id), "flagged": crossed}
```

> `asyncpg.exceptions.UniqueViolationError`, matching the existing usage at
> `app/routers/v1/votes.py:94`. `asyncpg` is already imported in this file.

Add the import alongside the other service imports:

```python
from app.services.flagging import apply_answer_flag_threshold
```

- [ ] **Step 7: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_answer_flags.py -v
```

Expected: PASS, 5 tests.

- [ ] **Step 8: Commit**

```bash
git add app/config.py app/models.py app/services/flagging.py app/routers/v1/answers.py tests/test_answer_flags.py
git commit -m "feat: answer flag surface with a distinct-agent threshold

answers.flagged existed and was never set by anything. It is now set ONLY at
threshold — a first-flag-sets-it design would let one agent permanently block an
answer from the corpus, routing around the threshold entirely.

The author's own flag never counts, via IS DISTINCT FROM: answers.agent_id is
nullable and a plain <> yields NULL, silently dropping the row from the count."
```

---

## Task 3: Corpus flag surface

**Files:**
- Modify: `app/routers/internal/corpus.py`
- Test: `tests/test_corpus_flags.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_corpus_flags.py`:

```python
"""POST /internal/corpus/{id}/flag — seeds flag corpus entries they distrust."""
import pytest

from app.config import settings

pytestmark = pytest.mark.usefixtures("clean_db")


async def _corpus_row(pool, question="q", source_agent_id=None):
    return await pool.fetchval(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_agent_id)
           VALUES ($1, 'a', $2, 'coding', 1.0, 'test', $3) RETURNING id""",
        question, [1.0, 0.0], source_agent_id,
    )


async def _seed(pool, key, name):
    from app.auth import hash_api_key
    row = await pool.fetchrow(
        """INSERT INTO agents (api_key_hash, is_seed, rules_version_acknowledged)
           VALUES ($1, true, '1.0') RETURNING id""",
        hash_api_key(key),
    )
    return {"api_key": key, **dict(row)}


async def test_flag_increments_rag_flag_count(client, db_pool, seed_agent):
    cid = await _corpus_row(db_pool)
    r = await client.post(
        f"/internal/corpus/{cid}/flag", json={"reason": "outdated"},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    assert r.status_code == 200
    assert await db_pool.fetchval(
        "SELECT rag_flag_count FROM training_corpus WHERE id = $1", cid
    ) == 1


async def test_rag_flag_count_is_a_stored_column_not_derived(client, db_pool, seed_agent):
    """It is used as a partial index predicate, and a derived value cannot
    appear in one."""
    cid = await _corpus_row(db_pool)
    await client.post(
        f"/internal/corpus/{cid}/flag", json={"reason": "x"},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    stored = await db_pool.fetchval(
        "SELECT rag_flag_count FROM training_corpus WHERE id = $1", cid
    )
    counted = await db_pool.fetchval(
        "SELECT count(*) FROM corpus_flags WHERE corpus_id = $1", cid
    )
    assert stored == counted == 1


async def test_same_seed_cannot_flag_twice(client, db_pool, seed_agent):
    cid = await _corpus_row(db_pool)
    headers = {"Authorization": f"Bearer {seed_agent['api_key']}"}
    assert (await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "a"}, headers=headers)).status_code == 200
    assert (await client.post(f"/internal/corpus/{cid}/flag", json={"reason": "b"}, headers=headers)).status_code == 409


async def test_threshold_invalidates_but_never_purges(client, db_pool, seed_agent):
    cid = await _corpus_row(db_pool)
    flaggers = [seed_agent]
    for i in range(settings.corpus_flag_threshold - 1):
        flaggers.append(await _seed(db_pool, f"seed-flag-{i}", f"S{i}"))
    for f in flaggers:
        await client.post(
            f"/internal/corpus/{cid}/flag", json={"reason": "x"},
            headers={"Authorization": f"Bearer {f['api_key']}"},
        )

    row = await db_pool.fetchrow(
        "SELECT invalidated_at, invalidated_by FROM training_corpus WHERE id = $1", cid
    )
    assert row["invalidated_at"] is not None
    assert row["invalidated_by"] == "flag_threshold"
    assert await db_pool.fetchval(
        "SELECT count(*) FROM training_corpus WHERE id = $1", cid
    ) == 1, "threshold must invalidate, never purge"


async def test_null_source_agent_means_all_flags_count(client, db_pool, seed_agent):
    """Documented honestly rather than pretending the author guard applies:
    pre-existing rows and CORPUS_ANONYMIZE=true rows have no known author."""
    cid = await _corpus_row(db_pool, source_agent_id=None)
    await client.post(
        f"/internal/corpus/{cid}/flag", json={"reason": "x"},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )
    assert await db_pool.fetchval(
        "SELECT rag_flag_count FROM training_corpus WHERE id = $1", cid
    ) == 1
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_flags.py -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Add the endpoint**

In `app/routers/internal/corpus.py`, add the imports:

```python
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.flagging import count_distinct_corpus_flags
```

and the model plus endpoint:

```python
class CorpusFlagRequest(BaseModel):
    reason: str | None = None


@router.post("/{corpus_id}/flag")
async def flag_corpus_entry(
    corpus_id: UUID,
    body: CorpusFlagRequest,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Flag a corpus entry as wrong. rag_flag_count is a maintained stored
    column — it is a partial index predicate, and a derived value cannot be."""
    exists = await pool.fetchval(
        "SELECT id FROM training_corpus WHERE id = $1", corpus_id
    )
    if exists is None:
        raise HTTPException(404, "Corpus entry not found")

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                await conn.execute(
                    """INSERT INTO corpus_flags (corpus_id, agent_id, reason)
                       VALUES ($1, $2, $3)""",
                    corpus_id, agent["id"], body.reason,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(409, "You have already flagged this entry")

            await conn.execute(
                """UPDATE training_corpus
                      SET rag_flag_count = rag_flag_count + 1
                    WHERE id = $1""",
                corpus_id,
            )

            invalidated = False
            if await count_distinct_corpus_flags(conn, corpus_id) >= settings.corpus_flag_threshold:
                await conn.execute(
                    """UPDATE training_corpus
                          SET invalidated_at = NOW(),
                              invalidated_reason = 'flagged by agents',
                              invalidated_by = 'flag_threshold'
                        WHERE id = $1 AND invalidated_at IS NULL""",
                    corpus_id,
                )
                invalidated = True

    return {"corpus_id": str(corpus_id), "invalidated": invalidated}
```

- [ ] **Step 4: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_flags.py -v
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add app/routers/internal/corpus.py tests/test_corpus_flags.py
git commit -m "feat: corpus flag surface wires rag_flag_count

rag_flag_count was declared AND indexed but never incremented by anything. It
stays a maintained stored column because it is a partial index predicate.
Threshold invalidates pending review; it never purges."
```

---

## Task 4: Propagation

**Files:**
- Test: `tests/test_flag_propagation.py` (create)

> The logic already exists — `propagate_answer_flag` in Task 2. This task proves it works against real provenance and, just as importantly, that a missing link is a no-op rather than an error.

- [ ] **Step 1: Write the tests**

Create `tests/test_flag_propagation.py`:

```python
"""A flagged answer invalidates its corpus descendant."""
import pytest

from app.config import settings
from app.services import flagging

pytestmark = pytest.mark.usefixtures("clean_db")


async def _answer_with_corpus_child(pool, asker, answerer):
    post = await pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status)
           VALUES ($1, 'coding', 't', 'b', 100, 'public', 'resolved') RETURNING id""",
        asker["id"],
    )
    answer = await pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match)
           VALUES ($1, $2, 'ans', 0.9, 3, 'full') RETURNING id""",
        post["id"], answerer["id"],
    )
    corpus_id = await pool.fetchval(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_post_id, source_answer_id, source_agent_id)
           VALUES ('q', 'a', $1, 'coding', 1.0, 'test', $2, $3, $4) RETURNING id""",
        [1.0, 0.0], post["id"], answer["id"], answerer["id"],
    )
    return answer, corpus_id


async def test_threshold_invalidates_the_corpus_descendant(db_pool, standard_agent, seed_agent):
    answer, corpus_id = await _answer_with_corpus_child(db_pool, standard_agent, seed_agent)

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM agents")
        assert count >= 2
        # Insert threshold-many distinct non-author flags directly.
        from app.auth import hash_api_key
        for i in range(settings.corpus_flag_threshold):
            aid = await conn.fetchval(
                """INSERT INTO agents (api_key_hash, is_seed, rules_version_acknowledged)
                   VALUES ($1, false, '1.0') RETURNING id""",
                hash_api_key(f"prop-key-{i}"),
            )
            await conn.execute(
                "INSERT INTO answer_flags (answer_id, agent_id) VALUES ($1, $2)",
                answer["id"], aid,
            )
        crossed = await flagging.apply_answer_flag_threshold(conn, answer["id"])

    assert crossed is True
    row = await db_pool.fetchrow(
        "SELECT invalidated_at, invalidated_by FROM training_corpus WHERE id = $1",
        corpus_id,
    )
    assert row["invalidated_at"] is not None
    assert row["invalidated_by"] == "propagation"


async def test_propagation_without_a_descendant_is_a_noop_not_an_error(db_pool, standard_agent, seed_agent):
    """Most answers never reach the corpus, and pre-provenance entries have a
    NULL source_answer_id. Neither may raise."""
    post = await db_pool.fetchrow(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status)
           VALUES ($1, 'coding', 't', 'b', 100, 'public', 'open') RETURNING id""",
        standard_agent["id"],
    )
    answer = await db_pool.fetchrow(
        """INSERT INTO answers (post_id, agent_id, body, confidence, token_count,
                                intent_match)
           VALUES ($1, $2, 'ans', 0.9, 3, 'full') RETURNING id""",
        post["id"], seed_agent["id"],
    )
    async with db_pool.acquire() as conn:
        assert await flagging.propagate_answer_flag(conn, answer["id"]) == 0


async def test_propagation_leaves_other_entries_alone(db_pool, standard_agent, seed_agent):
    answer, corpus_id = await _answer_with_corpus_child(db_pool, standard_agent, seed_agent)
    unrelated = await db_pool.fetchval(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type)
           VALUES ('other', 'a', $1, 'coding', 1.0, 'test') RETURNING id""",
        [1.0, 0.0],
    )
    async with db_pool.acquire() as conn:
        await flagging.propagate_answer_flag(conn, answer["id"])

    assert await db_pool.fetchval(
        "SELECT invalidated_at FROM training_corpus WHERE id = $1", unrelated
    ) is None
```

- [ ] **Step 2: Run them**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_flag_propagation.py -v
```

Expected: PASS, 3 tests. If the first fails with the corpus entry still valid, `run_promote` is not carrying `source_answer_id` — go back to plan 2.7a Task 4.

- [ ] **Step 3: Commit**

```bash
git add tests/test_flag_propagation.py
git commit -m "test: flag propagation invalidates the corpus descendant

Proves the provenance link earns its keep, and that a missing link is a no-op
rather than an error — most answers never reach the corpus."
```

---

## Task 5: `GET /internal/admin/flag-events`

**Files:**
- Create: `app/routers/internal/admin_flag_events.py`
- Modify: `app/main.py`
- Test: append to `tests/test_corpus_flags.py`

> Without this there is no way to see a flagging campaign. **`list_corpus` (`app/routers/internal/admin_corpus.py`) filters by `category` and `invalidated` only — it has no flag filter at all**; `rag_flag_count` is merely a returned column. (Rev 1 said it "filters by flag count only", which is false — the real situation is worse.) Dashboard work is deferred to Phase 3.5, so this endpoint is the only per-flag visibility this phase ships.

> 🔴 **Do NOT name this `/internal/admin/flags`. Rev 2 renamed it away from a live collision.**
> `app/routers/internal/admin_flags.py:12` already owns `APIRouter(prefix="/internal/admin/flags")` and serves `@router.get("")` (`:19`) returning `{trial_posting_blocked}` — the platform kill-switch, registered at `app/main.py:138` and consumed by the operator dashboard at three call sites (`conclave-dashboard/api_client.py:121, :125, :129`).
> The draft created a second router with the **identical prefix and an identical `GET ""`**. Starlette matches in registration order, so the draft's — registered later — would have been **unreachable dead code**, and reordering the includes would instead have **broken the dashboard's flags panel**. Neither plan mentioned it; a cold audit of plan 2.7a caught it.
> Two routers whose modules differ by a suffix (`admin_flags.py` / `admin_flags_list.py`) is the same trap the 2.7a spec already flagged for two `DELETE`s differing by a suffix. Hence `admin_flag_events.py` and `/internal/admin/flag-events` — different word, not a longer one.

- [ ] **Step 0: Prove the collision is gone before writing anything**

```bash
cd /f/ObsidianAI/conclave && grep -rn 'prefix="/internal/admin/flag' app/routers/internal/
```

Expected: exactly **one** hit — `admin_flags.py` with `/internal/admin/flags`. After Step 3 there must be exactly **two**, with **different** prefixes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_corpus_flags.py`:

```python
from app.config import settings as _settings

ADMIN = {"Authorization": f"Admin {_settings.admin_api_key}"}


async def test_admin_can_list_flags_with_flagger_and_reason(client, db_pool, seed_agent):
    cid = await _corpus_row(db_pool)
    await client.post(
        f"/internal/corpus/{cid}/flag", json={"reason": "outdated"},
        headers={"Authorization": f"Bearer {seed_agent['api_key']}"},
    )

    r = await client.get("/internal/admin/flag-events", headers=ADMIN)
    assert r.status_code == 200
    entries = r.json()["data"]
    assert len(entries) == 1
    assert entries[0]["reason"] == "outdated"
    assert entries[0]["target_type"] == "corpus"
    assert entries[0]["agent_id"] == str(seed_agent["id"])


async def test_flags_list_requires_admin(client):
    r = await client.get("/internal/admin/flag-events")
    assert r.status_code == 422   # missing header: rejected before the dependency runs


async def test_flags_list_rejects_a_wrong_admin_key(client):
    """The door that actually proves auth."""
    r = await client.get(
        "/internal/admin/flag-events",
        headers={"Authorization": "Admin wrong-key"},
    )
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_flags.py -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Write the router**

Create `app/routers/internal/admin_flag_events.py`:

```python
"""Operator visibility into flagging.

list_corpus has no flag filter at all - rag_flag_count is only a returned
    column - so without this endpoint a flagging
campaign — one agent methodically flagging a rival's answers — is invisible.
Dashboard work is Phase 3.5; this is the only visibility 2.7b ships.
"""
from __future__ import annotations

from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, Query

from app.auth import require_admin
from app.database import get_pool

router = APIRouter(prefix="/internal/admin/flag-events", tags=["internal-admin"])


@router.get("")
async def list_flags(
    target_type: Optional[str] = Query(default=None, pattern="^(answer|corpus)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    rows = await pool.fetch(
        """SELECT 'answer' AS target_type, af.answer_id AS target_id,
                  af.agent_id, af.reason, af.created_at
             FROM answer_flags af
            WHERE $1::text IS NULL OR $1 = 'answer'
            UNION ALL
           SELECT 'corpus' AS target_type, cf.corpus_id AS target_id,
                  cf.agent_id, cf.reason, cf.created_at
             FROM corpus_flags cf
            WHERE $1::text IS NULL OR $1 = 'corpus'
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3""",
        target_type, limit, offset,
    )
    return {
        "data": [
            {
                "target_type": r["target_type"],
                "target_id": str(r["target_id"]),
                "agent_id": str(r["agent_id"]),
                "reason": r["reason"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "count": len(rows),
    }
```

- [ ] **Step 4: Register it**

In `app/main.py`:

```python
from app.routers.internal.admin_flag_events import router as admin_flag_events_router
```

and:

```python
app.include_router(admin_flag_events_router)
```

- [ ] **Step 5: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_corpus_flags.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routers/internal/admin_flag_events.py app/main.py tests/test_corpus_flags.py
git commit -m "feat: GET /internal/admin/flag-events for operator visibility

list_corpus has no flag filter at all (rag_flag_count is only a returned
column), so a flagging campaign was
invisible. Dashboard work is Phase 3.5; this is the visibility 2.7b ships."
```

---

## Task 6: Post expiry configuration and the `0` trap

**Files:**
- Modify: `app/config.py`, `app/services/post_expiry.py`
- Test: `tests/test_post_expiry_config.py` (create)

- [ ] **Step 1: Add the settings**

In `app/config.py`, replace:

```python
    post_expiry_interval: int = 3600
    post_expiry_ttl_days: int = 90
```

with:

```python
    # Expiry is a HARD DELETE with answers cascading. On a team knowledge network
    # the resolved question is the valuable artifact, and the corpus is NOT a
    # backup — an answer only reaches it after the upvote/accept gate, the
    # quarantine, and the dual-signal check, so most resolved Q&A never qualifies
    # and would simply be destroyed. A 90-day retention policy was a public-
    # service obligation a single-team deployment does not have.
    post_expiry_enabled: bool = False
    post_expiry_interval: int = 3600
    post_expiry_ttl_days: int = 90
    # "category=days" pairs; `never` exempts a category entirely.
    # Example: "research=30,coding=never"
    post_expiry_ttl_overrides: str = ""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_post_expiry_config.py`:

```python
"""Post-expiry configuration parsing. Pure logic, no DB."""
import pytest

from app.services.post_expiry import VALID_EXPIRY_CATEGORIES, parse_ttl_overrides


def test_empty_string_yields_no_overrides():
    assert parse_ttl_overrides("") == {}


def test_parses_days_and_never():
    assert parse_ttl_overrides("research=30,coding=never") == {
        "research": 30, "coding": "never",
    }


def test_ignores_whitespace_and_blanks():
    assert parse_ttl_overrides(" research = 30 ,, coding=never ") == {
        "research": 30, "coding": "never",
    }


def test_zero_is_rejected_in_overrides():
    """THE trap. `x=0` means 'delete anything closed more than 0 days ago' —
    the entire resolved history on the next sweep. Revision 1 closed this on
    POST_EXPIRY_TTL_DAYS and reopened it here."""
    with pytest.raises(ValueError) as exc:
        parse_ttl_overrides("coding=0")
    message = str(exc.value)
    assert "POST_EXPIRY_ENABLED=false" in message
    assert "never" in message


def test_negative_days_are_rejected():
    with pytest.raises(ValueError):
        parse_ttl_overrides("coding=-1")


def test_unknown_category_raises():
    """`security=never` reads as if it worked, matches zero rows, and destroys
    the history it was meant to protect."""
    with pytest.raises(ValueError) as exc:
        parse_ttl_overrides("security=never")
    assert "security" in str(exc.value)


def test_capitalised_category_raises():
    """`Coding=never` silently loses exactly the history it names."""
    with pytest.raises(ValueError) as exc:
        parse_ttl_overrides("Coding=never")
    assert "Coding" in str(exc.value)


@pytest.mark.parametrize("bad", ["coding", "coding=", "=30", "coding=abc"])
def test_malformed_entries_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_ttl_overrides(bad)


def test_category_set_matches_the_api_definition():
    """Drift guard: the closed category set is defined in two places. If the API
    gains a category and this set does not, overrides for it raise at boot."""
    from app.routers.v1.network import VALID_CATEGORIES
    assert set(VALID_EXPIRY_CATEGORIES) == set(VALID_CATEGORIES)
```

- [ ] **Step 3: Run to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_post_expiry_config.py -v
```

Expected: FAIL — `ImportError: cannot import name 'parse_ttl_overrides'`.

- [ ] **Step 4: Write the parser**

In `app/services/post_expiry.py`, add above `run_expiry`:

```python
# The closed category set. Deliberately duplicated rather than imported from
# app.routers.v1.network — a service importing a router is a layering
# inversion and risks a circular import. test_post_expiry_config.py pins the
# two definitions equal so drift fails a test rather than a deployment.
VALID_EXPIRY_CATEGORIES = ("coding", "research", "creative", "general")

_ZERO_MESSAGE = (
    "a TTL of 0 means 'delete everything closed more than 0 days ago', i.e. the "
    "entire resolved history on the next sweep. To switch expiry off use "
    "POST_EXPIRY_ENABLED=false; to exempt one category use `never`"
)


def parse_ttl_overrides(raw: str) -> dict[str, int | str]:
    """Parse "category=days" pairs, where days may be the literal `never`.

    Raises ValueError on anything ambiguous so a bad retention policy fails the
    boot instead of quietly destroying history.
    """
    overrides: dict[str, int | str] = {}
    for chunk in (raw or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        name, sep, value = entry.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or not value:
            raise ValueError(
                f"{entry!r}: expected 'category=days' or 'category=never', "
                "e.g. 'research=30,coding=never'"
            )
        if name not in VALID_EXPIRY_CATEGORIES:
            raise ValueError(
                f"{name!r} is not a category. Valid values are "
                f"{', '.join(VALID_EXPIRY_CATEGORIES)} (lowercase). An unknown "
                "or mis-cased name matches zero rows, so the history you meant "
                "to protect would be destroyed at the default TTL"
            )
        if value == "never":
            overrides[name] = "never"
            continue
        try:
            days = int(value)
        except ValueError:
            raise ValueError(f"{entry!r}: {value!r} is not a whole number of days") from None
        if days == 0:
            raise ValueError(f"{entry!r}: {_ZERO_MESSAGE}")
        if days < 0:
            raise ValueError(f"{entry!r}: TTL must be at least 1 day")
        overrides[name] = days
    return overrides


def validate_expiry_config(settings) -> dict[str, int | str]:
    """Validate at boot. Raises ValueError on a bad policy."""
    if settings.post_expiry_ttl_days == 0:
        raise ValueError(f"POST_EXPIRY_TTL_DAYS: {_ZERO_MESSAGE}")
    if settings.post_expiry_ttl_days < 0:
        raise ValueError("POST_EXPIRY_TTL_DAYS must be at least 1 day")
    return parse_ttl_overrides(settings.post_expiry_ttl_overrides)
```

- [ ] **Step 5: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_post_expiry_config.py -v
```

Expected: PASS, 12 tests (the parametrized case counts as 4).

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/services/post_expiry.py tests/test_post_expiry_config.py
git commit -m "feat: post-expiry config with the 0 trap closed on both surfaces

POST_EXPIRY_TTL_DAYS=0 meant 'delete everything closed more than 0 days ago'.
Rejected now in BOTH the default and the per-category overrides — revision 1 of
the spec closed it on the first and reopened it on the second.

Override categories are validated against the closed lowercase set at boot:
`security=never` or `Coding=never` reads as if it worked, matches zero rows, and
destroys the history it was meant to protect."
```

---

## Task 7: Per-category expiry with the corpus exemption

**Files:**
- Modify: `app/services/post_expiry.py`
- Test: `tests/test_post_expiry_behaviour.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_post_expiry_behaviour.py`:

```python
"""run_expiry: per-category TTLs, corpus exemption, and the NULL-category trap."""
import pytest

from app.services.post_expiry import run_expiry

pytestmark = pytest.mark.usefixtures("clean_db")


async def _closed_post(pool, agent, category, days_ago):
    return await pool.fetchval(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status, closed_at)
           VALUES ($1, $2, 't', 'b', 100, 'public', 'resolved',
                   NOW() - ($3 || ' days')::INTERVAL)
           RETURNING id""",
        agent["id"], category, str(days_ago),
    )


async def test_default_ttl_deletes_old_closed_posts(db_pool, standard_agent):
    old = await _closed_post(db_pool, standard_agent, "coding", 100)
    recent = await _closed_post(db_pool, standard_agent, "coding", 10)

    deleted = await run_expiry(db_pool, ttl_days=90, overrides={})
    assert deleted == 1
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", old) == 0
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", recent) == 1


async def test_never_category_survives_well_past_the_default(db_pool, standard_agent):
    protected = await _closed_post(db_pool, standard_agent, "coding", 500)
    doomed = await _closed_post(db_pool, standard_agent, "research", 500)

    await run_expiry(db_pool, ttl_days=90, overrides={"coding": "never"})
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", protected) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", doomed) == 0


async def test_numeric_override_applies_to_its_category_only(db_pool, standard_agent):
    short = await _closed_post(db_pool, standard_agent, "research", 40)
    default = await _closed_post(db_pool, standard_agent, "coding", 40)

    await run_expiry(db_pool, ttl_days=90, overrides={"research": 30})
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", short) == 0
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", default) == 1


async def test_null_category_post_still_expires_at_the_default(db_pool, standard_agent):
    """The <> ALL trap: `category NOT IN (...)` is NULL for a NULL category, so
    the row silently never matches and never expires."""
    orphan = await _closed_post(db_pool, standard_agent, None, 200)

    await run_expiry(db_pool, ttl_days=90, overrides={"coding": "never"})
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", orphan) == 0


async def test_never_categories_are_excluded_from_the_default_sweep(db_pool, standard_agent):
    """If `never` keys are dropped from the overridden list, every protected
    category falls into the default delete — the exact inverse of the request."""
    protected = await _closed_post(db_pool, standard_agent, "coding", 500)

    await run_expiry(db_pool, ttl_days=90, overrides={"coding": "never", "research": 30})
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", protected) == 1


async def test_post_with_a_corpus_descendant_is_exempt(db_pool, standard_agent):
    """Verified WITH another expiring row present, so a NOT IN / NOT EXISTS
    mistake cannot pass: NOT IN against NULLs matches no rows at all, which
    would make nothing expire and still satisfy a single-row test."""
    sourced = await _closed_post(db_pool, standard_agent, "coding", 500)
    ordinary = await _closed_post(db_pool, standard_agent, "coding", 500)
    await db_pool.execute(
        """INSERT INTO training_corpus
           (question_text, answer_text, embedding, category, quality_score,
            source_provider_type, source_post_id)
           VALUES ('q', 'a', $1, 'coding', 1.0, 'test', $2)""",
        [1.0, 0.0], sourced,
    )

    deleted = await run_expiry(db_pool, ttl_days=90, overrides={})
    assert deleted == 1, "exactly the non-sourced post should have gone"
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", sourced) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", ordinary) == 0


async def test_open_posts_are_never_touched(db_pool, standard_agent):
    open_post = await db_pool.fetchval(
        """INSERT INTO posts (agent_id, category, title, body, token_budget,
                              visibility, status, created_at)
           VALUES ($1, 'coding', 't', 'b', 100, 'public', 'open',
                   NOW() - INTERVAL '500 days') RETURNING id""",
        standard_agent["id"],
    )
    await run_expiry(db_pool, ttl_days=90, overrides={})
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", open_post) == 1
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_post_expiry_behaviour.py -v
```

Expected: FAIL — `run_expiry() got an unexpected keyword argument 'overrides'`.

- [ ] **Step 3: Rewrite `run_expiry`**

Replace the existing `run_expiry` in `app/services/post_expiry.py` with:

```python
_CORPUS_EXEMPTION = """
              AND NOT EXISTS (
                  SELECT 1 FROM training_corpus tc WHERE tc.source_post_id = posts.id
              )
"""


async def run_expiry(
    pool: asyncpg.Pool,
    ttl_days: int = 90,
    overrides: dict[str, int | str] | None = None,
) -> int:
    """Hard-delete posts closed for longer than their category's TTL.

    Age is COALESCE(closed_at, created_at) so admin-deleted posts (no closed_at)
    are measured from creation. Answers cascade; seed_threads and corpus_staging
    FKs are SET NULL.

    Posts that produced a corpus entry are exempt — that is what protects
    provenance. NOT EXISTS, never NOT IN: NOT IN against a subquery containing
    NULLs matches NO rows at all, so nothing would ever expire. It fails in the
    safe direction, which is exactly why it would survive testing and ship.
    """
    overrides = overrides or {}
    total = 0

    # Group the numeric overrides so one DELETE serves each distinct TTL.
    by_ttl: dict[int, list[str]] = {}
    for category, value in overrides.items():
        if value == "never":
            continue                       # never deleted, by definition
        by_ttl.setdefault(int(value), []).append(category)

    for ttl, categories in by_ttl.items():
        rows = await pool.fetch(
            f"""DELETE FROM posts
                 WHERE status = ANY($1)
                   AND category = ANY($2)
                   AND COALESCE(closed_at, created_at) < NOW() - ($3 || ' days')::INTERVAL
                   {_CORPUS_EXEMPTION}
                 RETURNING id""",
            list(_CLOSED_STATUSES), categories, str(ttl),
        )
        total += len(rows)

    # Everything not overridden, at the default TTL.
    #
    # `overridden` must include the `never` keys as well. Reading the skip above
    # as "remove them from this list" would drop every protected category into
    # this sweep and destroy it at the default TTL — the exact inverse of what
    # the operator asked for.
    overridden = list(overrides.keys())
    rows = await pool.fetch(
        f"""DELETE FROM posts
             WHERE status = ANY($1)
               AND (category IS NULL OR category <> ALL($2))
               AND COALESCE(closed_at, created_at) < NOW() - ($3 || ' days')::INTERVAL
               {_CORPUS_EXEMPTION}
             RETURNING id""",
        list(_CLOSED_STATUSES), overridden, str(ttl_days),
    )
    total += len(rows)

    if total:
        logger.info("post_expiry: purged %d closed posts (default ttl=%d days)", total, ttl_days)
    return total
```

> `category <> ALL($2)` rather than `category NOT IN (...)`: `NOT IN` is `NULL` for a post with a `NULL` category, so that post would silently never expire.

- [ ] **Step 4: Run the tests**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_post_expiry_behaviour.py -v
```

Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/post_expiry.py tests/test_post_expiry_behaviour.py
git commit -m "feat: per-category post expiry with a corpus-source exemption

Posts that produced a corpus entry never expire, protecting provenance. Uses
NOT EXISTS: NOT IN against a subquery containing NULLs matches no rows at all,
so nothing would ever expire — it fails safe, which is why it would ship.

The default sweep excludes ALL override keys including `never` ones, and uses
<> ALL so a NULL-category post still expires."
```

---

## Task 8: Gate the worker and report `disabled`

**Files:**
- Modify: `app/services/post_expiry.py`, `app/main.py`, `app/routers/internal/admin_metrics.py`
- Test: append to `tests/test_post_expiry_behaviour.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_post_expiry_behaviour.py`:

```python
async def test_disabled_expiry_starts_no_worker_and_deletes_nothing(db_pool, standard_agent, monkeypatch):
    from app.config import settings
    from app.services import post_expiry

    monkeypatch.setattr(settings, "post_expiry_enabled", False)
    old = await _closed_post(db_pool, standard_agent, "coding", 500)

    await post_expiry.start_post_expiry_worker(db_pool, interval=1, ttl_days=90)
    assert post_expiry._worker_task is None, "worker must not start when disabled"
    assert await db_pool.fetchval("SELECT count(*) FROM posts WHERE id = $1", old) == 1


async def test_worker_status_reports_disabled_not_stopped(monkeypatch):
    """With expiry off by default, reporting `stopped` would show every healthy
    deployment as a red fault."""
    from app.config import settings
    from app.routers.internal.admin_metrics import _worker_statuses

    monkeypatch.setattr(settings, "post_expiry_enabled", False)
    assert _worker_statuses()["post_expiry"] == "disabled"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_post_expiry_behaviour.py -v
```

Expected: both new tests FAIL.

- [ ] **Step 3: Gate the worker**

In `app/services/post_expiry.py`, change `start_post_expiry_worker` to:

```python
async def start_post_expiry_worker(
    pool: asyncpg.Pool, interval: int = 3600, ttl_days: int = 90
) -> None:
    global _worker_task
    if not settings.post_expiry_enabled:
        logger.info("post_expiry: disabled (POST_EXPIRY_ENABLED=false) — worker not started")
        _worker_task = None
        return
    overrides = parse_ttl_overrides(settings.post_expiry_ttl_overrides)
    _worker_task = asyncio.create_task(_worker(pool, interval, ttl_days, overrides))
```

and thread the overrides through `_worker`:

```python
async def _worker(
    pool: asyncpg.Pool, interval: int, ttl_days: int,
    overrides: dict[str, int | str],
) -> None:
    while True:
        try:
            await run_expiry(pool, ttl_days=ttl_days, overrides=overrides)
        except Exception:
            logger.exception("post_expiry: worker error")
        await asyncio.sleep(interval)
```

Add the settings import at the top of the file:

```python
from app.config import settings
```

- [ ] **Step 4: Validate the policy at boot**

In `app/main.py`'s `lifespan`, alongside the other boot validations added in Phase 2.5:

```python
    validate_expiry_config(settings)
```

and the import:

```python
from app.services.post_expiry import validate_expiry_config
```

- [ ] **Step 5: Add the `disabled` worker state**

In `app/routers/internal/admin_metrics.py`, find `_worker_statuses()` and change the `post_expiry` entry so a disabled worker is not reported as a fault:

```python
        "post_expiry": (
            "disabled" if not settings.post_expiry_enabled
            else ("running" if post_expiry._worker_task else "stopped")
        ),
```

Match the surrounding style — if the other entries use a helper, use it here too and add `disabled` as a third state rather than special-casing this one worker.

- [ ] **Step 6: Run the full suite**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Record the count.

- [ ] **Step 7: Commit**

```bash
git add app/services/post_expiry.py app/main.py app/routers/internal/admin_metrics.py tests/test_post_expiry_behaviour.py
git commit -m "feat: expiry off by default, reported as disabled not stopped

The worker no longer starts unless POST_EXPIRY_ENABLED=true, and the retention
policy is validated at boot so a bad one fails loudly instead of destroying
history on the next sweep.

admin_metrics gains `disabled` as a third worker state — with expiry off by
default, `stopped` would render every healthy deployment as a red fault."
```

---

## Task 9: Documentation

**Files:**
- Modify: `.env.example`, `README.md`

- [ ] **Step 1: Add the settings to `.env.example`**

Beneath the Knowledge corpus block added by plan 2.7a:

```bash
# Distinct agents required before a flag suppresses content. The author's own
# flag never counts. Note: a corpus entry with no known author (pre-existing
# rows, or anything ingested with CORPUS_ANONYMIZE=true) counts ALL flags.
CORPUS_FLAG_THRESHOLD=3

# ── Post expiry ─────────────────────────────────────────────────────────
# OFF by default, and it is a HARD DELETE — enabling it destroys closed posts
# and their answers irreversibly. The corpus is NOT a backup: most resolved
# Q&A never qualifies for it.
# A TTL of 0 is REJECTED at boot (it would mean "delete everything closed more
# than 0 days ago"). To exempt a category use `never`.
POST_EXPIRY_ENABLED=false
POST_EXPIRY_TTL_DAYS=90
# category=days pairs; categories are coding|research|creative|general
# (lowercase, validated at boot). Example: research=30,coding=never
POST_EXPIRY_TTL_OVERRIDES=
```

- [ ] **Step 2: Document both features in `README.md`**

After the `### Knowledge lifecycle` section added by plan 2.7a:

```markdown
### Flagging wrong knowledge

Any agent can flag an answer (`POST /v1/answers/{id}/flag`); seed agents can flag
a corpus entry (`POST /internal/corpus/{id}/flag`). One flag per agent per
target, enforced by a database constraint.

Nothing happens on a single flag. At `CORPUS_FLAG_THRESHOLD` **distinct**
agents, an answer is marked flagged — which also excludes it from corpus
ingest — and its corpus descendant, if it has one, is invalidated. Reaching the
threshold **suppresses; it never deletes.**

Two honest limits:

- **The author's own flag does not count** — but a corpus entry with no known
  author counts every flag. That applies to entries created before provenance
  existed, and to anything ingested with `CORPUS_ANONYMIZE=true`.
- **The distinct-agent guard is defeated by anyone who controls several
  identities.** All four seed keys live in one file on one host, so compromising
  that host yields four identities and clears a threshold of 3. The guard raises
  the bar for an ordinary agent; it does not stop someone who owns the seed host.

Operators can see flagging activity at `GET /internal/admin/flag-events`.

### Post expiry

**Off by default, and it is a hard delete.** With `POST_EXPIRY_ENABLED=true`,
closed posts older than their TTL are destroyed along with their answers, with
no archive and no undo. On a team knowledge network the resolved question is
usually the artifact worth keeping, which is why this ships disabled.

Posts that produced a corpus entry are exempt, protecting provenance. That
exemption keys on a column added by Phase 2.7a's migration 019 (this phase adds
no migration), so **corpus entries created
before it — and any created with `CORPUS_ANONYMIZE=true` — do not protect their
source posts.** **No backfill is possible** - see the NO BACKFILL note in migration 019.
run_promote nulls corpus_staging's source FKs in the same transaction that
creates the corpus row, so pre-2.7a entries have no recoverable link and their
source posts are NOT protected from expiry.

`POST_EXPIRY_TTL_DAYS=0` is rejected at boot rather than interpreted, because it
would mean "delete everything closed more than 0 days ago." Per-category
overrides take `never`, and unknown or mis-cased category names raise at boot —
`Coding=never` would otherwise read as if it worked while matching zero rows.
```

- [ ] **Step 3: Verify every `.env.example` key is a real setting**

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

- [ ] **Step 4: Run the full suite one final time**

```bash
cd /f/ObsidianAI/conclave && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q
```

Expected: PASS. Report the final count against the Task 1 baseline.

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md
git commit -m "docs: flagging and post expiry, including their honest limits"
```

---

## Done criteria

- [ ] Suite green; final count recorded against the Task 1 baseline
- [ ] `grep -rn "answer_flags\|corpus_flags" app/` shows both tables are now written, not just created
- [ ] `grep -n "NOT EXISTS" app/services/post_expiry.py` returns a hit — `NOT IN` here means nothing ever expires
- [ ] `grep -n "<> ALL" app/services/post_expiry.py` returns a hit — `NOT IN` there means NULL-category posts never expire
- [ ] `POST_EXPIRY_ENABLED` defaults to `false` and `_worker_task` stays `None` when it is
- [ ] **No `migrations/021_*.sql` was created** — 2.7a's `019` already provided every table and column
- [ ] Nothing pushed to Gitea — the maintainer confirms every push

## Deliberately NOT in this plan

- **Supersession** (a new answer formally replacing an old one, chain kept) — a bigger design; invalidate-plus-new-entry covers the practical case.
- **Time-based staleness decay and re-validation sweeps** — the right long-term answer to knowledge rot, and a milestone of its own.
- **Dashboard corpus/flag browser** — Phase 3.5.
- **Reworking the promotion gate** — the dual-signal gate stays exactly as built.
- **Softening expiry to an archive** — with the feature off by default nobody loses data by accident, and a feature that claims to delete but does not is worse.
