"""
Tests for api/blank_matcher.py

All DB calls are mocked so these run without a live database.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.blank_matcher import (
    HARD_LIMIT_FIRST_CUT_MM,
    HARD_LIMIT_SPACING_MM,
    CUT_COUNT_MISMATCH_PENALTY,
    _score_blank_list,
    match_blank_candidates,
    select_best_candidate,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

KW1 = {
    "blank_code": "KW1",
    "cut_count": 5,
    "first_cut_from_shoulder_mm": 3.683,
    "cut_spacing_mm": 3.810,
    "blade_length_mm": 25.1,
    "bitting_min": 1,
    "bitting_max": 7,
}

WR5 = {
    "blank_code": "WR5",
    "cut_count": 5,
    "first_cut_from_shoulder_mm": 3.810,
    "cut_spacing_mm": 3.810,
    "blade_length_mm": 25.3,
    "bitting_min": 1,
    "bitting_max": 7,
}

SC1 = {
    "blank_code": "SC1",
    "cut_count": 6,
    "first_cut_from_shoulder_mm": 3.861,
    "cut_spacing_mm": 3.861,
    "blade_length_mm": 28.3,
    "bitting_min": 0,
    "bitting_max": 9,
}

SC4 = {
    "blank_code": "SC4",
    "cut_count": 6,
    "first_cut_from_shoulder_mm": 3.861,
    "cut_spacing_mm": 3.861,
    "blade_length_mm": 28.3,
    "bitting_min": 0,
    "bitting_max": 9,
}

SC9 = {
    "blank_code": "SC9",
    "cut_count": 6,
    "first_cut_from_shoulder_mm": 3.861,
    "cut_spacing_mm": 3.861,
    "blade_length_mm": 28.3,
    "bitting_min": 0,
    "bitting_max": 9,
}

M1 = {
    "blank_code": "M1",
    "cut_count": 4,
    "first_cut_from_shoulder_mm": 3.500,
    "cut_spacing_mm": 3.750,
    "blade_length_mm": 20.3,
    "bitting_min": 1,
    "bitting_max": 6,
}


# ── _score_blank_list ─────────────────────────────────────────────────────────

class TestScoreBlankList:

    def test_perfect_match_scores_zero(self):
        results = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
            blade_length_mm=None,
        )
        assert len(results) == 1
        assert results[0]["match_score"] == pytest.approx(0.0, abs=1e-6)

    def test_score_increases_with_error(self):
        results = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810 + 0.3,  # 0.3mm spacing error
            approx_first_cut_mm=3.683,
            blade_length_mm=None,
        )
        assert results[0]["match_score"] > 0.0

    def test_first_cut_weighted_more_than_spacing(self):
        # Both at same absolute error; first_cut has weight 2.0 vs spacing weight 1.5
        r_first_cut = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683 + 0.2,
            blade_length_mm=None,
        )
        r_spacing = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810 + 0.2,
            approx_first_cut_mm=3.683,
            blade_length_mm=None,
        )
        assert r_first_cut[0]["match_score"] > r_spacing[0]["match_score"]

    def test_hard_limit_first_cut_excludes(self):
        results = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683 + HARD_LIMIT_FIRST_CUT_MM + 0.1,
            blade_length_mm=None,
        )
        assert results == []

    def test_hard_limit_spacing_excludes(self):
        results = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810 + HARD_LIMIT_SPACING_MM + 0.1,
            approx_first_cut_mm=3.683,
            blade_length_mm=None,
        )
        assert results == []

    def test_extra_penalty_applied(self):
        base = _score_blank_list(
            [KW1], approx_spacing_mm=3.810, approx_first_cut_mm=3.683,
            blade_length_mm=None, extra_penalty=0.0,
        )
        penalised = _score_blank_list(
            [KW1], approx_spacing_mm=3.810, approx_first_cut_mm=3.683,
            blade_length_mm=None, extra_penalty=CUT_COUNT_MISMATCH_PENALTY,
        )
        assert penalised[0]["match_score"] == pytest.approx(
            base[0]["match_score"] + CUT_COUNT_MISMATCH_PENALTY, abs=1e-6
        )

    def test_blank_with_zero_geometry_skipped(self):
        bad = {**KW1, "first_cut_from_shoulder_mm": 0, "cut_spacing_mm": 0}
        results = _score_blank_list(
            [bad], approx_spacing_mm=3.810, approx_first_cut_mm=3.683,
            blade_length_mm=None,
        )
        assert results == []

    def test_blade_length_included_in_score(self):
        # KW1 blade_length=25.1; measured at 27.0 → error of 1.9mm × weight 0.5 = 0.95
        results = _score_blank_list(
            [KW1],
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
            blade_length_mm=27.0,
        )
        assert results[0]["match_score"] > 0.0


# ── match_blank_candidates ────────────────────────────────────────────────────

class TestMatchBlankCandidates:

    @pytest.fixture
    def mock_db_5cut(self):
        """Mock DB returning KW1 and WR5 for cut_count=5."""
        with patch("api.blank_matcher.get_blanks_by_cut_count", new_callable=AsyncMock) as m:
            def side_effect(n):
                if n == 5:
                    return [KW1, WR5]
                if n == 4:
                    return [M1]
                if n == 6:
                    return [SC1, SC4, SC9]
                return []
            m.side_effect = side_effect
            yield m

    @pytest.fixture
    def mock_db_6cut(self):
        """Mock DB returning SC1/SC4/SC9 for cut_count=6."""
        with patch("api.blank_matcher.get_blanks_by_cut_count", new_callable=AsyncMock) as m:
            def side_effect(n):
                if n == 6:
                    return [SC1, SC4, SC9]
                if n == 5:
                    return [KW1, WR5]
                if n == 7:
                    return []
                return []
            m.side_effect = side_effect
            yield m

    async def test_returns_top_n_candidates(self, mock_db_5cut):
        results = await match_blank_candidates(
            cut_count=5,
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
            max_results=2,
        )
        assert len(results) <= 2

    async def test_kw1_beats_wr5_for_kw1_geometry(self, mock_db_5cut):
        """KW1 first_cut (3.683) is closer than WR5 (3.810) when measured=3.683."""
        results = await match_blank_candidates(
            cut_count=5,
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
        )
        assert results[0]["blank_code"] == "KW1"

    async def test_wr5_beats_kw1_for_wr5_geometry(self, mock_db_5cut):
        """WR5 first_cut (3.810) wins when measured=3.810."""
        results = await match_blank_candidates(
            cut_count=5,
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.810,
        )
        assert results[0]["blank_code"] == "WR5"

    async def test_schlage_candidates_all_tied(self, mock_db_6cut):
        """SC1/SC4/SC9 have identical geometry — all score 0.0."""
        results = await match_blank_candidates(
            cut_count=6,
            approx_spacing_mm=3.861,
            approx_first_cut_mm=3.861,
        )
        assert len(results) == 3
        scores = [r["match_score"] for r in results]
        assert scores[0] == pytest.approx(scores[1], abs=1e-6)
        assert scores[1] == pytest.approx(scores[2], abs=1e-6)

    async def test_fallback_adds_penalty(self, mock_db_5cut):
        """
        If peak detection under-counts (reports 4 instead of 5), the 5-cut
        blanks should appear in the result with a penalty on their score.
        """
        results = await match_blank_candidates(
            cut_count=4,                   # detector missed a cut
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
        )
        # KW1 should still appear (via +1 fallback)
        codes = [r["blank_code"] for r in results]
        assert "KW1" in codes

        # KW1's score should be exactly CUT_COUNT_MISMATCH_PENALTY above its exact score
        kw1_score = next(r["match_score"] for r in results if r["blank_code"] == "KW1")
        assert kw1_score == pytest.approx(CUT_COUNT_MISMATCH_PENALTY, abs=0.01)

    async def test_deduplicates_blank_codes(self, mock_db_5cut):
        """Each blank_code appears at most once in results."""
        results = await match_blank_candidates(
            cut_count=5,
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
        )
        codes = [r["blank_code"] for r in results]
        assert len(codes) == len(set(codes))

    async def test_exact_match_ranks_above_fallback(self, mock_db_5cut):
        """Exact cut_count=5 blanks beat cut_count=4 fallback M1."""
        results = await match_blank_candidates(
            cut_count=5,
            approx_spacing_mm=3.810,
            approx_first_cut_mm=3.683,
        )
        # KW1 should come before M1 (which appears via ±1 fallback)
        codes = [r["blank_code"] for r in results]
        if "M1" in codes:
            assert codes.index("KW1") < codes.index("M1")


# ── select_best_candidate ─────────────────────────────────────────────────────

class TestSelectBestCandidate:

    def test_returns_top_candidate(self):
        candidates = [
            {**KW1, "match_score": 0.1, "stamp_confirmed": False},
            {**WR5, "match_score": 0.9, "stamp_confirmed": False},
        ]
        best = select_best_candidate(candidates)
        assert best["blank_code"] == "KW1"

    def test_stamp_override_wins(self):
        candidates = [
            {**KW1, "match_score": 0.0, "stamp_confirmed": False},
            {**SC1, "match_score": 5.0, "stamp_confirmed": False},
        ]
        best = select_best_candidate(candidates, stamp_override="SC1")
        assert best["blank_code"] == "SC1"
        assert best["stamp_confirmed"] is True
        assert best["match_score"] == 0.0

    def test_stamp_override_not_in_list_falls_through(self):
        candidates = [
            {**KW1, "match_score": 0.5, "stamp_confirmed": False},
        ]
        best = select_best_candidate(candidates, stamp_override="SC4")
        # SC4 not in list → falls back to normal top candidate
        assert best["blank_code"] == "KW1"

    def test_empty_list_returns_none(self):
        assert select_best_candidate([]) is None

    def test_sets_stamp_confirmed_false_on_normal_result(self):
        candidates = [{**KW1, "match_score": 0.0}]
        best = select_best_candidate(candidates)
        assert best["stamp_confirmed"] is False
