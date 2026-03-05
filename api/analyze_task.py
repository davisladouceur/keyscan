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
import traceback
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


async def run_analysis_pipeline(order_id: str, image_paths: list[str], customer_email: str | None = None):
    """
    Full analysis pipeline — async, runs in FastAPI's event loop.

    Called directly as a FastAPI BackgroundTask on Railway (no Redis/Celery needed),
    or wrapped by the Celery task below for local docker-compose development.

    All async DB calls (update_order_status, save_pipeline_results, get_blank_spec)
    are awaited directly in this function so they always use the same event loop
    as the SQLAlchemy engine — avoiding asyncpg "attached to a different loop" errors.

    The sync CPU-heavy work (OpenCV, Claude SDK) runs via asyncio.to_thread() so the
    event loop remains free to serve other requests during processing.
    """
    from api.order_manager import update_order_status, save_pipeline_results
    from api.blank_specs import get_blank_spec

    try:
        await update_order_status(order_id, "analyzing")

        # ── Phase 1: Claude visual analysis (sync SDK → run in thread) ───── #
        phase1 = await asyncio.to_thread(analyze_photos, image_paths)

        if phase1["photo_quality"] == "reject":
            await update_order_status(order_id, "rejected")
            return

        blank_family = phase1["blank_family"]
        if blank_family == "unknown":
            # Still try OpenCV but flag for review
            phase1["confidence"] = min(phase1["confidence"], 0.5)

        # Fetch blank spec here (async DB call in the correct event loop)
        blank_spec = await get_blank_spec(blank_family)
        if blank_spec is None:
            blank_spec = await get_blank_spec("KW1")

        # ── Phase 2: OpenCV measurement pipeline (CPU-heavy → run in thread) #
        best_idx = phase1.get("best_photo_index", 0)
        primary_path = image_paths[min(best_idx, len(image_paths) - 1)]

        # Pass blank_spec directly so no async DB calls are needed inside the thread
        opencv_result = await asyncio.to_thread(
            _run_opencv_pipeline_sync, primary_path, blank_spec
        )

        # ── Phase 3: Claude validation (sync SDK → run in thread) ─────────── #
        phase3 = await asyncio.to_thread(
            validate_bitting,
            blank_family,
            opencv_result["bitting"],
            phase1.get("estimated_bitting", []),
            opencv_result["overall_confidence"],
        )

        final_bitting = phase3["final_bitting"]
        human_review = phase3["human_review"]
        final_confidence = phase3["overall_confidence"]

        # Generate CNC instructions (pure CPU, fast)
        cnc = generate_cnc_instruction(blank_family, final_bitting)

        # Persist results (async DB call in the correct event loop)
        await save_pipeline_results(
            order_id=order_id,
            blank_code=blank_family,
            bitting=final_bitting,
            cnc_instruction=cnc["standard"],
            phase1_result=phase1,
            opencv_result=opencv_result,
            phase3_result=phase3,
            overall_confidence=final_confidence,
            human_review=human_review,
        )

    except Exception:
        # Log the full traceback to Railway logs for debugging
        traceback.print_exc()
        try:
            await update_order_status(order_id, "error")
        except Exception:
            pass


@celery_app.task(bind=True, max_retries=2)
def run_analysis(self, order_id: str, image_paths: list[str], customer_email: str | None = None):
    """
    Celery task wrapper — used only in local docker-compose development.
    On Railway, run_analysis_pipeline is called directly via FastAPI BackgroundTasks.
    """
    try:
        asyncio.run(run_analysis_pipeline(order_id, image_paths, customer_email))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


def _run_opencv_pipeline_sync(image_path: str, blank_spec: dict) -> dict:
    """
    Run the full OpenCV measurement pipeline synchronously.

    Takes a pre-fetched blank_spec dict so no async DB calls are needed here —
    safe to run inside asyncio.to_thread().
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
