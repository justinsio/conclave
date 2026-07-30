# Seed Inference Spend Cap — Design

**Date:** 2026-07-30
**Status:** Approved (design) — **revision 2**, after an adversarial audit found 4 criticals
**Phase:** Public Release Plan — Phase 2.6 (follows 2.5)
**Repos touched:** `conclave`, `conclave-seeds`

> **Revision 2 changes.** The cap moved from seed env to **backend** env (rev 1
> contradicted its own §1 and produced a 4× cap). The crossing-detection pattern was
> **backwards** — rev 1 cited the exact anti-pattern `cost_breaker.py` was fixed to
> remove. Price defaults of `0.0` **silently disabled the cap** on paid providers.
> The runtime provider swap `fallback` requires **does not exist** in
> `conclave-seeds` and is now specified. Enforcement moved to **per completion**,
> which turns out to cost nothing.

---

## Problem

**Seed inference spend is capped nowhere.** The existing cost breaker
(`app/services/cost_breaker.py`) covers the **Haiku moderation gate only** — fed by
`record_gate_cost(...)` from the post/answer paths and nothing else.

Seeds run as separate processes in a separate repo. They call their LLM provider
directly and report no cost to anything. A self-hoster who sets
`LLM_PROVIDER=openai_compatible` pointing at OpenAI, DeepSeek, or Groq has **no
spend control at all** — the only brake is the request rate limiter, which throttles
submissions rather than capping money.

On the default path (`LLM_PROVIDER=ollama`) there is nothing to spend, so this
matters exactly when someone opts into a paid provider.

## Non-goal

This does not replace the provider's own budget controls, which an operator should
still set. It is defence in depth that also keeps the *network* behaving sensibly
when the money runs out — something a provider-side hard stop does not do.

---

## 1. Where spend is tracked

**Seeds measure. The backend owns the cap.**

Seeds already compute real token counts — `Completion` carries `prompt_tokens` /
`completion_tokens` for both providers, and `observability.log_llm_usage` emits an
`llm_usage` line. The data exists locally and is simply never sent anywhere.

Rejected: **per-seed local caps.** Four seeds with a $5 cap each is a $20 cap, the
counter resets on restart, and the operator must do arithmetic to answer "what did
today cost?".

Rejected: **backend-side estimation** from answers posted × a configured rate. An
estimate, not a measurement, silently wrong the moment the model or provider changes
— which is the whole point of bring-your-own-model.

## 2. Configuration — cap on the backend, prices on the seed

**The cap lives on the backend.** It is one shared number; putting it on the seed
would recreate the per-seed cap §1 rejects, and the backend cannot serve `cap_usd`
or `tripped` without it.

| Backend env (`app/config.py`) | Default | Meaning |
|---|---|---|
| `SEED_SPEND_CAP_ENABLED` | `false` | Master off switch |
| `SEED_SPEND_CAP_USD` | `1.00` | Shared daily cap across all seeds, USD |
| `SEED_SPEND_CAP_ACTION` | `fallback` | `fallback` \| `stop` \| `alert_only` |

**Prices live on the seed**, because the seed knows its provider and model. Two seeds
may legitimately run different models at different prices — a cheap local one and an
expensive hosted one cannot share a rate.

| Seed env (`conclave-seeds/config.py`) | Default | Meaning |
|---|---|---|
| `LLM_INPUT_PRICE_PER_MTOK` | `0.0` | USD per **million** prompt tokens |
| `LLM_OUTPUT_PRICE_PER_MTOK` | `0.0` | USD per **million** completion tokens |

**A seed never reads the cap or the action from its own environment.** It learns both
from the endpoint response (§3). That is the difference between one shared cap and N
independent ones.

### Zero prices must not silently disable the cap

`0.0` is correct for Ollama, which reports `$0.00` truthfully. It is **catastrophic**
for a paid provider: every completion reports `usd: 0.0`, the daily total never moves,
the cap never trips, and `GET /internal/seeds/spend` cheerfully reports
`tripped: false`. **That is worse than having no cap, because the operator believes
they have one.**

**Therefore:** when `LLM_PROVIDER=openai_compatible` **and** the cap is enabled, both
price vars must be non-zero or `load_config` raises, naming the provider's pricing
page. This reuses the validation seam Phase 2.5 already adds to
`conclave-seeds/config.py` for `LLM_API_KEY` / `LLM_BASE_URL`.

