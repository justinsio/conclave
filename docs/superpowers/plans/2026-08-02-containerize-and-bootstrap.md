# Containerize & Bootstrap Implementation Plan — **revision 4**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stranger clones this repository, edits one file, runs `docker compose up -d`, mints a key, and gets a working private Conclave instance — proven on a box that has never run it.

**Architecture:** A `db` + one-shot `migrate` + `api` core, with `seeds` and `dashboard` behind compose profiles. Migrations run in their own service that `api` waits on, mirroring the systemd `ExecStartPre`. Bootstrap is one CLI script plus the same logic behind a renamed HTTP endpoint.

**Tech Stack:** Docker Compose · Postgres 16 (pgcrypto) · Python 3.12 · FastAPI/uvicorn · Streamlit

---

> [!note] 📋 Revision 4 — written against the **third** audit. Not yet re-audited.
> **Audit history: rev 1 → 8 criticals · rev 2 → 4 · rev 3 → 2.** The trend is the point: each round is smaller, and **every round's defects were in the previous round's corrections**, not in the original text.
>
> - Rev 2's criticals: three rev-1 findings *discussed but not fixed*, plus a **security regression created by a fix** (shipping the admin key empty, when an empty key made `Authorization: Admin ` authenticate).
> - Rev 3's criticals: an `ENVIRONMENT=production` default that **would not boot**, because the decision was made without the `.env.example` line that makes it true; and a CI fix that **left CI red**, because `--env-file` does not satisfy `env_file:`.
>
> 🔑 **The standing lesson: a fix is unaudited code.** Rev 2 declared "every verification step must be able to fail" and shipped one that couldn't. Rev 3 wrote a `.dockerignore` probe with no build guard — the same defect, two paragraphs below its own warning against it. **A decision is not true until the file it depends on matches it, and a correction is not done until something can prove it.**
>
> SEC-1 was fixed in code (`fa0411c`) rather than carried in this plan. Baseline is now **579/65/4**.
>
> The rev-2 findings are preserved below; several are live constraints, not history.

> [!warning] 🛑 REV 2 findings — the record (all fixed in rev 3 unless marked)
> **Three of the eight rev-1 criticals are not actually closed, and one rev-2 "fix" created a worse hole than the defect it replaced.** A rev 3 is required. All four criticals below were independently re-verified.
>
> ### 🔴🔴 SEC-1 — a LIVE VULNERABILITY in shipped code, not a plan defect
> `app/auth.py:176-180`:
> ```python
> key = authorization.removeprefix("Admin ")
> if not secrets.compare_digest(key, settings.admin_api_key):
>     raise HTTPException(403, "Invalid admin key")
> ```
> **When `ADMIN_API_KEY` is empty, `secrets.compare_digest("", "")` returns `True`** — so the header `Authorization: Admin ` (trailing space, no key) is **full admin**: key minting, bans, kill switches. Verified by execution. The preflight cannot save you: `preflight.py:22` returns immediately unless `ENVIRONMENT=production`, and `.env.example:6` ships `dev`.
> **This exists today, independent of this plan.** Setting `ADMIN_API_KEY=` empty — a natural thing to do when you don't want a feature — silently opens the admin surface.
> ⚠️ **Rev 2's Task 0 Step 3 would have made this the DEFAULT for every self-hoster**, by shipping the key empty so `${VAR:?}` would fire. It doesn't fire — `ADMIN_API_KEY` reaches the app through `env_file`, not interpolation.
> **Fix (do this first, as its own change, before any Plan B work):** `require_admin` must reject an empty configured `admin_api_key` unconditionally in every environment, *before* the compare. Add `""` to the rejected-placeholder set. Only then consider shipping the value empty.
>
> ### 🔴 C-3 — Task 3 Step 1 reintroduces the exact defect C1 fixed
> Task 0 Step 1 declares one field (`postgres_password`); Task 3 Step 1 then adds `SEED_GENERAL_KEY` to `.env.example`. pydantic-settings **skips empty undeclared keys**, so it ships green and CI stays green — then breaks the instant an operator fills it in, which is the only reason the variable exists. `ValidationError … seed_general_key … Extra inputs are not permitted` → `import app.config` fails → the **api container** dies, plus every dev box and the systemd host. And the topology has **four** seeds, not one, plus any `CONCLAVE_ADMIN_KEY` bridge. **Fix:** declare every compose-only key in `Settings`, or move to `extra='ignore'` with a test that pins it — and the test must use a **non-empty** value or it passes vacuously.
>
> ### 🔴 C-4 — the TTL decision 500s on create and 404s on a successful extend
> The SQL analysis was right; it stops one layer too early. `BetaUserCreated.key_expires_at` is `datetime`, **not** `datetime | None` (`admin_beta_users.py:40`) — writing NULL raises an unhandled `ValidationError` → **500 on every mint under the new default**. And extend uses `None` as its row-not-found sentinel (`if new_expiry is None: raise HTTPException(404)`), so a **successful** extend of a never-expiring key returns `404 user_not_found` after modifying the row.
> ✏️ **My SQL citation was also wrong:** the extend site is `COALESCE(key_expires_at, NOW()) + make_interval(days => $2)`, not bare `make_interval` — a *different* trap from the one I described.
> ✏️ **And "expected 575/65/4 plus new tests" is false** — `tests/test_beta_accounts.py:93,144,167` and `tests/test_admin_audit_log.py:53,66,71` all assert an expiry exists. At least three break. *(575 was the baseline at the time; it is 579 after `fa0411c`.)*
>
> ### 🔴 C-2 — the ENVIRONMENT decision resolves 1 of 3 blockers and still names no value
> `preflight.py` hard-fails production on **five** things. The decision relaxed only `anthropic_api_key` and then claimed "the other production controls stay unconditional" while naming just two of the remaining four. Still blocking the same user: line 30 `moderation_gate_enabled` — which makes the *"private team runs with the gate off"* posture the decision explicitly endorses **impossible under `production`** — and line 38 `trusted_proxy_ips`, which a LAN self-hoster without a proxy does not have. **The plan still never states which `ENVIRONMENT` value ships**, yet Task 6 Step 2 tells the writer to document "whatever Task 0 Step 2 settles on." **Fix:** decide all five. `warn_self_host_posture` (`preflight.py:64`) already exists and is the right home for the gate check.
>
> **Also newly broken by rev 2:** Task 2 Step 3 and Task 8 Step 2 both run `docker compose config` on a tree with no `.env`, which now **always fails** because of Task 2 Step 1's `${POSTGRES_PASSWORD:?}` — CI would go permanently red. `db` is missing `POSTGRES_USER`/`POSTGRES_DB` that its own DSN requires, and bare `pg_isready` returns green anyway, so the healthcheck passes while `migrate` fails. Task 2 Step 6 is **still a check that cannot fail** (`apply_migrations.py` exits 0 on both branches). Task 6 Step 2's prescribed sentence "the dashboard has no auth and binds loopback" becomes **false** once Task 1 Step 4 mandates `--server.address=0.0.0.0` — it is then reachable unauthenticated from every container on the compose network, **including the seeds**.
>
> **What the audit confirmed as sound:** Task 0 Step 1's probe (real reproduction, real fix), Task 1 Step 2's rewritten image probe (all three cases distinguish correctly), the `${VAR:?}`-on-a-profiled-service diagnosis on v5.3.1, NULL-means-never-expires, `users.email NOT NULL UNIQUE`, the `@local.invalid` approach, the **exact eval figures** (re-scored offline: 1,370 verdicts, 0 egregious leaks, 0.0%, 1.8%, 100%), the 575/65/4 baseline, and every other file:line citation.

