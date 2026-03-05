"""
Celery task: run the full KeyScan analysis pipeline on uploaded photos.

Pipeline sequence:
  1. Claude Phase 1 — blank ID + photo quality
  2. OpenCV — perspective correction + bitting measurement
  3. Claude Phase 3 — validation + human review flag
  4. Save results to database
"""

import asyncio
import os
from pathlib import Path

import cv2
import numpy as np

from api.celery_app import celery_app
from api.aruco_detector import detect_markers
from api.homography import correct_perspective
from api.scale_calibrator import calibrate_scale
from api.blade_isolator import isolate_blade
from api.cut_detector import detect_cuts
from api.depth_measurer import measure_cuts, pad_to_expected_count
from api.confidence_scorer import score_cuts, overall_confidence, needs_human_review
from api.claude_phase1 import analyze_photos
from api.claude_phase3 import validate_bitting
from api.cnc_generator import generate_cnc_instruction


def _run_async(coro):
    """Run an async function from a synchronous Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def run_analysis_pipeline(order_id: str, image_paths: list[str], customer_email: str | None = None):
    """
    Full analysis pipeline — runs synchronously in whatever context calls it.

    Called directly as a FastAPI BackgroundTask on Railway (no Redis/Celery needed),
    or wrapped by the Celery task below for local docker-compose development.

    Args:
        order_id:      UUID string of the pre-created order.
        image_paths:   List of absolute paths to the uploaded photos.
        customer_email: Optional email for customer contact.
    """
    from api.order_manager import update_order_status, save_pipeline_results
    import traceback

    try:
        _run_async(update_order_status(order_id, "analyzing"))

        # ── Phase 1: Claude visual analysis ──────────────────────────────── #
        phase1 = analyze_photos(image_paths)

        if phase1["photo_quality"] == "reject":
            _run_async(update_order_status(order_id, "rejected"))
            return

        blank_family = phase1["blank_family"]
        if blank_family == "unknown":
            # Still try OpenCV but flag for review
            phase1["confidence"] = min(phase1["confidence"], 0.5)

        # ── Phase 2: OpenCV measurement pipeline ─────────────────────────── #
        best_idx = phase1.get("best_photo_index", 0)
        primary_path = image_paths[min(best_idx, len(image_paths) - 1)]

        opencv_result = _run_opencv_pipeline(primary_path, blank_family)

        # ── Phase 3: Claude validation ────────────────────────────────────── #
        phase3 = validate_bitting(
            blank_family=blank_family,
            opencv_bitting=opencv_result["bitting"],
            phase1_estimate=phase1.get("estimated_bitting", []),
            opencv_confidence=opencv_result["overall_confidence"],
        )

        final_bitting = phase3["final_bitting"]
        human_review = phase3["human_review"]
        final_confidence = phase3["overall_confidence"]

        # Generate CNC instructions
        cnc = generate_cnc_instruction(blank_family, final_bitting)

        # Persist results
        _run_async(save_pipeline_results(
            order_id=order_id,
            blank_code=blank_family,
            bitting=final_bitting,
            cnc_instruction=cnc["standard"],
            phase1_result=phase1,
            opencv_result=opencv_result,
            phase3_result=phase3,
            overall_confidence=final_confidence,
            human_review=human_review,
        ))

    except Exception:
        # On failure, mark order as errored and log for debugging
        traceback.print_exc()
        try:
            _run_async(update_order_status(order_id, "error"))
        except Exception:
            pass


@celery_app.task(bind=True, max_retries=2)
def run_analysis(self, order_id: str, image_paths: list[str], customer_email: str | None = None):
    """
    Celery task wrapper — used only in local docker-compose development.
    On Railway, run_analysis_pipeline is called directly via FastAPI BackgroundTasks.
    """
    try:
        run_analysis_pipeline(order_id, image_paths, customer_email)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


def _run_opencv_pipeline(image_path: str, blank_family: str) -> dict:
    """
    Run the OpenCV measurement pipeline on a single image.

    Returns a dict with bitting array, cut details, and confidence scores.
    """
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Could not read image: {image_path}")

    # Step 1: Detect ArUco markers
    marker_corners = detect_markers(image)
    if marker_corners is None:
        return {
            "bitting": [],
            "cut_details": [],
            "overall_confidence": 0.1,
            "error": "ArUco markers not detected — calibration sheet not found in image",
        }

    # Step 2: Perspective correction
    corrected = correct_perspective(image, marker_corners)

    # Step 3: Scale verification
    scale_info = calibrate_scale(corrected)

    # Step 4: Blade isolation
    blade_result = isolate_blade(corrected)
    if blade_result is None:
        return {
            "bitting": [],
            "cut_details": [],
            "overall_confidence": 0.15,
            "error": "Key blade not detected in placement zone",
        }

    # Get blank spec for this family (fall back to KW1 defaults if unknown)
    from api.blank_specs import get_blank_spec

    blank_spec = _run_async(get_blank_spec(blank_family))
    if blank_spec is None:
        # Use KW1 as a safe default for unknown blanks
        blank_spec = _run_async(get_blank_spec("KW1"))

    # Step 5: Detect cuts
    detected_cuts = detect_cuts(blade_result.blade_gray, blank_spec["cut_count"])

    # Step 6: Measure depths
    measured_cuts = measure_cuts(
        detected_cuts,
        blank_spec,
        px_per_mm=scale_info.get("px_per_mm"),
    )
    measured_cuts = pad_to_expected_count(measured_cuts, blank_spec["cut_count"], blank_spec)

    # Step 7: Score confidence
    cut_scores = score_cuts(measured_cuts, detected_cuts)
    overall = overall_confidence(cut_scores)

    bitting = [mc.bitting_code for mc in measured_cuts]

    cut_details = [
        {
            "position": cs.position_number,
            "depth_mm": measured_cuts[i].depth_mm,
            "bitting_code": cs.bitting_code,
            "confidence": cs.confidence,
        }
        for i, cs in enumerate(cut_scores)
    ]

    return {
        "bitting": bitting,
        "cut_details": cut_details,
        "overall_confidence": overall,
        "scale_info": scale_info,
        "blade_isolation_confidence": blade_result.confidence,
    }


def _format_cut_details(opencv_result: dict) -> list[dict]:
    return opencv_result.get("cut_details", [])
