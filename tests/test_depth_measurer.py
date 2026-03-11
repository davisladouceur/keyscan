"""
Tests for api/depth_measurer.py

Verifies that pixel depths are converted correctly to mm depths and then
to integer bitting codes, and that missing cuts are padded correctly.
"""

import pytest

from api.cut_detector import DetectedCut
from api.depth_measurer import measure_cuts, pad_to_expected_count, MeasuredCut

PX_PER_MM = 20.0

KW1_SPEC = {
    "blank_code": "KW1",
    "cut_count": 5,
    "first_cut_from_shoulder_mm": 3.683,
    "cut_spacing_mm": 3.810,
    "depth_min": 1.270,
    "depth_max": 3.048,
    "depth_increment": 0.3556,
    "bitting_min": 1,
    "bitting_max": 7,
}

SC1_SPEC = {
    "blank_code": "SC1",
    "cut_count": 6,
    "first_cut_from_shoulder_mm": 3.861,
    "cut_spacing_mm": 3.861,
    "depth_min": 0.000,
    "depth_max": 2.108,
    "depth_increment": 0.2345,
    "bitting_min": 0,
    "bitting_max": 9,
}


def _make_detected_cut(position_px: int, depth_px: float) -> DetectedCut:
    return DetectedCut(
        position_px=position_px,
        valley_depth_px=depth_px,
        prominence=10.0,
        width_px=8.0,
    )


def _depth_for_code(code: int, spec: dict) -> float:
    """Compute the exact pixel depth for a given bitting code."""
    code_zero = spec["bitting_min"]
    depth_mm = spec["depth_min"] + (code - code_zero) * spec["depth_increment"]
    return depth_mm * PX_PER_MM


# ── measure_cuts ──────────────────────────────────────────────────────────────

class TestMeasureCuts:

    def test_returns_measured_cut_instances(self):
        detected = [_make_detected_cut(100, _depth_for_code(3, KW1_SPEC))]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert all(isinstance(r, MeasuredCut) for r in results)

    def test_length_matches_input(self):
        detected = [
            _make_detected_cut(100 + i * 76, _depth_for_code(3, KW1_SPEC))
            for i in range(5)
        ]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert len(results) == 5

    def test_position_numbers_are_1_indexed(self):
        detected = [_make_detected_cut(100, 10.0), _make_detected_cut(200, 20.0)]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].position_number == 1
        assert results[1].position_number == 2

    def test_kw1_code3_roundtrip(self):
        """A depth that corresponds exactly to KW1 code 3 should round-trip."""
        target_code = 3
        depth_px = _depth_for_code(target_code, KW1_SPEC)
        detected = [_make_detected_cut(100, depth_px)]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].bitting_code == target_code

    def test_kw1_code7_roundtrip(self):
        target_code = 7
        depth_px = _depth_for_code(target_code, KW1_SPEC)
        detected = [_make_detected_cut(100, depth_px)]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].bitting_code == target_code

    def test_sc1_code0_roundtrip(self):
        target_code = 0
        depth_px = _depth_for_code(target_code, SC1_SPEC)
        detected = [_make_detected_cut(100, depth_px)]
        results = measure_cuts(detected, SC1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].bitting_code == 0

    def test_sc1_code9_roundtrip(self):
        target_code = 9
        depth_px = _depth_for_code(target_code, SC1_SPEC)
        detected = [_make_detected_cut(100, depth_px)]
        results = measure_cuts(detected, SC1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].bitting_code == 9

    def test_depth_mm_stored_correctly(self):
        depth_mm = 2.0
        detected = [_make_detected_cut(100, depth_mm * PX_PER_MM)]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert abs(results[0].depth_mm - depth_mm) < 0.01

    def test_empty_input_returns_empty(self):
        results = measure_cuts([], KW1_SPEC, px_per_mm=PX_PER_MM)
        assert results == []

    def test_bitting_codes_clamped_to_valid_range(self):
        """Extremely deep cut → clamped to bitting_max (not out-of-range)."""
        detected = [_make_detected_cut(100, 999.0)]  # huge depth
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].bitting_code <= KW1_SPEC["bitting_max"]

    def test_zero_depth_maps_to_min_bitting(self):
        """Zero depth = no cut = minimum bitting code."""
        detected = [_make_detected_cut(100, 0.0)]
        results = measure_cuts(detected, KW1_SPEC, px_per_mm=PX_PER_MM)
        assert results[0].bitting_code == KW1_SPEC["bitting_min"]


# ── pad_to_expected_count ─────────────────────────────────────────────────────

class TestPadToExpectedCount:

    def _make_measured_cuts(self, n: int, spec: dict) -> list[MeasuredCut]:
        return [
            MeasuredCut(
                position_number=i + 1,
                position_px=100 + i * 76,
                depth_px=20.0,
                depth_mm=1.0,
                bitting_code=3,
                boundary_distance=0.3,
            )
            for i in range(n)
        ]

    def test_no_padding_needed(self):
        measured = self._make_measured_cuts(5, KW1_SPEC)
        result = pad_to_expected_count(measured, 5, KW1_SPEC)
        assert len(result) == 5

    def test_pads_to_expected_count(self):
        measured = self._make_measured_cuts(3, KW1_SPEC)
        result = pad_to_expected_count(measured, 5, KW1_SPEC)
        assert len(result) == 5

    def test_padded_cuts_use_midrange_code(self):
        measured = self._make_measured_cuts(3, KW1_SPEC)
        result = pad_to_expected_count(measured, 5, KW1_SPEC)
        mid_code = (KW1_SPEC["bitting_min"] + KW1_SPEC["bitting_max"]) // 2
        padded = result[3:]
        assert all(c.bitting_code == mid_code for c in padded)

    def test_padded_cuts_have_zero_boundary_distance(self):
        """Boundary distance = 0 flags padded cuts as maximally ambiguous."""
        measured = self._make_measured_cuts(3, KW1_SPEC)
        result = pad_to_expected_count(measured, 5, KW1_SPEC)
        assert all(c.boundary_distance == 0.0 for c in result[3:])

    def test_truncates_excess_cuts(self):
        measured = self._make_measured_cuts(7, KW1_SPEC)
        result = pad_to_expected_count(measured, 5, KW1_SPEC)
        assert len(result) == 5

    def test_position_numbers_preserved(self):
        measured = self._make_measured_cuts(3, KW1_SPEC)
        result = pad_to_expected_count(measured, 5, KW1_SPEC)
        assert result[0].position_number == 1
        # Padded entries get auto-assigned numbers
        assert result[4].position_number == 5
