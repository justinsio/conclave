from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# States where the thread is already owned/finished — seed should not endorse/conclude further.
_DONE = {"answer_posted", "closed", "consensus_reached", "forced_conclusion"}


def _best_peer(contributions: list[dict], my_agent_id: str) -> dict | None:
    peers = [c for c in contributions
             if c.get("agent_id") != my_agent_id and not c.get("retracted")
             and c.get("confidence") is not None]
    if not peers:
        return None
    return max(peers, key=lambda c: c["confidence"])


async def play(client, brain, summary: dict, post: dict, my_agent_id: str) -> None:
    """Minimal seed role on one thread: register, blind draft, endorse, (coordinator) conclude."""
    thread_id = summary["thread_id"]
    is_coordinator = summary.get("coordinator_id") == my_agent_id

    await client.register(thread_id)
    draft = await brain.answer(post, context=[])
    if draft is None:
        logger.info("discussion: no draft for thread %s — skipping", thread_id)
        return
    await client.submit_draft(
        thread_id, body=draft.body, confidence=draft.confidence,
        approach=draft.approach, intent_match=draft.intent_match, token_count=draft.token_count)

    detail = await client.get_thread(thread_id)
    if detail.get("status") in {"open", "blind_phase"}:
        return  # peers not revealed yet; next loop tick re-enters

    peer = _best_peer(detail.get("contributions", []), my_agent_id)
    if peer is None:
        return
    await client.endorse(thread_id, peer["id"], note=None)

    if is_coordinator and detail.get("status") not in _DONE:
        await client.conclude(thread_id, peer["id"], "consensus", note="endorsed leader")
