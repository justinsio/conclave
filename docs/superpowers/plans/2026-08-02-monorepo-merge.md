# Monorepo Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `conclave-seeds` and `conclave-dashboard` into this repository as `seeds/` and `dashboard/`, preserving full git history, with all three test suites green and one CI workflow running all of them.

**Architecture:** `git subtree add` grafts each repository's history under a prefix without rewriting any existing commit. The three test suites stay independent — each keeps its own `pytest.ini`, and pytest's rootdir resolution gives each the `sys.path` it expects. A single root CI workflow runs all three, because Gitea only reads workflows from the repository root.

**Tech Stack:** git subtree · pytest · ruff 0.15.20 · bandit 1.9.4 · Gitea Actions (self-hosted runner, label `homelab`)

---

## Why this is Plan A of two

The design spec (`docs/superpowers/specs/2026-08-02-self-hostable-stack-design.md`) requires the merge to land and CI to go green **before** any compose work, so a broken build has one candidate cause rather than two. Plan B (Dockerfile, `compose.yaml`, `mint_key.py`, `smoke.py`, fresh-box verification) is written **after** this plan executes, against verified paths and a green baseline.

This follows a lesson already recorded in this project: **auditing plan N finds bugs in plan N+1 — audit close to execution, not in a batch up front.**

## The collision this plan exists to solve

Verified 2026-08-02:

| Repo | `pytest.ini` | Import style in tests |
|---|---|---|
| `conclave` | `asyncio_mode = auto`, `testpaths = tests` | `from app…`, `from tests…` |
| `conclave-seeds` | `asyncio_mode = auto`, `pythonpath = .` | **bare top-level**: `from client`, `from brain`, `from config`, `from providers`, `from tests`, `from scripts` |
| `conclave-dashboard` | `pythonpath = .` | `from api_client` |

After the merge, **`tests` and `scripts` are ambiguous top-level names** — both the backend root and `seeds/` provide them. The resolution is *not* to rewrite imports. It is to keep each suite's `pytest.ini` in place and always invoke the sub-suites by directory (`pytest seeds/`), which makes pytest select that directory as rootdir and apply its `pythonpath = .`. Task 4 verifies this empirically rather than trusting the reasoning.

## Environment facts, verified 2026-08-02 before execution

Confirmed by running them on the target machine, not assumed:

- **The interpreter is `.venv/Scripts/python.exe`, not `.venv/bin/python`.** This is a Windows box (Python 3.12.13); `.venv/bin/python` does not exist. The **CI workflow correctly keeps `.venv/bin/python`** because the `homelab` runner is Linux. Every *local* command below uses the Windows path; the only place `bin/python` survives is inside the CI YAML, and that is deliberate. `scripts/run_all_tests.sh` (Task 5) resolves the path at runtime so it works in both.
- **The backend suite runs here: 575 tests collected.** Postgres is listening on 5432 and `.env` supplies both `DATABASE_URL` and `TEST_DATABASE_URL`. ⚠️ The CI default `postgres:postgres@localhost` **does not** authenticate on this machine — the local credentials come from `.env`. Do not "fix" a connection failure by editing the connection string; the working configuration is already there.
- **Branch: all work happens on `feat/monorepo-merge`**, never directly on `master`. This matches the repo's existing convention (`feat/self-host-config`, `feat/moderation-v1-content-gate`) and makes the entire merge discardable by deleting one branch.

## File structure after this plan

```
conclave/
├── app/  migrations/  scripts/  tests/     # backend, untouched
├── seeds/                  # subtree: keeps its own pytest.ini, Dockerfile, .gitignore, README
├── dashboard/              # subtree: keeps its own pytest.ini, README
├── deploy/conclave.service
├── .gitea/workflows/ci.yml # NOW runs all three suites
├── scripts/run_all_tests.sh   # NEW — the one command that runs everything
├── pytest.ini              # unchanged (testpaths = tests keeps bare `pytest` backend-only)
├── ruff.toml               # may need per-directory sections (Task 8)
└── LICENSE  CONTRIBUTING.md  SECURITY.md   # one set (Task 6)
```

