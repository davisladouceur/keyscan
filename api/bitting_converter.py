"""
Convert between physical depth measurements (mm) and integer bitting codes.

Each key blank family has its own depth table derived from:
  depth(code) = depth_min + (code - bitting_min) * depth_increment

Inverse:
  code = round((depth - depth_min) / depth_increment) + bitting_min

Both functions clamp the result to the blank's legal bitting range.
"""

from typing import Optional


def depth_to_bitting(depth_mm: float, blank_spec: dict) -> tuple[int, float]:
    """
    Convert a measured cut depth (mm) to an integer bitting code.

    Args:
        depth_mm:   Measured depth from the blade shoulder baseline.
        blank_spec: Dict from blank_specs.get_blank_spec(), containing
                    depth_min, depth_increment, bitting_min, bitting_max.

    Returns:
        Tuple of (bitting_code, boundary_distance) where boundary_distance
        is the mm gap to the nearest bitting boundary — used for confidence
        scoring (small gap → ambiguous measurement → lower confidence).
    """
    depth_min = blank_spec["depth_min"]
    depth_increment = blank_spec["depth_increment"]
    bitting_min = blank_spec["bitting_min"]
    bitting_max = blank_spec["bitting_max"]

    # Raw (possibly fractional) bitting code
    raw_code = (depth_mm - depth_min) / depth_increment + bitting_min

    # Round to nearest integer and clamp to legal range
    code = int(round(raw_code))
    code = max(bitting_min, min(bitting_max, code))

    # Boundary distance: how far the raw measurement is from the nearest
    # integer boundary (0.0 = right on a boundary, 0.5 = perfectly centred)
    fractional_part = abs(raw_code - round(raw_code))
    boundary_distance = min(fractional_part, 1.0 - fractional_part)

    return code, boundary_distance


def bitting_to_depth(code: int, blank_spec: dict) -> float:
    """
    Convert a bitting code to the expected cut depth in mm.

    Used for validation — compare against OpenCV-measured depth.
    """
    return (
        blank_spec["depth_min"]
        + (code - blank_spec["bitting_min"]) * blank_spec["depth_increment"]
    )


def validate_bitting_array(bitting: list[int], blank_spec: dict) -> list[str]:
    """
    Check that all bitting values are within the blank's legal range.

    Returns a list of error strings (empty list = all valid).
    """
    errors = []
    expected_cuts = blank_spec["cut_count"]

    if len(bitting) != expected_cuts:
        errors.append(
            f"Expected {expected_cuts} cuts for {blank_spec['blank_code']}, "
            f"got {len(bitting)}"
        )

    for i, code in enumerate(bitting):
        if not (blank_spec["bitting_min"] <= code <= blank_spec["bitting_max"]):
            errors.append(
                f"Cut {i+1}: code {code} is outside legal range "
                f"[{blank_spec['bitting_min']}–{blank_spec['bitting_max']}] "
                f"for {blank_spec['blank_code']}"
            )

    return errors


def depth_table(blank_spec: dict) -> list[dict]:
    """
    Return the full depth lookup table for a blank as a list of dicts.

    Useful for debugging and documentation.
    """
    rows = []
    for code in range(blank_spec["bitting_min"], blank_spec["bitting_max"] + 1):
        depth = bitting_to_depth(code, blank_spec)
        rows.append({"bitting_code": code, "depth_mm": round(depth, 4)})
    return rows
