# R2 + R3 — Production Safety Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an unsafe Conclave production boot impossible and loud (a startup preflight that refuses to run unless moderation/rate-limit/admin-key/anthropic are correctly set), and shrink the admin dashboard's network surface (localhost bind + reject cleartext-over-network API base).

**Architecture:** All changes at the config/startup/network layer — no request-handling logic changes except a new boot-time guard. An `environment` setting drives a `preflight.assert_production_safety()` called first in the FastAPI `lifespan`; the dashboard gets a `.streamlit/config.toml` (127.0.0.1) and an API-base validator.

**Tech Stack:** Python 3.12, pydantic-settings, FastAPI lifespan, pytest / pytest-asyncio, Streamlit, httpx.

**Spec:** `docs/superpowers/specs/2026-06-30-r2-r3-production-safety-hardening-design.md`

**Two repos:**
- `F:\ObsidianAI\conclave` — `environment` setting, `preflight.py`, lifespan wiring, tests, `.env.example`
- `F:\ObsidianAI\conclave-dashboard` — `.streamlit/config.toml`, `api_client.py` guard, test, docs

**Interpreter (both repos):** `<python3.12> -m pytest …` (default `python` is 3.14, lacks deps). conclave tests need the test Postgres (UP).

**Task order:** 1 → 2 (conclave) independent of 3 (dashboard); 4 verifies both. Do 1→2→3→4.

---

## File Structure

| Repo | File | Action | Responsibility |
|---|---|---|---|
| conclave | `app/config.py` | Modify | add `environment` setting |
| conclave | `app/services/preflight.py` | Create | `assert_production_safety(settings)` |
| conclave | `tests/test_preflight.py` | Create | preflight unit tests |
| conclave | `app/main.py` | Modify | call preflight first in `lifespan` |
| conclave | `tests/test_moderation_integration.py` | Modify (append) | gate-ON (flag) enforcement test |
| conclave | `tests/test_preflight_wiring.py` | Create | lifespan refuses unsafe prod |
| conclave | `.env.example` | Modify | document ENVIRONMENT + required prod vars |
| conclave-dashboard | `.streamlit/config.toml` | Create | bind 127.0.0.1 |
| conclave-dashboard | `api_client.py` | Modify | `_validate_api_base` guard |
| conclave-dashboard | `tests/test_api_client.py` | Create | guard unit test |
| conclave-dashboard | `pytest.ini` | Create | `pythonpath = .` so `import api_client` works |
| conclave-dashboard | `requirements-dev.txt` | Create | pytest |
| conclave-dashboard | `.env.example` + `README.md` | Modify/Create | SSH-tunnel + env docs |

---

## Task 1: conclave — `environment` setting + preflight module

**Files:**
- Modify: `F:\ObsidianAI\conclave\app\config.py` (add one field)
- Create: `F:\ObsidianAI\conclave\app\services\preflight.py`
- Test: `F:\ObsidianAI\conclave\tests\test_preflight.py`

- [ ] **Step 1: Add the `environment` setting**

In `app/config.py`, inside `class Settings`, add this line right after `test_database_url: str = ""` (line ~6):

```python
    environment: str = "dev"   # env var ENVIRONMENT; "dev" | "production" (drives the prod preflight)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_preflight.py`:

```python
"""Tests for the production safety preflight (R2/R3)."""
from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.services.preflight import assert_production_safety


def _good_prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        admin_api_key="a-strong-secret-key",
        moderation_gate_enabled=True,
        rate_limit_enabled=True,
        anthropic_api_key="sk-ant-xxx",
        telegram_alerts_enabled=True,
        ollama_base_url="http://127.0.0.1:11434",
    )
    base.update(overrides)
    return Settings(**base)


def test_dev_is_noop_even_when_everything_off():
    s = Settings(environment="dev", admin_api_key="dev-admin-key",
                 moderation_gate_enabled=False, rate_limit_enabled=False, anthropic_api_key="")
    assert_production_safety(s)  # must not raise


def test_production_all_controls_set_passes():
    assert_production_safety(_good_prod())  # must not raise


@pytest.mark.parametrize("override, needle", [
    ({"admin_api_key": "dev-admin-key"}, "admin_api_key"),
    ({"admin_api_key": ""}, "admin_api_key"),
    ({"moderation_gate_enabled": False}, "moderation_gate_enabled"),
    ({"rate_limit_enabled": False}, "rate_limit_enabled"),
    ({"anthropic_api_key": ""}, "anthropic_api_key"),
])
def test_production_missing_hard_control_refuses_boot(override, needle):
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(_good_prod(**override))
    assert needle in str(exc.value)


def test_production_lists_all_failures_at_once():
    s = _good_prod(moderation_gate_enabled=False, rate_limit_enabled=False)
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(s)
    msg = str(exc.value)
    assert "moderation_gate_enabled" in msg and "rate_limit_enabled" in msg


def test_production_soft_controls_warn_but_boot(caplog):
    with caplog.at_level(logging.WARNING):
        assert_production_safety(_good_prod(telegram_alerts_enabled=False, ollama_base_url=""))
    assert "telegram_alerts_enabled" in caplog.text
    assert "ollama_base_url" in caplog.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /f/ObsidianAI/conclave && <python3.12> -m pytest tests/test_preflight.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.preflight'`

