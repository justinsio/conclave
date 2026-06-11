from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_seed_agent
from app.database import get_pool
from app.models import (
    ChallengeRequest,
    ConcludeRequest,
    ConcludeResponse,
    ContributionCreate,
    ContributionResponse,
    DraftCreate,
    DraftCreatedResponse,
    EndorseRequest,
    RegisterResponse,
    RetractResponse,
    SignalResponse,
    SynthesizeRequest,
    ThreadCreate,
    ThreadCreatedResponse,
    ThreadDetailResponse,
    ThreadListItem,
    ThreadListResponse,
)
from app.services.blind_phase import advance_blind_phase
from app.services.divergence import effective_confidence
from app.services.moderation import detect_framing_alert, run_consensus_gate
from app.services.token_count import compute_token_count

router = APIRouter(prefix="/internal/threads", tags=["internal-threads"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _get_active_thread(conn: asyncpg.Connection, thread_id: str) -> asyncpg.Record:
    thread = await conn.fetchrow(
        "SELECT * FROM seed_threads WHERE id = $1",
        thread_id,
    )
    if not thread:
        raise HTTPException(404, "Thread not found")
    return thread


async def _require_coordinator(thread: asyncpg.Record, agent_id) -> None:
    if str(thread["coordinator_id"]) != str(agent_id):
        raise HTTPException(403, "Only the coordinator can perform this action")


async def _source_integrity_check(
    conn: asyncpg.Connection, post: asyncpg.Record
) -> tuple[bool, bool]:
    """
    Returns (elevated_risk, framing_alert).
    elevated_risk: posting agent has prior injection flags.
    framing_alert: post contains a contestable assertion framed as fact — seeds
                   are prompted to challenge premises when this is set.
    """
    agent = await conn.fetchrow(
        "SELECT injection_flag FROM agents WHERE id = $1",
        post["agent_id"],
    )
    elevated_risk = bool(agent and agent.get("injection_flag"))
    framing_alert = detect_framing_alert(
        post.get("title") or "", post.get("body") or ""
    )
    return elevated_risk, framing_alert


async def _post_consensus_gate(
    conn: asyncpg.Connection, contribution_body: str
) -> bool:
    """
    Returns True if the answer passes post-consensus content safety checks.
    Calls Ollama when configured; passes through when ollama_base_url is unset.
    """
    return await run_consensus_gate(contribution_body)


def _build_contribution_response(
    row: asyncpg.Record,
    requesting_agent_id,
    thread_is_open: bool,
) -> ContributionResponse:
    # Hide confidence from peers while thread is still open (prevent anchoring)
    hide_confidence = thread_is_open and str(row["agent_id"]) != str(requesting_agent_id)
    return ContributionResponse(
        id=row["id"],
        thread_id=row["thread_id"],
        agent_id=row["agent_id"],
        body=row["body"],
        confidence=None if hide_confidence else row["confidence"],
        approach=row["approach"],
        token_count=row["token_count"],
        intent_match=row["intent_match"],
        retracted=row["retracted"],
        from_draft=row["from_draft"],
        is_synthesis=row["is_synthesis"],
        endorsement_count=row["endorsement_count"],
        challenge_count=row["challenge_count"],
        created_at=row["created_at"],
    )


# ─── POST /internal/threads ───────────────────────────────────────────────────

@router.post("", status_code=201, response_model=ThreadCreatedResponse)
async def create_thread(
    body: ThreadCreate,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        # Reject if active thread already exists for this post
        existing = await conn.fetchrow(
            """SELECT id FROM seed_threads
               WHERE source_post_id = $1
                 AND status NOT IN ('answer_posted', 'closed')""",
            body.source_post_id,
        )
        if existing:
            raise HTTPException(409, "Active thread already exists for this post. Submit a draft to the existing thread.")

        post = await conn.fetchrow(
            "SELECT id, agent_id, created_at, token_budget, category, intent, title, tags FROM posts WHERE id = $1",
            body.source_post_id,
        )
        if not post:
            raise HTTPException(404, "Post not found")

        elevated_risk, framing_alert = await _source_integrity_check(conn, post)

        now = _utcnow()
        deadline = post["created_at"] + timedelta(minutes=12)
        blind_phase_ends_at = now + timedelta(seconds=90)

        thread = await conn.fetchrow(
            """INSERT INTO seed_threads
               (source_post_id, coordinator_id, status, deadline, blind_phase_ends_at,
                elevated_risk, framing_alert, coordinator_last_seen_at)
               VALUES ($1, $2, 'blind_phase', $3, $4, $5, $6, NOW())
               RETURNING *""",
            body.source_post_id,
            agent["id"],
            deadline,
            blind_phase_ends_at,
            elevated_risk,
            framing_alert,
        )

        return ThreadCreatedResponse(
            thread_id=thread["id"],
            source_post_id=thread["source_post_id"],
            coordinator_id=thread["coordinator_id"],
            status=thread["status"],
            blind_phase_ends_at=thread["blind_phase_ends_at"],
            deadline=thread["deadline"],
            elevated_risk=thread["elevated_risk"],
            framing_alert=thread["framing_alert"],
            created_at=thread["created_at"],
        )


# ─── POST /internal/threads/{thread_id}/register ─────────────────────────────

@router.post("/{thread_id}/register", response_model=RegisterResponse)
async def register(
    thread_id: UUID,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        async with conn.transaction():
            thread = await conn.fetchrow(
                "SELECT status, registered_agent_ids, blind_phase_ends_at FROM seed_threads WHERE id = $1 FOR UPDATE",
                thread_id,
            )
            if not thread:
                raise HTTPException(404, "Thread not found")
            if thread["status"] != "blind_phase":
                raise HTTPException(400, "Thread is not in blind_phase")

            registered = thread["registered_agent_ids"] or []
            if agent["id"] in registered:
                raise HTTPException(409, "Already registered for this thread")

            await conn.execute(
                "UPDATE seed_threads SET registered_agent_ids = array_append(registered_agent_ids, $2) WHERE id = $1",
                thread_id,
                agent["id"],
            )

    return RegisterResponse(
        registered=True,
        thread_id=thread_id,
        blind_phase_ends_at=thread["blind_phase_ends_at"],
    )


# ─── POST /internal/threads/{thread_id}/draft ─────────────────────────────────

@router.post("/{thread_id}/draft", status_code=201, response_model=DraftCreatedResponse)
async def submit_draft(
    thread_id: UUID,
    body: DraftCreate,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        thread = await conn.fetchrow(
            """SELECT st.status, st.blind_phase_ends_at, st.registered_agent_ids,
                      p.token_budget
               FROM seed_threads st
               LEFT JOIN posts p ON p.id = st.source_post_id
               WHERE st.id = $1""",
            thread_id,
        )
        if not thread:
            raise HTTPException(404, "Thread not found")
        if thread["status"] not in ("blind_phase", "divergence_hold"):
            raise HTTPException(400, "Thread is not in blind_phase. Submit a contribution instead.")

        existing_draft = await conn.fetchrow(
            "SELECT id FROM seed_drafts WHERE thread_id = $1 AND agent_id = $2",
            thread_id, agent["id"],
        )
        if existing_draft:
            raise HTTPException(409, "Draft already submitted for this thread")

        token_count = compute_token_count(body.body)
        if thread["token_budget"] and token_count > thread["token_budget"]:
            raise HTTPException(422, f"Body exceeds token budget ({token_count} > {thread['token_budget']})")

        draft = await conn.fetchrow(
            """INSERT INTO seed_drafts
               (thread_id, agent_id, body, confidence, approach, token_count, intent_match)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               RETURNING id""",
            thread_id, agent["id"], body.body, body.confidence,
            body.approach, token_count, body.intent_match,
        )

        # Check if all registered seeds have now submitted → advance immediately
        registered = thread["registered_agent_ids"] or []
        if registered:
            submitted_count = await conn.fetchval(
                "SELECT COUNT(*) FROM seed_drafts WHERE thread_id = $1",
                thread_id,
            )
            if submitted_count >= len(registered):
                # Release connection before calling advance (it acquires its own)
                pass  # advance happens after this block

    # Outside the connection context to avoid deadlock
    registered = thread["registered_agent_ids"] or []
    if registered:
        submitted_count = await pool.fetchval(
            "SELECT COUNT(*) FROM seed_drafts WHERE thread_id = $1",
            thread_id,
        )
        if submitted_count >= len(registered):
            await advance_blind_phase(pool, str(thread_id))

    return DraftCreatedResponse(
        draft_id=draft["id"],
        status="committed",
        blind_phase_ends_at=thread["blind_phase_ends_at"],
    )


# ─── GET /internal/threads ────────────────────────────────────────────────────

@router.get("", response_model=ThreadListResponse)
async def list_threads(
    status: str = Query(default="open"),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=10, le=25),
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    allowed_statuses = {"open", "blind_phase", "deliberating", "divergence_hold"}
    requested = {s.strip() for s in status.split(",")}
    filter_statuses = list(requested & allowed_statuses) or ["open"]

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT st.id, st.source_post_id, st.coordinator_id, st.status,
                      st.deadline, st.pending_coordinator_notification,
                      p.category AS source_post_category,
                      p.intent   AS source_post_intent,
                      p.title    AS source_post_title,
                      (SELECT COUNT(*) FROM seed_contributions sc
                       WHERE sc.thread_id = st.id AND sc.retracted = FALSE) AS contribution_count
               FROM seed_threads st
               LEFT JOIN posts p ON p.id = st.source_post_id
               WHERE st.status = ANY($1::varchar[])
                 AND ($2::varchar IS NULL OR p.category = $2)
               ORDER BY st.deadline ASC
               LIMIT $3""",
            filter_statuses,
            category,
            limit,
        )

        now = _utcnow()
        items = []
        for row in rows:
            is_coordinator = str(row["coordinator_id"]) == str(agent["id"])
            promoted = is_coordinator and bool(row["pending_coordinator_notification"])

            # Clear the notification flag if this agent is seeing it
            if promoted:
                await conn.execute(
                    "UPDATE seed_threads SET pending_coordinator_notification = FALSE WHERE id = $1",
                    row["id"],
                )

            deadline = row["deadline"]
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds_left = max(0, int((deadline - now).total_seconds()))

            items.append(
                ThreadListItem(
                    thread_id=row["id"],
                    source_post_id=row["source_post_id"],
                    source_post_category=row["source_post_category"],
                    source_post_intent=row["source_post_intent"],
                    source_post_title=row["source_post_title"],
                    coordinator_id=row["coordinator_id"],
                    status=row["status"],
                    contribution_count=row["contribution_count"],
                    deadline=row["deadline"],
                    time_to_deadline_seconds=seconds_left,
                    promoted_to_coordinator=promoted,
                )
            )

    return ThreadListResponse(data=items, count=len(items))


# ─── GET /internal/threads/{thread_id} ────────────────────────────────────────

@router.get("/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: UUID,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        thread = await conn.fetchrow(
            "SELECT * FROM seed_threads WHERE id = $1",
            thread_id,
        )
        if not thread:
            raise HTTPException(404, "Thread not found")

        # Update coordinator's last-seen timestamp
        if str(thread["coordinator_id"]) == str(agent["id"]):
            await conn.execute(
                "UPDATE seed_threads SET coordinator_last_seen_at = NOW() WHERE id = $1",
                thread_id,
            )

        thread_is_open = thread["status"] not in ("answer_posted", "closed")

        contributions_rows = await conn.fetch(
            """SELECT * FROM seed_contributions
               WHERE thread_id = $1
               ORDER BY created_at ASC""",
            thread_id,
        )

        signals_rows = await conn.fetch(
            """SELECT * FROM seed_signals
               WHERE thread_id = $1
               ORDER BY created_at ASC""",
            thread_id,
        )

        contributions = [
            _build_contribution_response(row, agent["id"], thread_is_open)
            for row in contributions_rows
        ]

        signals = [
            SignalResponse(
                id=row["id"],
                thread_id=row["thread_id"],
                agent_id=row["agent_id"],
                signal_type=row["signal_type"],
                target_contribution_id=row["target_contribution_id"],
                counter_contribution_id=row["counter_contribution_id"],
                merges=row["merges"],
                body=row["body"],
                created_at=row["created_at"],
            )
            for row in signals_rows
        ]

    return ThreadDetailResponse(
        thread_id=thread["id"],
        source_post_id=thread["source_post_id"],
        coordinator_id=thread["coordinator_id"],
        status=thread["status"],
        deadline=thread["deadline"],
        contributions=contributions,
        signals=signals,
    )


# ─── POST /internal/threads/{thread_id}/contribute ────────────────────────────

@router.post("/{thread_id}/contribute", status_code=201, response_model=ContributionResponse)
async def contribute(
    thread_id: UUID,
    body: ContributionCreate,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        thread = await conn.fetchrow(
            """SELECT st.status, p.token_budget
               FROM seed_threads st
               LEFT JOIN posts p ON p.id = st.source_post_id
               WHERE st.id = $1""",
            thread_id,
        )
        if not thread:
            raise HTTPException(404, "Thread not found")
        if thread["status"] not in ("deliberating", "consensus_reached"):
            raise HTTPException(403, "Thread is closed or not open for contributions")

        existing = await conn.fetchrow(
            "SELECT id FROM seed_contributions WHERE thread_id = $1 AND agent_id = $2 AND retracted = FALSE",
            thread_id, agent["id"],
        )
        if existing:
            raise HTTPException(409, "You already have an active contribution. Retract it first.")

        token_count = compute_token_count(body.body)
        if thread["token_budget"] and token_count > thread["token_budget"]:
            raise HTTPException(422, f"Body exceeds token budget ({token_count} > {thread['token_budget']})")

        row = await conn.fetchrow(
            """INSERT INTO seed_contributions
               (thread_id, agent_id, body, confidence, approach, token_count, intent_match)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               RETURNING *""",
            thread_id, agent["id"], body.body, body.confidence,
            body.approach, token_count, body.intent_match,
        )

    return _build_contribution_response(row, agent["id"], thread_is_open=True)


# ─── POST /internal/threads/{thread_id}/retract/{contribution_id} ─────────────

@router.post("/{thread_id}/retract/{contribution_id}", response_model=RetractResponse)
async def retract_contribution(
    thread_id: UUID,
    contribution_id: UUID,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        contrib = await conn.fetchrow(
            "SELECT id, agent_id FROM seed_contributions WHERE id = $1 AND thread_id = $2",
            contribution_id, thread_id,
        )
        if not contrib:
            raise HTTPException(404, "Contribution not found in this thread")
        if str(contrib["agent_id"]) != str(agent["id"]):
            raise HTTPException(403, "You can only retract your own contributions")

        await conn.execute(
            "UPDATE seed_contributions SET retracted = TRUE WHERE id = $1",
            contribution_id,
        )

    return RetractResponse(contribution_id=contribution_id, retracted=True)


# ─── POST /internal/threads/{thread_id}/endorse ───────────────────────────────

@router.post("/{thread_id}/endorse", status_code=201, response_model=SignalResponse)
async def endorse(
    thread_id: UUID,
    body: EndorseRequest,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        # Verify target contribution is in this thread
        target = await conn.fetchrow(
            "SELECT id FROM seed_contributions WHERE id = $1 AND thread_id = $2 AND retracted = FALSE",
            body.target_contribution_id, thread_id,
        )
        if not target:
            raise HTTPException(404, "Target contribution not found in this thread")

        # Check for duplicate endorse
        existing = await conn.fetchrow(
            """SELECT id FROM seed_signals
               WHERE thread_id = $1 AND agent_id = $2
                 AND signal_type = 'endorse' AND target_contribution_id = $3""",
            thread_id, agent["id"], body.target_contribution_id,
        )
        if existing:
            raise HTTPException(409, "Already endorsed this contribution")

        async with conn.transaction():
            signal = await conn.fetchrow(
                """INSERT INTO seed_signals
                   (thread_id, agent_id, signal_type, target_contribution_id, body)
                   VALUES ($1, $2, 'endorse', $3, $4)
                   RETURNING *""",
                thread_id, agent["id"], body.target_contribution_id, body.note,
            )
            await conn.execute(
                "UPDATE seed_contributions SET endorsement_count = endorsement_count + 1 WHERE id = $1",
                body.target_contribution_id,
            )

    return SignalResponse(
        id=signal["id"],
        thread_id=signal["thread_id"],
        agent_id=signal["agent_id"],
        signal_type=signal["signal_type"],
        target_contribution_id=signal["target_contribution_id"],
        counter_contribution_id=None,
        merges=None,
        body=signal["body"],
        created_at=signal["created_at"],
    )


# ─── POST /internal/threads/{thread_id}/challenge ─────────────────────────────

@router.post("/{thread_id}/challenge", status_code=201, response_model=SignalResponse)
async def challenge(
    thread_id: UUID,
    body: ChallengeRequest,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            "SELECT id FROM seed_contributions WHERE id = $1 AND thread_id = $2 AND retracted = FALSE",
            body.target_contribution_id, thread_id,
        )
        if not target:
            raise HTTPException(404, "Target contribution not found in this thread")

        counter = await conn.fetchrow(
            "SELECT id FROM seed_contributions WHERE id = $1 AND thread_id = $2 AND retracted = FALSE",
            body.counter_contribution_id, thread_id,
        )
        if not counter:
            raise HTTPException(400, "counter_contribution_id must be an active contribution in this thread")

        async with conn.transaction():
            signal = await conn.fetchrow(
                """INSERT INTO seed_signals
                   (thread_id, agent_id, signal_type, target_contribution_id,
                    counter_contribution_id, body)
                   VALUES ($1, $2, 'challenge', $3, $4, $5)
                   RETURNING *""",
                thread_id, agent["id"], body.target_contribution_id,
                body.counter_contribution_id, body.reasoning,
            )
            await conn.execute(
                "UPDATE seed_contributions SET challenge_count = challenge_count + 1 WHERE id = $1",
                body.target_contribution_id,
            )

    return SignalResponse(
        id=signal["id"],
        thread_id=signal["thread_id"],
        agent_id=signal["agent_id"],
        signal_type=signal["signal_type"],
        target_contribution_id=signal["target_contribution_id"],
        counter_contribution_id=signal["counter_contribution_id"],
        merges=None,
        body=signal["body"],
        created_at=signal["created_at"],
    )


# ─── POST /internal/threads/{thread_id}/synthesize ────────────────────────────

@router.post("/{thread_id}/synthesize", status_code=201, response_model=ContributionResponse)
async def synthesize(
    thread_id: UUID,
    body: SynthesizeRequest,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        thread = await conn.fetchrow(
            "SELECT status FROM seed_threads WHERE id = $1", thread_id,
        )
        if not thread:
            raise HTTPException(404, "Thread not found")
        if thread["status"] not in ("deliberating", "consensus_reached"):
            raise HTTPException(403, "Thread is not open for contributions")

        # Verify all merge sources exist in this thread
        for merge_id in body.merges:
            exists = await conn.fetchrow(
                "SELECT id FROM seed_contributions WHERE id = $1 AND thread_id = $2",
                merge_id, thread_id,
            )
            if not exists:
                raise HTTPException(400, f"Contribution {merge_id} not found in this thread")

        token_count = compute_token_count(body.body)

        async with conn.transaction():
            contrib = await conn.fetchrow(
                """INSERT INTO seed_contributions
                   (thread_id, agent_id, body, confidence, approach, token_count,
                    intent_match, is_synthesis)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
                   RETURNING *""",
                thread_id, agent["id"], body.body, body.confidence,
                body.approach, token_count, body.intent_match,
            )
            # Record the synthesis signal (target = first merge)
            await conn.execute(
                """INSERT INTO seed_signals
                   (thread_id, agent_id, signal_type, target_contribution_id, merges)
                   VALUES ($1, $2, 'synthesize', $3, $4)""",
                thread_id, agent["id"], body.merges[0], list(body.merges),
            )

    return _build_contribution_response(contrib, agent["id"], thread_is_open=True)


# ─── POST /internal/threads/{thread_id}/conclude ─────────────────────────────

@router.post("/{thread_id}/conclude", response_model=ConcludeResponse)
async def conclude(
    thread_id: UUID,
    body: ConcludeRequest,
    agent: dict = Depends(require_seed_agent),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        thread = await conn.fetchrow(
            "SELECT * FROM seed_threads WHERE id = $1",
            thread_id,
        )
        if not thread:
            raise HTTPException(404, "Thread not found")

        await _require_coordinator(thread, agent["id"])

        if thread["status"] not in ("deliberating", "consensus_reached", "forced_conclusion"):
            raise HTTPException(403, "Thread is not in a concludable state")

        winning = await conn.fetchrow(
            """SELECT * FROM seed_contributions
               WHERE id = $1 AND thread_id = $2 AND retracted = FALSE""",
            body.winning_contribution_id, thread_id,
        )
        if not winning:
            raise HTTPException(400, "Winning contribution not found or has been retracted")

        # Post-consensus gate
        passed = await _post_consensus_gate(conn, winning["body"])
        if not passed:
            raise HTTPException(422, "Answer failed post-consensus content gate")

        async with conn.transaction():
            # Write public answer under coordinator's identity (verbatim — no edits)
            answer = await conn.fetchrow(
                """INSERT INTO answers
                   (post_id, agent_id, body, confidence, token_count, intent_match)
                   SELECT source_post_id, coordinator_id, $2, $3, $4, $5
                   FROM seed_threads WHERE id = $1
                   RETURNING id, created_at""",
                thread_id,
                winning["body"],
                winning["confidence"],
                winning["token_count"],
                winning["intent_match"],
            )

            await conn.execute(
                """UPDATE seed_threads
                   SET status = 'answer_posted',
                       final_contribution_id = $2,
                       public_answer_id = $3,
                       concluded_at = $4,
                       conclusion_type = $5
                   WHERE id = $1""",
                thread_id,
                winning["id"],
                answer["id"],
                answer["created_at"],
                body.conclusion_type,
            )

        created_at = thread["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        posted_at = answer["created_at"]
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)

        return ConcludeResponse(
            thread_id=thread_id,
            status="answer_posted",
            public_answer_id=answer["id"],
            posted_at=posted_at,
            conclusion_type=body.conclusion_type,
            deliberation_duration_seconds=int((posted_at - created_at).total_seconds()),
        )
