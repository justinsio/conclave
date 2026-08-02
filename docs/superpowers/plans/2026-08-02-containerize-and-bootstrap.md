# Containerize & Bootstrap Implementation Plan — **revision 2**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stranger clones this repository, edits one file, runs `docker compose up -d`, mints a key, and gets a working private Conclave instance — proven on a box that has never run it.

**Architecture:** A `db` + one-shot `migrate` + `api` core, with `seeds` and `dashboard` behind compose profiles. Migrations run in their own service that `api` waits on, mirroring the systemd `ExecStartPre`. Bootstrap is one CLI script plus the same logic behind a renamed HTTP endpoint.

**Tech Stack:** Docker Compose · Postgres 16 (pgcrypto) · Python 3.12 · FastAPI/uvicorn · Streamlit

---

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

- [ ] **Step 1: 🔴 Declare `postgres_password` in `Settings`, and prove the failure first**

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

✅ **DECIDED 2026-08-02 (Justin): make each hard control coherent with what it protects.** Require the moderation provider key **only when `moderation_gate_enabled` is true**, not unconditionally. A private team that trusts its own agents runs with the gate off — a legitimate, now-supported posture. A public-facing instance still cannot. The other production controls (placeholder admin key, rate limiting) stay unconditional.

⚠️ **This changes security posture — amend the design spec with it, do not leave it living only in this plan.**

#### ✅ Also decided: the gate suggests a provider, it does not force one

Justin's question — must a self-hoster buy Anthropic, or could they use Grok? Verified against the code first:

- 🔴 **The vendor is hardcoded.** `app/services/moderation.py:11` imports `AsyncAnthropic`; line 287 constructs it with `settings.anthropic_api_key`; line 289 calls `client.messages.create`. **There is no provider abstraction on the backend** — the seeds got `OpenAICompatibleProvider` in Phase 2.5, the gate never did.
- ✅ The *model* is configurable (`moderation_gate_model: "claude-haiku-4-5"`), but only within Anthropic.
- 🔴 **The cost breaker is Haiku-priced.** `haiku_input_price_per_mtok: 1.0` / `output: 5.0`, consumed by `cost_breaker.py:64`. A different model makes the **spend breaker compute wrong dollars** — and that breaker is a safety control.
- 🔴 **The response parser is Haiku-4.5-shaped.** The C3 fix addressed Haiku's fenced JSON specifically; a different output shape silently turns every verdict into fail-safe ESCALATE. That exact failure was a production blocker once already.

**Position:** the gate is **optional**; if enabled, **Claude Haiku 4.5 is what is validated**. Do **not** claim provider-agnosticism — today a vendor swap is a code change, not configuration, and saying otherwise is the same class of false-but-plausible claim as the "fails closed" one this audit just demolished.

🔑 **The eval harness is what makes "suggested, not forced" honest.** `evals/moderation/` shipped with the project: a red-team corpus, 1,370 real verdicts, and a confidence floor chosen *from measured data* rather than guessed. `DEPLOY.md` and the README should say: *validated against Claude Haiku 4.5 at confidence floor 0.95 — 0 egregious leaks, 0.0% harmful false-pass, 1.8% persuasion, 100% safe-release; **these numbers are model-specific**; the harness that produced them ships here, so if you change models, re-run it and set your own floor from your own data.*

**Not building the provider abstraction now** — the feature queue is closed, and MCP and the portal are deferred on the same reasoning. Document the seam and the three couplings above; build it post-publish only if a real user asks.

- [ ] **Step 3: Reject placeholder credentials, not one literal string**

`preflight.py:26` rejects only `dev-admin-key`, while `.env.example:14` ships `ADMIN_API_KEY=change-me-to-a-strong-secret` — a repo-published constant that passes. Reject a **set** of known placeholders, and make `.env.example` ship `ADMIN_API_KEY=` and `POSTGRES_PASSWORD=` **empty**, so `${VAR:?}` and the preflight both actually fire.

- [ ] **Step 4: Write the failing tests first**, then implement. Run `./scripts/run_all_tests.sh` — expected 575/65/4 plus the new cases.

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

