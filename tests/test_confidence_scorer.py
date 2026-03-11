"""
Tests for api/confidence_scorer.py

Verifies per-cut confidence scoring, overall confidence aggregation,
and human-review flagging logic.
"""

import pytest

from api.cut_detector import DetectedCut
from api.depth_measurer import MeasuredCut
from api.confidence_scorer import (
    score_cuts,
    overall_confidence,
    needs_human_review,
    CutScore,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_detected_cut(position_px: int, prominence: float) -> DetectedCut:
    return DetectedCut(
        position_px=position_px,
        valley_depth_px=30.0,
        prominence=prominence,
        width_px=8.0,
    )


def _make_measured_cut(
    position_number: int,
    position_px: int,
    bitting_code: int = 3,
    boundary_distance: float = 0.4,
) -> MeasuredCut:
    return MeasuredCut(
        position_number=position_number,
        position_px=position_px,
        depth_px=30.0,
        depth_mm=1.5,
        bitting_code=bitting_code,
        boundary_distance=boundary_distance,
    )


def _make_cut_score(
    position_number: int = 1,
    bitting_code: int = 3,
    confidence: float = 0.9,
    consistent: bool = True,
    boundary_score: float = 0.8,
) -> CutScore:
    return CutScore(
        position_number=position_number,
        bitting_code=bitting_code,
        depth_mm=1.5,
        confidence=confidence,
        sharpness_score=0.8,
        boundary_score=boundary_score,
        consistent=consistent,
    )


# ── score_cuts ────────────────────────────────────────────────────────────────

class TestScoreCuts:

    def test_returns_one_score_per_cut(self):
        measured = [_make_measured_cut(i + 1, 100 + i * 76) for i in range(5)]
        detected = [_make_detected_cut(100 + i * 76, prominence=15.0) for i in range(5)]
        scores = score_cuts(measured, detected)
        assert len(scores) == 5

    def test_returns_cut_score_instances(self):
        measured = [_make_measured_cut(1, 100)]
        detected = [_make_detected_cut(100, 15.0)]
        scores = score_cuts(measured, detected)
        assert all(isinstance(s, CutScore) for s in scores)

    def test_confidence_between_0_and_1(self):
        measured = [_make_measured_cut(1, 100)]
        detected = [_make_detected_cut(100, 15.0)]
        scores = score_cuts(measured, detected)
        assert 0.0 <= scores[0].confidence <= 1.0

    def test_high_prominence_raises_sharpness(self):
        """A very prominent peak yields higher sharpness than a weak one."""
        measured = [_make_measured_cut(1, 100)]
        strong = score_cuts(measured, [_make_detected_cut(100, prominence=100.0)])
        weak   = score_cuts(measured, [_make_detected_cut(100, prominence=1.0)])
        assert strong[0].sharpness_score >= weak[0].sharpness_score

    def test_boundary_score_highest_at_midpoint(self):
        """boundary_distance = 0.5 (perfectly centred) → boundary_score = 1.0."""
        measured_mid = [_make_measured_cut(1, 100, boundary_distance=0.5)]
        measured_edge = [_make_measured_cut(1, 100, boundary_distance=0.05)]
        detected = [_make_detected_cut(100, 15.0)]
        s_mid  = score_cuts(measured_mid, detected)
        s_edge = score_cuts(measured_edge, detected)
        assert s_mid[0].boundary_score > s_edge[0].boundary_score

    def test_inconsistent_cuts_reduce_confidence(self):
        """When the same cut differs across photos, confidence drops."""
        measured = [_make_measured_cut(1, 100)]
        detected = [_make_detected_cut(100, 15.0)]
        # Photo 0 says code 3; photo 1 says code 5 → inconsistent
        multi_consistent   = [[3], [3], [3]]
        multi_inconsistent = [[3], [5], [3]]
        s_good = score_cuts(measured, detected, multi_photo_codes=multi_consistent)
        s_bad  = score_cuts(measured, detected, multi_photo_codes=multi_inconsistent)
        assert s_good[0].confidence > s_bad[0].confidence

    def test_consistent_flag_true_when_all_agree(self):
        measured = [_make_measured_cut(1, 100)]
        detected = [_make_detected_cut(100, 15.0)]
        scores = score_cuts(measured, detected, multi_photo_codes=[[3], [3], [3]])
        assert scores[0].consistent is True

    def test_consistent_flag_false_when_they_disagree(self):
        measured = [_make_measured_cut(1, 100)]
        detected = [_make_detected_cut(100, 15.0)]
        scores = score_cuts(measured, detected, multi_photo_codes=[[3], [5]])
        assert scores[0].consistent is False

    def test_position_numbers_preserved(self):
        measured = [_make_measured_cut(i + 1, 100 + i * 76) for i in range(5)]
        detected = [_make_detected_cut(100 + i * 76, 15.0) for i in range(5)]
        scores = score_cuts(measured, detected)
        assert [s.position_number for s in scores] == [1, 2, 3, 4, 5]

    def test_no_multi_photo_assumed_consistent(self):
        """Without multi-photo data, cuts are treated as consistent."""
        measured = [_make_measured_cut(1, 100)]
        detected = [_make_detected_cut(100, 15.0)]
        scores = score_cuts(measured, detected, multi_photo_codes=None)
        assert scores[0].consistent is True


# ── overall_confidence ────────────────────────────────────────────────────────

class TestOverallConfidence:

    def test_empty_returns_zero(self):
        assert overall_confidence([]) == 0.0

    def test_single_cut_returns_its_confidence(self):
        scores = [_make_cut_score(confidence=0.8)]
        result = overall_confidence(scores)
        # 0.6 * min(0.8) + 0.4 * mean(0.8) = 0.8
        assert result == pytest.approx(0.8, abs=0.01)

    def test_dominated_by_minimum(self):
        """One very low cut should drag the overall score down significantly."""
        scores = [
            _make_cut_score(1, confidence=0.95),
            _make_cut_score(2, confidence=0.95),
            _make_cut_score(3, confidence=0.10),  # bad cut
        ]
        result = overall_confidence(scores)
        mean_conf = (0.95 + 0.95 + 0.10) / 3
        # result = 0.6 * 0.10 + 0.4 * mean ≈ 0.393
        expected = 0.6 * 0.10 + 0.4 * mean_conf
        assert result == pytest.approx(expected, abs=0.01)

    def test_all_perfect_returns_1(self):
        scores = [_make_cut_score(i + 1, confidence=1.0) for i in range(5)]
        assert overall_confidence(scores) == pytest.approx(1.0, abs=0.01)

    def test_all_zero_returns_0(self):
        scores = [_make_cut_score(i + 1, confidence=0.0) for i in range(5)]
        assert overall_confidence(scores) == pytest.approx(0.0, abs=0.01)

    def test_result_between_0_and_1(self):
        scores = [_make_cut_score(i + 1, confidence=0.5 + i * 0.05) for i in range(5)]
        result = overall_confidence(scores)
        assert 0.0 <= result <= 1.0


# ── needs_human_review ────────────────────────────────────────────────────────

class TestNeedsHumanReview:

    def test_high_confidence_no_review(self):
        scores = [_make_cut_score(i + 1, confidence=0.95) for i in range(5)]
        review, flags = needs_human_review(0.95, scores)
        assert review is False
        assert flags == []

    def test_low_overall_triggers_review(self):
        scores = [_make_cut_score(i + 1, confidence=0.5) for i in range(5)]
        review, flags = needs_human_review(0.70, scores)
        assert review is True
        assert any("Overall confidence" in f for f in flags)

    def test_inconsistent_cut_triggers_review(self):
        scores = [
            _make_cut_score(1, confidence=0.95, consistent=True),
            _make_cut_score(2, confidence=0.95, consistent=False),  # inconsistent
        ]
        review, flags = needs_human_review(0.90, scores)
        assert review is True
        assert any("inconsistent" in f for f in flags)

    def test_low_confidence_single_cut_triggers_review(self):
        scores = [
            _make_cut_score(1, confidence=0.95),
            _make_cut_score(2, confidence=0.30),  # very low
        ]
        review, flags = needs_human_review(0.90, scores)
        assert review is True
        assert any("low confidence" in f for f in flags)

    def test_boundary_ambiguity_triggers_review(self):
        scores = [
            _make_cut_score(1, confidence=0.95, boundary_score=0.9),
            _make_cut_score(2, confidence=0.90, boundary_score=0.05),  # near boundary
        ]
        review, flags = needs_human_review(0.90, scores)
        assert review is True
        assert any("boundary" in f for f in flags)

    def test_custom_threshold_respected(self):
        scores = [_make_cut_score(i + 1, confidence=0.75) for i in range(5)]
        # With default threshold 0.85: overall 0.75 < 0.85 → review required
        review_default, _ = needs_human_review(0.75, scores)
        assert review_default is True

        # With low threshold 0.60: overall 0.75 > 0.60 → no review
        review_low, _ = needs_human_review(0.75, scores, confidence_threshold=0.60)
        assert review_low is False

    def test_multiple_issues_reported(self):
        scores = [
            _make_cut_score(1, confidence=0.30, consistent=False, boundary_score=0.05),
        ]
        review, flags = needs_human_review(0.60, scores)
        assert review is True
        assert len(flags) >= 2  # multiple issues flagged
