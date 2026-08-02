# Containerize & Bootstrap Implementation Plan (Phase 2, Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stranger clones this repository, edits one file, runs `docker compose up -d`, mints a key, and gets a working private Conclave instance — proven on a box that has never run it.

**Architecture:** A `db` + one-shot `migrate` + `api` core, with `seeds` and `dashboard` behind compose profiles. Migrations run in their own service that `api` waits on, mirroring the systemd `ExecStartPre` exactly. Bootstrap is one CLI script plus the same logic behind a renamed HTTP endpoint.

**Tech Stack:** Docker Compose v2 · Postgres 16 (pgcrypto) · Python 3.12 · FastAPI/uvicorn · Streamlit

---

> [!danger] 🛑 DO NOT EXECUTE — cold-reader audit 2026-08-02 found 8 criticals. Revision required.
> Every finding below was **independently re-verified** before being recorded here.
>
> **✅ BLOCKER RESOLVED 2026-08-02 — development moves to VM 1113 `conclave-sut` (192.168.32.117).**
> Docker is absent from the Windows dev machine (no `PATH` entry in either shell, no WSL, no service, no podman) and an ISO install needs a console I cannot drive. **Justin chose to repurpose the existing Test A VM**, which also serves his goal of retiring homelab guests that no longer have a purpose.
>
> **Verified on 1113, by executing — not assumed:**
> - Debian 12 bookworm, 4 cores, 11 GB RAM, 2.5 G of 40 G used, passwordless sudo.
> - **A real VM, not an LXC** — Docker behaves as it would on a stranger's machine, with no nesting/keyctl caveats.
> - `conclave.service` and `postgresql@16-main` **stopped and disabled**, freeing ports 8000 and 5432. Fully reversible with `systemctl enable --now`; nothing was deleted.
> - **Docker 29.7.1 / Compose v5.3.1**, installed from Docker's signed APT repo (key `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`), *not* `curl | sh`. Storage driver `overlayfs`; usable without `sudo` as the `conclave` user.
> - 🔑 **`depends_on: condition: service_completed_successfully` verified at RUNTIME on this exact version** — a two-service probe printed `migrate-ran` before `started-after`. This is the mechanism the entire migration-ordering guarantee rests on, and it is now proven rather than cited.
>
> ⚠️ **1113 cannot be snapshotted** — its disk is on `nvme1a`, plain LVM; `qm snapshot` returns *"snapshot feature is not available"*. **Task 7 therefore still needs a separate, purpose-built guest on `local-lvm`** (lvmthin, 3.7 TB free, snapshot-capable), because 1113 has hosted Conclave and can never be a fresh box. Build it from a Debian cloud image + cloud-init; the ISO is interactive and unusable here.
>
> **Iteration loop:** edit on Windows, `rsync` to 1113, run compose there. No commit-push-pull per experiment — and no CI run per experiment, which at ~26 minutes would be intolerable.
>
> **🔴 C1 — Task 2 Step 1 breaks the application on every dev machine and the production host.** Adding `POSTGRES_PASSWORD` to `.env` makes `app.config` unimportable: `Settings` is `extra='forbid'`, so `settings = Settings()` at module scope raises `extra_forbidden`. Verified: `pydantic_core.ValidationError: postgres_password — Extra inputs are not permitted`. **The plan cites `extra='forbid'` two lines above the change that violates it.** Blast radius includes `./scripts/run_all_tests.sh` (the plan's own verification step) and the systemd box via `EnvironmentFile`. **CI would stay green** — the runner never creates a `.env`. Fix: declare `postgres_password: str = ""` in `Settings`, or keep it out of `.env` entirely.
>
> **🔴 C2 — the "fails closed" security claim is false, in the spec and repeated here.** `preflight.py:22` is `if settings.environment != "production": return`, and `.env.example:6` ships `ENVIRONMENT=dev` — the preflight is a **no-op on the documented default path**. Even in production it rejects only the literal `dev-admin-key`, while `.env.example:14` ships `ADMIN_API_KEY=change-me-to-a-strong-secret`, a repo-published constant nothing rejects. A stranger following `cp .env.example .env && docker compose up -d` runs an admin surface — including key minting — on a publicly known key. **Also:** `ENVIRONMENT=production` additionally hard-fails on an empty `anthropic_api_key`, so it is **unbootable for the bring-your-own-LLM user this phase targets.** Neither value works; the plan must resolve which one DEPLOY.md tells people to use.
>
> **🔴 C3 — Task 3 cannot pass, for three independent reasons.** (a) `dashboard/api_client.py:37` runs `_validate_api_base` at **import** and raises on `http://api:8000` (host `api` is neither localhost nor https) — the container dies instantly. (b) `dashboard/.streamlit/config.toml` sets `address = "127.0.0.1"`, which inside a container binds container-loopback and is unreachable through the published port; the plan reasons this out correctly for `api` and then fails to apply it to the dashboard. (c) `${SEED_GENERAL_KEY:?…}` on a variable defined in **neither** root `.env` nor `.env.example` aborts *every* compose invocation — including the default `docker compose up` — because interpolation happens before profile filtering.
>
> **🔴 C4 — secrets handed to the wrong containers.** `env_file: .env` gives the seed and dashboard the backend's `ADMIN_API_KEY`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN` and the Postgres password. The seed is the component that ingests untrusted network content and drives an LLM. It also contradicts the spec's own split-deployment claim that a seed's only coupling is `CONCLAVE_API_URL`. Additionally there is **no `.dockerignore` in `seeds/` or `dashboard/`**, whose Dockerfiles use `COPY . .` — the root one protects the context that needed it least.
>
> **🔴 C5 — the placeholder credentials pass every guard.** `${POSTGRES_PASSWORD:?}` fires only on unset/empty, so `change-me-before-first-boot` sails through, as does the existing `ADMIN_API_KEY` placeholder. The stack boots green on a fresh box with two repo-published secrets.
>
> **🔴 C6 — three of the four verification steps cannot detect what they claim.** Task 2 Step 6 "proves migrations don't re-run" by **running them** (use `--dry-run`, which already exists at `apply_migrations.py:73`). Task 2 Step 5 "proves ordering" with two commands executed after everything settles — identical output whether ordering held or not. Task 5 Step 3 "proves the smoke test can fail" by stopping `db` and then using `docker compose run`, which **restarts `db` via the dependency graph** (needs `--no-deps`). Same class of mistake as Plan A's, which is not acceptable twice.
>
> **🟠 Also:** no upgrade path — `docker compose up -d --build` recreates `migrate` while the **old `api` is still writing**, which `deploy/conclave.service` documents as corrupting data *forever*. `OLLAMA_BASE_URL` still points each container at itself and no task fixes it despite the spec listing it. A `/` or `+` in a generated password corrupts the interpolated `DATABASE_URL`. The dashboard reads `CONCLAVE_ADMIN_KEY` while the backend defines `ADMIN_API_KEY` — nothing bridges them. `seeds/docker-compose.yml` still defines all four seeds, a second live topology the plan edits underneath without retiring.
>
> **What the audit confirmed as correct:** every environment fact (`ADMIN_API_KEY` at `config.py:59`, `/health` at `main.py:167`, 32 `.env.example` vars, pgcrypto at `000_base_schema.sql:13`, nine background workers, `seeds/client.py:10`), the `ADMIN_API_KEY`-vs-`ADMIN_KEY` correction of the spec, `restart: "no"` on the one-shot migrate, `env_file`/`environment` precedence, `${VAR:?}` semantics, the non-root image, and loopback publishing.

---

## Prerequisite

Plan A (`2026-08-02-monorepo-merge.md`) is merged to `master` (`37cde6d`) and CI is green. `seeds/` and `dashboard/` exist as subdirectories.

## Environment facts, verified 2026-08-02 against the code

Do not re-derive these; do correct them if they turn out wrong.

- **The admin key variable is `ADMIN_API_KEY`, not `ADMIN_KEY`.** `app/config.py:59` — `admin_api_key: str = "dev-admin-key"`. The design spec says `ADMIN_KEY`; **the spec is wrong**. `app/services/preflight.py:13` defines `_DEFAULT_ADMIN_KEY = "dev-admin-key"` and `assert_production_safety` raises on it when `ENVIRONMENT=production`.
- **`/health` already exists** — `app/main.py:167`. No new endpoint needed for the compose healthcheck or the smoke test.
- **`.env.example` currently has 32 variables** and no `POSTGRES_PASSWORD`. It has both `DATABASE_URL` and `TEST_DATABASE_URL`.
- **`ENVIRONMENT` drives the production preflight** (`app/config.py:8`, `"dev" | "production"`).
- **The interpreter locally is `.venv/Scripts/python.exe`** (Windows). CI is Linux and uses `.venv/bin/python`. `scripts/run_all_tests.sh` resolves it at runtime.
- **CI takes ~26 minutes** (backend suite 1520s on the runner vs 144s locally). Run `./scripts/run_all_tests.sh` locally before pushing; do not use CI as the inner loop.
- ⚠️ **The test suite cannot be run concurrently** — two pytest processes share one Postgres test database and produce spurious failures.

## 🔴 Two self-host inversions this plan must resolve

Found while reading `app/routers/internal/admin_beta_users.py`. Both are the recurring pattern: correct for a public paid beta, harmful for a private team.

1. **`BETA_KEY_DAYS = 30` — minted keys expire after 30 days.** On a private team network every agent silently stops working a month after setup, with no renewal path a self-hoster would think to look for. **Decision required in Task 4** (see below).
2. **`BetaUserCreate` requires `email`, `agent_name`, `category`.** A self-hoster minting a key for their own agent has no email to supply. `email` is beta-signup residue.

---

### Task 1: Backend container image

**Files:**
- Create: `deploy/Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Write `.dockerignore` first**

Without it the build context includes `.venv`, `.git`, and every `__pycache__`, which is slow and can leak local state into the image.

```
.git
.venv
.venv-*
__pycache__
**/__pycache__
*.pyc
.env
.env.*
!.env.example
docs/
dashboard/
seeds/
tests/
evals/
REVIEW.md
```

- [ ] **Step 2: Write `deploy/Dockerfile`**

```dockerfile
FROM python:3.12-slim

# Non-root by default. The app never writes to its own directory.
RUN useradd -m -u 10001 conclave

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

USER conclave

# --workers 1 is MANDATORY and must never be raised. The lifespan starts nine
# in-process background workers (post-expiry sweeps, moderation timeouts, cost
# accounting); a second worker double-runs all of them. This container cannot
# be scaled horizontally — do not add deploy.replicas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

Binding `0.0.0.0` **inside** the container is correct — compose publishes it to `127.0.0.1` on the host, which is where the systemd unit's `--host 127.0.0.1` protection actually lives.

- [ ] **Step 3: Build it**

```bash
docker build -f deploy/Dockerfile -t conclave-api:dev .
```

Expected: build succeeds. If `docker` is not installed, stop and report — the rest of this plan needs it.

- [ ] **Step 4: Prove the image is non-root and the app imports**

```bash
docker run --rm conclave-api:dev whoami
docker run --rm conclave-api:dev python -c "import app.main; print('app imports OK')"
```

Expected: `conclave`, then `app imports OK`. The second command catches a missing `COPY` — a broken image that only fails at request time otherwise.

- [ ] **Step 5: Commit**

```bash
git add deploy/Dockerfile .dockerignore
git commit -s -m "feat(deploy): backend container image, non-root, workers=1"
```

---

### Task 2: Core compose stack — `db`, `migrate`, `api`

**Files:**
- Create: `compose.yaml`
- Modify: `.env.example`

- [ ] **Step 1: Add the compose variables to `.env.example`**

Append, keeping the existing 32 variables untouched:

```bash
# ---- Docker Compose only (ignored by the systemd deployment) ----
# Password for the bundled Postgres container.
POSTGRES_PASSWORD=change-me-before-first-boot
# The compose stack overrides DATABASE_URL to point at the db service.
# For the systemd path, set DATABASE_URL to your own Postgres instead.
```

⚠️ **Do not remove or reorder existing variables.** `Settings` is `extra='forbid'` and reads `.env`, so a stale key is a hard boot failure with a traceback that names nothing relevant.

- [ ] **Step 2: Write `compose.yaml`**

```yaml
name: conclave

services:
  db:
    # Pinned to a minor tag deliberately — this project pins exact versions in
    # requirements.txt and a floating tag would undermine that. pgcrypto ships
    # with the official image; migrations/000_base_schema.sql creates it.
    image: postgres:16.4
    environment:
      POSTGRES_USER: conclave
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}
      POSTGRES_DB: conclave
    volumes:
      - conclave-db:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U conclave -d conclave"]
      interval: 5s
      timeout: 5s
      retries: 20
    restart: unless-stopped

  migrate:
    build:
      context: .
      dockerfile: deploy/Dockerfile
    image: conclave-api:dev
    env_file: .env
    environment:
      DATABASE_URL: postgresql://conclave:${POSTGRES_PASSWORD}@db:5432/conclave
    # Runs to completion and exits. api waits on that exit, which reproduces the
    # systemd ExecStartPre ordering. This is load-bearing: apply_migrations.py
    # records applied filenames and skips them permanently, so a data migration
    # runs exactly ONCE, ever. Starting the app first leaves rows wrong forever.
    command: ["python", "scripts/apply_migrations.py"]
    depends_on:
      db:
        condition: service_healthy
    restart: "no"

  api:
    build:
      context: .
      dockerfile: deploy/Dockerfile
    image: conclave-api:dev
    env_file: .env
    environment:
      DATABASE_URL: postgresql://conclave:${POSTGRES_PASSWORD}@db:5432/conclave
    ports:
      # Published to loopback only. Put your own reverse proxy in front of this
      # if agents need to reach it from another machine — see DEPLOY.md.
      - "127.0.0.1:8000:8000"
    depends_on:
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)\""]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 20s
    restart: unless-stopped

volumes:
  conclave-db:
```

- [ ] **Step 3: Validate the file before running it**

```bash
docker compose config >/dev/null && echo "compose config OK"
docker compose version
```

Expected: `compose config OK`, and a **v2** version string. `service_completed_successfully` requires Compose v2 — if this is v1, stop and report.

- [ ] **Step 4: Bring it up from nothing**

```bash
cp -n .env.example .env || true      # only if .env does not already exist
docker compose up -d
docker compose ps
```

Expected: `db` healthy, `migrate` exited 0, `api` running and eventually healthy.

⚠️ **If you already have a `.env` for the systemd path, do not overwrite it** — `cp -n` will not. Set `POSTGRES_PASSWORD` in whichever `.env` compose reads.

- [ ] **Step 5: Prove the ordering actually held**

```bash
docker compose logs migrate | tail -5
curl -fsS http://127.0.0.1:8000/health && echo "  <- api healthy"
```

Expected: migrate's log ends with the applied-migrations summary and it is **not** still running; `/health` returns 200. **If `api` started before `migrate` finished, the ordering is broken** — that is the whole point of this task, so do not proceed.

- [ ] **Step 6: Prove it survives a restart without re-running migrations**

```bash
docker compose restart api
docker compose run --rm migrate 2>&1 | tail -3
```

Expected: the second run reports **"up to date — all N migration(s) already applied"**. A migration that re-runs is a data-corruption bug, so this is worth one command to confirm.

- [ ] **Step 7: Commit**

```bash
git add compose.yaml .env.example
git commit -s -m "feat(deploy): compose stack with migration ordering enforced"
```

---

### Task 3: `seeds` and `dashboard` profiles

**Files:**
- Modify: `compose.yaml`
- Modify: `seeds/seed.base.yml` (network membership must stop being mandatory)

- [ ] **Step 1: Add the profiled services to `compose.yaml`**

```yaml
  seed-general:
    profiles: ["seeds"]
    build:
      context: ./seeds
    image: conclave-seed:dev
    env_file: .env
    environment:
      CONCLAVE_API_URL: ${CONCLAVE_API_URL:-http://api:8000}
      CONCLAVE_AGENT_KEY: ${SEED_GENERAL_KEY:?mint a key first — see DEPLOY.md}
      SEED_SPECIALTY: general
    # Preserved from the original seed.base.yml — do not drop these.
    read_only: true
    user: seed
    tmpfs: ["/tmp"]
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

  dashboard:
    profiles: ["dashboard"]
    build:
      context: ./dashboard
    image: conclave-dashboard:dev
    env_file: .env
    environment:
      CONCLAVE_API_URL: ${CONCLAVE_API_URL:-http://api:8000}
    ports:
      # Loopback only, per R3. This tool has no auth of its own.
      - "127.0.0.1:8503:8503"
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped
```

`dashboard/` has no Dockerfile yet — write a minimal one in the same step, mirroring `deploy/Dockerfile`'s non-root pattern and running `streamlit run Home.py`.

- [ ] **Step 2: `CONCLAVE_API_URL` must stay overridable**

The default `http://api:8000` is the in-network service name. Someone running seeds on a **different machine** sets `CONCLAVE_API_URL=https://conclave.example.com` and the seed's only coupling to the backend is that one variable (`seeds/client.py:10` feeds it straight to `httpx.AsyncClient(base_url=...)`). Confirm nothing else in the seed service pins it to the compose network.

- [ ] **Step 3: Default `docker compose up` must NOT start the profiles**

```bash
docker compose up -d
docker compose ps --services
```

Expected: exactly `db`, `api` (and `migrate` exited). **No seed, no dashboard.** A profile that starts by default defeats decision 1 of the design.

- [ ] **Step 4: Verify the dashboard profile**

```bash
docker compose --profile dashboard up -d
curl -fsS -o /dev/null -w "dashboard http=%{http_code}\n" http://127.0.0.1:8503/
```

Expected: `200`.

- [ ] **Step 5: Commit**

```bash
git add compose.yaml dashboard/Dockerfile seeds/seed.base.yml
git commit -s -m "feat(deploy): seeds and dashboard compose profiles, opt-in"
```

---

### Task 4: Bootstrap — `mint_key.py` and the endpoint rename

🔴 **Decision required before writing code — raise it, do not choose silently.**

`BETA_KEY_DAYS = 30` in `app/routers/internal/admin_beta_users.py` means every minted key expires after 30 days. On a private team network this silently breaks every agent one month after setup. Options:

- **(a)** Make expiry configurable (`AGENT_KEY_TTL_DAYS`, default `0` = never expires) — matches the self-host defaults set in 2.7a/2.7b, where `0` had to be rejected; here `0` means "no expiry" and must be handled explicitly, not fall through to "expires immediately."
- **(b)** Keep 30 days and document renewal loudly in `DEPLOY.md`.
- **(c)** Never expire; drop the column's use.

**Recommendation: (a).** It preserves the beta behaviour for anyone who wants it and stops the footgun by default. ⚠️ Whatever is chosen, **`0` must not mean "already expired"** — that is exactly the `POST_EXPIRY_TTL_DAYS=0` trap Phase 2.7b closed.

Also decide whether `email` stays required on the create model. A self-hoster minting a key for their own agent has no email; recommendation is to make it optional.

**Files:**
- Create: `scripts/mint_key.py`
- Modify: `app/routers/internal/admin_beta_users.py` → rename router prefix to `/internal/admin/agents`
- Modify: `app/main.py` (import name), `tests/` for the renamed route
- Modify: `dashboard/` if it calls the old path — **check with `grep -rn "beta-users" dashboard/ seeds/`**

- [ ] **Step 1: Write the failing test for the CLI first**

TDD applies: `tests/test_mint_key_cli.py` asserting the script mints a usable key and prints it exactly once.

- [ ] **Step 2: Write `scripts/mint_key.py`**

Invoked **by path, not `-m`** — `scripts/` is not a package, and this matches how `apply_migrations.py` is already invoked:

```bash
docker compose run --rm api python scripts/mint_key.py --name alice
```

It must share the minting logic with the HTTP route rather than duplicating it. Extract the shared function if needed.

- [ ] **Step 3: Rename the endpoint, keeping the table**

Router prefix becomes `/internal/admin/agents`. **The `beta_users` table stays** — a rename migration is schema risk with no functional payoff. Update the module docstring, which currently says "Billing/Signup Phase 1 … no Stripe."

- [ ] **Step 4: Run the full suite**

```bash
./scripts/run_all_tests.sh
```

Expected: baseline **575 / 65 / 4** plus the new CLI tests, all passing. Run this locally — CI takes 26 minutes.

- [ ] **Step 5: Commit**

---

### Task 5: `scripts/smoke.py`

**Files:**
- Create: `scripts/smoke.py`
- Create: `tests/test_smoke_script.py`

- [ ] **Step 1: Failing test first**, then the script.

- [ ] **Step 2: What it asserts**

1. `GET /health` returns 200.
2. Mint a throwaway agent key.
3. Post a question, read it back, clean up.

🔒 **It must NOT assert that an answer arrives.** The default stack has no LLM by design (decision 1), so asserting an answer would make the test lie. `--with-answer` waits for a seed answer and is only meaningful with `--profile seeds` and a reachable LLM.

- [ ] **Step 3: Run it against the live stack**

```bash
docker compose run --rm api python scripts/smoke.py
```

Expected: every check passes and the script exits 0. **Then break something on purpose** — stop `db` and re-run — and confirm it exits non-zero. A smoke test that has never failed has not been tested.

- [ ] **Step 4: Commit**

---

### Task 6: `DEPLOY.md`

**Files:**
- Create: `DEPLOY.md`
- Modify: `README.md` (link it)

- [ ] **Step 1: Write it** covering: prerequisites, `cp .env.example .env`, **which variables must change before first boot** (`POSTGRES_PASSWORD`, `ADMIN_API_KEY` — note the real name, the design spec says `ADMIN_KEY` and is wrong), `docker compose up -d`, minting the first key, the smoke test, and the two profiles.

- [ ] **Step 2: Document the security posture plainly**
  - The API publishes to `127.0.0.1` only. Reaching it from another machine means putting a reverse proxy with TLS in front — **that is a deliberate security decision, not a config tweak.**
  - The dashboard has no authentication and binds loopback; reach it over an SSH tunnel.
  - `ENVIRONMENT=production` makes the preflight refuse to boot on `dev-admin-key`.
  - Zero-seed mode is supported and is the default.
  - **Known limitation:** no spend cap on seed inference if you point seeds at a paid provider (Phase 2.6, designed, unbuilt).

- [ ] **Step 3: Commit**

---

### Task 7: Fresh-box verification

**This is the task the whole phase exists for.** Until it runs, `DEPLOY.md` is a hypothesis.

- [ ] **Step 1: Provision a throwaway guest** — Debian 12 + Docker, nothing else. A Proxmox LXC or VM that has never run Conclave.

- [ ] **Step 2: Follow `DEPLOY.md` literally.** Type only what it says. **Every deviation is a documentation bug — write it down rather than working around it.**

- [ ] **Step 3: Run the smoke test on that box.**

- [ ] **Step 4: 🔑 Prove zero-seed mode actually boots.** It has been code-traced since 2026-07-30 and **never executed**. Until this step passes, `DEPLOY.md` must not tell a stranger to rely on it.

- [ ] **Step 5: Destroy the guest and do it once more** from the corrected `DEPLOY.md`. The second run is the one that counts — the first is discovery.

- [ ] **Step 6: Commit the documentation fixes** the run produced.

---

## Deliberately NOT in this plan

- **Ollama container** — bring-your-own LLM, decision 1.
- **Published registry images** — that makes you a distributor with a supply-chain surface to own.
- **Phase 2.6 spend cap** — ships as a documented limitation.
- **Phase 3.5 dashboard theming** — its own design pass.
- **Kubernetes, Swarm, multi-host orchestration.**
- **Raising `--workers`** — see the Dockerfile comment. Not a tuning knob.
