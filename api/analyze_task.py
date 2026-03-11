"""
KeyScan two-phase analysis pipeline.

Phase A — Identify (fast, ~10s):
  1. OpenCV geometric measurement → cut count, spacing, first-cut position
  2. Database matching → ranked blank candidates
  3. Claude Phase 1 → photo quality + stamp reading (run in parallel with A1/A2)
  4. Merge: stamp overrides database candidates
  5. Save candidates to order, status → "identified"
  → Client shows confirm screen; user picks blank

Phase B — Measure (slower, ~20s, after user confirms blank):
  1. Load saved photos + confirmed blank spec
  2. OpenCV spec-based bitting measurement (uses known geometry)
  3. Claude Phase 3 validation
  4. Save results, status → "approved" | "review_required"
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
from api.cut_detector import detect_cuts, measure_blade_geometry
from api.depth_measurer import measure_cuts, pad_to_expected_count
from api.confidence_scorer import score_cuts, overall_confidence, needs_human_review
from api.claude_phase1 import analyze_photos
from api.claude_phase3 import validate_bitting
from api.cnc_generator import generate_cnc_instruction
from api.blank_matcher import match_blank_candidates, select_best_candidate


# ── Phase A: Identify ─────────────────────────────────────────────────────── #

async def run_identify_pipeline(order_id: str, image_paths: list[str], customer_email: str | None = None):
    """
    Phase A: Geometric measurement + blank candidate matching.

    Runs OpenCV and database matching concurrently with Claude Phase 1
    (quality check + stamp reading).  The two results are merged: a stamp
    overrides the geometric candidates.

    Sets order status to "identified" with the candidate list so the
    client can show the confirm screen.
    """
    from api.order_manager import update_order_status, save_identify_results

    try:
        await update_order_status(order_id, "analyzing")

        best_idx = 0  # will be updated after Phase 1 returns
        primary_path = image_paths[0]

        # Run OpenCV geometry and Claude Phase 1 concurrently
        opencv_task  = asyncio.to_thread(_run_geometry_pipeline_sync, primary_path)
        phase1_task  = asyncio.to_thread(analyze_photos, image_paths)

        geometry_result, phase1 = await asyncio.gather(opencv_task, phase1_task)

        # Photo quality gate
        if phase1["photo_quality"] == "reject":
            await update_order_status(order_id, "rejected")
            return

        best_idx = phase1.get("best_photo_index", 0)

        # ── Database candidate matching ──────────────────────────────────── #
        stamp_override = None
        blank_stamp = phase1.get("blank_stamp", "").strip().upper()
        if blank_stamp and blank_stamp != "UNKNOWN":
            stamp_override = blank_stamp

        candidates = []
        measurements = {}

        if geometry_result and geometry_result.get("geometry"):
            geo = geometry_result["geometry"]
            print(
                f"[identify] Geometry: cuts={geo.cut_count}, "
                f"spacing={geo.approx_spacing_mm:.3f}mm, "
                f"first_cut={geo.approx_first_cut_mm:.3f}mm, "
                f"blade={geo.blade_length_mm:.1f}mm, "
                f"shoulder_x={geo.shoulder_x_px}px, "
                f"peaks={geo.peak_positions_px}"
            )
            measurements = {
                "cut_count":             geo.cut_count,
                "approx_spacing_mm":     geo.approx_spacing_mm,
                "approx_first_cut_mm":   geo.approx_first_cut_mm,
                "blade_length_mm":       geo.blade_length_mm,
            }
            candidates = await match_blank_candidates(
                cut_count=geo.cut_count,
                approx_spacing_mm=geo.approx_spacing_mm,
                approx_first_cut_mm=geo.approx_first_cut_mm,
                blade_length_mm=geo.blade_length_mm,
                max_results=3,
            )
            print(f"[identify] Candidates: {[(c['blank_code'], c['match_score']) for c in candidates]}")
        elif geometry_result and geometry_result.get("error"):
            print(f"[identify] Geometry error: {geometry_result['error']}")

        # Stamp override: put the stamped blank first in the list (score 0)
        if stamp_override:
            from api.blank_specs import get_blank_spec
            stamped_spec = await get_blank_spec(stamp_override)
            if stamped_spec:
                stamped_spec["match_score"]   = 0.0
                stamped_spec["match_details"] = f"Stamp '{stamp_override}' confirmed"
                stamped_spec["stamp_confirmed"] = True
                # Remove it from the list if already present, prepend
                candidates = [c for c in candidates if c["blank_code"] != stamp_override]
                candidates.insert(0, stamped_spec)
            else:
                print(f"[identify] Stamp '{stamp_override}' not in database — ignored")

        # Fallback: no candidates found — include all blanks as options
        if not candidates:
            from api.blank_specs import get_all_blanks
            all_blanks = await get_all_blanks()
            candidates = [
                {**b, "match_score": 99.0, "match_details": "No geometry match — manual selection required", "stamp_confirmed": False}
                for b in all_blanks
            ]
            print(f"[identify] No geometric candidates found — returning all blanks as fallback")

        await save_identify_results(
            order_id=order_id,
            candidates=candidates,
            measurements=measurements,
            phase1_result=phase1,
            image_paths=image_paths,
        )

    except Exception:
        traceback.print_exc()
        try:
            await update_order_status(order_id, "error")
        except Exception:
            pass


# ── Phase B: Measure ──────────────────────────────────────────────────────── #

async def run_measure_pipeline(order_id: str, confirmed_blank: str):
    """
    Phase B: Spec-based bitting measurement using the confirmed blank.

    Loads saved photos from identify_result.image_paths and runs the full
    OpenCV measurement pipeline with the confirmed blank's spec.
    """
    from api.order_manager import update_order_status, save_pipeline_results, confirm_blank
    from api.blank_specs import get_blank_spec

    try:
        # Mark order as measuring + save confirmed blank_code + get saved image_paths
        image_paths = await confirm_blank(order_id, confirmed_blank)

        if not image_paths:
            await update_order_status(order_id, "error")
            return

        blank_spec = await get_blank_spec(confirmed_blank)
        if blank_spec is None:
            blank_spec = await get_blank_spec("KW1")

        # Load the identify_result for phase1 data (stamps, quality info)
        from api.order_manager import get_order
        order_data = await get_order(order_id)
        identify_result = order_data.get("identify_result") or {}
        phase1 = identify_result.get("phase1_result", {})

        # ── OpenCV spec-based measurement ────────────────────────────────── #
        best_idx = phase1.get("best_photo_index", 0)
        primary_path = image_paths[min(best_idx, len(image_paths) - 1)]

        opencv_result = await asyncio.to_thread(
            _run_opencv_pipeline_sync, primary_path, blank_spec
        )

        # ── Phase 3: Claude validation ────────────────────────────────────── #
        phase3 = await asyncio.to_thread(
            validate_bitting,
            confirmed_blank,
            opencv_result["bitting"],
            phase1.get("estimated_bitting", []),
            opencv_result["overall_confidence"],
        )

        final_bitting   = phase3["final_bitting"]
        human_review    = phase3["human_review"]
        final_confidence = phase3["overall_confidence"]

        cnc = generate_cnc_instruction(confirmed_blank, final_bitting)

        await save_pipeline_results(
            order_id=order_id,
            blank_code=confirmed_blank,
            bitting=final_bitting,
            cnc_instruction=cnc["standard"],
            phase1_result={**phase1, "blank_family": confirmed_blank, "user_selected": True},
            opencv_result=opencv_result,
            phase3_result=phase3,
            overall_confidence=final_confidence,
            human_review=human_review,
        )

    except Exception:
        traceback.print_exc()
        try:
            await update_order_status(order_id, "error")
        except Exception:
            pass


# ── Legacy single-shot pipeline (kept for backward compat) ────────────────── #

async def run_analysis_pipeline(order_id: str, image_paths: list[str], customer_email: str | None = None, blank_family_override: str | None = None):
    """
    Legacy entry point — runs identify + auto-confirms the top candidate.

    Used when the caller supplies a blank_family_override (e.g. from the old
    manual-selection screen).  Kept so no existing callers break.
    """
    if blank_family_override:
        # Fast path: user told us the blank — skip identify, go straight to measure
        from api.order_manager import update_order_status, save_pipeline_results
        from api.blank_specs import get_blank_spec

        try:
            await update_order_status(order_id, "analyzing")
            phase1 = await asyncio.to_thread(analyze_photos, image_paths)

            if phase1["photo_quality"] == "reject":
                await update_order_status(order_id, "rejected")
                return

            blank_family = blank_family_override.upper()
            phase1["blank_family"] = blank_family
            phase1["user_selected"] = True

            blank_spec = await get_blank_spec(blank_family) or await get_blank_spec("KW1")

            best_idx = phase1.get("best_photo_index", 0)
            primary_path = image_paths[min(best_idx, len(image_paths) - 1)]
            opencv_result = await asyncio.to_thread(_run_opencv_pipeline_sync, primary_path, blank_spec)

            phase3 = await asyncio.to_thread(
                validate_bitting, blank_family, opencv_result["bitting"],
                phase1.get("estimated_bitting", []), opencv_result["overall_confidence"],
            )

            final_bitting    = phase3["final_bitting"]
            human_review     = phase3["human_review"]
            final_confidence = phase3["overall_confidence"]
            cnc = generate_cnc_instruction(blank_family, final_bitting)

            await save_pipeline_results(
                order_id=order_id, blank_code=blank_family, bitting=final_bitting,
                cnc_instruction=cnc["standard"], phase1_result=phase1,
                opencv_result=opencv_result, phase3_result=phase3,
                overall_confidence=final_confidence, human_review=human_review,
            )
        except Exception:
            traceback.print_exc()
            try:
                await update_order_status(order_id, "error")
            except Exception:
                pass
    else:
        # New path: run geometric identify → auto-confirm top candidate
        await run_identify_pipeline(order_id, image_paths, customer_email)
        from api.order_manager import get_order
        order_data = await get_order(order_id)
        if order_data and order_data["status"] == "identified":
            candidates = (order_data.get("identify_result") or {}).get("candidates", [])
            top_blank = candidates[0]["blank_code"] if candidates else "KW1"
            await run_measure_pipeline(order_id, top_blank)


@celery_app.task(bind=True, max_retries=2)
def run_analysis(self, order_id: str, image_paths: list[str], customer_email: str | None = None, blank_family_override: str | None = None):
    """
    Celery task wrapper — used only in local docker-compose development.
    """
    try:
        asyncio.run(run_analysis_pipeline(order_id, image_paths, customer_email, blank_family_override))
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)


def _load_image_exif_aware(image_path: str) -> np.ndarray:
    """
    Load a JPEG respecting its EXIF orientation tag.

    cv2.imread ignores EXIF rotation, so phone photos taken in portrait
    mode arrive sideways and completely break the homography / zone crop.
    PIL's exif_transpose fixes this before we hand off to OpenCV.
    """
    from PIL import Image, ImageOps
    pil_img = Image.open(image_path)
    pil_img = ImageOps.exif_transpose(pil_img)   # apply EXIF rotation
    if pil_img.mode != "RGB":
        pil_img = pil_img.convert("RGB")
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _run_geometry_pipeline_sync(image_path: str) -> dict:
    """
    Run the geometric pre-measurement pipeline synchronously (Phase A).

    Does NOT need a blank_spec — uses peak-based detection to measure
    cut count, spacing, and first-cut position for database matching.
    Returns a dict with 'geometry' (BladeGeometry dataclass) and 'error'.
    """
    image = _load_image_exif_aware(image_path)
    if image is None or image.size == 0:
        return {"geometry": None, "error": f"Could not read image: {image_path}"}

    marker_corners = detect_markers(image)
    if marker_corners is None:
        return {"geometry": None, "error": "ArUco markers not detected"}

    corrected  = correct_perspective(image, marker_corners)
    scale_info = calibrate_scale(corrected)
    blade_result = isolate_blade(corrected)

    if blade_result is None:
        return {"geometry": None, "error": "Key blade not detected in placement zone"}

    px_per_mm = scale_info.get("px_per_mm")
    geometry = measure_blade_geometry(blade_result.blade_gray, px_per_mm)

    return {"geometry": geometry, "error": None}


def _run_opencv_pipeline_sync(image_path: str, blank_spec: dict) -> dict:
    """
    Run the full OpenCV measurement pipeline synchronously.

    Takes a pre-fetched blank_spec dict so no async DB calls are needed here —
    safe to run inside asyncio.to_thread().
    """
    image = _load_image_exif_aware(image_path)
    if image is None or image.size == 0:
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
    # Pass blank_spec so the detector can use known cut geometry (preferred)
    detected_cuts = detect_cuts(
        blade_result.blade_gray,
        blank_spec["cut_count"],
        blank_spec=blank_spec,
    )

    # Step 6: Measure depths
    measured_cuts = measure_cuts(
        detected_cuts,
        blank_spec,
        px_per_mm=scale_info.get("px_per_mm"),
    )
    measured_cuts = pad_to_expected_count(measured_cuts, blank_spec["cut_count"], blank_spec)

    # Step 6b: Physical constraint validation — reject impossible depths.
    # If any measured depth exceeds the blank's physical maximum (with 30%
    # tolerance), the blade crop almost certainly still contains the bow or
    # the perspective correction failed.  Return an error rather than
    # propagating garbage bitting codes.
    depth_max_physical = (
        blank_spec["depth_min"]
        + (blank_spec["bitting_max"] - blank_spec["bitting_min"])
        * blank_spec["depth_increment"]
    )
    impossible = [
        mc for mc in measured_cuts
        if mc.depth_mm > depth_max_physical * 1.30   # 30 % tolerance
        and mc.position_px > 0                        # skip padded placeholders
    ]
    if impossible:
        bad = impossible[0]
        return {
            "bitting": [],
            "cut_details": [],
            "overall_confidence": 0.05,
            "blade_isolation_confidence": blade_result.confidence,
            "error": (
                f"Cut {bad.position_number} depth {bad.depth_mm:.2f} mm exceeds "
                f"physical maximum {depth_max_physical:.2f} mm for "
                f"{blank_spec['blank_code']}. Key bow may be inside the "
                f"placement zone — place only the blade in the dashed box."
            ),
        }

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
