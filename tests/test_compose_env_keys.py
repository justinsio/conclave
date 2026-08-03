"""Every key an operator can put in .env must be declared in Settings.

`app/config.py` is `extra='forbid'` (pydantic-settings' default — the string does
not appear in the file, so grepping for it finds nothing) and Settings reads
`.env`. An undeclared key there is a hard import failure, which kills the api and
migrate containers, every dev box, and the systemd host.

🔒 The trap this exists for: pydantic-settings SKIPS empty undeclared keys. Ship
`FOO=` in .env.example and everything is green — until an operator fills it in,
which is the only reason the variable exists. So this test populates every key
with a NON-EMPTY value. An empty-value version of this test passes vacuously and
proves nothing.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

from app.config import Settings

ENV_EXAMPLE = Path(__file__).parent.parent / ".env.example"


def _populated() -> dict[str, str]:
    vals = dotenv_values(ENV_EXAMPLE)
    assert vals, f"read 0 keys from {ENV_EXAMPLE} — wrong path or unreadable"
    # Keep real values (they are already type-valid); substitute a placeholder
    # only where the shipped value is empty, which is the case that matters.
    return {k.lower(): (v if v else "x") for k, v in vals.items() if v is not None}


def test_no_env_example_key_is_undeclared():
    try:
        Settings(**_populated())
    except ValidationError as exc:
        undeclared = sorted(
            str(e["loc"][0]) for e in exc.errors() if e["type"] == "extra_forbidden"
        )
        assert not undeclared, (
            "these keys ship in .env.example but are not declared in Settings, so "
            "`import app.config` fails the moment an operator fills one in: "
            f"{undeclared}"
        )


def test_the_probe_can_actually_fail():
    """A guard that cannot fail is decoration. Feed Settings a key that is
    genuinely undeclared and confirm this test's detection logic trips."""
    try:
        Settings(**{**_populated(), "definitely_not_a_real_setting": "x"})
    except ValidationError as exc:
        undeclared = [e["loc"][0] for e in exc.errors() if e["type"] == "extra_forbidden"]
        assert "definitely_not_a_real_setting" in undeclared
    else:
        raise AssertionError(
            "Settings accepted an undeclared key — extra='forbid' is no longer in "
            "effect, and every other guard in this file is now vacuous"
        )


def test_seed_llm_keys_are_declared():
    """The seed containers need LLM configuration passed through compose. Those
    variables land in the SAME root .env that api and migrate consume via
    env_file, so each one must be declared here too — the C-3 defect."""
    s = Settings(
        llm_provider="ollama",
        ollama_model="qwen2.5:3b",
        llm_api_key="k",
        llm_base_url="http://example.invalid",
        llm_model="m",
    )
    assert s.llm_provider == "ollama"
    assert s.ollama_model == "qwen2.5:3b"


def test_seed_tuning_keys_survive_an_empty_value():
    """Typed str on purpose. These are pass-through strings the backend never
    reads, and seeds/config.py does the parsing — but they live in the root .env
    that api and migrate consume, so an int-typed POLL_INTERVAL_SECONDS left
    empty would fail coercion at import and take the api container down."""
    s = Settings(
        poll_interval_seconds="",
        solo_threshold="",
        open_thread_threshold="",
        draft_after_minutes="",
        answer_after_minutes="",
    )
    assert s.draft_after_minutes == ""

    s2 = Settings(draft_after_minutes="1", answer_after_minutes="3")
    assert (s2.draft_after_minutes, s2.answer_after_minutes) == ("1", "3")
