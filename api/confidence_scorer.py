"""
Per-cut and overall confidence scoring for the OpenCV measurement pipeline.

Combines three signals:
  1. Cut detection sharpness (scipy prominence)
  2. Depth boundary proximity (how close the depth is to the midpoint between codes)
  3. Multi-photo consistency (if multiple photos are provided)
"""

from dataclasses import dataclass

import numpy as np

from api.cut_detector import DetectedCut
from api.depth_measurer import MeasuredCut


@dataclass
class CutScore:
    position_number: int
    bitting_code: int
    depth_mm: float
    confidence: float          # 0–1
    sharpness_score: float     # 0–1 based on peak prominence
    boundary_score: float      # 0–1 based on distance from bitting boundary
    consistent: bool           # True if same code across all photos


def score_cuts(
    measured_cuts: list[MeasuredCut],
    detected_cuts: list[DetectedCut],
    multi_photo_codes: list[list[int]] | None = None,
) -> list[CutScore]:
    """
    Compute a per-cut confidence score.

    Args:
        measured_cuts:       Output from depth_measurer.measure_cuts().
        detected_cuts:       Raw detection output (for prominence values).
        multi_photo_codes:   List of bitting arrays from multiple photos,
                             one array per photo. Used for consistency check.

    Returns:
        List of CutScore in the same order as measured_cuts.
    """
    # Build a prominence lookup by position
    prominence_by_pos = {}
    max_prominence = max((c.prominence for c in detected_cuts), default=1.0)
    for cut in detected_cuts:
        prominence_by_pos[cut.position_px] = cut.prominence

    scores = []
    for i, mc in enumerate(measured_cuts):
        # 1. Sharpness score: normalised peak prominence
        raw_prominence = prominence_by_pos.get(mc.position_px, 0.0)
        sharpness = min(1.0, raw_prominence / max(max_prominence, 1.0))

        # 2. Boundary score: distance from nearest bitting boundary
        # boundary_distance of 0.5 = perfectly centred = highest confidence
        # boundary_distance of 0.0 = right on boundary = lowest confidence
        boundary_score = min(1.0, mc.boundary_distance * 2.0)

        # 3. Multi-photo consistency
        consistent = True
        if multi_photo_codes and len(multi_photo_codes) > 1:
            codes_for_cut = [
                photo[i] for photo in multi_photo_codes
                if i < len(photo)
            ]
            consistent = len(set(codes_for_cut)) == 1

        consistency_bonus = 1.0 if consistent else 0.7

        # Combined confidence (weighted average)
        confidence = (
            0.40 * sharpness
            + 0.40 * boundary_score
            + 0.20 * (1.0 if consistent else 0.0)
        ) * consistency_bonus

        confidence = round(min(1.0, max(0.0, confidence)), 3)

        scores.append(CutScore(
            position_number=mc.position_number,
            bitting_code=mc.bitting_code,
            depth_mm=mc.depth_mm,
            confidence=confidence,
            sharpness_score=round(sharpness, 3),
            boundary_score=round(boundary_score, 3),
            consistent=consistent,
        ))

    return scores


def overall_confidence(cut_scores: list[CutScore]) -> float:
    """
    Aggregate per-cut confidences into a single order confidence score.

    The lowest-confidence cut dominates — a single bad cut can compromise
    the whole key, so we use a weighted minimum:
    0.6 * min_cut_confidence + 0.4 * mean_cut_confidence
    """
    if not cut_scores:
        return 0.0

    confidences = [c.confidence for c in cut_scores]
    return round(
        0.6 * min(confidences) + 0.4 * float(np.mean(confidences)),
        3,
    )


def needs_human_review(
    overall: float,
    cut_scores: list[CutScore],
    confidence_threshold: float = 0.85,
) -> tuple[bool, list[str]]:
    """
    Determine if an order should go to the human review queue.

    Returns (needs_review, list_of_reason_strings).
    """
    flags = []

    if overall < confidence_threshold:
        flags.append(
            f"Overall confidence {overall:.2f} is below threshold {confidence_threshold}"
        )

    for cs in cut_scores:
        if not cs.consistent:
            flags.append(f"Cut {cs.position_number}: inconsistent across photos")
        if cs.confidence < 0.5:
            flags.append(
                f"Cut {cs.position_number}: low confidence ({cs.confidence:.2f})"
            )
        if cs.boundary_score < 0.2:
            flags.append(
                f"Cut {cs.position_number}: depth near bitting boundary (ambiguous)"
            )

    return bool(flags), flags
