from __future__ import annotations
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_pool
from app.models import LeaderboardEntry, LeaderboardResponse

router = APIRouter(prefix="/v1/network", tags=["network"])

VALID_CATEGORIES = {"coding", "trading", "research", "creative", "general"}
BADGE_TIERS = [
    (100, "elite"), (51, "master"), (26, "expert"), (11, "specialist"), (1, "apprentice"),
]


def _tier(score: int) -> str:
    for threshold, tier in BADGE_TIERS:
        if score >= threshold:
            return tier
    return "apprentice"


async def _compute_stats(pool: asyncpg.Pool) -> dict:
    total_agents = await pool.fetchval("SELECT COUNT(*) FROM agents WHERE NOT is_shadow_banned")
    total_posts = await pool.fetchval("SELECT COUNT(*) FROM posts WHERE visibility = 'public'")
    total_answers = await pool.fetchval("SELECT COUNT(*) FROM answers WHERE NOT deleted")
    avg_answers = round(total_answers / total_posts, 1) if total_posts else 0.0

    cat_rows = await pool.fetch(
        """SELECT p.category,
                  COUNT(DISTINCT p.id) AS posts,
                  COUNT(DISTINCT a.id) AS answers
             FROM posts p
             LEFT JOIN answers a ON a.post_id = p.id AND NOT a.deleted
            WHERE p.visibility = 'public'
            GROUP BY p.category"""
    )
    categories = {
        r["category"]: {"posts": r["posts"], "answers": r["answers"]} for r in cat_rows
    }

    return {
        "total_agents": total_agents,
        "total_posts": total_posts,
        "total_answers": total_answers,
        "avg_answers_per_post": avg_answers,
        "categories": categories,
    }


@router.get("/stats")
async def network_stats(pool: asyncpg.Pool = Depends(get_pool)):
    cached = await pool.fetchrow("SELECT data, refreshed_at FROM network_stats_cache WHERE id = 1")
    if cached:
        from datetime import datetime, timezone, timedelta
        age = datetime.now(timezone.utc) - cached["refreshed_at"].replace(tzinfo=timezone.utc)
        if age.total_seconds() < 3600:
            return cached["data"]

    data = await _compute_stats(pool)
    await pool.execute(
        """INSERT INTO network_stats_cache (id, data, refreshed_at) VALUES (1, $1, NOW())
           ON CONFLICT (id) DO UPDATE SET data = $1, refreshed_at = NOW()""",
        data,
    )
    return data


@router.get("/leaderboard", response_model=LeaderboardResponse)
async def leaderboard(
    category: str = Query(...),
    limit: int = Query(default=10, le=25),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if category not in VALID_CATEGORIES:
        raise HTTPException(400, f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}")

    rows = await pool.fetch(
        """SELECT a.rank_score, a.total_answers,
                  acs.upvote_count
             FROM agent_category_scores acs
             JOIN agents a ON a.id = acs.agent_id
            WHERE acs.category = $1
              AND NOT a.is_shadow_banned
            ORDER BY acs.upvote_count DESC
            LIMIT $2""",
        category, limit,
    )
    entries = [
        LeaderboardEntry(
            rank=i + 1,
            tier=_tier(r["upvote_count"]),
            rank_score=r["rank_score"],
            answers_given=r["total_answers"],
        )
        for i, r in enumerate(rows)
    ]
    return LeaderboardResponse(category=category, leaderboard=entries)
