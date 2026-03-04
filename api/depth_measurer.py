"""
Convert detected cut pixel depths to mm depths and then to integer bitting codes.

Combines the outputs of cut_detector.py with blank specifications to produce
the bitting array that gets sent to the CNC machine.
"""

from dataclasses import dataclass

import numpy as np

from api.bitting_converter import depth_to_bitting
from api.cut_detector import DetectedCut
from api.homography import MM_PER_PX


@dataclass
class MeasuredCut:
    position_number: int       # 1-indexed (cut 1 = nearest shoulder)
    position_px: int           # X position in blade crop
    depth_px: float            # Raw pixel depth from baseline
    depth_mm: float            # Converted to mm
    bitting_code: int          # Final integer bitting code
    boundary_distance: float   # mm to nearest bitting boundary (confidence proxy)


def measure_cuts(
    detected_cuts: list[DetectedCut],
    blank_spec: dict,
    px_per_mm: float | None = None,
) -> list[MeasuredCut]:
    """
    Convert detected cut positions to bitting codes.

    Args:
        detected_cuts: Output from cut_detector.detect_cuts().
        blank_spec:    Blank specification dict from blank_specs.get_blank_spec().
        px_per_mm:     Actual scale from scale_calibrator (uses nominal if None).

    Returns:
        List of MeasuredCut in order from shoulder (cut 1) to tip.
        Length may be less than blank_spec["cut_count"] if detection missed cuts.
    """
    actual_px_per_mm = px_per_mm if px_per_mm else (1.0 / MM_PER_PX)

    measured = []
    for i, cut in enumerate(detected_cuts):
        depth_mm = cut.valley_depth_px / actual_px_per_mm
        bitting_code, boundary_distance = depth_to_bitting(depth_mm, blank_spec)

        measured.append(MeasuredCut(
            position_number=i + 1,
            position_px=cut.position_px,
            depth_px=cut.valley_depth_px,
            depth_mm=round(depth_mm, 4),
            bitting_code=bitting_code,
            boundary_distance=round(boundary_distance, 4),
        ))

    return measured


def pad_to_expected_count(
    measured_cuts: list[MeasuredCut],
    expected_count: int,
    blank_spec: dict,
) -> list[MeasuredCut]:
    """
    If fewer cuts were detected than expected, pad with mid-range placeholders.

    This ensures the bitting array always has the correct length, with
    low-confidence flags for any padded positions.
    """
    if len(measured_cuts) >= expected_count:
        return measured_cuts[:expected_count]

    mid_code = (blank_spec["bitting_min"] + blank_spec["bitting_max"]) // 2
    from api.bitting_converter import bitting_to_depth
    mid_depth = bitting_to_depth(mid_code, blank_spec)

    padded = list(measured_cuts)
    for i in range(len(measured_cuts), expected_count):
        padded.append(MeasuredCut(
            position_number=i + 1,
            position_px=0,
            depth_px=0.0,
            depth_mm=mid_depth,
            bitting_code=mid_code,
            boundary_distance=0.0,  # 0.0 = maximally ambiguous (boundary)
        ))

    return padded
