from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ─── Effective confidence ─────────────────────────────────────────────────────

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


# ─── Similarity helpers ───────────────────────────────────────────────────────

def _cosine_similarity(a: str, b: str) -> float:
    """BOW cosine — used as fallback when Ollama embeddings are unavailable."""
    words_a = Counter(a.lower().split())
    words_b = Counter(b.lower().split())
    common = set(words_a) & set(words_b)
    dot = sum(words_a[w] * words_b[w] for w in common)
    mag_a = math.sqrt(sum(v * v for v in words_a.values()))
    mag_b = math.sqrt(sum(v * v for v in words_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _vector_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def _get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """
    Fetch embeddings from Ollama nomic-embed-text in a single batch call.
    Returns None when ollama_base_url is unset or the call fails — callers
    fall back to BOW cosine automatically.
    """
    if not settings.ollama_base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.ollama_base_url}/api/embed",
                json={"model": settings.embedding_model, "input": texts},
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]
    except Exception as exc:
        logger.warning("divergence: embeddings call failed (%s) — falling back to BOW", exc)
        return None


# ─── Divergence gate ──────────────────────────────────────────────────────────

@dataclass
class DivergenceResult:
    action: str  # 'proceed' | 'divergence_hold'
    flagged_agent_id: UUID | None = None  # set when one seed is an outlier


async def check_divergence(
    drafts: list[dict],  # each: {agent_id, body, confidence}
    post_is_complex: bool,
) -> DivergenceResult:
    """
    Requires ≥ 3 drafts — caller is responsible for the threshold check.
    Uses nomic-embed-text semantic similarity when Ollama is available;
    falls back to BOW cosine otherwise.
    """
    if len(drafts) < 3:
        return DivergenceResult("proceed")

    # Gate 1: all seeds ≥ 0.88 confidence on a question tagged complex
    if post_is_complex and all(d["confidence"] >= 0.88 for d in drafts):
        return DivergenceResult("divergence_hold")

    # Gate 2: outlier detection — one seed's answer semantically distant from all others
    bodies = [d["body"] for d in drafts]
    embeddings = await _get_embeddings(bodies)

    for i, draft in enumerate(drafts):
        if embeddings is not None:
            sims = [
                _vector_cosine(embeddings[i], embeddings[j])
                for j in range(len(drafts)) if j != i
            ]
        else:
            sims = [
                _cosine_similarity(draft["body"], drafts[j]["body"])
                for j in range(len(drafts)) if j != i
            ]
        if all(s < 0.30 for s in sims):
            return DivergenceResult("proceed", flagged_agent_id=draft["agent_id"])

    return DivergenceResult("proceed")
