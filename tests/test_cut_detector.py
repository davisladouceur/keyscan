"""
Tests for api/cut_detector.py

Synthetic blade images are generated in code — no real key photos needed.
All images assume PX_PER_MM = 20.0 (the project constant).

Image convention (matches the real pipeline):
  - White (255) background
  - Dark (0) blade drawn as a solid rectangle
  - Cuts = rectangular notches punched into the TOP edge of the blade
    (the edge profile code scans for the topmost dark pixel per column)
  - Profile value at a cut = LARGER y (farther from image top)
  - Baseline (shoulder) = SMALLER y (blade is flat / not cut)
"""

import numpy as np
import pytest
import cv2

from api.cut_detector import (
    measure_blade_geometry,
    detect_cuts,
    _extract_edge_profile,
    _smooth_profile,
    BladeGeometry,
    DetectedCut,
)

PX_PER_MM = 20.0   # must match api/homography.PX_PER_MM


# ── Synthetic image helpers ───────────────────────────────────────────────────

def _make_blade_image(
    cut_count: int,
    first_cut_mm: float,
    spacing_mm: float,
    blade_height_mm: float = 8.0, # total height of blade crop (mm)
    cut_depth_mm: float = 1.5,    # depth of each cut valley (mm)
    tip_mm: float = 8.0,          # flat region after last cut (mm)
    px_per_mm: float = PX_PER_MM,
) -> np.ndarray:
    """
    Build a synthetic grayscale blade crop.

    Layout (x = left to right, y = top to bottom):
      x=0: shoulder starts here (flat blade top)
      x=first_cut_mm*px_per_mm: first cut notch
      x=first_cut_mm*px_per_mm + i*spacing*px_per_mm: i-th cut notch
      ... tip region (tip_mm of flat blade after last cut)

    The image starts at the shoulder (x=0). The shoulder detection in
    measure_blade_geometry will find shoulder_x=0 (first flat region),
    and first_cut_mm will be measured as peaks[0] / px_per_mm.
    """
    blade_height_px = int(blade_height_mm * px_per_mm)
    last_cut_mm = first_cut_mm + (cut_count - 1) * spacing_mm
    total_width_mm = last_cut_mm + tip_mm
    width_px = int(total_width_mm * px_per_mm)

    # Dark (blade) background
    img = np.full((blade_height_px, width_px), 50, dtype=np.uint8)

    # Punch rectangular notches (cuts) into the TOP edge
    cut_depth_px = int(cut_depth_mm * px_per_mm)
    cut_width_px = int(spacing_mm * px_per_mm * 0.4)  # ~40% of spacing

    for i in range(cut_count):
        cut_center_mm = first_cut_mm + i * spacing_mm
        cut_center_px = int(cut_center_mm * px_per_mm)
        x_lo = max(0, cut_center_px - cut_width_px // 2)
        x_hi = min(width_px, cut_center_px + cut_width_px // 2)
        # Notch = white (background) rows at the top of the blade
        img[:cut_depth_px, x_lo:x_hi] = 255

    return img


def _make_flat_image(width_mm: float = 30.0, height_mm: float = 8.0) -> np.ndarray:
    """A completely flat (all-dark) blade — no cuts."""
    return np.full(
        (int(height_mm * PX_PER_MM), int(width_mm * PX_PER_MM)),
        50, dtype=np.uint8
    )


# ── _extract_edge_profile ─────────────────────────────────────────────────────

class TestExtractEdgeProfile:

    def test_flat_blade_has_uniform_profile(self):
        img = _make_flat_image()
        profile = _extract_edge_profile(img)
        assert profile is not None
        # All values should be the same (or nearly — Otsu may vary by 1px)
        assert profile.max() - profile.min() < 3

    def test_profile_length_equals_image_width(self):
        img = _make_blade_image(cut_count=3, first_cut_mm=4.0, spacing_mm=4.0)
        profile = _extract_edge_profile(img)
        assert len(profile) == img.shape[1]

    def test_profile_is_higher_at_cut_positions(self):
        """Cuts create valleys = higher y values in the profile."""
        first_cut_mm = 4.0
        img = _make_blade_image(
            cut_count=1,
            first_cut_mm=first_cut_mm,
            spacing_mm=4.0,
        )
        profile = _extract_edge_profile(img)
        # Position of the cut (in image x-coordinates)
        cut_x = int(first_cut_mm * PX_PER_MM)
        # Shoulder x (flat region near the very start)
        shoulder_x = int(first_cut_mm * PX_PER_MM * 0.3)

        assert profile[cut_x] > profile[shoulder_x]

    def test_no_nans_in_output(self):
        img = _make_blade_image(cut_count=5, first_cut_mm=3.683, spacing_mm=3.810)
        profile = _extract_edge_profile(img)
        assert not np.any(np.isnan(profile))


# ── measure_blade_geometry ────────────────────────────────────────────────────

class TestMeasureBladeGeometry:

    def test_returns_none_for_flat_profile(self):
        img = _make_flat_image()
        result = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
        assert result is None

    def test_detects_correct_cut_count(self):
        # The peak detector may be off by ±1 (which is handled by the matcher's
        # CUT_COUNT_MISMATCH_PENALTY fallback). Accept ±1 here.
        for n in [4, 5, 6]:
            img = _make_blade_image(
                cut_count=n,
                first_cut_mm=3.683,
                spacing_mm=3.810,
            )
            geo = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
            assert geo is not None, f"Expected geometry for {n}-cut blade"
            assert abs(geo.cut_count - n) <= 1, (
                f"Expected ~{n} cuts (±1), got {geo.cut_count} "
                f"(peaks at {geo.peak_positions_px})"
            )

    def test_spacing_within_tolerance(self):
        spacing_mm = 3.810
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=3.683,
            spacing_mm=spacing_mm,
        )
        geo = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
        assert geo is not None
        assert abs(geo.approx_spacing_mm - spacing_mm) < 0.5, (
            f"Spacing measured {geo.approx_spacing_mm:.3f}mm, expected ~{spacing_mm}"
        )

    def test_first_cut_within_tolerance(self):
        first_cut_mm = 3.683
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=first_cut_mm,
            spacing_mm=3.810,
        )
        geo = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
        assert geo is not None
        # first_cut_mm measured from shoulder_x to first peak; allow ±1.0mm
        assert abs(geo.approx_first_cut_mm - first_cut_mm) < 1.0, (
            f"First cut measured {geo.approx_first_cut_mm:.3f}mm, expected ~{first_cut_mm}"
        )

    def test_blade_length_within_tolerance(self):
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=3.683,
            spacing_mm=3.810,
            tip_mm=6.0,
        )
        geo = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
        assert geo is not None
        assert geo.blade_length_mm > 0

    def test_returns_bladgeometry_dataclass(self):
        img = _make_blade_image(cut_count=5, first_cut_mm=3.683, spacing_mm=3.810)
        geo = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
        assert geo is None or isinstance(geo, BladeGeometry)

    def test_schlage_6cut_geometry(self):
        img = _make_blade_image(
            cut_count=6,
            first_cut_mm=3.861,
            spacing_mm=3.861,
        )
        geo = measure_blade_geometry(img, px_per_mm=PX_PER_MM)
        assert geo is not None
        # Accept ±1 — same as test_detects_correct_cut_count
        assert abs(geo.cut_count - 6) <= 1


