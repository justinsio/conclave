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
        trusted_proxy_ips="203.0.113.1",
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


def test_production_boots_without_a_trusted_proxy():
    """A LAN self-hoster with no reverse proxy has none to declare."""
    assert_production_safety(_good_prod(trusted_proxy_ips=""))  # must not raise


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
# The moderation gate and trusted_proxy_ips were HARD production failures until
# 2026-08-02. Both were demoted here, because both made ENVIRONMENT=production
# unbootable for a self-hoster — the gate needs a paid LLM, and a LAN deployment
# has no proxy to declare. Demoting a control must never mean deleting it, so
# each has a warning test below.


def test_self_host_posture_warns_when_no_trusted_proxy_is_declared(caplog):
    """Demoted from a hard failure — it must still be audible."""
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True, trusted_proxy_ips="")
        )
    assert "trusted_proxy_ips" in caplog.text


def test_the_proxy_warning_names_the_waitlist_not_the_rate_limiter(caplog):
    """The old text claimed the rate limiter collapses to one shared bucket.

    False: enforce_rate_limit is agent-keyed (rate_limit_counters is keyed on
    agent_id, not IP). The only consumer of trusted_proxy_ips is the public
    waitlist form's IP throttle. Telling a self-hoster their agent rate limiting
    is broken when it is not sends them debugging the wrong subsystem.
    """
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True, trusted_proxy_ips="")
        )
    assert "waitlist" in caplog.text
    assert "shared bucket" not in caplog.text


def test_self_host_posture_is_quiet_when_a_proxy_is_declared(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(
            Settings(environment="dev", moderation_gate_enabled=True,
                     trusted_proxy_ips="203.0.113.1")
        )
    assert "trusted_proxy_ips" not in caplog.text

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