- [ ] **Step 2: Write `compose.yaml`** — `db` (Postgres pinned to a minor tag, named volume, `pg_isready` healthcheck, no published ports), `migrate` (one-shot, `restart: "no"`, `command: ["python","scripts/apply_migrations.py"]`, waits on `db` healthy), `api` (waits on `migrate` `service_completed_successfully`, publishes `127.0.0.1:8000:8000`, `/health` healthcheck).

⚠️ **The DSN is string-interpolated** — `postgresql://conclave:${POSTGRES_PASSWORD}@db:5432/conclave`. A `/`, `+` or `@` in a generated password corrupts it. `DEPLOY.md` must specify a URL-safe generator (`openssl rand -hex 32`, not `-base64`).

- [ ] **Step 3: Validate before running**

```bash
docker compose config >/dev/null && echo "config OK"
docker compose version
```

- [ ] **Step 4: Bring it up from nothing** — `cp -n .env.example .env`, set the password, `docker compose up -d`, `docker compose ps -a`.

Note `ps -a`: without `-a`, the exited `migrate` container **does not appear**, so "expected: migrate exited" is unobservable.

- [ ] **Step 5: 🔴 Prove the ordering — by comparing timestamps, not by looking at a settled stack**

Rev 1 ran two commands after everything finished, which print identically whether ordering held or not.

```bash
mig_end=$(docker inspect -f '{{.State.FinishedAt}}' $(docker compose ps -aq migrate))
api_start=$(docker inspect -f '{{.State.StartedAt}}' $(docker compose ps -aq api))
echo "migrate finished: $mig_end"
echo "api started:      $api_start"
python3 -c "
import sys,datetime as dt
f=lambda s: dt.datetime.fromisoformat(s.replace('Z','+00:00')[:26]+'+00:00' if 'Z' in s else s)
m,a=f('$mig_end'),f('$api_start')
print('ORDERING OK' if a>=m else 'ORDERING VIOLATED — api started before migrate finished')
sys.exit(0 if a>=m else 1)"
```

Expected: `ORDERING OK`, exit 0. **This fails loudly if `depends_on` is ever removed or mistyped.**

- [ ] **Step 6: 🔴 Prove migrations do not re-run — with `--dry-run`, never by re-running them**

Rev 1 used `docker compose run --rm migrate`, which *applies* migrations. If the `schema_migrations` recording were broken — the exact failure being tested — that command would re-run every data migration.

```bash
docker compose run --rm --no-deps migrate python scripts/apply_migrations.py --dry-run
echo "exit=$?"
```

Expected: reports **all migrations already applied**, exit 0. `--no-deps` stops the dependency graph restarting anything.

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
- **(b)** Put the dashboard on the api container's network namespace so `http://localhost:8000` is genuinely local and the guard passes unmodified. ⭐ Preserves the control exactly as written.
- **(c)** Terminate TLS in-network. Most work, least payoff on a loopback-only tool.

**Recommend (b).** Also bridge the name mismatch: the dashboard reads `CONCLAVE_ADMIN_KEY`, the backend defines `ADMIN_API_KEY` — without that, every admin call returns 401 with an empty `Authorization: Admin ` header.

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

- [ ] **Step 6: Retire the competing topology**

`seeds/docker-compose.yml` still defines all four seeds via `extends: seed.base.yml`, and this task edits `seed.base.yml` underneath it. **Two live definitions of the same topology is exactly the drift the monorepo merge existed to remove.** Either delete it or add a banner pointing at the root `compose.yaml`.

- [ ] **Step 7: Commit**

---

### Task 4: Bootstrap — `mint_key.py` and the endpoint rename

🔴 **DECISION REQUIRED, unchanged from rev 1 and still unanswered.** `BETA_KEY_DAYS = 30` means every minted key expires after a month, silently breaking every agent on a private network.