## Revision 2 — what changed and why

Revision 1 was audited cold and came back **not safe to execute**: 8 criticals. Every finding was independently re-verified before this rewrite. The headline defects:

| # | Defect in rev 1 | Status |
|---|---|---|
| C1 | Adding `POSTGRES_PASSWORD` to `.env` made `app.config` unimportable (`extra='forbid'`) — breaking every dev box and the production systemd host **while CI stayed green** | Fixed, Task 0 |
| C2 | The "operator fails closed" claim is **false** — preflight is a no-op unless `ENVIRONMENT=production`, and `.env.example` ships `dev` | Escalated to a design decision, Task 0 |
| C3 | Task 3 could not pass: dashboard rejects `http://api:8000` at import, binds container-loopback, and `${SEED_GENERAL_KEY:?}` aborted **every** compose command | Fixed, Task 3 |
| C4 | `env_file: .env` handed the backend's admin key and DB password to the seed and dashboard; no `.dockerignore` in either sub-context | Fixed, Tasks 1 & 3 |
| C5 | Placeholder credentials passed every guard | Fixed, Task 0 + Task 2 |
| C6 | **Three of four verification steps could not detect what they claimed** — including one that "proves migrations don't re-run" by running them | Fixed throughout |

🔑 **The lesson carried forward:** rev 1 shipped checks that pass when the thing is broken, which is the same class of mistake as Plan A's `git log -- <prefix>`. **Every verification step in this revision must be able to fail.** If you cannot describe the failure that makes a step print something different, the step is decoration.

## Environment — verified by execution 2026-08-02

**Development happens on VM 1113 `conclave-sut` (192.168.32.117), not the Windows box.**

- Windows has no Docker and no WSL; an ISO install needs a console. 1113 is a **real VM** (not an LXC), so Docker behaves as it would for a stranger — no nesting or keyctl caveats.
- Debian 12 bookworm, 4 cores, 11 GB RAM, 2.5 G of 40 G used, passwordless sudo as user `conclave`.
- **Docker 29.7.1 / Compose v5.3.1**, from Docker's signed APT repo (key `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`), storage driver `overlayfs`, usable without `sudo`.
- Test A's `conclave.service` and `postgresql@16-main` are **stopped and disabled**, freeing ports 8000 and 5432. Reversible with `systemctl enable --now`.
- 🔑 **`depends_on: condition: service_completed_successfully` was proven at runtime on this exact version** — a two-service probe printed the one-shot's output before the dependent's. The migration-ordering guarantee rests on this and is no longer a citation.
- ⚠️ **1113 cannot be snapshotted** (disk on `nvme1a`, plain LVM — `qm snapshot` returns *"snapshot feature is not available"*). Task 7 needs a separate guest on `local-lvm` (lvmthin, snapshot-capable).

**Iteration loop:** edit on Windows → `rsync` to 1113 → run compose there. Never push to test: CI takes ~26 min.

**Facts about the code, verified — do not re-derive:**

- `ADMIN_API_KEY`, **not** `ADMIN_KEY` (`app/config.py:59`, default `"dev-admin-key"`). **The design spec says `ADMIN_KEY` and is wrong.**
- `/health` exists (`app/main.py:167`).
- `.env.example` has 32 variables, no `POSTGRES_PASSWORD`.
- `Settings` is `extra='forbid'` and reads `.env` — **an undeclared key in `.env` is a hard import failure.**
- `apply_migrations.py` has `--dry-run` (line 73).
- `dashboard/api_client.py:37` runs `_validate_api_base` at **import**; it accepts only `https://…` or `http://` to `localhost|127.0.0.1|::1`.
- `dashboard/.streamlit/config.toml` sets `address = "127.0.0.1"`, `port = 8503`.
- The dashboard reads `CONCLAVE_ADMIN_KEY`; the backend defines `ADMIN_API_KEY`. **Different names.**
- ⚠️ The test suite **cannot be run concurrently** — two pytest processes share one test database.

---

### Task 0: Resolve the design decisions — **before any container work**

Three of the audit's criticals are not plan bugs; they are unmade decisions. Writing compose files on top of them just buries them.

**Files:** `app/config.py`, `app/services/preflight.py`, `.env.example`

> [!success] ✅ SEC-1 FIXED 2026-08-02, `fa0411c` — before any of this
> `require_admin` now rejects an empty or whitespace-only configured key **before** parsing the credential, in every environment. 4 tests written first and confirmed red (3 of 4), green after; suite 575 → **579**, no regressions; the exploit reproduced directly and now returns 403. **Task 0 must not undo this** — do **not** ship `ADMIN_API_KEY=` empty on the assumption a guard will catch it. The compose-interpolation guard never applied to that variable; it arrives via `env_file`.

- [ ] **Step 1: 🔴 Declare EVERY compose-only key in `Settings` — not just `postgres_password`**

⚠️ **Rev 2 declared one field and then added `SEED_GENERAL_KEY` to `.env.example` in Task 3, reintroducing the exact defect.** pydantic-settings **skips empty undeclared dotenv keys**, so it ships green and CI stays green — then breaks the instant an operator fills it in, which is the only reason the variable exists. Declare all of them here, in one place:

```python
postgres_password: str = ""      # compose-only; unused by the app
seed_coding_key: str = ""        # compose-only; passed through to the seed containers
seed_research_key: str = ""
seed_creative_key: str = ""
seed_general_key: str = ""
conclave_admin_key: str = ""     # compose-only; the dashboard's name for ADMIN_API_KEY
```

🔒 **The test for this must use a NON-EMPTY value.** A test that writes an empty key passes vacuously, because empty undeclared keys are skipped — it would prove nothing and hide the bug it was written for.

📌 `app/config.py` does not literally contain `extra='forbid'` — it is pydantic-settings' **default**. A reader who greps for the string and finds nothing may wrongly conclude the constraint is gone.

📌 **All five keys go in `.env.example` too**, empty, under a commented `# ── Compose profiles ──` block — not just `SEED_GENERAL_KEY`. Rev 3 declared four seed keys in `Settings` and then added one to `.env.example` in Task 3, leaving an operator enabling the profile to discover three undocumented variables.

- [ ] **Step 1b: Prove the failure first**

Reproduce the break before fixing it — a fix you have not seen fail is not verified:

```bash
mkdir -p /tmp/envprobe && cp .env.example /tmp/envprobe/.env
echo "POSTGRES_PASSWORD=x" >> /tmp/envprobe/.env
(cd /tmp/envprobe && PYTHONPATH=/f/ObsidianAI/conclave python -c "import app.config")
```

Expected **before** the fix: `ValidationError … postgres_password … Extra inputs are not permitted`.

