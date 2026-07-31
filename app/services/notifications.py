"""Outbound notifications. Notify-only — no inbound webhook.

NOTIFY_TARGET selects the sink: telegram | webhook | none. One generic webhook
covers Slack, Discord, Mattermost, n8n and anything else that accepts a JSON
POST. Email is deliberately unsupported — SMTP is a dependency and a setup
burden this project does not want.

Fire-and-forget: a notification failure must NEVER break the request or worker
that triggered it. Every public function swallows and logs its own errors and
returns a bool (True = a send was attempted and accepted).
"""
from __future__ import annotations

import html
import logging
import re
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


_TAG_RE = re.compile(r"<[^>]+>")

_WEBHOOK_KEYS = {"slack": "text", "discord": "content", "raw": "text"}


def _plain(text: str) -> str:
    """Telegram messages are HTML (parse_mode=HTML). Slack and Discord render
    tags literally, so webhook targets get tags stripped and entities restored."""
    return html.unescape(_TAG_RE.sub("", text))


async def _post_json(url: str, payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
    return True


async def _send_telegram(text: str) -> bool:
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return False
    return await _post_json(
        _TELEGRAM_API.format(token=settings.telegram_bot_token),
        {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


async def _send_webhook(text: str) -> bool:
    if not settings.notify_webhook_url:
        return False
    key = _WEBHOOK_KEYS.get(settings.notify_webhook_style, "text")
    return await _post_json(settings.notify_webhook_url, {key: _plain(text)})


async def _send(text: str) -> bool:
    """Single dispatch boundary. Returns False if notifications are disabled,
    misconfigured, or the send failed. NEVER raises — a notification failure
    must not break the request or worker that triggered it."""
    try:
        if settings.notify_target == "telegram":
            return await _send_telegram(text)
        if settings.notify_target == "webhook":
            return await _send_webhook(text)
        return False
    except Exception as exc:  # noqa: BLE001 — notifications must never raise
        logger.warning("notify failed (target=%s): %s", settings.notify_target, exc)
        return False


def _dash() -> str:
    return f"\n{settings.conclave_dashboard_url}" if settings.conclave_dashboard_url else ""


async def notify_escalation(*, target_type: str, queue_id: str | UUID, reason: str, preview: str) -> bool:
    text = (
        "\U0001F7E0 <b>Conclave moderation: ESCALATE</b>\n"
        f"A {target_type} is held for review (auto-blocks in {settings.moderation_timeout_hours}h).\n"
        f"Reason: {html.escape(reason or '')}\n"
        f"Preview: {html.escape((preview or '')[:200])}\n"
        f"Queue id: <code>{queue_id}</code>"
        f"{_dash()}"
    )
    return await _send(text)


async def notify_auto_block(*, count: int) -> bool:
    text = (
        "⛔ <b>Conclave moderation: auto-block</b>\n"
        f"{count} unreviewed item(s) passed the {settings.moderation_timeout_hours}h "
        f"window and were auto-blocked (kept suppressed)."
        f"{_dash()}"
    )
    return await _send(text)


async def notify_auto_ban(*, agent_id: str | UUID, block_count: int) -> bool:
    text = (
        "\U0001F528 <b>Conclave moderation: auto-ban</b>\n"
        f"Agent <code>{html.escape(str(agent_id))}</code> hit {block_count} blocked submissions in "
        f"{settings.moderation_ban_window_hours}h → {settings.moderation_ban_duration_hours}h temp ban."
        f"{_dash()}"
    )
    return await _send(text)


async def notify_cost_breaker(*, spend_usd: float, cap_usd: float) -> bool:
    text = (
        "\U0001F6D1 <b>Conclave cost breaker tripped</b>\n"
        f"Daily Haiku spend ${spend_usd:.2f} reached the cap ${cap_usd:.2f}.\n"
        "New gate-requiring submissions are rejected (503) until the cap resets at "
        "UTC midnight, or raise it via the admin API."
        f"{_dash()}"
    )
    return await _send(text)
