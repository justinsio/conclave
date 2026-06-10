"""Unit tests for divergence gate and effective_confidence — no DB needed."""
import pytest

from app.services.divergence import DivergenceResult, check_divergence, effective_confidence


# ─── effective_confidence ─────────────────────────────────────────────────────

def test_effective_confidence_null_calibration():
    assert effective_confidence(0.95, None) == pytest.approx(0.80)
    assert effective_confidence(0.75, None) == pytest.approx(0.75)
    assert effective_confidence(0.50, None) == pytest.approx(0.50)


def test_effective_confidence_bad_calibration():
    assert effective_confidence(0.95, 0.30) == pytest.approx(0.75)
    assert effective_confidence(0.70, 0.30) == pytest.approx(0.70)


def test_effective_confidence_acceptable_calibration():
    assert effective_confidence(0.80, 0.65) == pytest.approx(0.80)
    assert effective_confidence(0.95, 0.65) == pytest.approx(0.95)


def test_effective_confidence_well_calibrated():
    assert effective_confidence(0.99, 0.90) == pytest.approx(0.99)
    assert effective_confidence(0.50, 0.80) == pytest.approx(0.50)


# ─── check_divergence ─────────────────────────────────────────────────────────

def _draft(body: str, confidence: float = 0.75, agent_id: str = "a1"):
    return {"agent_id": agent_id, "body": body, "confidence": confidence}


def test_divergence_fewer_than_3_proceeds():
    drafts = [_draft("answer A"), _draft("answer B")]
    result = check_divergence(drafts, post_is_complex=True)
    assert result.action == "proceed"


def test_divergence_normal_spread_proceeds():
    drafts = [
        _draft("Use a bloom filter pre-pass then dict.fromkeys()", 0.78, "a1"),
        _draft("Chunked hash set approach safer for unbounded integers", 0.82, "a2"),
        _draft("Sort and deduplicate preserving original order via index map", 0.74, "a3"),
    ]
    result = check_divergence(drafts, post_is_complex=False)
    assert result.action == "proceed"


def test_divergence_all_high_confidence_complex_triggers_hold():
    drafts = [
        _draft("bloom filter approach", 0.91, "a1"),
        _draft("bloom filter approach similar", 0.90, "a2"),
        _draft("bloom filter approach also", 0.92, "a3"),
    ]
    result = check_divergence(drafts, post_is_complex=True)
    assert result.action == "divergence_hold"


def test_divergence_all_high_confidence_not_complex_proceeds():
    drafts = [
        _draft("bloom filter approach", 0.91, "a1"),
        _draft("bloom filter approach similar", 0.90, "a2"),
        _draft("bloom filter approach also", 0.92, "a3"),
    ]
    result = check_divergence(drafts, post_is_complex=False)
    assert result.action == "proceed"


def test_divergence_outlier_flagged():
    # Two seeds agree; one seed has totally different answer
    drafts = [
        _draft("bloom filter pre-pass then dict.fromkeys for deduplication", 0.78, "a1"),
        _draft("bloom filter approach removes duplicates efficiently", 0.80, "a2"),
        # Completely different topic — will have cosine similarity < 0.30 vs others
        _draft("quantum entanglement solves the traveling salesman problem", 0.75, "a3"),
    ]
    result = check_divergence(drafts, post_is_complex=False)
    assert result.action == "proceed"
    assert str(result.flagged_agent_id) == "a3"


def test_divergence_no_outlier_similar_drafts():
    # All three drafts share enough exact tokens that BOW cosine stays above 0.30
    drafts = [
        _draft("dict.fromkeys deduplicates the list preserving insertion order", 0.85, "a1"),
        _draft("dict.fromkeys deduplicates the list preserving insertion order efficiently", 0.83, "a2"),
        _draft("dict.fromkeys deduplicates the list and preserves insertion order", 0.81, "a3"),
    ]
    result = check_divergence(drafts, post_is_complex=False)
    assert result.action == "proceed"
    assert result.flagged_agent_id is None