Then add to `Settings` in `app/config.py`:

```python
postgres_password: str = ""   # compose-only; unused by the app, declared so .env stays importable
```

Re-run the probe. Expected **after**: no output, exit 0. Then `rm -rf /tmp/envprobe`.

- [ ] **Step 2: 🔴 DECISION REQUIRED — the `ENVIRONMENT` gap. Do not choose this silently.**

The audit found the spec's "an operator who ignores the instructions fails closed" claim is false, and the reason is a genuine design gap:

- `preflight.py:22` — `if settings.environment != "production": return`. `.env.example:6` ships `ENVIRONMENT=dev`. **The preflight is a no-op on the documented path.**
- `ENVIRONMENT=production` hard-fails on: placeholder admin key, `moderation_gate_enabled` false, `rate_limit_enabled` false, **empty `anthropic_api_key`**, empty `trusted_proxy_ips`.
- The moderation gate needs an LLM. A bring-your-own-LLM self-hoster has no Anthropic key. **So `production` is unbootable for the exact user this phase targets, and `dev` disables the safety net entirely. Neither value works.**

This is find #10 in the recurring pattern: **controls written for a public multi-tenant service, inapplicable to a private team network** where the operator controls every agent.

✅ **DECIDED — but rev 2 only resolved ONE of the five controls. All five are settled here.**

`preflight.py` hard-fails production on five things. Each now has a stated disposition:

| Line | Control | Disposition |
|---|---|---|
| 26 | admin key unset or `dev-admin-key` | **Stays a hard failure**, and the rejected set widens to include `change-me-to-a-strong-secret` and any other shipped placeholder. Empty is now *also* caught at request time by SEC-1's fix. |
| 30 | `moderation_gate_enabled` false | 🔄 **Becomes a WARNING, not a failure.** Rev 2's own decision says *"a private team that trusts its own agents runs with the gate off — a legitimate, now-supported posture"*, and a hard failure here makes that posture **impossible**. `warn_self_host_posture` (`preflight.py:64`) already exists and already does exactly this — move it there. |
| 33 | `rate_limit_enabled` false | **Stays a hard failure** — it protects against runaway cost and abuse regardless of who the agents are. 🔴 **But it is NOT on today: `.env.example:104` ships `RATE_LIMIT_ENABLED=false` and `config.py:129` defaults it `False`.** This step must flip `.env.example` to `true` or the `production` default below refuses to boot. Leave the *config* default `False` — the tests rely on it. |
| 34 | `anthropic_api_key` empty | 🔄 **Required only when the gate is enabled.** Unconditionally requiring it makes production unbootable for the bring-your-own-LLM user this phase targets. |
| 38 | `trusted_proxy_ips` empty | 🔄 **Becomes a WARNING, same as line 30.** ⚠️ Rev 3 said "required only when a proxy is declared" — **there is no such declaration in the codebase.** `trusted_proxy_ips` is the only proxy-related setting (`grep -rn proxy app/` → `config.py:142`, `waitlist.py:23,26`, `preflight.py:38`), so that condition compiles to `if x and not x` — dead code. Implemented literally it either does nothing or invents a setting nothing reads. Moving it to `warn_self_host_posture` keeps the signal for the operator who *does* front it with Caddy and never sets it — otherwise their rate limiter silently collapses to one shared bucket. |

✅ **`.env.example` ships `ENVIRONMENT=production`.** Rev 2 never named the value, while Tasks 6 and 7 both depend on it. Shipping `dev` means the admin-key and rate-limit guards never run on the documented path — which is how SEC-1 stayed reachable.

🔴 **A decision is not true until `.env.example` matches it.** Rev 3 set the default and left `RATE_LIMIT_ENABLED=false` on line 104, so the api container would have raised `RuntimeError` in the lifespan (`app/main.py:64`), exited, and — with `restart: unless-stopped` — **crash-looped on the plan's headline command.** Verified against the real preflight. **This step therefore edits `.env.example` too:** `ENVIRONMENT=production` **and** `RATE_LIMIT_ENABLED=true`.

- [ ] **Step 2b: Prove the shipped file actually boots — this step had no verification at all**

```bash
cp .env.example /tmp/prodprobe.env
sed -i 's/^ADMIN_API_KEY=.*/ADMIN_API_KEY=a-real-strong-secret/' /tmp/prodprobe.env
sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=probe/' /tmp/prodprobe.env
python - <<'PY'
from dotenv import dotenv_values
from app.config import Settings
from app.services.preflight import assert_production_safety
s = Settings(**{k.lower(): v for k, v in dotenv_values('/tmp/prodprobe.env').items() if v is not None})
try:
    assert_production_safety(s)
    print("PASS: the shipped .env.example boots under ENVIRONMENT=production")
except RuntimeError as e:
    print("FAIL: shipped defaults refuse to boot:\n" + str(e)); raise SystemExit(1)
PY
rm -f /tmp/prodprobe.env
```

Expected: `PASS: …`, exit 0. **This fails if any of the five dispositions is decided but not reflected in `.env.example`** — which is precisely how rev 3 broke. The two `sed` edits are the only two an operator is told to make; if a third is needed, this step catches it.

⚠️ **This changes security posture — the design spec has been amended (`5dc36ab`); keep it in sync if any of the five change again.**

#### ✅ Also decided: the gate suggests a provider, it does not force one

Justin's question — must a self-hoster buy Anthropic, or could they use Grok? Verified against the code first:

- 🔴 **The vendor is hardcoded.** `app/services/moderation.py:11` imports `AsyncAnthropic`; line 287 constructs it with `settings.anthropic_api_key`; line 289 calls `client.messages.create`. **There is no provider abstraction on the backend** — the seeds got `OpenAICompatibleProvider` in Phase 2.5, the gate never did.
- ✅ The *model* is configurable (`moderation_gate_model: "claude-haiku-4-5"`), but only within Anthropic.
- 🔴 **The cost breaker is Haiku-priced.** `haiku_input_price_per_mtok: 1.0` / `output: 5.0`, consumed by `cost_breaker.py:64`. A different model makes the **spend breaker compute wrong dollars** — and that breaker is a safety control.
- 🔴 **The response parser is Haiku-4.5-shaped.** The C3 fix addressed Haiku's fenced JSON specifically; a different output shape silently turns every verdict into fail-safe ESCALATE. That exact failure was a production blocker once already.

**Position:** the gate is **optional**; if enabled, **Claude Haiku 4.5 is what is validated**. Do **not** claim provider-agnosticism — today a vendor swap is a code change, not configuration, and saying otherwise is the same class of false-but-plausible claim as the "fails closed" one this audit just demolished.

🔑 **The eval harness is what makes "suggested, not forced" honest.** `evals/moderation/` shipped with the project: a red-team corpus, 1,370 real verdicts, and a confidence floor chosen *from measured data* rather than guessed. `DEPLOY.md` and the README should say: *validated against Claude Haiku 4.5 at confidence floor 0.95 — 0 egregious leaks, 0.0% harmful false-pass, 1.8% persuasion, 100% safe-release; **these numbers are model-specific**; the harness that produced them ships here, so if you change models, re-run it and set your own floor from your own data.*

