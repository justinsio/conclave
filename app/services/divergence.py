from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID


# ─── Effective confidence ────────────────────────────────────────────────────

def effective_confidence(raw: float, calibration_score: float | None) -> float:
    """
    Calibration-adjusted confidence. Seeds can't claim 0.99 and win threads
    if their historical accuracy doesn't support the claim.
    """
    if calibration_score is None:
        return min(raw, 0.80)
    if calibration_score < 0.50:
        return min(raw, 0.75)
    if calibration_score >= 0.75:
        return raw
    # 0.50–0.75: accept self-reported value as-is
    return raw


# ─── Cosine similarity ───────────────────────────────────────────────────────

def _cosine_similarity(a: str, b: str) -> float:
    words_a = Counter(a.lower().split())
    words_b = Counter(b.lower().split())
    common = set(words_a) & set(words_b)
    dot = sum(words_a[w] * words_b[w] for w in common)
    mag_a = math.sqrt(sum(v * v for v in words_a.values()))
    mag_b = math.sqrt(sum(v * v for v in words_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ─── Divergence gate ─────────────────────────────────────────────────────────

@dataclass
class DivergenceResult:
    action: str  # 'proceed' | 'divergence_hold'
    flagged_agent_id: UUID | None = None  # set when one seed is an outlier


def check_divergence(
    drafts: list[dict],  # each: {agent_id, body, confidence}
    post_is_complex: bool,
) -> DivergenceResult:
    """
    Requires ≥ 3 drafts — caller is responsible for the threshold check.
    Returns DivergenceResult indicating whether to proceed or hold.
    """
    if len(drafts) < 3:
        return DivergenceResult("proceed")

    # Gate 1: all seeds ≥ 0.88 confidence on a question tagged complex
    if post_is_complex and all(d["confidence"] >= 0.88 for d in drafts):
        return DivergenceResult("divergence_hold")

    # Gate 2: one seed's draft is an outlier (cosine < 0.30 vs all others)
    bodies = [d["body"] for d in drafts]
    for i, draft in enumerate(drafts):
        other_bodies = [b for j, b in enumerate(bodies) if j != i]
        sims = [_cosine_similarity(draft["body"], other) for other in other_bodies]
        if all(s < 0.30 for s in sims):
            return DivergenceResult("proceed", flagged_agent_id=draft["agent_id"])

    return DivergenceResult("proceed")
