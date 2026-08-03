"""Tests for the production safety preflight (R2/R3)."""
from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.services.preflight import assert_production_safety, warn_self_host_posture


def _good_prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        admin_api_key="a-strong-secret-key",
        moderation_gate_enabled=True,
        rate_limit_enabled=True,
        anthropic_api_key="sk-ant-xxx",
        notify_target="telegram",
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
    # Placeholders shipped in .env.example are rejected as a SET, not one literal.
    ({"admin_api_key": "change-me-to-a-strong-secret"}, "admin_api_key"),
    ({"rate_limit_enabled": False}, "rate_limit_enabled"),
])
def test_production_missing_hard_control_refuses_boot(override, needle):
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(_good_prod(**override))
    assert needle in str(exc.value)


def test_production_lists_all_failures_at_once():
    s = _good_prod(admin_api_key="dev-admin-key", rate_limit_enabled=False)
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(s)
    msg = str(exc.value)
    assert "admin_api_key" in msg and "rate_limit_enabled" in msg


# ─── The self-host dispositions (decided 2026-08-02) ──────────────────────────
# Controls written for a public multi-tenant service, made coherent with what
# they actually protect. A private team that trusts its own agents must be able
# to run ENVIRONMENT=production; before this, `production` was unbootable for
# exactly the bring-your-own-LLM operator the self-host release targets.

def test_production_boots_with_the_gate_off():
    """The gate needs an LLM. A private team without one must still boot."""
    assert_production_safety(
        _good_prod(moderation_gate_enabled=False, anthropic_api_key="")
    )  # must not raise


def test_production_requires_the_provider_key_ONLY_when_the_gate_is_on():
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(_good_prod(moderation_gate_enabled=True, anthropic_api_key=""))
    assert "anthropic_api_key" in str(exc.value)


def test_production_soft_controls_warn_but_boot(caplog):
    with caplog.at_level(logging.WARNING):
        assert_production_safety(_good_prod(notify_target="none", ollama_base_url=""))
    assert "notify_target" in caplog.text
    assert "ollama_base_url" in caplog.text


def test_notify_target_none_warns_but_boots(caplog):
    with caplog.at_level("WARNING"):
        assert_production_safety(_good_prod(notify_target="none"))
    assert "notify_target" in caplog.text


def test_notify_target_set_produces_no_notification_warning(caplog):
    with caplog.at_level("WARNING"):
        assert_production_safety(_good_prod(notify_target="telegram"))
    assert "notify_target" not in caplog.text


# ─── Self-host posture warnings ───────────────────────────────────────────────
# These live outside assert_production_safety because that function is a no-op
# unless environment == "production", while these must reach an operator in ANY
# environment. main.py calls both, in that order.
#
# The moderation gate was a HARD production failure until 2026-08-02. It was
# demoted here because it made ENVIRONMENT=production unbootable for a
# self-hoster — the gate needs a paid LLM. Demoting a control must never mean
# deleting it, so it has a warning test below.
#
# A trusted_proxy_ips warning lived here too and was DELETED 2026-08-03, not
# demoted: the setting's only reader was the public waitlist route, removed with
# the rest of the pre-launch marketing surface. The three tests that covered it
# are gone because the behaviour is gone — replaced by the one below, so that
# re-introducing the setting has to be a deliberate act rather than a silent one.


def test_nothing_warns_about_trusted_proxy_ips_any_more(caplog):
    """Its reader (POST /v1/waitlist) is gone, so the warning is too. Nothing in
    the codebase is IP-keyed now — the rate limiter is keyed on agent_id."""
    with caplog.at_level("WARNING"):
        warn_self_host_posture(Settings(environment="dev", moderation_gate_enabled=True))
    assert "trusted_proxy_ips" not in caplog.text
    assert "waitlist" not in caplog.text


def test_a_retired_setting_still_in_a_live_env_does_not_break_the_import():
    """🔴 The upgrade hazard, pinned.

    Settings is extra='forbid' and reads .env. Deleting the retired
    trusted_proxy_ips FIELD would make `import app.config` raise for any operator
    whose .env still carries a real value — which the production ops runbook told
    them to set — killing the api container on upgrade. CI would not notice: it
    has no .env. Verified both directions before this test existed: an empty
    value imports (pydantic-settings skips empty undeclared dotenv keys), a
    non-empty one raises extra_forbidden.
    """
    assert "trusted_proxy_ips" in Settings.model_fields, (
        "trusted_proxy_ips was deleted. Nothing reads it, but the field must "
        "remain until no live .env sets it — see the comment in app/config.py."
    )
    Settings(trusted_proxy_ips="203.0.113.1")  # must not raise


def test_self_host_posture_warns_when_moderation_gate_is_off(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(Settings(environment="dev", moderation_gate_enabled=False))
    assert "moderation_gate_enabled" in caplog.text


def test_self_host_posture_is_silent_when_the_gate_is_on(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(Settings(environment="dev", moderation_gate_enabled=True))
    assert "moderation_gate_enabled" not in caplog.text


def test_self_host_posture_warns_when_ollama_is_missing(caplog):
    """Retrieval silently returns nothing without Ollama. Now that it is a
    headline capability, the operator must be told at boot."""
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True, ollama_base_url="")
        )
    assert "knowledge retrieval" in caplog.text


def test_self_host_posture_is_quiet_when_ollama_is_configured(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True,
                     ollama_base_url="http://127.0.0.1:11434")
        )
    assert "knowledge retrieval" not in caplog.text
