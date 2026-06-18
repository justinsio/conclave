"""Unit tests for outbound notifications (Telegram, notify-only)."""
from __future__ import annotations

import pytest

from app.services import notifications


class TestSendTelegramDisabled:
    @pytest.mark.asyncio
    async def test_disabled_returns_false(self, monkeypatch):
        monkeypatch.setattr(notifications.settings, "telegram_alerts_enabled", False)
        assert await notifications._send_telegram("hi") is False

    @pytest.mark.asyncio
    async def test_enabled_but_missing_token_returns_false(self, monkeypatch):
        monkeypatch.setattr(notifications.settings, "telegram_alerts_enabled", True)
        monkeypatch.setattr(notifications.settings, "telegram_bot_token", "")
        monkeypatch.setattr(notifications.settings, "telegram_chat_id", "123")
        assert await notifications._send_telegram("hi") is False


class TestNotifyFormatting:
    @pytest.mark.asyncio
    async def test_escalation_calls_boundary_with_key_fields(self, monkeypatch):
        captured = {}

        async def _cap(text):
            captured["text"] = text
            return True

        monkeypatch.setattr(notifications, "_send_telegram", _cap)
        ok = await notifications.notify_escalation(
            target_type="post", queue_id="q-1", reason="harmful", preview="bad stuff",
        )
        assert ok is True
        assert "ESCALATE" in captured["text"]
        assert "harmful" in captured["text"]
        assert "q-1" in captured["text"]

    @pytest.mark.asyncio
    async def test_auto_block_message(self, monkeypatch):
        captured = {}

        async def _cap(text):
            captured["text"] = text
            return True

        monkeypatch.setattr(notifications, "_send_telegram", _cap)
        await notifications.notify_auto_block(count=4)
        assert "auto-block" in captured["text"]
        assert "4" in captured["text"]

    @pytest.mark.asyncio
    async def test_auto_ban_message(self, monkeypatch):
        captured = {}

        async def _cap(text):
            captured["text"] = text
            return True

        monkeypatch.setattr(notifications, "_send_telegram", _cap)
        await notifications.notify_auto_ban(agent_id="a-1", block_count=3)
        assert "auto-ban" in captured["text"]
        assert "a-1" in captured["text"]
