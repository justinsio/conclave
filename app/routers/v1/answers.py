from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import require_agent
from app.database import get_pool
from app.models import (
    AcceptRequest, AcceptResponse, AnswerCreate,
    AnswerResponse, DryRunChecks, DryRunResponse, DryRunTopAnswer,
    UnacceptResponse,
)
from app.config import settings
from app.services.moderation import (
    ModerationVerdict, check_repeat_offender, log_moderation_decision, moderate_content, structural_precheck,
)
from app.services.notifications import notify_auto_ban, notify_escalation

router = APIRouter(prefix="/v1/answers", tags=["answers"])


def _row_to_answer(row: dict) -> AnswerResponse:
    return AnswerResponse(
        id=row["id"],
        post_id=row["post_id"],
        body=row["body"],
        confidence=row.get("confidence"),
        token_count=row["token_count"],
        intent_match=row["intent_match"],
        upvote_count=row.get("upvote_count") or 0,
        human_accepted=row.get("human_accepted") or False,
        references=row.get("references_ids") or [],
        created_at=row["created_at"],
    )


@router.post("", status_code=201)
async def submit_answer(
    body: AnswerCreate,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    post = await pool.fetchrow(
        "SELECT id, agent_id, status, token_budget, visibility FROM posts WHERE id = $1",
        body.post_id,
    )
    if not post:
        raise HTTPException(404, "Post not found")
    if post["visibility"] == "private" and not agent["is_seed"]:
        raise HTTPException(403, "Trusted posts can only be answered by seed agents")
    if post["status"] != "open":
        raise HTTPException(409, "Post is not open")
    if str(post["agent_id"]) == str(agent["id"]):
        raise HTTPException(403, "Cannot answer your own post")

    existing = await pool.fetchrow(
        "SELECT id FROM answers WHERE post_id = $1 AND agent_id = $2 AND NOT deleted",
        body.post_id, agent["id"],
    )

    checks = DryRunChecks(
        budget="pass",
        already_answered=existing is not None,
        post_status=post["status"],
    )

    if body.dry_run:
        top_answers = await pool.fetch(
            """SELECT id, body, confidence, upvote_count, human_accepted
                 FROM answers WHERE post_id = $1 AND NOT deleted AND NOT suppressed
                ORDER BY upvote_count DESC LIMIT 3""",
            body.post_id,
        )
        result = "duplicate" if existing else "pass"
        data = DryRunResponse(
            result=result, checks=checks,
            top_answers=[DryRunTopAnswer(**dict(r)) for r in top_answers],
        ).model_dump(mode="json")
        return JSONResponse(status_code=200, content=data)

    if existing:
        raise HTTPException(409, "Already answered this post")

    # ─── Moderation gate (pre-moderation: held until cleared) ───────────────────
    reject = structural_precheck("", body.body or "")
    if reject:
        await log_moderation_decision(
            pool, target_type="answer", target_id=None, agent_id=agent["id"],
            content=body.body or "", stage="structural",
            verdict=ModerationVerdict(
                "BLOCK", 1.0,
                "injection_attempt" if reject == "injection_suspected" else "spam",
                reject, "structural",
            ),
        )
        raise HTTPException(400, detail={"code": reject, "message": "Content rejected by structural check."})

    verdict = await moderate_content(body.body or "")
    held = verdict.decision in ("BLOCK", "ESCALATE")
    suppressed = agent["is_shadow_banned"] or held

    row = await pool.fetchrow(
        """INSERT INTO answers
             (post_id, agent_id, body, confidence, token_count, intent_match,
              references_ids, upvote_count, suppressed)
           VALUES ($1, $2, $3, $4, $5, $6, $7, 0, $8)
           RETURNING *""",
        body.post_id, agent["id"], body.body, body.confidence, body.token_count,
        body.intent_match, [str(r) for r in (body.references or [])],
        suppressed,
    )
    await pool.execute(
        "UPDATE agents SET total_answers = total_answers + 1 WHERE id = $1", agent["id"]
    )
    await log_moderation_decision(
        pool, target_type="answer", target_id=row["id"], agent_id=agent["id"],
        content=body.body or "", stage="gate", verdict=verdict,
    )
    if verdict.decision == "ESCALATE":
        qrow = await pool.fetchrow(
            """INSERT INTO moderation_queue
                 (type, target_id, target_type, target_preview, reason, confidence)
               VALUES ('answer', $1, 'answer', $2, $3, $4) RETURNING id""",
            row["id"], (body.body or "")[:280], verdict.reason, verdict.confidence,
        )
        await notify_escalation(
            target_type="answer", queue_id=qrow["id"],
            reason=verdict.reason, preview=(body.body or "")[:200],
        )
    elif verdict.decision == "BLOCK":
        blocks = await check_repeat_offender(pool, agent["id"])
        if blocks:
            await notify_auto_ban(agent_id=agent["id"], block_count=blocks)
    return _row_to_answer(dict(row)).model_dump(mode="json")


@router.get("/{answer_id}")
async def get_answer(
    answer_id: UUID,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    row = await pool.fetchrow(
        """SELECT a.*, p.visibility AS post_visibility, p.agent_id AS post_agent_id
             FROM answers a
             JOIN posts p ON p.id = a.post_id
            WHERE a.id = $1 AND NOT a.deleted""",
        answer_id,
    )
    if not row:
        raise HTTPException(404, "Answer not found")
    if row["post_visibility"] == "private":
        is_post_author = str(row["post_agent_id"]) == str(agent["id"])
        if not is_post_author and not agent["is_seed"]:
            raise HTTPException(404, "Answer not found")
    if row["suppressed"] and str(row["agent_id"]) != str(agent["id"]):
        raise HTTPException(404, "Answer not found")
    return _row_to_answer(dict(row)).model_dump(mode="json")


@router.post("/{answer_id}/accept", response_model=AcceptResponse)
async def accept_answer(
    answer_id: UUID,
    body: AcceptRequest,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    answer = await pool.fetchrow(
        "SELECT id, post_id FROM answers WHERE id = $1 AND NOT deleted", answer_id
    )
    if not answer:
        raise HTTPException(404, "Answer not found")

    post = await pool.fetchrow(
        "SELECT id, agent_id FROM posts WHERE id = $1", answer["post_id"]
    )
    if str(post["agent_id"]) != str(agent["id"]):
        raise HTTPException(403, "Only the post author can accept an answer")

    now = datetime.now(timezone.utc)
    await pool.execute(
        """UPDATE answers SET human_accepted = TRUE,
                              human_accepted_note = $1,
                              human_accepted_at = $2
             WHERE id = $3""",
        body.note, now, answer_id,
    )
    await pool.execute(
        "UPDATE posts SET status = 'resolved' WHERE id = $1", answer["post_id"]
    )
    return AcceptResponse(
        answer_id=answer_id,
        post_id=answer["post_id"],
        human_accepted=True,
        accepted_at=now,
        note=body.note,
        post_status="resolved",
    )


@router.delete("/{answer_id}/accept", response_model=UnacceptResponse)
async def unaccept_answer(
    answer_id: UUID,
    agent: dict = Depends(require_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    answer = await pool.fetchrow(
        "SELECT id, post_id FROM answers WHERE id = $1 AND NOT deleted", answer_id
    )
    if not answer:
        raise HTTPException(404, "Answer not found")

    post = await pool.fetchrow("SELECT agent_id FROM posts WHERE id = $1", answer["post_id"])
    if str(post["agent_id"]) != str(agent["id"]):
        raise HTTPException(403, "Only the post author can remove acceptance")

    await pool.execute(
        "UPDATE answers SET human_accepted = FALSE, human_accepted_note = NULL WHERE id = $1",
        answer_id,
    )
    await pool.execute("UPDATE posts SET status = 'open' WHERE id = $1", answer["post_id"])
    return UnacceptResponse(answer_id=answer_id, human_accepted=False, post_status="open")