---

### Task 1: Capture the pre-merge baseline

Nothing in this plan can claim "nothing broke" without a recorded before-state.

**Files:**
- Create: `/tmp/premerge-baseline.txt` (scratch, not committed)

- [ ] **Step 1: Confirm `git subtree` exists**

```bash
git subtree --help >/dev/null 2>&1 && echo "subtree OK" || echo "MISSING — stop"
```

Expected: `subtree OK`. If missing, stop — the whole plan depends on it.

- [ ] **Step 2: Confirm all three working trees are clean**

```bash
for r in conclave conclave-seeds conclave-dashboard; do
  printf "%s: " "$r"; git -C "/f/ObsidianAI/$r" status --porcelain | wc -l
done
```

Expected: `0` for all three. A dirty tree makes `git subtree add` refuse.

- [ ] **Step 3: Record each suite's passing count**

```bash
{
  echo "=== conclave ==="
  (cd /f/ObsidianAI/conclave && .venv/Scripts/python.exe -m pytest -q 2>&1 | tail -3)
  echo "=== conclave-seeds ==="
  (cd /f/ObsidianAI/conclave-seeds && python -m pytest -q 2>&1 | tail -3)
  echo "=== conclave-dashboard ==="
  (cd /f/ObsidianAI/conclave-dashboard && python -m pytest -q 2>&1 | tail -3)
} | tee /tmp/premerge-baseline.txt
```

Expected: three `N passed` lines, no failures.

**If a sub-repo's dependencies are not installed in the `python` on `PATH`**, that suite will error on import rather than report a count. Create a throwaway environment for it and re-run:

```bash
cd /f/ObsidianAI/conclave-seeds && uv venv .venv-baseline --python 3.12 --seed \
  && .venv-baseline/Scripts/python.exe -m pip install -q -r requirements.txt \
  && .venv-baseline/Scripts/python.exe -m pytest -q 2>&1 | tail -3
```

(Windows venv layout again — `Scripts/`, not `bin/`. Delete `.venv-baseline` afterwards; it is scratch, and both sub-repos are about to stop being separate repositories anyway.)

**Skipping a baseline is not acceptable.** Task 4 compares against these numbers, and "I did not record it" and "it did not change" are indistinguishable afterwards.

**If any suite is already red, stop and fix that first** — merging on top of a red tree makes the cause of every later failure ambiguous. (This exact situation occurred on 2026-08-01: the tree was already red on arrival because a test rotted at midnight on a date.)

- [ ] **Step 4: No commit**

This task produces evidence, not changes.

---

### Task 2: Subtree-merge `conclave-seeds` into `seeds/`

**Files:**
- Create: `seeds/` (entire subtree)

- [ ] **Step 1: Add the source as a temporary local remote**

A local path avoids both the network and baking the Gitea LAN address into this repo's config.

```bash
cd /f/ObsidianAI/conclave
git remote add seeds-src /f/ObsidianAI/conclave-seeds
git fetch seeds-src master
```

Expected: `* branch master -> FETCH_HEAD`.

- [ ] **Step 2: Graft the history under `seeds/`**

**Do not pass `--squash`** — squashing discards the history this merge exists to preserve.

```bash
git subtree add --prefix=seeds seeds-src master
```

Expected: `Added dir 'seeds'` and a merge commit.

- [ ] **Step 3: Verify history came across, not just files**

```bash
git log --oneline -- seeds/ | wc -l
```

Expected: **more than 1** — around 27 commits. If it returns `1`, the history was squashed and Step 2 must be redone.

- [ ] **Step 4: Remove the temporary remote**

```bash
git remote remove seeds-src
git remote -v
```

Expected: only `origin` remains.

- [ ] **Step 5: Commit**

`git subtree add` already created the commit. Verify and move on:

```bash
git log -1 --format='%h %s'
git status --porcelain
```

Expected: a merge commit, and a clean tree.

---

### Task 3: Subtree-merge `conclave-dashboard` into `dashboard/`

Identical mechanics to Task 2, different prefix. Repeated in full deliberately — do not read this as "same as Task 2."

