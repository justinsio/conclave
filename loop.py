from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

import discussion

logger = logging.getLogger(__name__)


def post_age_minutes(post: dict) -> float:
    created = datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).total_seconds() / 60.0


async def run_once(client, brain, config) -> str:
    # 1. Existing discussion threads take priority.
    threads = await client.list_threads(config.subscriptions)
    if threads:
        summary = threads[0]
        detail = await client.get_thread(summary["thread_id"])
        post = await client.get_post(detail["source_post_id"]) if detail.get("source_post_id") else {}
        await discussion.play(client, brain, summary, post, my_agent_id=client.agent_id)
        return "played_thread"

    # 2. Unanswered posts in priority order (oldest first).
    posts = await client.list_unanswered_posts(config.specialty)
    eligible = [p for p in posts if post_age_minutes(p) >= config.draft_after_minutes]
    if not eligible:
        return "idle"
    target = eligible[0]

    context = await client.corpus_similar(target.get("title", ""), target.get("category", config.specialty))
    draft = await brain.answer(target, context)
    if draft is None:
        return "idle"

    overdue = post_age_minutes(target) >= config.answer_after_minutes
    if draft.confidence >= config.solo_threshold or overdue:
        await client.post_answer(target["id"], draft.body, draft.confidence,
                                 draft.token_count, draft.intent_match)
        return "answered"
    if draft.confidence >= config.open_thread_threshold:
        await client.open_thread(target["id"])
        return "opened_thread"
    return "idle"


async def main_loop(client, brain, config) -> None:
    await client.connect()
    logger.info("seed %s online (specialty=%s)", config.specialty, config.specialty)
    while True:
        try:
            action = await run_once(client, brain, config)
            logger.debug("tick: %s", action)
        except Exception:
            logger.exception("loop tick error")
        await asyncio.sleep(config.poll_interval)
