"""
ArUco marker detection for the KeyScan calibration sheet.

Detects the 4 corner markers (IDs 0-3, DICT_4X4_50) and returns
their pixel coordinates. Rejects the image if any marker is missing.
"""

import json
from pathlib import Path
from typing import Optional

import cv2
import cv2.aruco as aruco
import numpy as np

# Load the known physical marker positions (mm) at import time
_MARKER_POSITIONS_PATH = Path(__file__).parent / "marker_positions.json"
with open(_MARKER_POSITIONS_PATH) as f:
    _MARKER_POSITIONS_MM: dict = json.load(f)


def detect_markers(image: np.ndarray) -> Optional[dict]:
    """
    Detect all 4 ArUco calibration markers in the image.

    Args:
        image: BGR or grayscale numpy array.

    Returns:
        Dict mapping marker_id (int) → 4x2 array of corner pixel coords,
        ordered as: [top-left, top-right, bottom-right, bottom-left].
        Returns None if fewer than 4 markers are found or IDs are wrong.
    """
    gray = _to_gray(image)

    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    params = aruco.DetectorParameters()

    # Slightly more permissive error correction for printed markers
    params.errorCorrectionRate = 0.6

    detector = aruco.ArucoDetector(aruco_dict, params)
    corners, ids, rejected = detector.detectMarkers(gray)

    if ids is None or len(ids) < 4:
        found = 0 if ids is None else len(ids)
        return None  # caller will raise a user-facing error

    ids_flat = ids.flatten().tolist()
    if sorted(ids_flat) != [0, 1, 2, 3]:
        return None  # unexpected marker IDs

    # Build a clean dict: id → 4×2 float array of pixel corners
    result = {}
    for i, marker_id in enumerate(ids_flat):
        result[marker_id] = corners[i][0]  # shape (4, 2)

    return result


def get_marker_centers(marker_corners: dict) -> dict:
    """
    Compute the pixel centre of each detected marker.

    Args:
        marker_corners: Output from detect_markers().

    Returns:
        Dict mapping marker_id (int) → [cx, cy] pixel coords.
    """
    centers = {}
    for marker_id, corners in marker_corners.items():
        # Average of the 4 corner coordinates
        centers[marker_id] = corners.mean(axis=0).tolist()
    return centers


def get_reference_centers_mm() -> dict:
    """
    Return the known physical marker centres in mm from marker_positions.json.

    Returns:
        Dict mapping marker_id (int) → [cx_mm, cy_mm].
    """
    return {
        int(k): v["center_mm"] for k, v in _MARKER_POSITIONS_MM.items()
    }


def _to_gray(image: np.ndarray) -> np.ndarray:
    """Convert to grayscale if needed."""
    if len(image.shape) == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
