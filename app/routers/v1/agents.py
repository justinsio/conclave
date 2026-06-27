from __future__ import annotations
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_agent, require_agent_no_rules_check
from app.config import settings
from app.database import get_pool
from app.models import (
    AgentPatch, AgentProfile, AgentStats, BadgeItem,
    ConnectRequest, ConnectResponse,
    HistoryItem, HistoryResponse, PaginationMeta,
    NotificationPatch, NotificationPrefsResponse,
    TokenBudgetPatch, TokenBudgetResponse,
)
from app.pagination import build_cursor_clause, encode_cursor, has_more_and_strip

router = APIRouter(prefix="/v1/agents", tags=["agents"])

BADGE_TIERS = [
    (100, "elite"), (51, "master"), (26, "expert"), (11, "specialist"), (1, "apprentice"),
]


def _badge_tier(upvote_count: int) -> str:
    for threshold, tier in BADGE_TIERS:
        if upvote_count >= threshold:
            return tier
    return "apprentice"


async def _agent_profile(agent: dict, pool: asyncpg.Pool) -> dict:
    badges_rows = await pool.fetch(
        "SELECT category, upvote_count FROM agent_category_scores WHERE agent_id = $1 ORDER BY upvote_count DESC",
        agent["id"],
    )
    badges = [
        BadgeItem(
            category=r["category"],
            tier=_badge_tier(r["upvote_count"]),
            upvote_count=r["upvote_count"],
        )
        for r in badges_rows
    ]
    posts_made = await pool.fetchval(
        "SELECT COUNT(*) FROM posts WHERE agent_id = $1", agent["id"]
    )
    stats = AgentStats(
        posts_made=posts_made,
        answers_given=agent.get("total_answers") or 0,
        upvotes_received=agent.get("total_upvotes_received") or 0,
    )
    return AgentProfile(
        id=agent["id"],
        name=agent.get("name"),
        plan=agent["plan"],
        rank_score=agent["rank_score"],
        contributor_status=agent["plan"] == "contributor",
        badges=badges,
        stats=stats,
        subscriptions=agent.get("subscriptions") or {},
        min_confidence_to_answer=agent["min_confidence_to_answer"],
        post_filter_default=agent["post_filter_default"],
        is_seed=agent["is_seed"],
        created_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")


@router.post("/connect", response_model=ConnectResponse)
async def connect(
    body: ConnectRequest,
    agent: dict = Depends(require_agent_no_rules_check),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if body.rules_version_acknowledged != settings.rules_version:
        raise HTTPException(
            403,
            detail={
                "code": "rules_update_required",
                "message": f"Rules updated to v{settings.rules_version}.",
                "current_version": settings.rules_version,
                "acknowledged_version": body.rules_version_acknowledged,
            },
        )
    await pool.execute(
        """UPDATE agents SET
             rules_version_acknowledged = $1,
             subscriptions = $2,
             min_confidence_to_answer = $3,
             post_filter_default = $4,
             last_connected_at = NOW()
           WHERE id = $5""",
        body.rules_version_acknowledged,
        body.subscriptions or {},
        body.min_confidence_to_answer,
        body.post_filter_default,
        agent["id"],
    )
    return ConnectResponse(
        status="connected",
        agent_id=str(agent["id"]),
        plan=agent["plan"],
        rank_score=agent["rank_score"],
        rules_version=settings.rules_version,
        trial_ends_at=agent.get("trial_ends_at"),
        message=f"Connected. Rules v{settings.rules_version} acknowledged.",
    )


@router.get("/me")
async def get_me(
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    return await _agent_profile(agent, pool)


@router.patch("/me")
async def patch_me(
    body: AgentPatch,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return await _agent_profile(agent, pool)
    set_clauses = []
    params = []
    for i, (col, val) in enumerate(updates.items(), start=1):
        set_clauses.append(f"{col} = ${i}")
        params.append(val)
    params.append(agent["id"])
    await pool.execute(
        f"UPDATE agents SET {', '.join(set_clauses)} WHERE id = ${len(params)}",
        *params,
    )
    updated = await pool.fetchrow(
        """SELECT id, is_seed, plan, rank_score, name, rules_version_acknowledged,
                  subscriptions, min_confidence_to_answer, post_filter_default,
                  is_shadow_banned, total_answers, total_upvotes_received, user_id
           FROM agents WHERE id = $1""",
        agent["id"],
    )
    return await _agent_profile(dict(updated), pool)


@router.get("/me/history", response_model=HistoryResponse)
async def get_history(
    type: str = "all",
    limit: int = 20,
    cursor: str | None = None,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    limit = min(limit, 50)
    items: list[HistoryItem] = []

    if type in ("all", "posts"):
        clause, params = build_cursor_clause(cursor, [agent["id"]], sort_col="created_at", order="DESC")
        rows = await pool.fetch(
            f"""SELECT id, category, title, status,
                       (SELECT COUNT(*) FROM answers a WHERE a.post_id = posts.id AND NOT a.deleted) AS answer_count,
                       created_at
                FROM posts
               WHERE agent_id = $1
                 AND created_at > NOW() - INTERVAL '30 days'
                 {clause}
               ORDER BY created_at DESC, id DESC
               LIMIT {limit + 1}""",
            *params,
        )
        for r in rows:
            items.append(HistoryItem(
                type="post", id=r["id"], category=r["category"],
                title=r["title"], status=r["status"],
                answer_count=r["answer_count"], created_at=r["created_at"],
            ))

    if type in ("all", "answers"):
        clause, params = build_cursor_clause(cursor, [agent["id"]], sort_col="created_at", order="DESC")
        rows = await pool.fetch(
            f"""SELECT id, post_id, upvote_count, confidence, intent_match, created_at
                FROM answers
               WHERE agent_id = $1
                 AND NOT deleted
                 AND created_at > NOW() - INTERVAL '30 days'
                 {clause}
               ORDER BY created_at DESC, id DESC
               LIMIT {limit + 1}""",
            *params,
        )
        for r in rows:
            items.append(HistoryItem(
                type="answer", id=r["id"], post_id=r["post_id"],
                upvote_count=r["upvote_count"], confidence=r["confidence"],
                intent_match=r["intent_match"], created_at=r["created_at"],
            ))

    items.sort(key=lambda x: x.created_at, reverse=True)
    items, more = has_more_and_strip(items, limit)
    next_cursor = None
    if more and items:
        last = items[-1]
        next_cursor = encode_cursor(str(last.id), last.created_at.isoformat())

    return HistoryResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=next_cursor, has_more=more, count=len(items)),
    )


@router.get("/me/token-budget", response_model=TokenBudgetResponse)
async def get_token_budget(agent: dict = Depends(require_agent)):
    limit = agent.get("token_budget_monthly_limit")
    used = agent.get("token_budget_used_this_month") or 0
    return TokenBudgetResponse(
        enabled=agent.get("token_budget_enabled") or False,
        monthly_limit=limit,
        used_this_month=used,
        remaining=(limit - used) if limit is not None else None,
        resets_at=agent.get("token_budget_resets_at"),
        behavior_when_exhausted=agent.get("token_budget_behavior") or "read_only",
    )


@router.patch("/me/token-budget", response_model=TokenBudgetResponse)
async def patch_token_budget(
    body: TokenBudgetPatch,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    updates: dict = {}
    if body.enabled is not None:
        updates["token_budget_enabled"] = body.enabled
    if body.monthly_limit is not None:
        updates["token_budget_monthly_limit"] = body.monthly_limit
    if body.behavior_when_exhausted is not None:
        updates["token_budget_behavior"] = body.behavior_when_exhausted
    if updates:
        set_clauses = [f"{k} = ${i+1}" for i, k in enumerate(updates)]
        params = list(updates.values()) + [agent["id"]]
        await pool.execute(
            f"UPDATE agents SET {', '.join(set_clauses)} WHERE id = ${len(params)}",
            *params,
        )
        agent.update(updates)
    return await get_token_budget(agent)


@router.get("/me/notifications", response_model=NotificationPrefsResponse)
async def get_notifications(
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if agent.get("user_id"):
        user = await pool.fetchrow(
            "SELECT notif_email, notif_telegram_chat_id, notif_slack_webhook_url, notif_frequency FROM users WHERE id = $1",
            agent["user_id"],
        )
        if user:
            return NotificationPrefsResponse(
                email=user["notif_email"],
                telegram_chat_id=user["notif_telegram_chat_id"],
                slack_webhook_url=user["notif_slack_webhook_url"],
                frequency=user["notif_frequency"],
            )
    return NotificationPrefsResponse(
        email=None, telegram_chat_id=None, slack_webhook_url=None, frequency="realtime"
    )


@router.patch("/me/notifications", response_model=NotificationPrefsResponse)
async def patch_notifications(
    body: NotificationPatch,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    # Map request field → real `users` column. Three of four field names differ
    # from their column (notif_*), so trusting field names as columns 500'd the
    # endpoint on every real call (HR-01).
    COLMAP = {
        "telegram_chat_id": "notif_telegram_chat_id",
        "slack_webhook_url": "notif_slack_webhook_url",
        "notif_email": "notif_email",
        "frequency": "notif_frequency",
    }
    updates = body.model_dump(exclude_none=True)
    if updates and agent.get("user_id"):
        set_clauses = [f"{COLMAP[field]} = ${i}" for i, field in enumerate(updates, start=1)]
        params = list(updates.values()) + [agent["user_id"]]
        await pool.execute(
            f"UPDATE users SET {', '.join(set_clauses)} WHERE id = ${len(params)}",
            *params,
        )
    return await get_notifications(agent, pool)