**Files:**
- Create: `dashboard/` (entire subtree)

- [ ] **Step 1: Add the source as a temporary local remote**

```bash
cd /f/ObsidianAI/conclave
git remote add dashboard-src /f/ObsidianAI/conclave-dashboard
git fetch dashboard-src master
```

Expected: `* branch master -> FETCH_HEAD`.

- [ ] **Step 2: Graft the history under `dashboard/`**

```bash
git subtree add --prefix=dashboard dashboard-src master
```

Expected: `Added dir 'dashboard'`.

- [ ] **Step 3: Verify history came across**

```bash
git log --oneline -- dashboard/ | wc -l
```

Expected: **more than 1** — around 10 commits.

- [ ] **Step 4: Remove the temporary remote**

```bash
git remote remove dashboard-src
git remote -v
```

Expected: only `origin` remains.

- [ ] **Step 5: Verify the tree**

```bash
git status --porcelain
ls -d seeds dashboard
```

Expected: clean tree, both directories present.

---

### Task 4: Prove all three suites still pass

This is the task the collision analysis exists for. **Verify by executing, not by reasoning.**

**Files:**
- Modify: none expected — but `seeds/pytest.ini` or `dashboard/pytest.ini` may need adjustment if Step 2 or 3 fails.

- [ ] **Step 1: Backend suite — must be unaffected**

```bash
cd /f/ObsidianAI/conclave && .venv/Scripts/python.exe -m pytest -q 2>&1 | tail -3
```

Expected: the same passing count as the `conclave` section of `/tmp/premerge-baseline.txt`. `testpaths = tests` in the root `pytest.ini` means a bare `pytest` still collects only backend tests — `seeds/tests` and `dashboard/tests` are not swept in.

- [ ] **Step 2: Seeds suite — the risky one**

```bash
cd /f/ObsidianAI/conclave && .venv/Scripts/python.exe -m pytest seeds/ -q 2>&1 | tail -3
```

Expected: the same count as the `conclave-seeds` baseline.

**Why this should work:** pytest resolves rootdir from the arguments. With `seeds/` as the argument it finds `seeds/pytest.ini`, sets rootdir to `seeds/`, and applies that file's `pythonpath = .` — putting `seeds/` on `sys.path` so `from client`, `from brain`, `from tests` and `from scripts` all resolve to the seed modules, not the backend's.

**If it fails with `ModuleNotFoundError` or collects the wrong `tests` package**, the rootdir did not resolve as expected. Fix by making it explicit rather than by rewriting imports:

```bash
.venv/Scripts/python.exe -m pytest seeds/ -q -c seeds/pytest.ini --rootdir=seeds
```

If that works, record the working invocation — Task 5 and Task 7 must both use it.

- [ ] **Step 3: Dashboard suite**

```bash
cd /f/ObsidianAI/conclave && .venv/Scripts/python.exe -m pytest dashboard/ -q 2>&1 | tail -3
```

Expected: the same count as the `conclave-dashboard` baseline. Same fallback as Step 2 if it fails.

- [ ] **Step 4: Confirm no suite silently shrank**

```bash
cat /tmp/premerge-baseline.txt
```

Compare all three counts by eye. **A lower count is a failure even when nothing is red** — silently uncollected tests are the failure mode this step exists to catch.

- [ ] **Step 5: Commit only if a fix was needed**

If Steps 2 or 3 required changes:

```bash
git add seeds/pytest.ini dashboard/pytest.ini
git commit -s -m "test: keep sub-suite rootdir resolution working after the monorepo merge"
```

If no changes were needed, commit nothing.

---

### Task 5: One command that runs everything

Without this, someone runs bare `pytest`, sees green, and believes they tested the whole repository. They tested a third of it.

**Files:**
- Create: `scripts/run_all_tests.sh`

- [ ] **Step 1: Write the script**

Use the exact invocations proven in Task 4. If Task 4 needed the `-c … --rootdir=…` fallback, use that form here instead.