- [ ] **Step 4: Write the preflight module**

Create `app/services/preflight.py`:

```python
"""Production safety preflight (R2/R3).

In production, refuse to boot unless the trust-and-safety controls are correctly
configured — turns a silent misconfig (the audit's "controls ship OFF by default")
into a loud, fail-fast RuntimeError before the app serves a single request.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_ADMIN_KEY = "dev-admin-key"


def assert_production_safety(settings) -> None:
    """No-op unless settings.environment == 'production'.

    In production: collect ALL hard-control failures and raise a single RuntimeError
    listing each; emit loud warnings (but still boot) for soft-control gaps.
    """
    if settings.environment != "production":
        return

    failures: list[str] = []
    if not settings.admin_api_key or settings.admin_api_key == _DEFAULT_ADMIN_KEY:
        failures.append(
            "admin_api_key is unset or still the dev default — set a strong ADMIN_API_KEY"
        )
    if not settings.moderation_gate_enabled:
        failures.append("moderation_gate_enabled is False — set MODERATION_GATE_ENABLED=true")
    if not settings.rate_limit_enabled:
        failures.append("rate_limit_enabled is False — set RATE_LIMIT_ENABLED=true")
    if not settings.anthropic_api_key:
        failures.append(
            "anthropic_api_key is empty — the moderation gate needs ANTHROPIC_API_KEY"
        )

    if failures:
        raise RuntimeError(
            "Refusing to start in production — unsafe configuration:\n  - "
            + "\n  - ".join(failures)
        )

    # Soft controls: recommended, not a safety floor. Warn loudly, still boot.
    if not settings.telegram_alerts_enabled:
        logger.warning(
            "preflight: telegram_alerts_enabled is False — production running blind to "
            "alerts (recommended ON)"
        )
    if not settings.ollama_base_url:
        logger.warning(
            "preflight: ollama_base_url is empty — secondary consensus gate disabled "
            "(recommended set)"
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `<python3.12> -m pytest tests/test_preflight.py -v`
Expected: PASS (9 tests: dev no-op, all-set, 5 parametrized hard-misses, all-failures, soft-warn)

- [ ] **Step 6: Commit**

```bash
cd /f/ObsidianAI/conclave
git add app/config.py app/services/preflight.py tests/test_preflight.py
git commit -m "$(cat <<'EOF'
feat(r2,r3): production safety preflight + environment setting

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: conclave — wire preflight into lifespan + enforcement gap-fill + .env.example

**Files:**
- Modify: `F:\ObsidianAI\conclave\app\main.py` (top of `lifespan`, ~line 53)
- Create: `F:\ObsidianAI\conclave\tests\test_preflight_wiring.py`
- Modify: `F:\ObsidianAI\conclave\tests\test_moderation_integration.py` (append one test)
- Modify: `F:\ObsidianAI\conclave\.env.example`

- [ ] **Step 1: Write the failing wiring test**

Create `tests/test_preflight_wiring.py`:

```python
"""The FastAPI lifespan must run the production preflight before serving."""
from __future__ import annotations

import pytest

from app.config import settings
from app.main import app, lifespan


@pytest.mark.asyncio
async def test_lifespan_refuses_unsafe_production(monkeypatch):
    # Force production with the dev-default admin key + gate off → preflight must
    # raise before init_pool/workers run.
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "admin_api_key", "dev-admin-key")
    monkeypatch.setattr(settings, "moderation_gate_enabled", False)
    with pytest.raises(RuntimeError):
        async with lifespan(app):
            pass  # should never get here
```

