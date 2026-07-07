"""Faithful re-implementation of the moderation *pipeline decision*, for eval scoring.

This mirrors the real request path in app/routers/v1/posts.py and answers.py so the
eval measures the whole pipeline (the GO gate), not raw Haiku:

  1. structural_precheck  → any reject code short-circuits to a HELD outcome
                            (marker_injection / url_not_permitted / injection_suspected)
  2. Haiku gate verdict   → PASS | BLOCK | ESCALATE
  3. C1 confidence floor  → (PROPOSED, not yet in prod) a PASS below the floor
                            becomes ESCALATE

Terminal outcomes collapse to two release states:
  - RELEASE = content goes live      (pipeline PASS)
  - HELD    = blocked or escalated   (structural reject, BLOCK, or ESCALATE)

A *false-PASS* is a HELD-expected item that the pipeline RELEASEs.

Running these functions imports app.services.moderation, so run from the repo root
with the conclave venv active (same as the test suite).
"""
from __future__ import annotations

from app.services.moderation import structural_precheck

RELEASE = "RELEASE"
HELD = "HELD"


def apply_confidence_floor(decision: str, confidence: float, floor: float) -> str:
    """C1: a PASS the model isn't confident enough about becomes an ESCALATE.

    floor=0.0 disables the floor (matches today's prod behaviour, where confidence
    is collected but never gates a PASS).
    """
    if decision == "PASS" and confidence < floor:
        return "ESCALATE"
    return decision


def to_release_state(decision: str) -> str:
    return RELEASE if decision == "PASS" else HELD


def pipeline_outcome(
    title: str,
    body: str,
    gate_decision: str | None,
    gate_confidence: float | None,
    floor: float,
) -> tuple[str, str]:
    """Return (release_state, trace).

    gate_decision/gate_confidence are the raw Haiku verdict for content that cleared
    the structural pre-check. Pass None for both when the item was rejected
    structurally (the model was never called - mirrors the pipeline short-circuit).
    """
    reject = structural_precheck(title or "", body or "")
    if reject is not None:
        return HELD, f"structural:{reject}"
    if gate_decision is None:
        # Defensive: no structural reject but no verdict either. Treat as fail-safe HELD.
        return HELD, "gate:missing_verdict"
    effective = apply_confidence_floor(gate_decision, float(gate_confidence or 0.0), floor)
    return to_release_state(effective), f"gate:{effective}"