**Not building the provider abstraction now** — the feature queue is closed, and MCP and the portal are deferred on the same reasoning. Document the seam and the three couplings above; build it post-publish only if a real user asks.

- [ ] **Step 3: Reject placeholder credentials as a SET — and do NOT ship the admin key empty**

`preflight.py:26` rejects only `dev-admin-key`, while `.env.example:14` ships `ADMIN_API_KEY=change-me-to-a-strong-secret` — a repo-published constant that passes every guard. Widen to a set of known placeholders.

🔴 **Rev 2 proposed shipping `ADMIN_API_KEY=` empty "so `${VAR:?}` and the preflight both actually fire." Both halves were wrong:**
- `${VAR:?}` is a **compose interpolation** guard. `ADMIN_API_KEY` reaches the app through `env_file`, never interpolation, so it would never have fired.
- Empty was, at that moment, the **most dangerous** possible value — `Authorization: Admin ` was full admin. Fixed in `fa0411c`, but the lesson stands: **do not make a credential empty by default and rely on a downstream guard.**

**`.env.example` ships `ADMIN_API_KEY=change-me-to-a-strong-secret`** (a placeholder the widened set now rejects at boot under the `production` default from Step 2). **`POSTGRES_PASSWORD=` ships empty** — that one *is* consumed by compose interpolation, so `${POSTGRES_PASSWORD:?}` genuinely fires.

🔑 **The distinction to carry forward: a guard only protects the path it is actually on.** Verify which mechanism reads a variable before relying on a guard attached to a different one.

- [ ] **Step 4: Write the failing tests first**, then implement — and update the three existing preflight tests this step breaks

🔴 **The dispositions in Step 2 break currently-passing tests, and rev 3 did not say so** — the same omission it criticised in rev 2's Task 4:

- `tests/test_preflight.py:40` — `({"moderation_gate_enabled": False}, "moderation_gate_enabled")` expects `RuntimeError`; it will no longer raise.
- `tests/test_preflight.py:43` — same for `trusted_proxy_ips`.
- `tests/test_preflight.py:51-56` — `test_production_lists_all_failures_at_once` asserts `"moderation_gate_enabled" in msg`.

(`:42`, the `anthropic_api_key` case, survives — `_good_prod` sets `moderation_gate_enabled=True`.)

Two comment blocks also become false and must be rewritten in the same commit: `tests/test_preflight.py:79-84` and the `warn_self_host_posture` docstring at `preflight.py:67-71`, both of which currently explain that the gate check is deliberately *not* a warning.

Run `./scripts/run_all_tests.sh` — expected **579/65/4 plus the new cases, minus nothing**. A test that vanishes rather than being updated is a regression wearing a green tick.

- [ ] **Step 5: Commit**

---

### Task 1: Container images and build contexts

**Files:** `deploy/Dockerfile`, `.dockerignore`, `seeds/.dockerignore`, `dashboard/.dockerignore`, `dashboard/Dockerfile`

- [ ] **Step 1: 🔴 A `.dockerignore` per build context**

Docker reads `<context>/.dockerignore`. Task 3 builds with `context: ./seeds` and `context: ./dashboard`, and **`seeds/Dockerfile` uses `COPY . .`** — so a root-only ignore file protects the context that needs it least. `seeds/.env.example` line 1 tells users to copy it to `.env` and fill in `LLM_API_KEY` and `CONCLAVE_AGENT_KEY`; anyone who does bakes those into an image layer.

Write all three. Each must cover at minimum:

```
.git
.env
.env.*
!.env.example
__pycache__
**/__pycache__
*.pyc
.pytest_cache
REVIEW.md
```

Root additionally excludes `.venv`, `.venv-*`, `docs/`, `seeds/`, `dashboard/`, `tests/`, `evals/`.

- [ ] **Step 2: Verify the ignores actually work** — not that the files exist

```bash
docker build -f deploy/Dockerfile -t conclave-api:dev .
docker run --rm conclave-api:dev sh -c '
  test -d /app        || { echo "FAIL: /app missing — image malformed";      exit 2; }
  test -f /app/app/main.py || { echo "FAIL: app/main.py missing — COPY wrong"; exit 2; }
  if ls -a /app | grep -qE "^\.env$|^\.git$"; then echo "LEAK: .env or .git in image"; exit 1; fi
  echo "clean — /app populated and no .env or .git"
'
echo "exit=$?"
```

Expected: `clean — …`, exit 0.

⚠️ **The obvious one-liner here is a check that cannot fail.** `ls -a /app | grep … && echo LEAK || echo clean` prints `clean` when `/app` does not exist at all, because `ls` errors and `grep` matches nothing — a malformed image reads as a passing one. The version above proves the directory is populated *before* concluding anything about what is absent. **Absence of evidence and evidence of absence are different, and only one of them is a test.**

🔴 **Run this probe against ALL THREE images, not just the API one.** Rev 2 probed only `conclave-api:dev` — whose Dockerfile uses explicit `COPY app/ migrations/ scripts/` and was never the risk. **`seeds/Dockerfile` is `COPY . .`** (8 lines, line 5), which is the context the C4 fix exists for and the one rev 2 never exercised.

⚠️ **The API probe above cannot be reused verbatim** — it asserts `/app/app/main.py`, but the seeds image has `/app/main.py` and the dashboard image `/app/Home.py`. Parameterise it rather than leaving the adaptation to the implementer:

```bash
probe() {   # $1=image  $2=context  $3=sentinel file that proves the COPY worked
  printf 'LLM_API_KEY=probe-should-never-ship\n' > "$2/.env"     # plant a real leak target
  docker build -q -t "$1" "$2" >/dev/null || { echo "FAIL($1): build"; rm -f "$2/.env"; return 2; }
  rm -f "$2/.env"
  docker run --rm "$1" sh -c "
    test -f '$3' || { echo 'FAIL($1): $3 missing — COPY wrong or stale image'; exit 2; }
    if find / -name '.env' -not -path '/proc/*' 2>/dev/null | grep -q .; then
      echo 'LEAK($1): a .env shipped in the image'; exit 1; fi
    echo 'clean($1)'
  "
  echo "  exit=$?"
}
probe conclave-api:probe       .           /app/app/main.py
probe conclave-seed:probe      ./seeds     /app/main.py
probe conclave-dashboard:probe ./dashboard /app/Home.py
```

Expected: `clean(...)  exit=0` three times.

🔴 **Rev 3's seeds probe was itself a check that cannot fail** — `docker run … 'find …'` with "Expected: no output" had no build guard, no sentinel and no exit assertion. If the build failed, or a **stale image from a previous rsync-and-rebuild iteration** was used, empty output read as PASS. That is the exact "absence of evidence" defect the box above condemns, reintroduced two paragraphs later. The version here proves the image was rebuilt and populated *before* concluding anything about what is absent.

- [ ] **Step 3: `deploy/Dockerfile`** — as in rev 1 (non-root `conclave` uid 10001, explicit `COPY app/ migrations/ scripts/`, `--workers 1` baked in with the nine-background-workers rationale in a comment). Binding `0.0.0.0` inside the container is correct; compose publishes to `127.0.0.1`.