```bash
#!/usr/bin/env bash
# Runs all three test suites. A bare `pytest` only covers the backend, because
# the root pytest.ini sets `testpaths = tests` — the seeds and dashboard suites
# each need their own rootdir so their `pythonpath = .` resolves to their own
# directory. See docs/superpowers/plans/2026-08-02-monorepo-merge.md Task 4.
set -euo pipefail

# The venv layout differs by platform: Windows puts the interpreter in
# .venv/Scripts/python.exe, Linux (and the CI runner) in .venv/bin/python.
# Resolve it rather than hardcoding, so the same script works in both places.
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  echo "No interpreter found — expected .venv/Scripts/python.exe or .venv/bin/python." >&2
  echo "Override with PYTHON=/path/to/python" >&2
  exit 1
fi

echo "=== backend ==="
"$PY" -m pytest -q

echo "=== seeds ==="
"$PY" -m pytest seeds/ -q

echo "=== dashboard ==="
"$PY" -m pytest dashboard/ -q

echo "All three suites passed."
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/run_all_tests.sh
./scripts/run_all_tests.sh
```

Expected: three passing sections, then `All three suites passed.` Counts must match `/tmp/premerge-baseline.txt`.

- [ ] **Step 3: Verify it fails loudly**

`set -e` must abort on the first failing suite rather than running on and reporting success. The script takes no arguments, so force a failure through the one input it does read — the `PYTHON` override:

```bash
PYTHON=/nonexistent/python ./scripts/run_all_tests.sh; echo "exit=$?"
```

Expected: it aborts during the backend section with a **non-zero** exit and never prints `All three suites passed.` **If it prints the success line, `set -e` is not doing its job** — fix it before continuing, or this script will lie at exactly the moment it matters.

- [ ] **Step 4: Commit**

```bash
git add scripts/run_all_tests.sh
git commit -s -m "test: add scripts/run_all_tests.sh covering all three suites"
```

---

### Task 6: Collapse the duplicated policy files

The subtrees each brought their own `LICENSE`, `CONTRIBUTING.md` and `SECURITY.md`, committed 2026-08-02. Three copies of a policy means two of them go stale.

**Files:**
- Delete: `seeds/LICENSE`, `seeds/CONTRIBUTING.md`, `seeds/SECURITY.md`
- Delete: `dashboard/LICENSE`, `dashboard/CONTRIBUTING.md`, `dashboard/SECURITY.md`
- Modify: `seeds/README.md`, `dashboard/README.md` — repoint their License sections

- [ ] **Step 1: Confirm the root copies are the ones to keep**

```bash
md5sum LICENSE seeds/LICENSE dashboard/LICENSE
```

Expected: three identical hashes (`86d3f3a9…`). If they differ, stop and investigate before deleting anything.

- [ ] **Step 2: Delete the duplicates**

```bash
git rm seeds/LICENSE seeds/CONTRIBUTING.md seeds/SECURITY.md
git rm dashboard/LICENSE dashboard/CONTRIBUTING.md dashboard/SECURITY.md
```

- [ ] **Step 3: Repoint the sub-READMEs**

In both `seeds/README.md` and `dashboard/README.md`, replace the `## License` section body with:

```markdown
Copyright 2026 Justin Tucker

Licensed under the Apache License, Version 2.0. See [LICENSE](../LICENSE) for the full text.

Contributions require a DCO sign-off — see [CONTRIBUTING.md](../CONTRIBUTING.md).
To report a vulnerability, see [SECURITY.md](../SECURITY.md).
```

- [ ] **Step 4: Verify no broken relative links remain**

```bash
grep -rnE '\]\((\./)?(LICENSE|CONTRIBUTING\.md|SECURITY\.md)\)' seeds/ dashboard/ || echo "clean — no same-directory policy links left"
```

Expected: `clean — …`. Any hit is a link that now points at a deleted file.

- [ ] **Step 5: Commit**

```bash
git add -A seeds dashboard
git commit -s -m "docs: single set of policy files for the monorepo"
```

---

### Task 7: One CI workflow for all three suites

Gitea Actions only reads workflows from the repository root. After the merge, `seeds/.gitea/workflows/ci.yml` and `dashboard/.gitea/workflows/ci.yml` are dead files that will never run — and their presence implies coverage that does not exist.

