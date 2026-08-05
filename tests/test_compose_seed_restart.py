"""The seed services must not restart forever on a permanent misconfiguration.

`seeds/config.py` validates its environment before the poll loop starts and
raises with an actionable message naming the variable to set. That message was
already good. What was wrong was what happened next: under
`restart: unless-stopped` the container died in under a second, Docker restarted
it, it died again, and it kept doing that indefinitely. One instance ran that
loop for 47 hours. `docker compose ps` showed only "Restarting", the log was the
same paragraph repeated thousands of times, and nothing escalated.

`on-failure:3` fixes it without needing to classify exit codes, because Docker
resets the retry counter once a container has stayed up for roughly 10 seconds:

  * A config error (empty CONCLAVE_AGENT_KEY) fails in well under a second, so
    the counter never resets. Three attempts, then the container stops and sits
    in Exited(1) where `ps -a` shows it.
  * A seed that has been polling for hours and hits a transient network failure
    has long since reset the counter, so it still gets a fresh three attempts.
    Crash recovery is preserved.

NO YAML PARSER ON PURPOSE. This test runs in the root pytest job, whose venv is
built from requirements.txt alone. `yaml` happens to be importable there only
because bandit depends on PyYAML and CI installs bandit into the same venv one
step earlier. Importing it here would couple this test to that accident; adding
pyyaml to requirements.txt would put a test-only dependency into the production
image. A dozen lines of block scanning costs less than either.
"""
from __future__ import annotations

from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "compose.yaml"

# The anchor every seed service inherits from via `<<: *seed`. Changing the
# policy here changes all four; the other three never declare `restart`.
ANCHOR_SERVICE = "seed-coding"


def _service_block(name: str) -> list[str]:
    """Return the lines of one service block from compose.yaml.

    Services are indented exactly two spaces under `services:`. A block runs
    until the next line at that same indentation, which is the next service.
    """
    lines = COMPOSE.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"  {name}:") and not line.startswith("   "):
            start = i
            break
    assert start is not None, (
        f"service {name!r} not found in {COMPOSE.name}. If it was renamed, this "
        "guard needs updating rather than deleting — read the module docstring."
    )

    block = [lines[start]]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped and not line.startswith("   ") and line.startswith("  "):
            break  # next service at the same indent level
        block.append(line)
    return block


def test_seed_services_do_not_restart_forever():
    block = _service_block(ANCHOR_SERVICE)
    restarts = [ln.strip() for ln in block if ln.strip().startswith("restart:")]

    assert restarts, (
        f"{ANCHOR_SERVICE} declares no restart policy, so it inherits Docker's "
        "default of `no`. That is not the intent — a seed should survive a "
        "transient crash. Set `restart: on-failure:3`."
    )
    assert len(restarts) == 1, f"expected one restart policy, found {restarts}"

    policy = restarts[0]
    assert "unless-stopped" not in policy and "always" not in policy, (
        f"{ANCHOR_SERVICE} uses {policy!r}. Both `unless-stopped` and `always` "
        "retry indefinitely, which turns a permanent misconfiguration (an empty "
        "CONCLAVE_AGENT_KEY is the common one) into a silent infinite restart "
        "loop — 47 hours went unnoticed that way. Use `on-failure:<n>`."
    )
    assert policy.startswith("restart: on-failure:"), (
        f"expected a bounded on-failure policy, got {policy!r}"
    )

    retries = int(policy.split(":")[-1])
    assert 1 <= retries <= 10, (
        f"{retries} retries is outside the sensible range. Too few gives up on "
        "genuinely transient failures; too many re-creates the loop this guard "
        "exists to prevent."
    )


def test_only_seed_services_changed_policy():
    """db and api should still be `unless-stopped` — they are not misconfigurable
    in the same permanent way, and the API going down is exactly when you want
    Docker retrying indefinitely."""
    for service in ("db", "api"):
        block = _service_block(service)
        restarts = [ln.strip() for ln in block if ln.strip().startswith("restart:")]
        assert "restart: unless-stopped" in restarts, (
            f"{service} should remain `unless-stopped`; found {restarts}. The "
            "seed change is deliberately scoped to the seeds."
        )
