# R2 + R3 — Production Safety Hardening (Design)

- **Date:** 2026-06-30
- **Findings:** R2 (🔴 — safety controls ship OFF by default) + R3 (🔴 — admin compromise via default key + 0.0.0.0 dashboard) in the Conclave beta-readiness scorecard. Wave 0 of the path-to-GO.
- **Repos touched:** `conclave`, `conclave-dashboard`
- **Status:** design approved (brainstorm), pending implementation plan
- **Related:** `[[conclave-beta-readiness-scorecard]]` · `[[conclave-path-to-go]]` (Wave 0) · LR-01 (constant-time admin compare, already done) · R6 (provisioning — owns the actual secret values + live `.env`)

---

## 1. Problem (verified in code)

**R2 — controls ship OFF by default.** In `app/config.py`: `moderation_gate_enabled=False`, `rate_limit_enabled=False`, `anthropic_api_key=""`, `ollama_base_url=""`. There is **no `environment` concept**, so nothing prevents a production deploy from booting with the entire trust-and-safety story dark. The fix can't be "edit the prod `.env`" because that file lives on a server not yet provisioned (R6) — the durable fix is code that **refuses to boot unsafe in production**.

**R3 — admin surface compromise.** `admin_api_key="dev-admin-key"` default (`config.py:35`). `conclave-dashboard` has **no `.streamlit/config.toml`**, so Streamlit binds `0.0.0.0`; `api_client.py` reads `CONCLAVE_API_URL` (default `http://localhost:8000`) and sends `Authorization: Admin <key>` on every call — a non-localhost `http://` base would leak the admin key in cleartext. Constant-time key compare is **already fixed** (LR-01, `auth.py`).

## 2. Decisions (locked in brainstorm 2026-06-30)

1. **R2 mechanism:** a **production preflight guard** — an `environment` setting; when `production`, the app refuses to boot unless the safety controls are correctly set. (Chosen over flipping global defaults, which would break the 390-test suite, and over docs-only, which leaves an unsafe boot possible.)
2. **Scope:** two repos (`conclave` preflight + tests; `conclave-dashboard` binding + http-base guard). Actual secret **values** and the live `.env` are **deferred to R6 provisioning** — we deliver the guard, tests, `.env.example`, and dashboard config now.
3. **Vote-eligibility bar** (`vote_eligibility_min_days`/`min_answers`) is a **tuning knob, not a preflight requirement** — at a ~10-stranger cold-start beta, requiring it would lock out early voters. It's anti-brigading tuning that scales with network maturity, not a safety floor. Documented in `.env.example`, not enforced.

## 3. Architecture

All changes are at the config / startup / network layer. No request-handling logic changes except the new boot check. The unifying idea: **make an unsafe production boot impossible and loud, instead of relying on operator memory.**

---

## 4. conclave — production preflight + enforcement tests

### 4.1 New setting
Add to `Settings` (`app/config.py`):
```python
environment: str = "dev"   # env var ENVIRONMENT; "dev" | "production"
```
Dev is the default → tests and local dev are unaffected.

### 4.2 New module `app/services/preflight.py`
```python
def assert_production_safety(settings) -> None:
    """No-op unless settings.environment == 'production'. In production, collect
    ALL failures and raise RuntimeError listing each (fail-fast, fail-loud)."""
```
- **Hard (refuse boot in production):**
  - `admin_api_key` not `"dev-admin-key"` and non-empty
  - `moderation_gate_enabled is True`
  - `rate_limit_enabled is True`
  - `anthropic_api_key` non-empty (primary Haiku gate dependency)
- **Soft (`logger.warning`, still boots):**
  - `telegram_alerts_enabled` (monitoring — unobserved ≠ unsafe)
  - `ollama_base_url` (secondary consensus gate)
- Collects **all** hard failures into one `RuntimeError` message (not first-only), so a misconfigured deploy sees the full list at once.

### 4.3 Wiring
Call `assert_production_safety(settings)` at the **top of `lifespan`** in `app/main.py` — before `init_pool()` and the workers — so a misconfigured prod dies immediately with a clear message and never serves a request.

