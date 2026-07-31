"""Notification dispatch: target selection, payload shapes, fire-and-forget."""
import httpx
import pytest

from app.services import notifications


class _Recorder:
    """Stands in for httpx.AsyncClient, recording the single POST it receives."""

    def __init__(self, *args, **kwargs):
        self.calls: list[tuple[str, dict]] = []
        _Recorder.last = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kwargs):
        self.calls.append((url, json))
        return httpx.Response(200, request=httpx.Request("POST", url))


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(notifications.httpx, "AsyncClient", _Recorder)
    return _Recorder


def _configure(monkeypatch, **kwargs):
    for key, value in kwargs.items():
        monkeypatch.setattr(notifications.settings, key, value)


async def test_target_none_sends_nothing(monkeypatch, recorder):
    _configure(monkeypatch, notify_target="none")
    assert await notifications._send("hi") is False


async def test_telegram_target_posts_to_telegram(monkeypatch, recorder):
    _configure(
        monkeypatch, notify_target="telegram",
        telegram_bot_token="tok", telegram_chat_id="42",
    )
    assert await notifications._send("<b>hi</b>") is True
    url, payload = _Recorder.last.calls[0]
    assert "api.telegram.org/bottok/sendMessage" in url
    assert payload["chat_id"] == "42"
    assert payload["text"] == "<b>hi</b>"      # HTML preserved for Telegram
    assert payload["parse_mode"] == "HTML"


async def test_telegram_without_credentials_sends_nothing(monkeypatch, recorder):
    _configure(monkeypatch, notify_target="telegram", telegram_bot_token="", telegram_chat_id="")
    assert await notifications._send("hi") is False


async def test_slack_style_uses_text_key_and_strips_html(monkeypatch, recorder):
    _configure(
        monkeypatch, notify_target="webhook",
        notify_webhook_url="https://hooks.example/x", notify_webhook_style="slack",
    )
    assert await notifications._send("<b>Alert</b>\nbody") is True
    url, payload = _Recorder.last.calls[0]
    assert url == "https://hooks.example/x"
    assert payload == {"text": "Alert\nbody"}


async def test_discord_style_uses_content_key(monkeypatch, recorder):
    _configure(
        monkeypatch, notify_target="webhook",
        notify_webhook_url="https://discord.example/x", notify_webhook_style="discord",
    )
    assert await notifications._send("<b>Alert</b>") is True
    _url, payload = _Recorder.last.calls[0]
    assert payload == {"content": "Alert"}


async def test_raw_style_uses_text_key(monkeypatch, recorder):
    _configure(
        monkeypatch, notify_target="webhook",
        notify_webhook_url="https://x.example/y", notify_webhook_style="raw",
    )
    assert await notifications._send("<b>Alert</b>") is True
    _url, payload = _Recorder.last.calls[0]
    assert payload == {"text": "Alert"}


async def test_webhook_without_url_sends_nothing(monkeypatch, recorder):
    _configure(monkeypatch, notify_target="webhook", notify_webhook_url="")
    assert await notifications._send("hi") is False


async def test_html_entities_are_unescaped_for_webhooks(monkeypatch, recorder):
    _configure(
        monkeypatch, notify_target="webhook",
        notify_webhook_url="https://x.example/y", notify_webhook_style="raw",
    )
    await notifications._send("a &amp; b &lt;c&gt;")
    _url, payload = _Recorder.last.calls[0]
    assert payload == {"text": "a & b <c>"}


async def test_send_failure_never_raises(monkeypatch):
    class _Boom(_Recorder):
        async def post(self, url, json=None, **kwargs):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(notifications.httpx, "AsyncClient", _Boom)
    _configure(
        monkeypatch, notify_target="webhook",
        notify_webhook_url="https://x.example/y", notify_webhook_style="raw",
    )
    assert await notifications._send("hi") is False


async def test_notify_escalation_reaches_the_configured_webhook(monkeypatch, recorder):
    _configure(
        monkeypatch, notify_target="webhook",
        notify_webhook_url="https://x.example/y", notify_webhook_style="slack",
        moderation_timeout_hours=8, conclave_dashboard_url="",
    )
    assert await notifications.notify_escalation(
        target_type="post", queue_id="abc", reason="spam", preview="hello"
    ) is True
    _url, payload = _Recorder.last.calls[0]
    assert "ESCALATE" in payload["text"]
    assert "<b>" not in payload["text"]