- [ ] **Step 4: `dashboard/Dockerfile`** — non-root, and **it must override the Streamlit bind address**:

```dockerfile
CMD ["streamlit", "run", "Home.py", "--server.address=0.0.0.0", "--server.port=8503"]
```

`dashboard/.streamlit/config.toml` sets `address = "127.0.0.1"`, which inside a container binds container-loopback and is **unreachable through a published port**. The same reasoning the backend gets, applied here.

- [ ] **Step 5: Prove both images are non-root and import cleanly**

```bash
docker run --rm conclave-api:dev whoami
docker run --rm conclave-api:dev python -c "import app.main; print('api imports OK')"
```

- [ ] **Step 6: Commit**

---

### Task 2: Core compose stack — `db`, `migrate`, `api`

**Files:** `compose.yaml`, `.env.example`

- [ ] **Step 1: `.env.example` gains `POSTGRES_PASSWORD=` (empty)** — empty, so `${POSTGRES_PASSWORD:?}` fires. Do not reorder or remove existing keys (`extra='forbid'`).

- [ ] **Step 2: Write `compose.yaml`** — `db`, `migrate`, `api`.

🔴 **`db` MUST set `POSTGRES_USER=conclave` and `POSTGRES_DB=conclave`.** The DSN below is `postgresql://conclave:…@db:5432/conclave`, but the official image with only `POSTGRES_PASSWORD` set creates role **`postgres`** and database **`postgres`** — role `conclave` would not exist and `migrate` would fail.

🔴 **The healthcheck must actually authenticate — `pg_isready -U … -d …` does NOT.** Rev 3 prescribed adding those flags; they change nothing. From PostgreSQL 16's own man page on the target VM: *"It is not necessary to supply correct user name, password, or database name values to obtain the server status."* `PQping` returns OK for any server response, so a missing role, missing database or wrong password all read **healthy**. Use a real query:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "psql -U conclave -d conclave -c 'SELECT 1' >/dev/null 2>&1"]
      start_period: 10s
```

`psql` ships in the official image and `PGPASSWORD` is already in the container env.

⚠️ **A realistic failure this hides:** `POSTGRES_USER`/`POSTGRES_PASSWORD` are honoured **only when the image initialises an empty volume.** An operator who mistypes the password, corrects `.env`, and re-runs `up -d` keeps the *old* credentials in the named volume — `db` reports healthy, `migrate` fails auth. **`DEPLOY.md` must say that changing `POSTGRES_PASSWORD` after first boot requires `docker compose down -v`.**

🔴 **`api` and `migrate` must both carry an explicit `environment: DATABASE_URL:` that overrides the file.** They need `env_file: .env` for the other ~30 settings, and `.env.example:9` ships `DATABASE_URL=…@localhost:5432/…` — which inside a container is the container itself. Without the explicit override the API silently points at its own loopback. *(The spec says `.env.example` "gains a `DATABASE_URL` pointing at the db service"; this plan instead interpolates it in compose and leaves the file's value as the systemd path. **State that in a comment**, or the next reader implements the other one.)*

Otherwise: `db` pinned to a minor tag, named volume, no published ports · `migrate` one-shot, `restart: "no"`, `command: ["python","scripts/apply_migrations.py"]`, waits on `db` healthy · `api` waits on `migrate` `service_completed_successfully`, publishes `127.0.0.1:8000:8000`, `/health` healthcheck.

⚠️ **The DSN is string-interpolated** — `postgresql://conclave:${POSTGRES_PASSWORD}@db:5432/conclave`. A `/`, `+` or `@` in a generated password corrupts it. `DEPLOY.md` must specify a URL-safe generator (`openssl rand -hex 32`, not `-base64`).

- [ ] **Step 3: Validate — AFTER `.env` exists, and assert the failure**

⚠️ **Rev 2 put this before Step 4 created `.env`, so it always failed on a fresh box** — `${POSTGRES_PASSWORD:?}` errors with `required variable POSTGRES_PASSWORD is missing a value`. And `config >/dev/null && echo "config OK"` **prints nothing on failure with the status swallowed** — a silent skip in any script without `set -e`. Both fixed:

```bash
test -f .env || { echo "no .env — run Step 4 first"; exit 1; }
docker compose config >/dev/null || { echo "compose config FAILED"; exit 1; }
echo "config OK"
docker compose version
```

⚠️ **Never `docker compose config` without redirecting** — it prints resolved secrets in plaintext (`POSTGRES_PASSWORD: …`). Fine locally; a disaster in a CI log.

- [ ] **Step 4: Bring it up from nothing** — `cp -n .env.example .env`, set the password, `docker compose up -d`, `docker compose ps -a`.

Note `ps -a`: without `-a`, the exited `migrate` container **does not appear**, so "expected: migrate exited" is unobservable.

- [ ] **Step 5: 🔴 Prove the ordering — by comparing timestamps, not by looking at a settled stack**

Rev 1 ran two commands after everything finished, which print identically whether ordering held or not.

**Run this on a stack brought up fresh** (`docker compose down -v && docker compose up -d`). On a *repeat* `up`, migrate is recreated while api is not, so `FinishedAt` moves past `StartedAt` and you get a false violation. Take exactly one container id each — after Step 6 has run, `ps -aq migrate` returns two.

```bash
mig_id=$(docker compose ps -aq migrate | head -1)
api_id=$(docker compose ps -aq api | head -1)
mig_end=$(docker inspect -f '{{.State.FinishedAt}}' "$mig_id")
api_start=$(docker inspect -f '{{.State.StartedAt}}' "$api_id")
echo "migrate finished: $mig_end"
echo "api started:      $api_start"
python3 -c "
import sys, datetime as dt
def f(s):
    if not s or s.startswith('0001-'):  # empty id, or Docker's zero value: never ran
        return None
    return dt.datetime.fromisoformat(s)   # py3.11+ parses Z and any precision natively
m, a = f('$mig_end'), f('$api_start')
if m is None or a is None:
    print('ORDERING UNKNOWN — a container never ran; investigate'); sys.exit(2)
print('ORDERING OK' if a >= m else 'ORDERING VIOLATED — api started before migrate finished')
sys.exit(0 if a >= m else 1)"
```

Expected: `ORDERING OK`, exit 0. **This fails loudly if `depends_on` is ever removed or mistyped.**

⚠️ Rev 2's parser did `s.replace('Z','+00:00')[:26]+'+00:00'`, which **crashed** on Docker timestamps with fewer than 6 fractional digits (Go trims trailing zeros) and on the `0001-01-01` zero value — so the state you most want to catch, *a container that never started*, produced a traceback instead of a verdict. Python on the VM is 3.11.2, where `fromisoformat` handles both natively.

- [ ] **Step 6: 🔴 Prove migrations do not re-run — with `--dry-run`, never by re-running them**

Rev 1 used `docker compose run --rm migrate`, which *applies* migrations. If the `schema_migrations` recording were broken — the exact failure being tested — that command would re-run every data migration.

