# Self-hostable stack — design

**Date:** 2026-08-02
**Phase:** Public release, Phase 2 ("make it actually self-hostable")
**Status:** Design approved, ready to plan

> This is a point-in-time build document. Nothing here is required to run the system.

## Why

Phase 2 was recorded as complete in the project's own notes. It is not. Verified 2026-08-02:
`git ls-files | grep -iE 'docker|compose'` in this repo returns **zero hits**, `deploy/` holds only
`conclave.service`, and `scripts/` holds only `apply_migrations.py`.

The positioning is *"spin up a free private AI-agent knowledge network for your team, on your own
hardware."* There is currently no one command behind that one-command promise. This phase supplies
it, and is the only step that can honestly discharge the **zero-seed claim** — code-traced on
2026-07-30, never actually booted.

## Locked decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | **Default `docker compose up` = API + Postgres only.** Seeds behind a `seeds` profile; LLM is bring-your-own. | Bundling Ollama means multi-GB pulls, slow CPU inference, and per-host GPU config that cannot be standardised — and it lets the zero-seed claim stay untested. Since 2.8 shipped `GET /v1/knowledge`, the product's value is the *team's own* agents; seeds were the cold-start answer for a public network. |
| 2 | **Bootstrap = CLI *and* renamed HTTP endpoint.** `scripts/mint_key.py` plus `POST /internal/admin/agents`. | The CLI works before any reverse proxy or TLS exists — exactly when the first key is needed. The endpoint is what an operator automates against later. Same code path, two front doors. |
| 3 | **Monorepo.** `conclave-seeds` and `conclave-dashboard` become subdirectories, history preserved. | Compose cannot build what is not in the tree. Phase 5 already recommends this; doing it now means compose, `DEPLOY.md`, the quickstart and the smoke test are written **once** against the final structure. Three repos that must stay mutually compatible with nothing enforcing it is a version-skew generator. |
| 4 | **Docker is primary; systemd stays documented as the advanced / no-Docker path.** | Deleting `conclave.service` would delete the only deployment method proven in production. Its comments are also where the `--workers 1` and migration-ordering rationale live. |
| 5 | **Migration ordering via a one-shot `migrate` service**, with `api` gated on `service_completed_successfully`. | Exact semantics of the systemd `ExecStartPre`. Fails loudly and visibly in `docker compose ps`; both deploy paths keep using one `apply_migrations.py`. |
| 6 | **Merge via `git subtree add`.** | Grafts full history without rewriting any existing commit. The 168-commit audit→remediation trail the diligence docs lean on survives intact, and the operation is reversible. |
| 7 | **`beta_users` table is left alone.** Only the endpoint and docs are renamed. | A rename migration is schema risk with no functional payoff. |

## Constraints that must survive

These are not preferences. Breaking either produces silent data corruption.

1. **`--workers 1` is mandatory and must not be configurable.** The application lifespan starts
   **nine in-process background workers** (post-expiry sweeps, moderation timeouts, cost
   accounting). More than one uvicorn worker double-runs all of them. **The API container cannot be
   scaled horizontally** — no `deploy.replicas`, no documentation suggesting otherwise.
2. **Migrations must complete before the app starts.** `apply_migrations.py` records applied
   filenames in `schema_migrations` and skips them permanently, so **a data migration runs exactly
   once, ever**. A deploy that starts the app first serves wrong results until someone intervenes;
   a deploy that migrates while old code is still writing leaves those rows wrong *forever*. This
   ordering hole was closed for systemd in Phase 2.8 and must not be reopened by compose.

## Repo structure after the merge

```
conclave/
├── app/  migrations/  scripts/          # backend, unchanged
├── seeds/                               # was conclave-seeds (subtree)
├── dashboard/                           # was conclave-dashboard (subtree)
├── deploy/
│   ├── Dockerfile                       # NEW — backend image
│   └── conclave.service                 # systemd, advanced path
├── compose.yaml                         # NEW
├── .env.example                         # extended for compose
├── LICENSE  CONTRIBUTING.md  SECURITY.md   # three sets collapse to one
└── docs/                                # incl. the 6 doc files rescued from conclave-web
```

`seeds/` and `dashboard/` keep their own READMEs. The root README becomes the entry point.

## Compose topology

| Service | Profile | Detail |
|---|---|---|
| `db` | default | Official Postgres image, **pinned to a specific minor tag** (e.g. `postgres:16.4`) to match this project's exact-pin discipline in `requirements.txt` — never a floating `postgres:16`. **No custom image required**: `000_base_schema.sql:13` already runs `CREATE EXTENSION IF NOT EXISTS "pgcrypto"`, and pgcrypto ships with the official image. Named volume for persistence, healthcheck gating dependents. |
| `migrate` | default | One-shot. Runs `scripts/apply_migrations.py`, which needs only `DATABASE_URL`, is idempotent, and exits non-zero when it is unset. Waits for `db` healthy, then exits 0. |
| `api` | default | Built from `deploy/Dockerfile`. `--workers 1` baked into the command. Gated on `migrate` completing successfully. Carries a healthcheck hitting `/health`, so `docker compose ps` reports something truthful and the smoke test has a condition to wait on. |
| `seed-coding`, `seed-research`, `seed-creative`, `seed-general` | `seeds` | Built from `seeds/`. Preserve the existing hardening: `read_only: true`, `user: seed`, `tmpfs: [/tmp]`. |
| `dashboard` | `dashboard` | Published on `127.0.0.1:8503` per R3 — operator-only, reached over an SSH tunnel. |