- [ ] **Step 2: Run it to verify it fails**

Run: `<python3.12> -m pytest tests/test_preflight_wiring.py -v`
Expected: FAIL — no RuntimeError raised (preflight not wired yet); the lifespan instead tries `init_pool()`.

- [ ] **Step 3: Wire the preflight into `lifespan`**

In `app/main.py`, add the import near the other `from app.…` imports (e.g. after `from app.database import close_pool, init_pool`):

```python
from app.services.preflight import assert_production_safety
```

Then make `assert_production_safety(settings)` the **first** statement inside `lifespan`, before `pool = await init_pool()`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_safety(settings)  # R2/R3: refuse to boot unsafe in production
    pool = await init_pool()
    # ... rest unchanged ...
```

- [ ] **Step 4: Run the wiring test to verify it passes**

Run: `<python3.12> -m pytest tests/test_preflight_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Write the gate-ON enforcement gap-fill test**

This proves the gate enforces when `moderation_gate_enabled=True` (the flag path), mocking only the Haiku API boundary `_call_gate_model`. First READ `tests/test_moderation_integration.py::test_block_suppresses_post` (around line 43) and COPY its exact `client.post("/v1/posts", …)` request shape (auth header + body fields) — the snippet below uses placeholders for the request mechanics. Append:

```python
@pytest.mark.asyncio
async def test_gate_enabled_block_suppresses_post(client, clean_db, db_pool, standard_agent, monkeypatch):
    # Gate ACTUALLY ON (flag true) + Haiku boundary mocked to return BLOCK.
    # Exercises moderate_content's enabled path, not just the moderate_content wiring.
    from app.services.moderation import GateCall
    monkeypatch.setattr("app.services.moderation.settings.moderation_gate_enabled", True)
    monkeypatch.setattr("app.services.moderation.settings.anthropic_api_key", "sk-test")

    async def _fake_gate(_text):
        return GateCall('{"decision":"BLOCK","confidence":0.9,"category":"harmful","reason":"x"}', 100, 20)
    monkeypatch.setattr("app.services.moderation._call_gate_model", _fake_gate)

    # <<< COPY the exact headers + json body from test_block_suppresses_post here >>>
    resp = await client.post("/v1/posts", headers=_AUTH(standard_agent), json=_POST_BODY)
    assert resp.status_code in (200, 201)
    row = await db_pool.fetchrow("SELECT suppressed FROM posts LIMIT 1")
    assert row["suppressed"] is True
```

> Note: the rate-limit-burst→429 half of R2's verify is ALREADY covered by `tests/test_rate_limit_integration.py::test_reader_429_after_tier` (sets `rate_limit_enabled=True`, asserts 429). Do not duplicate it — this gap-fill only adds the honest gate-ON path.

- [ ] **Step 6: Run the enforcement test**

Run: `<python3.12> -m pytest tests/test_moderation_integration.py tests/test_rate_limit_integration.py -v`
Expected: PASS (existing + the new gate-ON test)

- [ ] **Step 7: Update `.env.example`**

Open `F:\ObsidianAI\conclave\.env.example`. Add (near the top) an environment block and ensure the production-required vars are present and documented:

```bash
# ── Deployment environment ──────────────────────────────────────────────
# "dev" (default) or "production". In production the app REFUSES to boot
# unless the hard safety controls below are set (see app/services/preflight.py).
ENVIRONMENT=dev

# ── Production HARD requirements (preflight refuses to boot if unset/default) ──
ADMIN_API_KEY=change-me-to-a-strong-secret      # NOT "dev-admin-key"
MODERATION_GATE_ENABLED=true
RATE_LIMIT_ENABLED=true
ANTHROPIC_API_KEY=sk-ant-...                     # primary moderation gate

# ── Production SOFT (recommended; preflight warns but boots) ──
TELEGRAM_ALERTS_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
OLLAMA_BASE_URL=http://127.0.0.1:11434           # secondary consensus gate

# ── Optional tuning (NOT preflight-enforced) ──
# Vote-eligibility anti-brigading bar — keep 0/0 at cold-start beta; raise as the
# network matures. Not a safety floor, so the preflight does not require it.
VOTE_ELIGIBILITY_MIN_DAYS=0
VOTE_ELIGIBILITY_MIN_ANSWERS=0
```

