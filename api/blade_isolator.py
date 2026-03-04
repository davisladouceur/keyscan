"""
Locate and orient the key blade within the corrected calibration sheet image.

The key placement zone is a known rectangle in mm coordinates. This module:
  1. Crops the placement zone from the corrected image
  2. Finds the largest elongated contour (the key blade)
  3. Rotates it so the shoulder is on the left, tip on the right
  4. Returns the oriented blade crop plus its bounding box metadata
"""

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from api.homography import PX_PER_MM

# Key placement zone in mm (from calibration sheet design)
ZONE_X_MM = 29.0
ZONE_Y_MM = 80.0
ZONE_W_MM = 90.0
ZONE_H_MM = 50.0

# Convert to pixel coordinates in the corrected image
ZONE_X_PX = int(ZONE_X_MM * PX_PER_MM)
ZONE_Y_PX = int(ZONE_Y_MM * PX_PER_MM)
ZONE_W_PX = int(ZONE_W_MM * PX_PER_MM)
ZONE_H_PX = int(ZONE_H_MM * PX_PER_MM)


@dataclass
class BladeResult:
    blade_crop: np.ndarray       # Oriented blade image (BGR)
    blade_gray: np.ndarray       # Grayscale version
    blade_x_mm: float            # X position of blade left edge in sheet mm
    blade_y_mm: float            # Y position of blade top edge in sheet mm
    blade_w_mm: float            # Width of blade in mm
    blade_h_mm: float            # Height of blade in mm (tip-to-tip, not length)
    shoulder_side: str           # 'left' — always normalised to left
    confidence: float            # 0–1 confidence in isolation quality


def isolate_blade(corrected_image: np.ndarray) -> Optional[BladeResult]:
    """
    Locate and extract the key blade from the corrected calibration image.

    Args:
        corrected_image: BGR image from homography.correct_perspective().

    Returns:
        BladeResult with oriented blade crop, or None if no key is detected.
    """
    # Crop to the key placement zone
    zone = corrected_image[
        ZONE_Y_PX: ZONE_Y_PX + ZONE_H_PX,
        ZONE_X_PX: ZONE_X_PX + ZONE_W_PX,
    ]

    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)

    # Adaptive threshold to handle varied lighting conditions
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=21,
        C=10,
    )

    # Remove small noise blobs
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return None

    # Find the contour that best matches a key blade:
    # - Large enough area (at least 10% of the zone)
    # - High aspect ratio (width > 3x height)
    min_area = ZONE_W_PX * ZONE_H_PX * 0.05
    blade_contour = None
    best_score = 0.0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(cnt)
        aspect = max(w, h) / max(min(w, h), 1)

        # Keys have aspect ratio ~4:1 to 10:1
        if aspect < 3.0:
            continue

        score = area * aspect
        if score > best_score:
            best_score = score
            blade_contour = cnt

    if blade_contour is None:
        return None

    x, y, w, h = cv2.boundingRect(blade_contour)

    # Add a small margin for edge scanning
    margin = max(3, int(min(w, h) * 0.05))
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(zone.shape[1], x + w + margin)
    y2 = min(zone.shape[0], y + h + margin)

    blade_crop = zone[y1:y2, x1:x2]
    blade_gray = gray[y1:y2, x1:x2]

    # Ensure blade is oriented with shoulder on the left (wider end)
    # Heuristic: the shoulder end has more metal area in the left third vs right third
    blade_crop, blade_gray = _orient_shoulder_left(blade_crop, blade_gray)

    # Convert pixel positions back to mm on the full sheet
    abs_x_px = ZONE_X_PX + x1
    abs_y_px = ZONE_Y_PX + y1
    blade_w_mm = (x2 - x1) / PX_PER_MM
    blade_h_mm = (y2 - y1) / PX_PER_MM

    # Confidence: based on how clean and elongated the detected contour is
    fill_ratio = cv2.contourArea(blade_contour) / ((x2 - x1) * (y2 - y1))
    confidence = min(1.0, fill_ratio * (min(w, h) / max(h, 1)) * 2.0)

    return BladeResult(
        blade_crop=blade_crop,
        blade_gray=blade_gray,
        blade_x_mm=abs_x_px / PX_PER_MM,
        blade_y_mm=abs_y_px / PX_PER_MM,
        blade_w_mm=blade_w_mm,
        blade_h_mm=blade_h_mm,
        shoulder_side="left",
        confidence=round(min(confidence, 1.0), 3),
    )


def _orient_shoulder_left(
    blade_crop: np.ndarray,
    blade_gray: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Flip the blade horizontally if the shoulder appears to be on the right.

    The shoulder end has more metal (darker pixels in a thresholded image).
    We compare the mean darkness in the left third vs the right third.
    """
    w = blade_gray.shape[1]
    third = w // 3

    left_darkness = 255 - blade_gray[:, :third].mean()
    right_darkness = 255 - blade_gray[:, w - third:].mean()

    if right_darkness > left_darkness:
        # Shoulder is on the right — flip to put it on the left
        blade_crop = cv2.flip(blade_crop, 1)
        blade_gray = cv2.flip(blade_gray, 1)

    return blade_crop, blade_gray