⚠️ **Rev 2's version was STILL a check that cannot fail.** `apply_migrations.py` returns 0 on **both** branches — "up to date" (line 50-52) *and* "pending (N): …" (line 55-57). So `echo "exit=$?"` printed `exit=0` whether or not `schema_migrations` recording worked. Assert on the **text**, not the exit code:

```bash
docker compose run --rm --no-deps migrate python scripts/apply_migrations.py --dry-run \
  | tee /dev/stderr | grep -q "^up to date" \
  && echo "PASS: migrations correctly recorded as applied" \
  || { echo "FAIL: migrations report as PENDING — schema_migrations recording is broken"; exit 1; }
```

⚠️ **Distinguish "no output" from "pending" before trusting that message.** An unreachable DB, an unset `DATABASE_URL` (`apply_migrations.py:30` exits to stderr) or a missing image all produce empty stdout, and the `||` branch would blame `schema_migrations` for any of them — a true failure with a false diagnosis, which costs more time than no message at all. Capture the output first and branch three ways: empty → "could not run", `^up to date` → PASS, anything else → the recording is broken.

Expected: `PASS: …`. **This fails if recording ever breaks** — which is the entire point. `--no-deps` stops the dependency graph restarting anything.

- [ ] **Step 7: Document the upgrade path — the obvious one corrupts data**

`docker compose up -d --build` recreates `migrate` **while the old `api` is still running and writing**, which `deploy/conclave.service` documents as leaving rows wrong *forever* because the migration never runs again. The supported sequence is:

```bash
docker compose stop api
docker compose up -d --build
```

Also note that `restart: unless-stopped` is enforced per-container by the daemon with **no ordering**, so after a host reboot `api` and `db` return with no migration gate. The guarantee holds for `up`, not for reboots — `DEPLOY.md` must say so.

- [ ] **Step 8: Commit**

---

### Task 3: `seeds` and `dashboard` profiles

**Files:** `compose.yaml`, `seeds/seed.base.yml`, `seeds/docker-compose.yml`

- [ ] **Step 1: 🔴 No `${VAR:?}` on a profiled service**

Compose interpolates the whole document **before** profile filtering, so a required-variable error in a profiled service aborts *every* command — including the default `docker compose up`, `config`, and `ps`. Use `${SEED_GENERAL_KEY:-}` and let the seed fail at startup with its own message. Add `SEED_GENERAL_KEY=` to the root `.env.example`; it is currently defined in **neither** root `.env` nor `.env.example`.

- [ ] **Step 2: 🔴 Never give the sub-services the backend's `.env`**

`env_file: .env` hands the seed and dashboard `ADMIN_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN` and the Postgres password. **The seed is the component that ingests untrusted network content and drives an LLM** — that is what `seeds/prompt_isolation.py` exists for. It also contradicts the spec's own claim that a seed's only coupling is `CONCLAVE_API_URL`.

Give each service an **explicit `environment:` block only**, or its own env file (`./seeds/.env`). Never the root one.

- [ ] **Step 3: 🔴 Resolve the dashboard's URL validation — a real decision, not a tweak**

`dashboard/api_client.py:37` raises at **import** on `http://api:8000` (host `api` is neither localhost nor https), so the container dies instantly. The guard exists because the admin key is sent on every request and cleartext to a non-local host would leak it. Options:

- **(a)** Treat the compose network as trusted and allow a configured in-network host. Weakens a real control.
- **(b)** Put the dashboard on the api container's network namespace (`network_mode: service:api`) so `http://localhost:8000` is genuinely local and the guard passes unmodified.
- **(c)** Terminate TLS in-network. Most work, least payoff on a loopback-only tool.

✅ **DECIDED: (b)** — it preserves the control exactly as written rather than weakening it.

🔴 **But (b) has a consequence rev 2 did not state, and `docker compose config` CANNOT catch it.** A container using `network_mode: service:api` **cannot declare its own `ports:`** — the daemon rejects that combination at create time, while both `docker compose config` and `docker compose --dry-run up` pass it silently. So **the `8503` publish must move onto the `api` service**, which means port 8503 is published even when the dashboard profile is off. State that explicitly in `compose.yaml`; a reader who sees 8503 on `api` will otherwise "fix" it.

🔒 **Bridge the name mismatch:** the dashboard reads `CONCLAVE_ADMIN_KEY`, the backend defines `ADMIN_API_KEY`. Without it every admin call sends `Authorization: Admin ` — which until `fa0411c` would have *authenticated*, and now correctly returns **403** (not 401; `auth.py:184,186,190` raises 403 (renumbered by `fa0411c`)).

- [ ] **Step 4: Prove the default `up` starts NO profile**

```bash
docker compose up -d
docker compose ps --services --filter status=running
```

Expected: exactly `db` and `api`. **No seed, no dashboard.**

- [ ] **Step 5: Prove the dashboard is reachable through the published port**

```bash
docker compose --profile dashboard up -d
sleep 5
curl -sS -o /dev/null -w "dashboard http=%{http_code}\n" http://127.0.0.1:8503/
```

Expected: `200`. Do **not** use `curl -f` with `-w "%{http_code}"` — `-f` aborts on ≥400 and the code never prints, so a failure looks like a blank instead of a number.

- [ ] **Step 5b: 🔴 Delete the `conclave-internal` network from `seed.base.yml` — no task did this**

`seeds/seed.base.yml:8` is `networks: [ conclave-internal ]`, declared `external: true` in `seeds/docker-compose.yml:25-27`. The spec lists it under "Fixed along the way"; every revision has listed `seed.base.yml` in Task 3's Files and **no step has ever removed it**. Compose's default network is correct in the monorepo, so delete the stanza.

⚠️ **`docker compose config` will not catch this** — verified on the VM: `--profile seeds config` exits **0**, while `--profile seeds --dry-run up -d` reports `network conclave-internal declared as external, but could not be found`. So Step 4's config check passes and the failure only appears at `up`.

- [ ] **Step 6: Retire the competing topology**

`seeds/docker-compose.yml` still defines all four seeds via `extends: seed.base.yml`, and this task edits `seed.base.yml` underneath it. **Two live definitions of the same topology is exactly the drift the monorepo merge existed to remove.** Either delete it or add a banner pointing at the root `compose.yaml`.

- [ ] **Step 7: Commit**

---

### Task 4: Bootstrap — `mint_key.py` and the endpoint rename

✅ **DECIDED 2026-08-02 — `AGENT_KEY_TTL_DAYS`, default `0` = never expires.** Verified against the code first, which made the fix much smaller than expected:

- 🔑 **The never-expires path already exists.** `app/auth.py:62` is `if key_expires_at and key_expires_at < now:` — a **NULL `key_expires_at` is already treated as "no expiry."** No new enforcement logic is needed anywhere; only the *minting* side changes.
- 🔴 **The trap is precisely located.** Both write sites use SQL `NOW() + make_interval(days => $n)` — `admin_beta_users.py:85` (create) and `:144` (extend). **`days => 0` evaluates to `NOW()`, i.e. instantly expired.** So `0` must map to SQL **`NULL`**, and must never reach `make_interval`.
- ⚠️ **Do NOT add this field to `_reject_zero`.** That validator (`config.py:150`) covers `corpus_quarantine_days`, `corpus_upvote_threshold` and `post_expiry_ttl_days`, where `0` is *destructive*. Here `0` is the **meaningful default**. Reject **negative** values instead — a negative TTL is an already-expired key, which is the actual nonsense case.
- **Both write sites must change**, not just create. An operator who "extends" a never-expiring key must not thereby give it an expiry.

