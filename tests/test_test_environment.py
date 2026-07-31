"""Guards on the test environment itself.

These do not test application behaviour — they test that the suite is hermetic.
A developer's local `.env` is loaded by conftest (deliberately, for
TEST_DATABASE_URL), which means production-shaped settings can leak into the
test process and silently change what the suite measures.

NOTE ON STYLE: every assertion below compares a *plain local bool*, never a
`settings.<attr>` expression. pytest rewrites assertions and prints the repr of
every intermediate value — asserting on `settings.foo` directly dumps the whole
Settings object, API keys and database passwords included, into the test output
and CI logs. Bind to a bool first. This is not a preference; it is why this file
does not read the way you would expect.
"""
from __future__ import annotations

from app.config import settings


def test_moderation_gate_is_disabled_in_the_test_suite():
    """The gate must be OFF by default in tests, whatever the local .env says.

    With it on and a real ANTHROPIC_API_KEY present, `moderate_content` makes a
    live, billed API call on every post and answer. The verdict is
    non-deterministic, and `posts.py` suppresses any post whose verdict is
    BLOCK or ESCALATE — so every test that asserts a non-author can see a post
    becomes a coin flip on a live LLM. The C1 confidence floor (0.95) widens
    that window further: a model PASS at 0.94 is downgraded to ESCALATE.

    Tests that need the gate turn it on explicitly (see
    test_cost_breaker_integration.py) or monkeypatch `moderate_content` with a
    fixed verdict (see test_moderation_integration.py). That is the contract:
    opt in, never inherit.
    """
    gate_enabled = bool(settings.moderation_gate_enabled)
    assert gate_enabled is False, (
        "The moderation gate is ENABLED in the test environment. The suite will "
        "make real, billed Anthropic API calls and fail non-deterministically. "
        "conftest.py is responsible for forcing this off."
    )


def test_no_real_anthropic_key_in_the_test_process():
    """Defence in depth: even if something flips the gate on, there must be no
    live key for it to spend. An empty key makes `_call_gate_model` fail, which
    fails safe to ESCALATE — wrong, but deterministic and free, rather than
    wrong, random and billed.
    """
    key_looks_real = settings.anthropic_api_key.startswith("sk-ant-")
    assert key_looks_real is False, (
        "A real Anthropic API key is visible to the test process. conftest.py "
        "is responsible for clearing it."
    )
