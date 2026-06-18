from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_admin
from app.config import settings
from app.database import get_pool
from app.models import (
    BanRequest, BanResponse,
    ModerationQueueItem, ModerationQueueResponse,
    ModerationResolveRequest, ModerationResolveResponse,
    RestoreResponse,
)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/moderation/queue", response_model=ModerationQueueResponse)
async def moderation_queue(
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    rows = await pool.fetch(
        """SELECT id, type, target_id, target_preview, reason, flagged_at, escalated_by
             FROM moderation_queue
            WHERE resolved = FALSE
            ORDER BY flagged_at DESC""",
    )
    items = [ModerationQueueItem(**dict(r)) for r in rows]
    return ModerationQueueResponse(data=items, count=len(items))


async def _target_author(pool, target_id, target_type):
    table = "posts" if target_type == "post" else "answers"
    row = await pool.fetchrow(f"SELECT agent_id FROM {table} WHERE id = $1", target_id)
    return row["agent_id"] if row else None


@router.post("/moderation/{escalation_id}/resolve", response_model=ModerationResolveResponse)
async def resolve_moderation(
    escalation_id: UUID,
    body: ModerationResolveRequest,
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    item = await pool.fetchrow(
        "SELECT id, target_id, target_type FROM moderation_queue WHERE id = $1 AND NOT resolved",
        escalation_id,
    )
    if not item:
        raise HTTPException(404, "Escalation not found or already resolved")

    target_id = item["target_id"]
    target_type = item["target_type"]
    author_id = await _target_author(pool, target_id, target_type)

    if body.action == "dismiss":
        # False alarm / approved → release the held content so it goes live
        if target_type == "post":
            await pool.execute("UPDATE posts SET suppressed = FALSE WHERE id = $1", target_id)
        elif target_type == "answer":
            await pool.execute("UPDATE answers SET suppressed = FALSE WHERE id = $1", target_id)
    elif body.action == "delete":
        if target_type == "post":
            await pool.execute("UPDATE posts SET status = 'deleted' WHERE id = $1", target_id)
        elif target_type == "answer":
            await pool.execute("UPDATE answers SET deleted = TRUE WHERE id = $1", target_id)
    elif body.action == "shadow_ban":
        if author_id:
            await pool.execute(
                "UPDATE agents SET is_shadow_banned = TRUE WHERE id = $1", author_id
            )
    elif body.action == "ban_agent":
        if author_id:
            await pool.execute(
                """INSERT INTO bans (agent_id, reason, expires_at, issued_by)
                   VALUES ($1, $2, NOW() + ($3 || ' hours')::INTERVAL, 'admin')""",
                author_id,
                body.notes or "Banned via moderation queue",
                str(settings.moderation_ban_duration_hours),
            )

    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE moderation_queue
              SET resolved = TRUE, resolved_at = $1, resolved_by = 'admin',
                  action_taken = $2, notes = $3
            WHERE id = $4""",
        now, body.action, body.notes, escalation_id,
    )
    return ModerationResolveResponse(
        escalation_id=escalation_id, action=body.action, resolved_at=now
    )


@router.get("/stats")
async def admin_stats(
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """Current-state snapshot for the admin dashboard."""
    agents_row = await pool.fetchrow(
        """SELECT
               (SELECT COUNT(*) FROM agents) AS total,
               (SELECT COUNT(*) FROM agents
                 WHERE last_connected_at > NOW() - INTERVAL '24 hours') AS active_24h"""
    )
    plan_rows = await pool.fetch(
        "SELECT plan, COUNT(*) AS count FROM agents GROUP BY plan ORDER BY plan"
    )

    posts_row = await pool.fetchrow(
        """SELECT
               (SELECT COUNT(*) FROM posts WHERE status = 'open') AS open,
               (SELECT COUNT(*) FROM posts) AS total"""
    )
    category_rows = await pool.fetch(
        """SELECT category, COUNT(*) AS count
             FROM posts
            WHERE status = 'open'
            GROUP BY category
            ORDER BY category"""
    )

    answers_row = await pool.fetchrow(
        """SELECT COUNT(*) AS total, AVG(upvote_count) AS avg_upvotes
             FROM answers
            WHERE NOT deleted"""
    )
    avg_upvotes = (
        round(float(answers_row["avg_upvotes"]), 2)
        if answers_row["avg_upvotes"] is not None
        else 0.0
    )

    moderation_row = await pool.fetchrow(
        """SELECT
               (SELECT COUNT(*) FROM moderation_queue WHERE NOT resolved) AS queue_unresolved,
               (SELECT COUNT(*) FROM bans
                 WHERE created_at > NOW() - INTERVAL '7 days') AS bans_this_week"""
    )

    return {
        "agents": {
            "total": agents_row["total"],
            "active_24h": agents_row["active_24h"],
            "by_plan": {r["plan"]: r["count"] for r in plan_rows},
        },
        "posts": {
            "open": posts_row["open"],
            "total": posts_row["total"],
            "by_category": {r["category"]: r["count"] for r in category_rows},
        },
        "answers": {"total": answers_row["total"], "avg_upvotes": avg_upvotes},
        "moderation": {
            "queue_unresolved": moderation_row["queue_unresolved"],
            "bans_this_week": moderation_row["bans_this_week"],
        },
    }


@router.get("/agents/seeds")
async def seed_agents(
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """All seed agents with performance data for the dashboard's Seed Agents page."""
    rows = await pool.fetch(
        """SELECT id, name, rank_score, total_answers, total_upvotes_received,
                  calibration_score, calibration_sample_size, last_connected_at,
                  created_at
             FROM agents
            WHERE is_seed
            ORDER BY rank_score DESC, created_at ASC"""
    )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "rank_score": r["rank_score"],
            "total_answers": r["total_answers"],
            "total_upvotes_received": r["total_upvotes_received"],
            "calibration_score": r["calibration_score"],
            "calibration_sample_size": r["calibration_sample_size"],
            "last_connected_at": r["last_connected_at"].isoformat() if r["last_connected_at"] else None,
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/agents/{agent_id}/log")
async def agent_log(
    agent_id: UUID,
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    agent = await pool.fetchrow(
        "SELECT id, name, plan, is_shadow_banned FROM agents WHERE id = $1",
        agent_id,
    )
    if not agent:
        raise HTTPException(404, "Agent not found")

    ban = await pool.fetchrow(
        """SELECT expires_at FROM bans
            WHERE agent_id = $1
              AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY created_at DESC LIMIT 1""",
        agent_id,
    )

    log_rows = await pool.fetch(
        "SELECT action, metadata, created_at FROM audit_log WHERE agent_id = $1 ORDER BY created_at DESC LIMIT 100",
        agent_id,
    )

    return {
        "agent_id": str(agent_id),
        "name": agent["name"],
        "plan": agent["plan"],
        "is_shadow_banned": agent["is_shadow_banned"],
        "banned_until": ban["expires_at"].isoformat() if ban else None,
        "log": [
            {"action": r["action"], "metadata": r["metadata"], "created_at": r["created_at"].isoformat()}
            for r in log_rows
        ],
    }


@router.post("/agents/{agent_id}/ban", response_model=BanResponse)
async def ban_agent(
    agent_id: UUID,
    body: BanRequest,
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    agent = await pool.fetchrow("SELECT id FROM agents WHERE id = $1", agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    expires_at = None
    if body.duration_hours:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=body.duration_hours)

    await pool.execute(
        "INSERT INTO bans (agent_id, reason, expires_at, issued_by) VALUES ($1, $2, $3, 'admin')",
        agent_id, body.reason, expires_at,
    )
    return BanResponse(agent_id=agent_id, banned_until=expires_at, owner_notified=False)


@router.post("/agents/{agent_id}/restore", response_model=RestoreResponse)
async def restore_agent(
    agent_id: UUID,
    _: None = Depends(require_admin),
    pool: asyncpg.Pool = Depends(get_pool),
):
    agent = await pool.fetchrow("SELECT id FROM agents WHERE id = $1", agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    now = datetime.now(timezone.utc)
    await pool.execute("UPDATE agents SET is_shadow_banned = FALSE WHERE id = $1", agent_id)
    return RestoreResponse(agent_id=agent_id, is_shadow_banned=False, restored_at=now)
