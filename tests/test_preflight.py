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
    ({"moderation_gate_enabled": False}, "moderation_gate_enabled"),
    ({"rate_limit_enabled": False}, "rate_limit_enabled"),
    ({"anthropic_api_key": ""}, "anthropic_api_key"),
    ({"trusted_proxy_ips": ""}, "trusted_proxy_ips"),
])
def test_production_missing_hard_control_refuses_boot(override, needle):
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(_good_prod(**override))
    assert needle in str(exc.value)


def test_production_lists_all_failures_at_once():
    s = _good_prod(moderation_gate_enabled=False, rate_limit_enabled=False)
    with pytest.raises(RuntimeError) as exc:
        assert_production_safety(s)
    msg = str(exc.value)
    assert "moderation_gate_enabled" in msg and "rate_limit_enabled" in msg


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
# These live outside assert_production_safety on purpose. That function is a
# no-op unless environment == "production", and inside production a disabled
# moderation gate is already a HARD failure that raises before any soft warning
# runs — so a "you have no moderation gate" warning placed there is unreachable
# in both directions. The operator who needs to hear it is precisely the
# self-hoster who is NOT running ENVIRONMENT=production.

def test_self_host_posture_warns_when_moderation_gate_is_off(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(Settings(environment="dev", moderation_gate_enabled=False))
    assert "moderation_gate_enabled" in caplog.text


def test_self_host_posture_is_silent_when_the_gate_is_on(caplog):
    with caplog.at_level("WARNING"):
        warn_self_host_posture(Settings(environment="dev", moderation_gate_enabled=True))
    assert "moderation_gate_enabled" not in caplog.text
