"""
Perspective correction using the 4 detected ArUco marker centres.

Computes the homography matrix from detected pixel positions → known mm
positions, then warps the image to a geometrically flat top-down view
at a consistent scale.
"""

import numpy as np
import cv2

from api.aruco_detector import get_marker_centers, get_reference_centers_mm

# Output image resolution: 1 pixel = 0.05 mm → 20 px/mm
# Sheet is 148×210 mm → output is 2960×4200 px
MM_PER_PX = 0.05
PX_PER_MM = 1.0 / MM_PER_PX

SHEET_W_MM = 148.0
SHEET_H_MM = 210.0
OUTPUT_W = int(SHEET_W_MM * PX_PER_MM)   # 2960
OUTPUT_H = int(SHEET_H_MM * PX_PER_MM)   # 4200


def correct_perspective(image: np.ndarray, marker_corners: dict) -> np.ndarray:
    """
    Warp the input image so the calibration sheet is flat and to scale.

    Args:
        image:          Raw BGR photo from the user's camera.
        marker_corners: Output from aruco_detector.detect_markers().

    Returns:
        Warped BGR image where 1 pixel ≈ MM_PER_PX mm.
    """
    # Source: detected pixel centres of the 4 markers
    pixel_centers = get_marker_centers(marker_corners)

    # Destination: known mm positions converted to output-image pixels
    ref_mm = get_reference_centers_mm()

    src_pts = np.float32([
        pixel_centers[0],  # top-left marker centre
        pixel_centers[1],  # top-right
        pixel_centers[2],  # bottom-left
        pixel_centers[3],  # bottom-right
    ])

    dst_pts = np.float32([
        [ref_mm[0][0] * PX_PER_MM, ref_mm[0][1] * PX_PER_MM],
        [ref_mm[1][0] * PX_PER_MM, ref_mm[1][1] * PX_PER_MM],
        [ref_mm[2][0] * PX_PER_MM, ref_mm[2][1] * PX_PER_MM],
        [ref_mm[3][0] * PX_PER_MM, ref_mm[3][1] * PX_PER_MM],
    ])

    H, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)

    warped = cv2.warpPerspective(image, H, (OUTPUT_W, OUTPUT_H))
    return warped


def compute_skew_angle(marker_corners: dict) -> float:
    """
    Estimate the camera tilt angle from marker positions.

    Uses the horizontal separation of the two top markers to compute skew.
    Returns angle in degrees (0 = perfectly flat, >15 = problematic).
    """
    centers = get_marker_centers(marker_corners)

    # Vector from top-left (0) to top-right (1) marker centre
    dx = centers[1][0] - centers[0][0]
    dy = centers[1][1] - centers[0][1]

    angle_rad = np.arctan2(abs(dy), abs(dx))
    return float(np.degrees(angle_rad))


def get_mm_per_pixel(marker_corners: dict) -> float:
    """
    Derive the mm/pixel scale from detected marker positions in the raw image.

    Compares the pixel distance between the two top markers against their
    known physical separation. Used as a secondary verification.
    """
    centers = get_marker_centers(marker_corners)
    ref_mm = get_reference_centers_mm()

    # Pixel distance between top markers (0 and 1)
    px_dx = centers[1][0] - centers[0][0]
    px_dy = centers[1][1] - centers[0][1]
    px_dist = float(np.sqrt(px_dx**2 + py_dy**2)) if (
        py_dy := centers[1][1] - centers[0][1]) != 0 else abs(px_dx)

    # Known mm distance between the same two marker centres
    mm_dx = ref_mm[1][0] - ref_mm[0][0]
    mm_dy = ref_mm[1][1] - ref_mm[0][1]
    mm_dist = float(np.sqrt(mm_dx**2 + mm_dy**2))

    return mm_dist / px_dist if px_dist > 0 else 0.0