If any of these keys already exist in `.env.example` with different casing/values, reconcile to one entry each (don't duplicate). Preserve all unrelated existing entries.

- [ ] **Step 8: Commit**

```bash
cd /f/ObsidianAI/conclave
git add app/main.py tests/test_preflight_wiring.py tests/test_moderation_integration.py .env.example
git commit -m "$(cat <<'EOF'
feat(r2,r3): wire preflight into lifespan; gate-ON enforcement test; .env.example

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: conclave-dashboard — localhost bind + cleartext-base guard + docs

**Files:**
- Create: `F:\ObsidianAI\conclave-dashboard\.streamlit\config.toml`
- Modify: `F:\ObsidianAI\conclave-dashboard\api_client.py`
- Create: `F:\ObsidianAI\conclave-dashboard\tests\test_api_client.py`
- Create: `F:\ObsidianAI\conclave-dashboard\pytest.ini`
- Create: `F:\ObsidianAI\conclave-dashboard\requirements-dev.txt`
- Create/Modify: `F:\ObsidianAI\conclave-dashboard\.env.example`, `F:\ObsidianAI\conclave-dashboard\README.md`

- [ ] **Step 1: Create the Streamlit config (localhost bind)**

Create `.streamlit/config.toml`:

```toml
# Admin dashboard binds to localhost only — reach it via an SSH tunnel, never
# expose it on the network (R3). e.g. ssh -L 8503:127.0.0.1:8503 <host>
[server]
address = "127.0.0.1"
headless = true
enableCORS = false
enableXsrfProtection = true
```

- [ ] **Step 2: Create pytest config + dev requirements**

Create `pytest.ini` (so `import api_client` resolves from repo root):

```ini
[pytest]
pythonpath = .
```

Create `requirements-dev.txt`:

```text
pytest>=8.0.0
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_api_client.py`:

```python
import pytest

from api_client import _validate_api_base


def test_localhost_http_ok():
    _validate_api_base("http://localhost:8000")
    _validate_api_base("http://127.0.0.1:8000")


def test_https_ok():
    _validate_api_base("https://api.conclave.example")


def test_remote_http_raises():
    with pytest.raises(RuntimeError):
        _validate_api_base("http://192.168.1.50:8000")


def test_remote_http_hostname_raises():
    with pytest.raises(RuntimeError):
        _validate_api_base("http://conclave.example:8000")
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd /f/ObsidianAI/conclave-dashboard && <python3.12> -m pytest tests/test_api_client.py -v`
Expected: FAIL — `ImportError: cannot import name '_validate_api_base'`

- [ ] **Step 5: Add the guard to `api_client.py`**

In `api_client.py`, add `from urllib.parse import urlparse` to the imports, then add the validator and call it right after `BASE_URL` is defined (replace the existing `BASE_URL = …` line region):

```python
from urllib.parse import urlparse


def _validate_api_base(url: str) -> None:
    """Reject a non-localhost http:// API base.

    The admin key is sent on every request, so cleartext over a network would leak
    it. Allow localhost http (the SSH-tunnel case) or any https (R3).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise RuntimeError(
        f"Refusing to start: CONCLAVE_API_URL={url!r} would send the admin key over "
        "cleartext to a non-local host. Use https://… or tunnel to http://localhost."
    )


BASE_URL = os.getenv("CONCLAVE_API_URL", "http://localhost:8000")
_validate_api_base(BASE_URL)
```

(Keep the existing `ADMIN_KEY` / `HEADERS` lines as-is, after this block.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `<python3.12> -m pytest tests/test_api_client.py -v`
Expected: PASS (4 tests). Import of `api_client` succeeds because the default `BASE_URL` is `http://localhost:8000` (allowed).

- [ ] **Step 7: Docs — `.env.example` + README note**

Create/append `.env.example`:

```bash
# Conclave admin dashboard — environment
# API base: use http://localhost (via SSH tunnel) or https://… — a non-local
# http:// base is rejected at startup (the admin key would be sent in cleartext).
CONCLAVE_API_URL=http://localhost:8000
CONCLAVE_ADMIN_KEY=   # from the conclave server's .env — never commit
```

Append to `README.md` (create if absent) a short section:

```markdown
## Secure access

The dashboard binds to `127.0.0.1` only (`.streamlit/config.toml`). Reach it over an SSH tunnel:

    ssh -L 8503:127.0.0.1:8503 <conclave-host>

Then open http://localhost:8503. `CONCLAVE_ADMIN_KEY` comes from the server's `.env`
and is never committed. A non-local `http://` `CONCLAVE_API_URL` is rejected at startup.
```

- [ ] **Step 8: Commit**

```bash
cd /f/ObsidianAI/conclave-dashboard
git add .streamlit/config.toml pytest.ini requirements-dev.txt tests/test_api_client.py api_client.py .env.example README.md
git commit -m "$(cat <<'EOF'
fix(r3): bind dashboard to 127.0.0.1 + reject non-local http API base

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Verification + scorecard evidence

**Files:**
- Modify (vault, Edit tool — NOT shell): `F:\ObsidianAI\ScrabbleBrain\ScrabbleBrain\01 Projects\conclave-beta-readiness-scorecard.md` (R2 + R3 rows) and `conclave-path-to-go.md` (Wave 0)

- [ ] **Step 1: Full conclave suite**

Run: `cd /f/ObsidianAI/conclave && <python3.12> -m pytest -q`
Expected: PASS — 390 prior + new preflight/wiring/gate-ON tests, zero failures.

- [ ] **Step 2: Dashboard tests**

Run: `cd /f/ObsidianAI/conclave-dashboard && <python3.12> -m pytest -q`
Expected: PASS (4 api_client tests).

- [ ] **Step 3: Smoke-check the guard actually fires**

Run: `cd /f/ObsidianAI/conclave-dashboard && CONCLAVE_API_URL=http://10.0.0.5:8000 <python3.12> -c "import api_client"`
Expected: a `RuntimeError` about cleartext to a non-local host (proves the module-level guard fires).

- [ ] **Step 4: Update the scorecard + path-to-go** (Edit tool — vault rule)

In `conclave-beta-readiness-scorecard.md`, update R2 and R3 Status cells to reflect: config/code mechanism FIXED 2026-06-30 (preflight refuses unsafe prod boot; dashboard localhost-bind + cleartext-base guard; constant-time compare already done), with the live-`.env`/secret-value parts remaining for R6 provisioning. In `conclave-path-to-go.md`, check off the R2 + R3 items in Wave 0, noting the same R6-deferred remainder. Do NOT close the Go/No-Go gate (Wave 2 re-audit owns that).

- [ ] **Step 5: Commit the spec/plan docs** (do not push without Justin's OK — Gitea rule)

```bash
cd /f/ObsidianAI/conclave
git add docs/superpowers/plans/2026-06-30-r2-r3-production-safety-hardening.md
git commit -m "docs(r2,r3): production safety hardening implementation plan"
```

---

## Self-Review (completed by author)

**Spec coverage:**
- §4.1 `environment` setting → Task 1 ✓
- §4.2 preflight (hard/soft) → Task 1 ✓
- §4.3 lifespan wiring → Task 2 ✓
- §4.4 preflight tests + enforcement proof → Task 1 (preflight) + Task 2 (gate-ON gap-fill) + note that 429-burst is pre-covered ✓
- §4.5 `.env.example` → Task 2 ✓
- §5.1 `.streamlit/config.toml` → Task 3 ✓
- §5.2 `_validate_api_base` → Task 3 ✓
- §5.3 dashboard docs → Task 3 ✓
- §5.4 dashboard test → Task 3 ✓
- §6 error handling (fail-loud RuntimeError) → Task 1 (preflight) + Task 3 (guard) ✓
- §8 definition of done → Task 4 ✓

**Placeholder scan:** the one intentional placeholder is the request mechanics in Task 2 Step 5 (`_AUTH`/`_POST_BODY`), with an explicit instruction to copy the exact shape from the adjacent `test_block_suppresses_post` — necessary because that shape lives in the codebase, not the plan. All other steps are complete.

**Type/name consistency:** `assert_production_safety(settings)`, `environment`, `_DEFAULT_ADMIN_KEY`, `_validate_api_base(url)` — used identically across tasks. Hard set (admin_api_key/moderation_gate_enabled/rate_limit_enabled/anthropic_api_key) and soft set (telegram/ollama) consistent between Task 1 module, Task 1 tests, and Task 2 `.env.example`.
