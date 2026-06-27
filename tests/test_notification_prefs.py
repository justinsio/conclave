"""HR-01 / R10 — PATCH /v1/agents/me/notifications must write the real columns.

The handler built SQL straight from the Pydantic field names, but 3 of 4 fields
(`telegram_chat_id`, `slack_webhook_url`, `frequency`) don't match their `users`
columns (`notif_*`), so every real PATCH 500'd with UndefinedColumnError. No test
covered it, so the green suite hid a dead public endpoint.
"""
from __future__ import annotations

import pytest

from app.auth import hash_api_key

pytestmark = pytest.mark.usefixtures("clean_db")

API_KEY = "test-notif-key-01"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


async def _user_linked_agent(db_pool) -> None:
    user_id = await db_pool.fetchval(
        "INSERT INTO users (email) VALUES ('notif@test.io') RETURNING id"
    )
    await db_pool.execute(
        """INSERT INTO agents (api_key_hash, is_seed, plan,
                               rules_version_acknowledged, user_id)
           VALUES ($1, false, 'reader', '1.0', $2)""",
        hash_api_key(API_KEY), user_id,
    )


async def test_patch_notifications_persists_all_fields(client, db_pool):
    await _user_linked_agent(db_pool)

    r = await client.patch(
        "/v1/agents/me/notifications",
        json={
            "telegram_chat_id": "tg-12345",
            "slack_webhook_url": "https://hooks.slack.test/abc",
            "notif_email": "alerts@test.io",
            "frequency": "daily_digest",
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["telegram_chat_id"] == "tg-12345"
    assert body["slack_webhook_url"] == "https://hooks.slack.test/abc"
    assert body["email"] == "alerts@test.io"
    assert body["frequency"] == "daily_digest"


async def test_patch_notifications_round_trips_through_get(client, db_pool):
    await _user_linked_agent(db_pool)

    await client.patch(
        "/v1/agents/me/notifications",
        json={"telegram_chat_id": "tg-999"},
        headers=AUTH,
    )
    r = await client.get("/v1/agents/me/notifications", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["telegram_chat_id"] == "tg-999"