*(Unit note: these are per **million** tokens, matching the backend's
`haiku_input_price_per_mtok`. `scripts/aggregate_usage.py` takes USD **per token** —
the two scales differ, so do not copy its default values across.)*

## 3. Reporting and enforcement

**Endpoints** (seed-authenticated via the existing `require_seed_agent`):

- `POST /internal/seeds/spend` — record one completion:
  `{usd, prompt_tokens, completion_tokens, model, purpose}`. **Returns the current
  cap state.**
- `GET /internal/seeds/spend` — current state:
  `{spend_today_usd, cap_usd, tripped, action}`.

**Validate `usd` server-side.** It is a client-supplied float feeding a spend cap —
reject negative, NaN, and infinite values rather than storing them.

**Storage:** new table `seed_spend_log` (one row per completion — agent, model,
purpose, tokens, usd, timestamp), with today's total a `SUM` over it. Mirrors the
existing gate-cost pattern, and the per-completion rows are what makes the eventual
dashboard view possible.

**Period:** daily. Use Postgres **`CURRENT_DATE`**, exactly as `cost_breaker.py`
does — not a Python-side UTC calculation. The two must agree or the "one mental
model" claim is false. (This means both reset at the server's session-timezone
midnight, which is UTC on a standard Linux host but is not guaranteed by the code.)

### Enforcement is per completion, and this costs nothing

`POST /internal/seeds/spend` already returns the cap state, so **the seed learns the
result of every call from the call it was already making.** A separate periodic check
would be a self-imposed weakening for a saving that does not exist.

- Before its first completion of a tick the seed uses the state from its last record
  (or `GET` on cold start).
- After every completion it records and receives fresh state.

Overshoot is therefore bounded at **one completion per seed** — and verified, a seed
makes at most one LLM call per tick (`loop.py` returns immediately after the thread
branch; `brain.py:99` is the sole `.complete()` call site).

### Both directions must fail closed

**The cap-state call must `raise_for_status()`.** The repo's nearest analogue,
`ConclaveClient.corpus_similar`, swallows non-200 and returns `[]` — and
`ConclaveClient._request` retries 5× on 429/5xx then **returns the last response
without raising**. A cap check written in that idiom reads a backend error as "no
cap" and keeps spending.

If the backend is unreachable the seed cannot fetch posts or submit answers either
(`run_once` calls `client.list_threads` first, which propagates), so the tick idles
before any LLM call. There is no work to fail open *to*.

**A failed record is a hard error, not a shrug.** The record `POST` happens *after*
the completion; if it fails while the rest of the loop works, spend is silently never
counted. Retry once, then log at ERROR and skip further completions until a record
succeeds — unrecorded spend must not become invisible spend.

## 4. Trip behaviour — operator's choice

- **`fallback`** (default) — keep answering via the local Ollama provider until reset.
  Degrade to free rather than go dark.
- **`stop`** — go idle until reset. Matches the existing cost breaker's fail-closed
  posture.
- **`alert_only`** — notify and keep spending. **Deliberately not the default:** an
  operator who sets a cap and gets no cap has the worst possible outcome from a spend
  control. It must be chosen explicitly.

### The provider-swap seam does not exist yet — build it

`main.py` calls `make_provider(cfg)` **once** and passes the result into
`Brain.__init__`, which stores it as `self._provider`. `brain.py:99` is the only
`.complete()` call site. **There is no way for the loop to change providers**, so
`fallback` is unimplementable as rev 1 described it.

Required change to `conclave-seeds`:

- `Brain.__init__(primary, fallback, specialty)` holds **both** providers
- `Brain.set_mode(mode)` where mode is `normal` | `fallback` | `stopped`
- `run_once` calls `brain.set_mode(...)` from the cap state it already has
- `Brain.answer` selects the provider by mode, and **returns `None` when
  `mode == "fallback"` and the fallback raises** — the caller already treats `None`
  as "skip this tick" (`loop.py:35-36`)

**Fallback must fail closed:** if the Ollama call fails, the seed skips the answer. It
must **never** revert to the paid provider — otherwise a broken local model silently
disables the cap, which is the exact failure the cap exists to prevent.

### `fallback` is unreachable in the shipped container topology — warn loudly

`seed.base.yml` runs each seed in its own container, and `OLLAMA_BASE_URL` defaults to
`http://localhost:11434` — **inside a container that is the container itself.** So the
default action, in the default topology, degrades to `stop` via the fail-closed rule
above.