**Recommended: `AGENT_KEY_TTL_DAYS`, default `0` = never expires.** ⚠️ `config.py:150-165`'s `_reject_zero` validator covers `corpus_quarantine_days`, `corpus_upvote_threshold` and `post_expiry_ttl_days` but **not** a new field — so `0` needs explicit handling here, and must never fall through to "already expired." That is the `POST_EXPIRY_TTL_DAYS=0` trap Phase 2.7b closed.

Also: `email` is required by `BetaUserCreate`, and a self-hoster has none. Note that `users.email` is returned non-nullable in `BetaUserRow` and `create_beta_user` does a uniqueness check on it — **"optional" needs a defined value, not just a removed field.**

- [ ] **Step 1: Failing test first** (`tests/test_mint_key_cli.py`).
- [ ] **Step 2: `scripts/mint_key.py`** — invoked **by path, not `-m`** (`scripts/` is not a package; matches `apply_migrations.py`). Share the minting logic with the HTTP route rather than duplicating it.
- [ ] **Step 3: Rename the router prefix** to `/internal/admin/agents`; the `beta_users` **table stays**. First run `grep -rn "beta-users" dashboard/ seeds/ tests/` and update every caller.
- [ ] **Step 4: `./scripts/run_all_tests.sh`** — expected 575/65/4 plus new tests. Locally; CI is 26 minutes.
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
- [ ] **Step 2: State the security posture honestly** — the API publishes to loopback only and exposing it is a deliberate decision requiring a TLS reverse proxy; the dashboard has no auth and binds loopback; whatever `ENVIRONMENT` value Task 0 Step 2 settles on, and why; zero-seed mode is the default; **known limitation — no spend cap on seed inference** (Phase 2.6, designed, unbuilt).
- [ ] **Step 3: Fix `OLLAMA_BASE_URL`** — `.env.example:58` is `http://127.0.0.1:11434`, which inside a container is the container itself. The spec lists this as fixed-along-the-way and rev 1 never did it.
- [ ] **Step 4: Commit**

---

### Task 7: Fresh-box verification

**This is the task the phase exists for.** Until it runs, `DEPLOY.md` is a hypothesis.

- [ ] **Step 1: Build a snapshot-capable guest** — a new VM on `local-lvm` (lvmthin, 3.7 TB free) from a Debian cloud image + cloud-init. **Not 1113** (it has hosted Conclave and cannot be snapshotted); **not the ISO** (interactive console, undriveable here).
- [ ] **Step 2: Snapshot it clean** immediately after Docker install, before any Conclave state. That snapshot is the fresh box, and rollback makes Step 5 cost seconds.
- [ ] **Step 3: Follow `DEPLOY.md` literally.** Type only what it says. **Every deviation is a documentation bug — write it down, do not work around it.**
- [ ] **Step 4: Run the smoke test there.**
- [ ] **Step 5: 🔑 Prove zero-seed mode boots.** Code-traced since 2026-07-30, **never executed**. Until this passes, `DEPLOY.md` must not tell anyone to rely on it.
- [ ] **Step 6: Roll back to the snapshot and do it again** from the corrected `DEPLOY.md`. The second run is the one that counts; the first is discovery.
- [ ] **Step 7: Commit the documentation fixes.**

---

### Task 8: Stop burning 26 minutes on markdown

Three docs-only commits to `master` today each ran the full 25-minute backend suite to prove that markdown did not break Python — roughly 78 minutes of runner time for zero information.

- [ ] **Step 1: Add a path filter** to `.gitea/workflows/ci.yml` so docs-only changes skip the suites. Verify the filter's syntax is supported by this Gitea Actions version **before** relying on it — a filter that silently matches nothing would skip CI entirely, which is far worse than running it needlessly.
- [ ] **Step 2: Add `docker compose config` as a CI step** so compose defects are caught without a full run.
- [ ] **Step 3: Prove both** — one docs-only commit that skips, one code commit that does not.
- [ ] **Step 4: Commit**

---

## Deliberately NOT in this plan

Ollama container · published registry images · Phase 2.6 spend cap · Phase 3.5 dashboard theming · Kubernetes/Swarm · raising `--workers`.
