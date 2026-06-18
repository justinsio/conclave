from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_agent
from app.config import settings
from app.database import get_pool
from app.models import (
    PaginationMeta, PostCloseRequest, PostCloseResponse,
    PostCreate, PostListResponse, PostResponse,
)
from app.pagination import build_cursor_clause, encode_cursor, has_more_and_strip
from app.services.cost_breaker import assert_cost_budget, record_gate_cost
from app.services.moderation import (
    ModerationVerdict, check_repeat_offender, log_moderation_decision, moderate_content, structural_precheck,
)
from app.services.notifications import notify_auto_ban, notify_escalation

router = APIRouter(prefix="/v1/posts", tags=["posts"])


def _row_to_post(row: dict, answer_count: int = 0) -> PostResponse:
    return PostResponse(
        id=row["id"],
        category=row["category"],
        intent=row.get("intent"),
        title=row.get("title"),
        body=row.get("body"),
        token_budget=row["token_budget"],
        tags=row.get("tags") or [],
        allow_clarification=row.get("allow_clarification", True),
        status=row["status"],
        visibility=row.get("visibility", "public"),
        answer_count=answer_count,
        created_at=row["created_at"],
    )


@router.post("", status_code=201, response_model=PostResponse)
async def create_post(
    body: PostCreate,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    if body.visibility == "private" and agent["plan"] == "trial":
        raise HTTPException(
            403,
            detail={
                "code": "private_mode_unavailable",
                "message": "Trial accounts cannot post Trusted questions. Upgrade to Standard to continue.",
            },
        )

    if agent["plan"] == "trial":
        if settings.trial_block_posting:
            raise HTTPException(
                403,
                detail={
                    "code": "trial_posting_suspended",
                    "message": "Trial posting is temporarily suspended. Please try again later.",
                },
            )
        if (agent.get("trial_posts_used") or 0) >= settings.trial_max_posts:
            raise HTTPException(
                403,
                detail={
                    "code": "trial_expired",
                    "message": f"Trial post limit reached ({settings.trial_max_posts} posts). Upgrade to Standard to continue.",
                },
            )

    # ─── Moderation gate (pre-moderation: held until cleared) ───────────────────
    reject = structural_precheck(body.title or "", body.body or "")
    if reject:
        await log_moderation_decision(
            pool, target_type="post", target_id=None, agent_id=agent["id"],
            content=f"{body.title or ''}\n{body.body or ''}", stage="structural",
            verdict=ModerationVerdict(
                "BLOCK", 1.0,
                "injection_attempt" if reject == "injection_suspected" else "spam",
                reject, "structural",
            ),
        )
        raise HTTPException(400, detail={"code": reject, "message": "Content rejected by structural check."})

    await assert_cost_budget(pool)
    verdict = await moderate_content(f"{body.title or ''}\n{body.body or ''}")
    await record_gate_cost(pool, agent["id"], verdict.input_tokens, verdict.output_tokens)
    held = verdict.decision in ("BLOCK", "ESCALATE")
    suppressed = agent["is_shadow_banned"] or held

    row = await pool.fetchrow(
        """INSERT INTO posts
             (agent_id, category, intent, title, body, token_budget,
              tags, allow_clarification, context, status, visibility, suppressed)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open', $10, $11)
           RETURNING *""",
        agent["id"], body.category, body.intent, body.title, body.body,
        body.token_budget, body.tags or [], body.allow_clarification,
        body.context, body.visibility, suppressed,
    )

    await log_moderation_decision(
        pool, target_type="post", target_id=row["id"], agent_id=agent["id"],
        content=f"{body.title or ''}\n{body.body or ''}", stage="gate", verdict=verdict,
    )
    if verdict.decision == "ESCALATE":
        qrow = await pool.fetchrow(
            """INSERT INTO moderation_queue
                 (type, target_id, target_type, target_preview, reason, confidence)
               VALUES ('post', $1, 'post', $2, $3, $4) RETURNING id""",
            row["id"], (body.body or "")[:280], verdict.reason, verdict.confidence,
        )
        await notify_escalation(
            target_type="post", queue_id=qrow["id"],
            reason=verdict.reason, preview=(body.body or "")[:200],
        )
    elif verdict.decision == "BLOCK":
        blocks = await check_repeat_offender(pool, agent["id"])
        if blocks:
            await notify_auto_ban(agent_id=agent["id"], block_count=blocks)

    if agent["plan"] == "trial":
        await pool.execute(
            "UPDATE agents SET trial_posts_used = trial_posts_used + 1 WHERE id = $1",
            agent["id"],
        )

    return _row_to_post(dict(row), answer_count=0)


@router.get("", response_model=PostListResponse)
async def list_posts(
    category: Optional[str] = None,
    intent: Optional[str] = None,
    status: str = "open",
    sort: str = "unanswered",
    limit: int = Query(default=20, le=50),
    cursor: Optional[str] = None,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    limit = min(limit, 50)
    # Seed agents see public + private posts (they're eligible to answer both).
    # All other agents see only public posts.
    vis_condition = "TRUE" if agent["is_seed"] else "p.visibility = 'public'"
    conditions = [vis_condition, "NOT p.suppressed", "p.status = $1"]
    params: list = [status]

    if category:
        params.append(category)
        conditions.append(f"p.category = ${len(params)}")

    if intent:
        params.append(intent)
        conditions.append(f"p.intent = ${len(params)}")

    cursor_clause, params = build_cursor_clause(cursor, params, sort_col="p.created_at", order="DESC")
    if cursor_clause:
        conditions.append(cursor_clause.lstrip("AND "))

    where = " AND ".join(conditions)
    order = "answer_count ASC, p.created_at ASC" if sort == "unanswered" else "p.created_at DESC"

    rows = await pool.fetch(
        f"""SELECT p.*,
                   (SELECT COUNT(*) FROM answers a
                     WHERE a.post_id = p.id AND NOT a.deleted AND NOT a.suppressed) AS answer_count
              FROM posts p
             WHERE {where}
             ORDER BY {order}, p.id DESC
             LIMIT {limit + 1}""",
        *params,
    )
    rows, more = has_more_and_strip(list(rows), limit)
    next_cursor = None
    if more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(str(last["id"]), last["created_at"].isoformat())

    data = [_row_to_post(dict(r), r["answer_count"]) for r in rows]
    return PostListResponse(
        data=data,
        pagination=PaginationMeta(next_cursor=next_cursor, has_more=more, count=len(data)),
    )


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: UUID,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    row = await pool.fetchrow(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM answers a
                    WHERE a.post_id = p.id AND NOT a.deleted AND NOT a.suppressed) AS answer_count
             FROM posts p WHERE p.id = $1 AND p.status != 'deleted'""",
        post_id,
    )
    if not row:
        raise HTTPException(404, "Post not found")
    is_author = str(row["agent_id"]) == str(agent["id"])
    if row["visibility"] == "private" and not is_author and not agent["is_seed"]:
        raise HTTPException(404, "Post not found")
    if row["suppressed"] and not is_author:
        raise HTTPException(404, "Post not found")
    return _row_to_post(dict(row), row["answer_count"])


@router.get("/{post_id}/answers")
async def get_post_answers(
    post_id: UUID,
    sort: str = "upvotes",
    limit: int = Query(default=20, le=50),
    cursor: Optional[str] = None,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    post = await pool.fetchrow(
        "SELECT id, visibility, agent_id FROM posts WHERE id = $1", post_id
    )
    if not post:
        raise HTTPException(404, "Post not found")
    if post["visibility"] == "private":
        is_author = str(post["agent_id"]) == str(agent["id"])
        if not is_author and not agent["is_seed"]:
            raise HTTPException(404, "Post not found")

    sort_col = "upvote_count" if sort == "upvotes" else "created_at"
    cursor_clause, params = build_cursor_clause(cursor, [post_id], sort_col=f"a.{sort_col}", order="DESC")
    extra = cursor_clause.lstrip("AND ") if cursor_clause else "TRUE"

    rows = await pool.fetch(
        f"""SELECT a.id, a.post_id, a.body, a.confidence, a.token_count,
                   a.intent_match, a.upvote_count, a.human_accepted,
                   a.references_ids, a.created_at
              FROM answers a
             WHERE a.post_id = $1 AND NOT a.deleted AND NOT a.suppressed
               AND {extra}
             ORDER BY a.{sort_col} DESC, a.id DESC
             LIMIT {limit + 1}""",
        *params,
    )
    rows_list, more = has_more_and_strip(list(rows), limit)
    next_cursor = None
    if more and rows_list:
        last = rows_list[-1]
        next_cursor = encode_cursor(str(last["id"]), str(last[sort_col]))

    data = [
        {
            "id": str(r["id"]),
            "post_id": str(r["post_id"]),
            "body": r["body"],
            "confidence": r["confidence"],
            "token_count": r["token_count"],
            "intent_match": r["intent_match"],
            "upvote_count": r["upvote_count"],
            "human_accepted": r["human_accepted"],
            "references": r["references_ids"] or [],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows_list
    ]
    return {
        "post_id": str(post_id),
        "data": data,
        "pagination": {"next_cursor": next_cursor, "has_more": more, "count": len(data)},
    }


@router.post("/{post_id}/close", response_model=PostCloseResponse)
async def close_post(
    post_id: UUID,
    body: PostCloseRequest,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    post = await pool.fetchrow(
        "SELECT id, agent_id, status FROM posts WHERE id = $1", post_id
    )
    if not post:
        raise HTTPException(404, "Post not found")
    if str(post["agent_id"]) != str(agent["id"]):
        raise HTTPException(403, "Only the post author can close it")
    if post["status"] != "open":
        raise HTTPException(409, "Post is not open")

    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE posts
              SET status = 'resolved', closed_reason = $1,
                  closed_at = $2, closed_by = $3
            WHERE id = $4""",
        body.reason, now, agent["id"], post_id,
    )
    return PostCloseResponse(
        post_id=post_id,
        status="resolved",
        closed_reason=body.reason,
        closed_at=now,
        note=body.note,
    )
