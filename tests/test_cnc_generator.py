"""
Unit tests for CNC instruction generation.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.cnc_generator import generate_cnc_instruction


class TestCncGenerator:
    def test_standard_format(self):
        result = generate_cnc_instruction("KW1", [3, 5, 2, 6, 4])
        assert result["standard"] == "KW1,35264"

    def test_machine_serial_format(self):
        result = generate_cnc_instruction("KW1", [3, 5, 2, 6, 4])
        assert result["machine_serial"] == "CMD:CUT BLANK=KW1 BITTING=3-5-2-6-4"

    def test_display_format(self):
        result = generate_cnc_instruction("KW1", [3, 5, 2, 6, 4])
        assert result["display"] == "KW1 — 3 5 2 6 4"

    def test_verbose_keys(self):
        result = generate_cnc_instruction("SC1", [5, 3, 7, 2, 8, 1])
        v = result["verbose"]
        assert v["blank"] == "SC1"
        assert v["cuts"] == [5, 3, 7, 2, 8, 1]
        assert v["cut_count"] == 6

    def test_sc1_six_cuts(self):
        result = generate_cnc_instruction("SC1", [5, 3, 7, 2, 8, 1])
        assert result["standard"] == "SC1,537281"

    def test_single_digit_bitting(self):
        result = generate_cnc_instruction("M1", [1, 6, 3, 4])
        assert result["standard"] == "M1,1634"
