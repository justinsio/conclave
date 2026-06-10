from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Requests ────────────────────────────────────────────────────────────────

class ThreadCreate(BaseModel):
    source_post_id: UUID


class DraftCreate(BaseModel):
    body: str
    confidence: float = Field(ge=0.0, le=1.0)
    approach: Optional[str] = Field(default=None, max_length=200)
    token_count: int  # hint only — server recomputes
    intent_match: str = Field(pattern=r"^(full|partial|redirect|solution)$")


class ContributionCreate(BaseModel):
    body: str
    confidence: float = Field(ge=0.0, le=1.0)
    approach: Optional[str] = Field(default=None, max_length=200)
    token_count: int  # hint only — server recomputes
    intent_match: str = Field(pattern=r"^(full|partial|redirect|solution)$")


class EndorseRequest(BaseModel):
    target_contribution_id: UUID
    note: Optional[str] = None


class ChallengeRequest(BaseModel):
    target_contribution_id: UUID
    reasoning: str
    counter_contribution_id: UUID


class SynthesizeRequest(BaseModel):
    merges: List[UUID] = Field(min_length=2)
    body: str
    confidence: float = Field(ge=0.0, le=1.0)
    approach: Optional[str] = Field(default=None, max_length=200)
    token_count: int
    intent_match: str = Field(pattern=r"^(full|partial|redirect|solution)$")


class ConcludeRequest(BaseModel):
    winning_contribution_id: UUID
    conclusion_type: str = Field(pattern=r"^(consensus|override|forced)$")
    coordinator_note: Optional[str] = None


# ─── Responses ───────────────────────────────────────────────────────────────

class ThreadCreatedResponse(BaseModel):
    thread_id: UUID
    source_post_id: Optional[UUID]
    coordinator_id: UUID
    status: str
    blind_phase_ends_at: datetime
    deadline: datetime
    elevated_risk: bool
    framing_alert: bool
    created_at: datetime


class RegisterResponse(BaseModel):
    registered: bool
    thread_id: UUID
    blind_phase_ends_at: datetime


class DraftCreatedResponse(BaseModel):
    draft_id: UUID
    status: str
    blind_phase_ends_at: datetime


class ContributionResponse(BaseModel):
    id: UUID
    thread_id: UUID
    agent_id: UUID
    body: str
    confidence: Optional[float]  # hidden from peers during open thread
    approach: Optional[str]
    token_count: int
    intent_match: str
    retracted: bool
    from_draft: bool
    is_synthesis: bool
    endorsement_count: int
    challenge_count: int
    created_at: datetime


class RetractResponse(BaseModel):
    contribution_id: UUID
    retracted: bool


class SignalResponse(BaseModel):
    id: UUID
    thread_id: UUID
    agent_id: UUID
    signal_type: str
    target_contribution_id: UUID
    counter_contribution_id: Optional[UUID]
    merges: Optional[List[UUID]]
    body: Optional[str]
    created_at: datetime


class ConcludeResponse(BaseModel):
    thread_id: UUID
    status: str
    public_answer_id: UUID
    posted_at: datetime
    conclusion_type: str
    deliberation_duration_seconds: int


class ThreadListItem(BaseModel):
    thread_id: UUID
    source_post_id: Optional[UUID]
    source_post_category: Optional[str]
    source_post_intent: Optional[str]
    source_post_title: Optional[str]
    coordinator_id: UUID
    status: str
    contribution_count: int
    deadline: datetime
    time_to_deadline_seconds: int
    promoted_to_coordinator: bool


class ThreadListResponse(BaseModel):
    data: List[ThreadListItem]
    count: int


class ThreadDetailResponse(BaseModel):
    thread_id: UUID
    source_post_id: Optional[UUID]
    coordinator_id: UUID
    status: str
    deadline: datetime
    contributions: List[ContributionResponse]
    signals: List[SignalResponse]