**Files:**
- Modify: `.gitea/workflows/ci.yml`
- Delete: `seeds/.gitea/workflows/ci.yml`, `dashboard/.gitea/workflows/ci.yml`

- [ ] **Step 1: Delete the dead workflows**

```bash
git rm seeds/.gitea/workflows/ci.yml dashboard/.gitea/workflows/ci.yml
```

- [ ] **Step 2: Extend the root workflow**

Add these steps to the existing `test` job in `.gitea/workflows/ci.yml`, after the existing `Run test suite` step. Keep `runs-on: homelab` and every existing step unchanged.

```yaml
      - name: Install sub-project dependencies
        run: |
          .venv/bin/pip install -r seeds/requirements.txt
          .venv/bin/pip install -r dashboard/requirements.txt -r dashboard/requirements-dev.txt

      - name: Run seeds suite
        run: .venv/bin/python -m pytest seeds/ -q

      - name: Run dashboard suite
        run: .venv/bin/python -m pytest dashboard/ -q
```

⚠️ **`dashboard/requirements.txt` pins `streamlit<1.50` because newer Streamlit needs `starlette>=0.40`, which conflicts with `fastapi 0.115` when sharing one Python environment — and CI shares one `.venv` across all three.** That pin is therefore load-bearing *in CI* even though the design spec notes it stops being necessary once each service has its own container. **Do not unpin it here.** If the combined install fails, split CI into three jobs with separate venvs rather than relaxing the pin.

- [ ] **Step 3: Verify the workflow parses**

```bash
.venv/Scripts/python.exe -c "import yaml,sys; yaml.safe_load(open('.gitea/workflows/ci.yml')); print('YAML OK')"
```

Expected: `YAML OK`.

- [ ] **Step 4: Verify the combined install actually resolves**

Do this locally before trusting CI:

```bash
.venv/bin/pip install --dry-run -r requirements.txt -r seeds/requirements.txt -r dashboard/requirements.txt -r dashboard/requirements-dev.txt
```

Expected: resolution succeeds. **A conflict here is the `streamlit`/`starlette` clash** — take the three-jobs route described in Step 2 rather than changing a pin.

- [ ] **Step 5: Commit**

```bash
git add .gitea/workflows/ci.yml
git add -A seeds/.gitea dashboard/.gitea
git commit -s -m "ci: single workflow running backend, seeds and dashboard suites"
```

---

### Task 8: Lint and security-scan the merged tree

`ruff check .` and `bandit` now cover code they never saw. The backend has a `ruff.toml`; the sub-projects were linted with defaults. New findings are expected and are not necessarily bugs.

**Files:**
- Modify: `ruff.toml` (only if genuinely needed)
- Modify: source files under `seeds/` or `dashboard/` (only for real findings)

- [ ] **Step 1: Run ruff across everything**

```bash
.venv/Scripts/python.exe -m ruff check .
```

Expected: either clean, or findings confined to `seeds/` and `dashboard/`.

- [ ] **Step 2: Resolve findings — fix code first**

Fix real issues in the code. Only widen `ruff.toml` when a rule is genuinely inapplicable to a sub-project, and add a comment saying why. **Do not blanket-exclude `seeds/` or `dashboard/`** — that silently drops them from linting forever, which is the opposite of what merging them was for.

- [ ] **Step 3: Run bandit over the new directories**

The existing backend invocation is `bandit -r app scripts -q -s B608`. Scan the new code with the sub-projects' own exclusions:

```bash
.venv/Scripts/python.exe -m bandit -r seeds dashboard -x ./seeds/tests,./seeds/docs,./dashboard/tests -q
```

Expected: no findings. Both sub-projects had a clean bandit baseline before the merge, so **any finding here means the exclusions moved, not that new insecure code appeared** — check the paths before changing code.

- [ ] **Step 4: Update the CI lint step to match**

Edit the `Lint + security scan` step in `.gitea/workflows/ci.yml` so the bandit invocation covers the new directories with the exclusions proven in Step 3.

- [ ] **Step 5: Re-run everything**