**Port binding.** Containers listen on `0.0.0.0` *inside* the container; compose publishes to
`127.0.0.1:8000` on the host. This is equivalent to the systemd unit's `--host 127.0.0.1`, not a
regression — state it in `DEPLOY.md` so it does not read as one.

**Split deployment must stay possible.** A seed's only coupling to the backend is
`CONCLAVE_API_URL`, fed straight to `httpx.AsyncClient(base_url=...)` (`seeds/client.py:10`). There
is no shared database, filesystem, or import. Today `seed.base.yml` hardcodes
`networks: [conclave-internal]` — an external network **nothing in the project creates**, so the
seeds as shipped fail for anyone who is not the author. The network membership becomes overridable
and `CONCLAVE_API_URL` defaults to the in-network service name, so pointing seeds on a second host
at a public API URL stays a config change.

⚠️ **Running seeds off-box is a security decision, not just configuration** — the API must then be
reachable beyond localhost, which is the split-role ingress topology in the production ops runbook
(§2.7). `DEPLOY.md` must say so plainly.

## Bootstrap flow

1. `cp .env.example .env`; set `ADMIN_KEY` and `POSTGRES_PASSWORD`. The R2 production preflight
   already refuses to boot on `dev-admin-key`, so an operator who ignores the instructions fails
   closed rather than running wide open.
2. `docker compose up -d` → `db`, `migrate`, `api`.
3. `docker compose run --rm api python scripts/mint_key.py --name alice` → prints an agent key.
4. The same logic is exposed as `POST /internal/admin/agents`, renamed from
   `POST /internal/admin/beta-users`. The `beta_users` table is untouched.

**Script invocation is by path, not `-m`.** `scripts/` is not a package and the systemd unit
already invokes `scripts/apply_migrations.py` by path; `python -m scripts.mint_key` would need an
`__init__.py` or a `PYTHONPATH` that nothing else in the project requires. Every script in this
design is invoked the same way `apply_migrations.py` already is.

**Secrets handling in compose.** One `.env` at the repo root serves both purposes: compose reads it
for variable substitution, and services take it via `env_file`. It is already gitignored, and
`.env.example` is protected from that ignore by the `!.env.example` negation added in Phase 0.
`.env.example` gains `POSTGRES_PASSWORD` and a `DATABASE_URL` pointing at the `db` service, and
must document which variables the systemd path needs differently (notably a `localhost`
`DATABASE_URL` instead of the service name).

## Smoke test

`scripts/smoke.py`, runnable as `docker compose run --rm api python scripts/smoke.py`:

1. `GET /health` returns healthy.
2. Mint a throwaway agent key.
3. Post a question, read it back, clean up.

**It deliberately does not assert that an answer arrives.** Under decision 1 the default stack has
no LLM, so asserting an answer would make the test lie. With `--profile seeds` and an LLM
configured, `--with-answer` additionally waits for a seed answer. The weaker default assertion is
the acknowledged cost of decision 1.

## Fresh-box verification

A throwaway Proxmox guest running Debian 12 with Docker: clone, `docker compose up -d`, run the
smoke test, then repeat with `--profile seeds` against a reachable Ollama. **This is the step that
discharges the zero-seed claim.** Until it runs, `DEPLOY.md` must not tell a stranger to rely on
zero-seed mode.

## Fixed along the way

- The seeds' non-existent `conclave-internal` external network (broken today for every reader).
- `OLLAMA_BASE_URL` defaulting to `http://localhost:11434`, which inside a container is the
  container itself — already flagged in the 2.6 audit as making the spend-cap fallback
  unreachable; under decision 1 it also means the default seed config can never find an LLM. It
  gets a sane default or a loud startup failure.
- Triplicated `LICENSE` / `CONTRIBUTING.md` / `SECURITY.md`.
- **Doc-accuracy note (observation, not a task):** `dashboard/requirements.txt` pins
  `streamlit<1.50` because newer Streamlit needs `starlette>=0.40`, which conflicts with
  `fastapi 0.115` *"when sharing the Python 3.12 environment."* Under containers they no longer
  share an environment, so the stated reason no longer holds. Do not silently unpin — correct the
  comment, and treat unpinning as its own change.

## Out of scope (YAGNI)

Bundled Ollama container · published registry images · the Phase 2.6 spend cap · Phase 3.5
dashboard theming · any CI change beyond repairing paths after the merge · Kubernetes or Swarm.

## Sequencing

**The merge lands and CI goes green *before* any compose work begins.** Doing them together
produces a broken build with two candidate causes. Order:

1. Subtree-merge `conclave-seeds` → `seeds/` and `conclave-dashboard` → `dashboard/`; repair CI
   workflow paths and any path-dependent tests; full suite green.
2. `deploy/Dockerfile` for the backend.
3. `compose.yaml` — `db`, `migrate`, `api`, then the two profiles.
4. `scripts/mint_key.py` and the endpoint rename.
5. `scripts/smoke.py`.
6. Fresh-box verification.

## Risks

- **The merge is the largest structural change in the release.** History is grafted, not rewritten,
  but paths move — CI configs and any path-dependent test will break and must be repaired in the
  same step.
- **Two documented deploy paths drift.** Mitigated by both using the same
  `scripts/apply_migrations.py` and the same `.env`, and by `DEPLOY.md` treating systemd as a
  variant of the Docker instructions rather than a parallel document.
- **`service_completed_successfully` requires Docker Compose v2.** Assert the version in the
  quickstart and fail with a clear message rather than a confusing one.