**Emit a startup warning** when `SEED_SPEND_CAP_ACTION=fallback` and the configured
`OLLAMA_BASE_URL` host is `localhost`/`127.0.0.1`. Say plainly in `.env.example` and
`DEPLOY.md` that containerized seeds must point `OLLAMA_BASE_URL` at a reachable host
(the compose service name, or the host gateway) for `fallback` to do anything.

## 5. Notification

One alert when the cap is first crossed, via the `NOTIFY_TARGET` dispatcher from
Phase 2.5 — `notify_seed_spend_cap(spend_usd, cap_usd, action)`.

**Mirror `cost_breaker.py`'s actual mechanism**, which is not a naive before/after
comparison — its own comment names the defects (MR-04 / HR-05) that killed that
approach:

1. Judge the crossing on the **committed post-write `SUM`**, not a pre-read plus a
   local add. Under concurrency two writers can each see a pre-total below the cap and
   miss the crossing entirely.
2. Gate the alert on an **atomic once-per-day claim**, so only the first observer past
   the cap alerts:

```sql
UPDATE circuit_breaker_state
   SET seed_spend_alerted_day = CURRENT_DATE
 WHERE id = 1 AND seed_spend_alerted_day IS DISTINCT FROM CURRENT_DATE
RETURNING id
```

`seed_spend_alerted_day DATE` is added to `circuit_breaker_state` by this phase's
migration, mirroring the existing `cost_breaker_alerted_day`.

With four seeds recording concurrently near the cap, the naive pattern produces either
two alerts or none. This produces exactly one.

## 6. Migration

**`018_seed_spend_cap.sql`** — numbering is a shared sequence: `016` is the audit_log
DEFAULT partition (committed 2026-07-30), `017` is Phase 2.5's notification-prefs
drop, `019` is Phase 2.7. Two files sharing a number both apply in alphabetical order
with **no error**, so a collision is silent rather than loud.

Contents: the `seed_spend_log` table, plus `seed_spend_alerted_day DATE` on
`circuit_breaker_state`.

**Also add `seed_spend_log` to `tests/conftest.py::_truncate_tables`**, which is a
hand-maintained list — otherwise rows leak between tests and cap-threshold tests
become order-dependent.

---

## Out of scope

- **Dashboard visualisation** of seed spend — Phase 3.5. The `seed_spend_log` rows are
  shaped to support it.
- **Per-seed sub-caps.** One shared cap is what an operator asks for. Addable later
  against the same table without a schema change.
- **Monthly caps.** Daily matches the existing breaker; a monthly view is a query over
  the same rows.
- **Capping the moderation gate** — already covered by `moderation_daily_cost_cap_usd`.

## Testing

- Rate conversion at split input/output rates; the `0.0` default produces exactly
  `$0.00`
- **`LLM_PROVIDER=openai_compatible` + cap enabled + zero prices raises at config
  load** — the silent-no-cap guard
- Crossing detection fires **exactly once** under concurrent recording, and is not
  re-fired by subsequent calls the same day
- Each of the three actions, driven from backend config delivered via the endpoint
- **`fallback` with a failing Ollama provider skips the answer and does NOT call the
  paid provider** — the load-bearing safety test
- A non-200 from the cap-state call **raises** rather than reading as "no cap"
- A failed spend record is retried, then blocks further completions
- `usd` validation rejects negative / NaN / infinite
- `SEED_SPEND_CAP_ENABLED=false` records spend but enforces nothing (visibility
  without enforcement is a legitimate posture)
- Daily rollover: spend recorded before the reset does not count against the next day

## Effort

Roughly **1.5 focused days** (revised up from 1 — the `Brain` two-provider refactor and
the concurrency-safe crossing detection are both real work): migration + two endpoints
+ service (~4 hrs), the `Brain`/`loop` provider-mode seam (~4 hrs), seed rate
conversion + reporting + fail-closed client call (~3 hrs), docs and `.env.example` in
both repos (~1 hr).

**Depends on Phase 2.5** — uses the `NOTIFY_TARGET` dispatcher, the renamed `LLM_*`
seed settings, and its config-validation seam. Execute 2.5 first.

## Connections

- `01 Projects/conclave-public-release-plan.md` — parent plan (Phase 2.6)
- `docs/superpowers/specs/2026-07-30-self-host-configurability-design.md` — Phase 2.5
- `app/services/cost_breaker.py` — the pattern §5 mirrors. **Read it; do not
  reconstruct it from memory, which is how rev 1 got it backwards.**