### 4.4 Tests
- `tests/test_preflight.py`:
  - `environment="dev"` → no-op regardless of other settings.
  - `environment="production"` + all hard controls set → passes (no raise).
  - `environment="production"` with each hard control missing (default admin key / gate off / rate-limit off / no anthropic key) → `RuntimeError` whose message names the failure(s). Parametrized; also a test that multiple failures are all listed.
  - Soft-miss (telegram/ollama unset) in production → boots (no raise), warning emitted.
- **Enforcement proof (R2 "not a mocked PASS"):** before writing new tests, audit existing `tests/test_moderation_integration.py` and `tests/test_rate_limit_integration.py`. Ensure coverage exists (add only the gaps) for:
  - gate ON (`moderation_gate_enabled=True`) + `_call_gate_model` mocked to return **BLOCK** for a rule-violating post → the post is stored **suppressed/held**, not live (real enforcement wiring; only the model *response* is mocked).
  - rate-limit ON (`rate_limit_enabled=True`) + a burst past the tier → **429** with correct headers.

### 4.5 `.env.example`
Add `ENVIRONMENT=production`, the four required prod vars (with guidance), the recommended soft vars (telegram/ollama), and a comment that vote-eligibility is an optional tuning knob (not preflight-required). No real secret values — placeholders only.

---

## 5. conclave-dashboard — shrink the admin surface

### 5.1 `.streamlit/config.toml` (new)
```toml
[server]
address = "127.0.0.1"
headless = true
enableCORS = false
enableXsrfProtection = true
```
Binds the admin UI to localhost (not `0.0.0.0`); access via SSH tunnel.

### 5.2 `api_client.py` — reject cleartext-over-network base
Extract `_validate_api_base(url) -> None`, run at module load:
- Allow `http://localhost…` / `http://127.0.0.1…` (SSH-tunnel / local).
- Allow any `https://…`.
- **Raise** (clear message) on any other `http://` host — refuses to start if pointed at a remote cleartext box, closing "admin key sent in cleartext."

### 5.3 `.env.example` + README note
Document SSH-tunnel access (`ssh -L 8503:127.0.0.1:8503 <host>`) and that `CONCLAVE_ADMIN_KEY` / `CONCLAVE_API_URL` come from env, never committed.

### 5.4 Tests
Dashboard has no test suite today. Add a minimal `tests/test_api_client.py` covering `_validate_api_base`: localhost-http OK, https OK, remote-http raises. Add `pytest` to dev requirements if needed; no heavy harness.

---

## 6. Error handling

- Preflight raises `RuntimeError` listing **all** hard failures (fail-fast, fail-loud). Dev/test never trip it (`environment="dev"`).
- Dashboard `_validate_api_base` raises at import with a clear message.
- Both are boot-time guards — they cannot affect a running request path.

## 7. Out of scope (YAGNI / R6 ops)

- Actual prod secret **values** + the live `.env` on the box → R6 provisioning.
- HTTPS termination / reverse-proxy setup → R6 ops.
- Vote-eligibility **values** → operator tuning.
- Flipping global config defaults to ON → rejected (breaks the 390-test suite; would force Anthropic/Ollama in dev).

## 8. Definition of done

- `environment=production` boot **refuses to start** (clear, complete error) when any hard control is misconfigured; boots (with warnings) when soft controls are unset; dev/test unaffected — proven by `test_preflight.py`.
- Enforcement is demonstrated, not mocked-PASS: a gate-ON rule-violating post is suppressed; a rate-limited burst returns 429 (existing tests verified/extended).
- Dashboard binds `127.0.0.1` and refuses a non-localhost `http://` API base — proven by config + `test_api_client.py`.
- Full conclave suite stays green; scorecard R2 + R3 updated to fixed-with-evidence (config/dashboard parts), noting the live-`.env`/secret-value parts remain for R6.

## 9. Connections
- `[[conclave-beta-readiness-scorecard]]` — R2 + R3 rows (the gate this clears two more 🔴 from)
- `[[conclave-path-to-go]]` — Wave 0
- `2026-06-30-r1-injection-isolation-design.md` — the prior Wave 1 item (R1, done)
