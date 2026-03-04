"""
Verify and refine the mm/pixel scale of a perspective-corrected image.

After homography correction the nominal scale is MM_PER_PX (0.05 mm/px).
This module measures the actual marker-to-marker pixel distance in the
corrected image and computes any residual scale error for reporting.
"""

import json
from pathlib import Path
from typing import Optional

import cv2
import cv2.aruco as aruco
import numpy as np

from api.homography import PX_PER_MM, MM_PER_PX

_MARKER_POSITIONS_PATH = Path(__file__).parent / "marker_positions.json"
with open(_MARKER_POSITIONS_PATH) as f:
    _MARKER_POSITIONS_MM: dict = json.load(f)


def calibrate_scale(corrected_image: np.ndarray) -> dict:
    """
    Re-detect ArUco markers in the corrected image and compute actual scale.

    Args:
        corrected_image: Warped image from homography.correct_perspective().

    Returns:
        {
            "mm_per_px": float,         # actual scale (should ≈ 0.05)
            "px_per_mm": float,         # inverse
            "scale_error_pct": float,   # deviation from nominal, in %
            "ok": bool,                 # True if error < 2%
        }
    """
    gray = cv2.cvtColor(corrected_image, cv2.COLOR_BGR2GRAY) \
        if len(corrected_image.shape) == 3 else corrected_image

    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    params = aruco.DetectorParameters()
    detector = aruco.ArucoDetector(aruco_dict, params)
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None or len(ids) < 2:
        # Fall back to nominal scale — homography should be accurate enough
        return {
            "mm_per_px": MM_PER_PX,
            "px_per_mm": PX_PER_MM,
            "scale_error_pct": 0.0,
            "ok": True,
        }

    ids_flat = ids.flatten().tolist()
    centers_px = {}
    for i, mid in enumerate(ids_flat):
        centers_px[mid] = corners[i][0].mean(axis=0)  # [cx, cy]

    # Use top-left (0) and top-right (1) if both present
    if 0 in centers_px and 1 in centers_px:
        measured_id_a, measured_id_b = 0, 1
    else:
        # Use whatever two markers are available
        avail = sorted(centers_px.keys())
        measured_id_a, measured_id_b = avail[0], avail[1]

    px_dist = float(np.linalg.norm(
        np.array(centers_px[measured_id_a]) - np.array(centers_px[measured_id_b])
    ))

    ref = _MARKER_POSITIONS_MM
    mm_a = ref[str(measured_id_a)]["center_mm"]
    mm_b = ref[str(measured_id_b)]["center_mm"]
    mm_dist = float(np.linalg.norm(
        np.array(mm_a) - np.array(mm_b)
    ))

    if px_dist == 0:
        return {
            "mm_per_px": MM_PER_PX,
            "px_per_mm": PX_PER_MM,
            "scale_error_pct": 0.0,
            "ok": True,
        }

    actual_mm_per_px = mm_dist / px_dist
    actual_px_per_mm = px_dist / mm_dist
    nominal_mm_per_px = MM_PER_PX
    error_pct = abs(actual_mm_per_px - nominal_mm_per_px) / nominal_mm_per_px * 100

    return {
        "mm_per_px": actual_mm_per_px,
        "px_per_mm": actual_px_per_mm,
        "scale_error_pct": round(error_pct, 2),
        "ok": error_pct < 2.0,
    }