```bash
.venv/Scripts/python.exe -m ruff check . && ./scripts/run_all_tests.sh
```

Expected: ruff clean, all three suites pass with baseline counts.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "chore(lint): extend ruff and bandit coverage across the merged tree"
```

---

### Task 9: Update the README for the new structure

The root README describes a single-project repository that no longer exists.

**Files:**
- Modify: `README.md` — the `## Repo layout` section and the test instructions

- [ ] **Step 1: Add `seeds/` and `dashboard/` to the repo-layout section**

Describe each in one line: `seeds/` is the seed agent runtime (optional), `dashboard/` is the operator UI bound to `127.0.0.1`.

- [ ] **Step 2: Correct the test instructions**

The current quickstart implies `pytest` runs the suite. State plainly that a bare `pytest` covers the backend only, and that `./scripts/run_all_tests.sh` runs all three.

**Do not restore a hardcoded expected test count.** It was deliberately removed on 2026-07-31 because the suite was not reproducible, and re-adding one recreates a claim the repo has to keep true by hand.

- [ ] **Step 3: Verify no stale cross-repo clone instructions remain**

```bash
grep -rniE 'conclave-seeds|conclave-dashboard|clone .*seeds' README.md seeds/README.md dashboard/README.md
```

Expected: no instruction telling a reader to clone a now-merged repository. Historical references in prose are fine; clone commands are not.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -s -m "docs: README describes the merged repository layout"
```

---

### Task 10: Final verification and push

- [ ] **Step 1: Full verification from a clean tree**

```bash
cd /f/ObsidianAI/conclave
git status --porcelain
./scripts/run_all_tests.sh
.venv/Scripts/python.exe -m ruff check .
```

Expected: clean tree, three passing suites at baseline counts, ruff clean.

- [ ] **Step 2: Confirm both histories survived**

```bash
echo "seeds:     $(git log --oneline -- seeds/ | wc -l) commits"
echo "dashboard: $(git log --oneline -- dashboard/ | wc -l) commits"
echo "total:     $(git log --oneline | wc -l) commits"
```

Expected: roughly 27, 10, and a total well above the pre-merge 149.

- [ ] **Step 3: Confirm no secrets came across**

```bash
git ls-files | grep -iE '(^|/)\.env$|\.env\.(local|production)$|\.key$|\.pem$|REVIEW\.md$' || echo "clean — no secret-shaped files tracked"
```

Expected: `clean — …`. The subtrees brought their own `.gitignore` files, but a file already *tracked* in a source repo would arrive tracked regardless of any ignore rule.

- [ ] **Step 4: Push**

Push **the branch**, not `master`. Must source the shared ssh-agent in the same shell — a fresh shell has no `SSH_AUTH_SOCK`:

```bash
. /c/Users/white/.ssh/agent.env && git -C /f/ObsidianAI/conclave push -u origin feat/monorepo-merge
```

Expected: the push succeeds and the branch is tracked. If it reports `Permission denied (publickey)`, the agent has no key loaded — Justin must run `ssh-add ~/.ssh/id_ed25519` **in Git Bash**.

**Do not merge to `master` in this plan.** Merging is a decision for Justin once CI is green on the branch — see `superpowers:finishing-a-development-branch`.

- [ ] **Step 5: Confirm CI went green**

Check the Gitea Actions run for the pushed commit. **Do not proceed to Plan B until it is green** — that is the whole reason the merge is sequenced first.

---

## Deliberately NOT in this plan

- **`Dockerfile`, `compose.yaml`, `mint_key.py`, `smoke.py`, fresh-box verification.** Plan B, written after this lands.
- **Fixing the seeds' `conclave-internal` external-network bug.** It is compose work; the spec assigns it to the containerization step.
- **Unpinning `streamlit<1.50`.** Task 7 explains why it is still load-bearing in CI.
- **Archiving the old `conclave-seeds` / `conclave-dashboard` Gitea repos.** Needs Justin's decision — archive (keeps them readable) rather than delete, plus a README banner pointing at the monorepo. Raise it after this plan is green; do not act unasked.