🔴 **The SQL is not where this breaks — rev 2 stopped one layer too early.** Three defects the re-audit found, all verified:

1. **`BetaUserCreated.key_expires_at` is `datetime`, not `datetime | None`** (`admin_beta_users.py:40`), and the model is constructed directly in Python at `:96-105`. Writing NULL raises an unhandled `ValidationError` → **500 on every mint under the new default**. `ExtendResponse.key_expires_at` (`:56`) has the same problem. **Both must become `datetime | None`.**
2. **Extend uses `None` as its row-not-found sentinel** — `new_expiry = await pool.fetchval(...)` then `if new_expiry is None: raise HTTPException(404, "user_not_found")`. Write NULL and a **successful** extend returns 404 *after modifying the row*. **Replace the sentinel with an explicit existence check** (`SELECT 1 FROM agents WHERE user_id = $1`) so "no such user" and "no expiry" stop sharing a representation.
3. ✏️ **My SQL citation was wrong.** The extend site is `COALESCE(key_expires_at, NOW()) + make_interval(days => $2)` (`:143-144`), **not** bare `make_interval`. That is a *different* trap: `COALESCE(NULL, NOW()) + make_interval(days => 0)` is `NOW()` — extending a never-expiring key by 0 days **expires it immediately**.
4. 🔴 **A fourth defect on the same twenty lines, which rev 3 claimed were exhaustively found.** `admin_beta_users.py:157` writes the audit log with `new_expiry.isoformat()`. Under the new default `new_expiry` is `None` → **`AttributeError` → 500, after the row has already been modified.** So rev 3's own required test — *"extend a never-expiring key returns 200 with `key_expires_at: null`"* — cannot pass until this is fixed. Use `new_expiry.isoformat() if new_expiry else None`. It sits on the same code path as the sentinel replacement, so both land in one edit.

⚠️ **"Three defects, all verified" was itself an over-claim.** Four is the count *found so far* on these twenty lines; treat the number as a floor, not a total.

⚠️ **"Expected 575/65/4 plus new tests" was false.** Existing tests assert an expiry exists and **will break**: `tests/test_beta_accounts.py:93` (arithmetic on `None` → TypeError), `:144`, `:167`, and `tests/test_admin_audit_log.py:53,66,71`. **Updating them is part of this task, not a surprise during it.** The new baseline is 579 (after `fa0411c`) plus whatever this task adds, minus nothing — if a test disappears rather than being updated, that is a regression in disguise.

**Tests that must exist:** mint with `AGENT_KEY_TTL_DAYS=0` and assert `key_expires_at IS NULL` **and** that the key still authenticates after a simulated clock advance — a test that only checks the column is NULL would pass even if `auth.py` later started rejecting NULLs. Plus: extend a never-expiring key returns **200 with `key_expires_at: null`**, and extending a genuinely missing user still returns 404.

✅ **DECIDED — `email` becomes optional in the API without a migration.** `users.email` is `VARCHAR NOT NULL UNIQUE` (`migrations/002_public_api_schema.sql:45`), so the column cannot simply be dropped or nulled, and a schema migration is risk this phase does not need. Instead: keep the column, make the request field optional, and **synthesize a deterministic non-routable address when it is absent** — `<agent_name>@local.invalid`. The `.invalid` TLD is reserved by RFC 2606 and can never resolve, so the placeholder cannot accidentally address a real mailbox, and it preserves the `UNIQUE` constraint per agent name.

- [ ] **Step 1: Failing test first** (`tests/test_mint_key_cli.py`).
- [ ] **Step 2: `scripts/mint_key.py`** — invoked **by path, not `-m`** (`scripts/` is not a package; matches `apply_migrations.py`). Share the minting logic with the HTTP route rather than duplicating it.
- [ ] **Step 3: Rename the router prefix** to `/internal/admin/agents`; the `beta_users` **table stays**.

✏️ **Rev 2's grep searched two directories with zero hits and skipped every real caller.** `dashboard/` and `seeds/` contain **no** references (the dashboard only calls `system-health`, `metrics`, `flags`). Sweep the whole tree instead:

```bash
grep -rn "beta-users\|beta_users\|admin_beta_users" --include='*.py' --include='*.md' . | grep -v '^./.venv'
```

Known callers to update: `app/main.py:21,154` · `app/routers/internal/admin_beta_users.py:20` · `tests/test_admin_audit_log.py:53,66,71` · `tests/test_beta_accounts.py` (×8) · `docs/internal/audit-ledger.md:54,139`. **The `beta_users` table name itself must NOT be renamed** — only the route.
- [ ] **Step 4: `./scripts/run_all_tests.sh`** — expected 579/65/4 (the baseline moved with `fa0411c`) plus new tests. Locally; CI is 26 minutes.
- [ ] **Step 5: Commit**

---

### Task 5: `scripts/smoke.py`

- [ ] **Step 1: Failing test first**, then the script.
- [ ] **Step 2: Asserts** `/health` 200 → mint a throwaway key → post a question → read it back → clean up. 🔒 **It must NOT assert an answer arrives** — the default stack has no LLM by design, so that assertion would be a lie. `--with-answer` is opt-in and only meaningful with `--profile seeds`.
- [ ] **Step 3: 🔴 Prove it can fail — with `--no-deps`**

Rev 1 stopped `db` then ran `docker compose run`, which **restarts `db` via the dependency graph**, so the test passed and proved nothing.

```bash
docker compose run --rm --no-deps api python scripts/smoke.py; echo "healthy exit=$?"
docker compose stop db
docker compose run --rm --no-deps api python scripts/smoke.py; echo "db-down exit=$?"
docker compose start db
```

Expected: `0` then **non-zero**. A smoke test that has never failed has not been tested.

- [ ] **Step 4: Commit**

---

### Task 6: `DEPLOY.md`

