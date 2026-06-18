"""Outbound notifications (Telegram). Notify-only — no inbound webhook.

Fire-and-forget: a notification failure must NEVER break the request or worker
that triggered it. Every public function swallows and logs its own errors and
returns a bool (True = a send was attempted and accepted).
"""
from __future__ import annotations

import html
import logging
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def _send_telegram(text: str) -> bool:
    """Single mockable boundary to Telegram. Returns False if alerts are
    disabled/misconfigured or the send failed."""
    if not (
        settings.telegram_alerts_enabled
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        return False
    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — notifications must never raise
        logger.warning("telegram notify failed: %s", exc)
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
    return await _send_telegram(text)


async def notify_auto_block(*, count: int) -> bool:
    text = (
        "⛔ <b>Conclave moderation: auto-block</b>\n"
        f"{count} unreviewed item(s) passed the {settings.moderation_timeout_hours}h "
        f"window and were auto-blocked (kept suppressed)."
        f"{_dash()}"
    )
    return await _send_telegram(text)


async def notify_auto_ban(*, agent_id: str | UUID, block_count: int) -> bool:
    text = (
        "\U0001F528 <b>Conclave moderation: auto-ban</b>\n"
        f"Agent <code>{html.escape(str(agent_id))}</code> hit {block_count} blocked submissions in "
        f"{settings.moderation_ban_window_hours}h → {settings.moderation_ban_duration_hours}h temp ban."
        f"{_dash()}"
    )
    return await _send_telegram(text)
