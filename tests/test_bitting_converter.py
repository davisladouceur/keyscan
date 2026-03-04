"""
Unit tests for bitting_converter.py

Tests the depth ↔ bitting math for all 4 supported blank families.
No database or network required.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from api.bitting_converter import (
    depth_to_bitting,
    bitting_to_depth,
    validate_bitting_array,
    depth_table,
)

# ── Blank specs (inline — no DB needed for unit tests) ─────────────────── #

KW1 = {
    "blank_code": "KW1", "cut_count": 5,
    "depth_min": 1.270, "depth_max": 3.048,
    "depth_increment": 0.3556,
    "bitting_min": 1, "bitting_max": 7,
}
SC1 = {
    "blank_code": "SC1", "cut_count": 6,
    "depth_min": 0.000, "depth_max": 2.108,
    "depth_increment": 0.2345,
    "bitting_min": 0, "bitting_max": 9,
}
M1 = {
    "blank_code": "M1", "cut_count": 4,
    "depth_min": 1.100, "depth_max": 2.700,
    "depth_increment": 0.3200,
    "bitting_min": 1, "bitting_max": 6,
}
WR5 = {
    "blank_code": "WR5", "cut_count": 5,
    "depth_min": 1.270, "depth_max": 3.048,
    "depth_increment": 0.3556,
    "bitting_min": 1, "bitting_max": 7,
}

ALL_BLANKS = [KW1, SC1, M1, WR5]


# ── depth_to_bitting ──────────────────────────────────────────────────── #

class TestDepthToBitting:
    def test_kw1_exact_depths(self):
        """Each bitting code should round-trip perfectly."""
        for code in range(KW1["bitting_min"], KW1["bitting_max"] + 1):
            depth = bitting_to_depth(code, KW1)
            result, _ = depth_to_bitting(depth, KW1)
            assert result == code, f"KW1 code {code}: expected {code}, got {result}"

    def test_sc1_exact_depths(self):
        for code in range(SC1["bitting_min"], SC1["bitting_max"] + 1):
            depth = bitting_to_depth(code, SC1)
            result, _ = depth_to_bitting(depth, SC1)
            assert result == code

    def test_m1_exact_depths(self):
        for code in range(M1["bitting_min"], M1["bitting_max"] + 1):
            depth = bitting_to_depth(code, M1)
            result, _ = depth_to_bitting(depth, M1)
            assert result == code

    def test_wr5_exact_depths(self):
        for code in range(WR5["bitting_min"], WR5["bitting_max"] + 1):
            depth = bitting_to_depth(code, WR5)
            result, _ = depth_to_bitting(depth, WR5)
            assert result == code

    def test_clamping_below_minimum(self):
        """Depths shallower than depth_min clamp to bitting_min."""
        code, _ = depth_to_bitting(-999.0, KW1)
        assert code == KW1["bitting_min"]

    def test_clamping_above_maximum(self):
        """Depths deeper than depth_max clamp to bitting_max."""
        code, _ = depth_to_bitting(999.0, KW1)
        assert code == KW1["bitting_max"]

    def test_boundary_distance_at_midpoint(self):
        """A depth exactly between two bitting codes has boundary_distance ~ 0.5."""
        depth = bitting_to_depth(3, KW1) + KW1["depth_increment"] / 2
        _, bd = depth_to_bitting(depth, KW1)
        assert abs(bd - 0.5) < 0.01, f"Expected ~0.5, got {bd}"

    def test_boundary_distance_at_exact_code(self):
        """A depth exactly on a bitting code boundary_distance should be 0."""
        depth = bitting_to_depth(4, KW1)
        _, bd = depth_to_bitting(depth, KW1)
        assert bd < 0.05, f"Expected ~0.0, got {bd}"


# ── validate_bitting_array ────────────────────────────────────────────── #

class TestValidateBitting:
    def test_valid_kw1(self):
        errors = validate_bitting_array([3, 5, 2, 6, 4], KW1)
        assert errors == []

    def test_valid_sc1_with_zeros(self):
        errors = validate_bitting_array([0, 3, 5, 2, 7, 9], SC1)
        assert errors == []

    def test_wrong_cut_count(self):
        errors = validate_bitting_array([3, 5, 2], KW1)
        assert any("5 cuts" in e for e in errors)

    def test_code_below_min(self):
        errors = validate_bitting_array([0, 5, 2, 6, 4], KW1)  # 0 invalid for KW1
        assert any("Cut 1" in e for e in errors)

    def test_code_above_max(self):
        errors = validate_bitting_array([3, 5, 2, 8, 4], KW1)  # 8 invalid for KW1
        assert any("Cut 4" in e for e in errors)


# ── depth_table ───────────────────────────────────────────────────────── #

class TestDepthTable:
    @pytest.mark.parametrize("blank", ALL_BLANKS)
    def test_table_length(self, blank):
        table = depth_table(blank)
        expected_len = blank["bitting_max"] - blank["bitting_min"] + 1
        assert len(table) == expected_len

    @pytest.mark.parametrize("blank", ALL_BLANKS)
    def test_table_monotonic(self, blank):
        table = depth_table(blank)
        depths = [row["depth_mm"] for row in table]
        assert all(depths[i] <= depths[i+1] for i in range(len(depths)-1)), \
            f"Depth table not monotonically increasing for {blank['blank_code']}"

    def test_kw1_depth_range(self):
        table = depth_table(KW1)
        assert abs(table[0]["depth_mm"] - KW1["depth_min"]) < 0.001
        # Last entry = depth_min + (bitting_max - bitting_min) * depth_increment
        expected_last = KW1["depth_min"] + (KW1["bitting_max"] - KW1["bitting_min"]) * KW1["depth_increment"]
        assert abs(table[-1]["depth_mm"] - expected_last) < 0.001
