"""Tests for the production safety preflight (R2/R3)."""
from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.services.preflight import assert_production_safety


def _good_prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        admin_api_key="a-strong-secret-key",
        moderation_gate_enabled=True,
        rate_limit_enabled=True,
        anthropic_api_key="sk-ant-xxx",
        trusted_proxy_ips="203.0.113.1",
        telegram_alerts_enabled=True,
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
        assert_production_safety(_good_prod(telegram_alerts_enabled=False, ollama_base_url=""))
    assert "telegram_alerts_enabled" in caplog.text
    assert "ollama_base_url" in caplog.text