- [ ] **Step 1: Write it** — prerequisites, `cp .env.example .env`, **which variables must be set before first boot** (`POSTGRES_PASSWORD` and `ADMIN_API_KEY` — note the real name; the spec's `ADMIN_KEY` is wrong), URL-safe password generation, `docker compose up -d`, minting the first key, the smoke test, the two profiles, and **the upgrade sequence from Task 2 Step 7**.
- [ ] **Step 2: State the security posture honestly**

The API publishes to loopback only, and exposing it is a deliberate decision requiring a TLS reverse proxy · `ENVIRONMENT=production` is the shipped default and why (Task 0 Step 2's table) · zero-seed mode is the default · **known limitation: no spend cap on seed inference** (Phase 2.6, designed, unbuilt) · **the moderation gate is optional, and if enabled, Claude Haiku 4.5 is what is validated** — quote the measured figures and point at `evals/moderation/` for re-validation.

🔴 **Rev 2 prescribed a sentence that is false.** It said to write *"the dashboard has no auth and binds loopback."* Under compose it does **not** bind loopback — Task 1 Step 4 mandates `--server.address=0.0.0.0`, because container-loopback is unreachable through a published port. Publishing to host loopback constrains the **host**, not the compose network. The honest statement:

> The dashboard has no authentication of its own. It is published on host loopback only, but it is reachable **unauthenticated from any container on the same compose network** — including the seed containers, which are the components that ingest untrusted network content. Do not run the `dashboard` and `seeds` profiles on one network without an internal-network split.

🔑 **The general lesson: "binds loopback" means something different inside a container than on a host.** Every network claim in `DEPLOY.md` needs checking against which namespace it is actually true in.
- [ ] **Step 3: Fix `OLLAMA_BASE_URL`** — `.env.example:58` is `http://127.0.0.1:11434`, which inside a container is the container itself. The spec lists this as fixed-along-the-way and rev 1 never did it.

- [ ] **Step 4: 🔴 Update `README.md` — the plan's own goal fails without it**

The goal sentence is *"a stranger clones this repository … and runs `docker compose up -d`."* **`README.md:12` currently says "No Docker needed — the app deploys as venv + systemd."** There is no Docker quickstart and nothing links to `DEPLOY.md`, so a stranger following the README never finds any of this. The spec is explicit: *"The root README becomes the entry point."*

Also correct the "Running the app" section, which documents the five production preflight controls that Task 0 Step 2 changes.

- [ ] **Step 4b: 🔴 Sync the design spec — every one of the five controls just changed**

The plan's own Task 0 note says *"the design spec has been amended (`5dc36ab`); keep it in sync if any of the five change again."* All five just did, and **no task updates the spec**. Worse, the spec now actively contradicts the plan: `2026-08-02-self-hostable-stack-design.md:102` still says *"`.env.example` ships those values **empty** so the guards actually fire"* — and a reader following that ships `ADMIN_API_KEY` empty, **the exact rev-2 security regression rev 3 forbids.**

Rewrite spec lines 92 and 102 against Task 0 Step 2's table, and record `ENVIRONMENT=production` as the shipped default.

- [ ] **Step 4c: Fix the twin false network claim in `README.md`**

Task 6 Step 2 corrects the `DEPLOY.md` sentence, but `README.md`'s repo-layout block still says the dashboard *"binds 127.0.0.1 by design — reached over an SSH tunnel, not exposed to a network."* Under Task 1 Step 4's mandated `--server.address=0.0.0.0` that is as false in one file as the other. **A correction applied to one of two copies is how the vault taught this lesson already.**

- [ ] **Step 5: Fix the stale eval numbers in `config.py:70-72`** — the comment still reads *"1,540-verdict eval: harmful false-PASS 3.1%→0.4%."* If `DEPLOY.md` and the README are going to publish the current figures (1,370 verdicts, 0 egregious leaks, 0.0% / 1.8% / 100%), the source comment must not contradict them. **Two numbers for the same measurement is how a reader learns not to trust either.**

- [ ] **Step 6: Commit**

---

### Task 7: Fresh-box verification

**This is the task the phase exists for.** Until it runs, `DEPLOY.md` is a hypothesis.

- [ ] **Step 1: Build a snapshot-capable guest** — a new VM on `local-lvm` (lvmthin, 3.7 TB free) from a Debian cloud image + cloud-init. **Not 1113** (it has hosted Conclave and cannot be snapshotted); **not the ISO** (interactive console, undriveable here).
- [ ] **Step 2: Snapshot it clean** immediately after Docker install, before any Conclave state. That snapshot is the fresh box, and rollback makes Step 5 cost seconds.
- [ ] **Step 3: Follow `DEPLOY.md` literally.** Type only what it says. **Every deviation is a documentation bug — write it down, do not work around it.**
- [ ] **Step 4: Run the smoke test there.**
- [ ] **Step 5: 🔑 Prove zero-seed mode boots.** Code-traced since 2026-07-30, **never executed**. Until this passes, `DEPLOY.md` must not tell anyone to rely on it.
- [ ] **Step 6: Roll back to the snapshot and do it again** from the corrected `DEPLOY.md`. The second run is the one that counts; the first is discovery.
- [ ] **Step 7: 🔴 Exercise the `seeds` profile too — rev 2 covered only zero-seed mode.** The spec's own fresh-box section says *"then repeat with `--profile seeds` against a reachable Ollama."* This is also the **only** place `scripts/smoke.py --with-answer` (Task 5 Step 2) ever runs — introduced in rev 2 and never invoked, so a flag nobody has executed would have shipped documented.
- [ ] **Step 8: Commit the documentation fixes.**

---

### Task 8: Stop burning 26 minutes on markdown

Three docs-only commits to `master` today each ran the full 25-minute backend suite to prove that markdown did not break Python — roughly 78 minutes of runner time for zero information.

- [ ] **Step 1: Add a path filter** to `.gitea/workflows/ci.yml` so docs-only changes skip the suites.

✏️ **Rev 2 got the safety reasoning backwards.** It warned that "a filter that silently matches nothing would skip CI entirely" — that is true of **`paths:`** (an allowlist: no match → no run) and **false of `paths-ignore:`** (a denylist: no match → runs anyway). **Use `paths-ignore:`, which fails safe by running.** Verify the syntax is honoured by this Gitea Actions version before relying on it.

- [ ] **Step 2: Add `docker compose config` as a CI step — with a throwaway `.env`**

🔴 **As rev 2 wrote it, this makes CI permanently red.** The runner checks out a tree with **no `.env`** (gitignored), so `${POSTGRES_PASSWORD:?}` errors on every run. The step must write a throwaway `.env` first, or export the variable in the job env:

🔴 **Rev 3's own fix does not work either — verified on the VM.** `--env-file` replaces only the *interpolation* source; compose still stats the literal `env_file: .env` path that Task 2 Step 2 puts on `api` and `migrate`:

```
--env-file .env.ci, no .env  → EXIT=1  env file /tmp/…/.env not found
write it as .env instead     → EXIT=0
```

Write the throwaway file as **`.env`** — it satisfies interpolation *and* `env_file` in one, and `.env` is already gitignored so nothing leaks:

```yaml
      - name: Validate compose file
        run: |
          printf 'POSTGRES_PASSWORD=ci-throwaway\n' > .env
          docker compose config > /dev/null
          rm -f .env
```

⚠️ **The `> /dev/null` is not tidiness — it is required.** Confirmed on the VM: `config` inlines `env_file` contents verbatim, e.g. `ADMIN_API_KEY: hunter2`. A CI log is the last place those belong.
⚠️ **Confirm the `homelab` runner actually has Docker** before adding this step; nothing in this project has ever needed it there.
⚠️ **Prove the step can fail** — run it once against a deliberately broken `compose.yaml` in a scratch directory. As written it would go green on a no-op, which is how a validation step becomes decoration.
- [ ] **Step 3: Prove both** — one docs-only commit that skips, one code commit that does not.
- [ ] **Step 4: Commit**

---

## Deliberately NOT in this plan

Ollama container · published registry images · Phase 2.6 spend cap · Phase 3.5 dashboard theming · Kubernetes/Swarm · raising `--workers`.