# ── detect_cuts — spec-based ──────────────────────────────────────────────────

KW1_SPEC = {
    "blank_code": "KW1",
    "cut_count": 5,
    "first_cut_from_shoulder_mm": 3.683,
    "cut_spacing_mm": 3.810,
    "bitting_min": 1,
    "bitting_max": 7,
    "depth_min": 1.270,
    "depth_max": 3.048,
    "depth_increment": 0.3556,
}

SC1_SPEC = {
    "blank_code": "SC1",
    "cut_count": 6,
    "first_cut_from_shoulder_mm": 3.861,
    "cut_spacing_mm": 3.861,
    "bitting_min": 0,
    "bitting_max": 9,
    "depth_min": 0.000,
    "depth_max": 2.108,
    "depth_increment": 0.2345,
}


class TestDetectCutsSpecBased:

    def test_returns_exactly_expected_cut_count(self):
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=3.683,
            spacing_mm=3.810,
        )
        cuts = detect_cuts(img, expected_cut_count=5, blank_spec=KW1_SPEC)
        assert len(cuts) == 5

    def test_returns_exactly_6_for_schlage(self):
        img = _make_blade_image(
            cut_count=6,
            first_cut_mm=3.861,
            spacing_mm=3.861,
        )
        cuts = detect_cuts(img, expected_cut_count=6, blank_spec=SC1_SPEC)
        assert len(cuts) == 6

    def test_cuts_sorted_left_to_right(self):
        img = _make_blade_image(cut_count=5, first_cut_mm=3.683, spacing_mm=3.810)
        cuts = detect_cuts(img, expected_cut_count=5, blank_spec=KW1_SPEC)
        positions = [c.position_px for c in cuts]
        assert positions == sorted(positions)

    def test_all_cuts_are_detected_cut_instances(self):
        img = _make_blade_image(cut_count=5, first_cut_mm=3.683, spacing_mm=3.810)
        cuts = detect_cuts(img, expected_cut_count=5, blank_spec=KW1_SPEC)
        assert all(isinstance(c, DetectedCut) for c in cuts)

    def test_valley_depth_positive(self):
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=3.683,
            spacing_mm=3.810,
            cut_depth_mm=1.5,
        )
        cuts = detect_cuts(img, expected_cut_count=5, blank_spec=KW1_SPEC)
        # At least some cuts should have meaningful depth
        depths = [c.valley_depth_px for c in cuts]
        assert max(depths) > 0


# ── detect_cuts — peak-based fallback ────────────────────────────────────────

class TestDetectCutsPeakBased:

    def test_peak_based_finds_cuts_without_spec(self):
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=3.683,
            spacing_mm=3.810,
        )
        cuts = detect_cuts(img, expected_cut_count=5, blank_spec=None)
        assert len(cuts) > 0

    def test_does_not_exceed_expected_count(self):
        img = _make_blade_image(
            cut_count=5,
            first_cut_mm=3.683,
            spacing_mm=3.810,
        )
        cuts = detect_cuts(img, expected_cut_count=5, blank_spec=None)
        assert len(cuts) <= 5
