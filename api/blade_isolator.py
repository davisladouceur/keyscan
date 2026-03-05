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

    # --- Thresholding: try adaptive first, fall back to Otsu if it finds
    # no valid blade (metallic keys can fool adaptive threshold) ----------
    def _find_blade_contour(binary: np.ndarray):
        """Return the best blade contour from a binary image, or None."""
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=3)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN,  kernel, iterations=1)

        cnts, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None

        # Keys: area ≥ 2 % of zone, aspect ratio ≥ 2:1
        # (was 5 % and 3:1 — too strict for keys with large bows)
        min_area = ZONE_W_PX * ZONE_H_PX * 0.02
        best, best_score = None, 0.0
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            aspect = max(w, h) / max(min(w, h), 1)
            if aspect < 2.0:          # was 3.0
                continue
            score = area * aspect
            if score > best_score:
                best_score = score
                best = cnt
        return best

    # Attempt 1: adaptive threshold (good for uneven lighting)
    thresh_adaptive = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=31,
        C=8,
    )
    blade_contour = _find_blade_contour(thresh_adaptive)

    # Attempt 2: Otsu's global threshold (better for uniform metallic keys)
    if blade_contour is None:
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh_otsu = cv2.threshold(
            blurred, 0, 255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )
        blade_contour = _find_blade_contour(thresh_otsu)

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

    # Remove the bow (if it fell inside the placement zone) so the cut detector
    # sees only the narrow blade strip.  The bow is ~15–20 mm tall vs ~6–7 mm
    # for the blade; scanning the bow's top edge produces garbage cut depths.
    blade_crop, blade_gray = _trim_bow_region(blade_crop, blade_gray)

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


def _trim_bow_region(
    blade_crop: np.ndarray,
    blade_gray: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Remove the key bow from the left side of the blade crop.

    After _orient_shoulder_left the bow (if present) is on the left.
    The bow is ~15–20 mm tall; the blade strip is only ~6–7 mm.
    We scan column heights left-to-right and find the first x where the
    key cross-section narrows to ≤ 9 mm — that is the bow-to-blade
    shoulder transition.  The crop is then trimmed to start there.

    If the crop is already ≤ 9 mm tall (bow-free), it is returned unchanged.
    """
    max_blade_h_px = int(9 * PX_PER_MM)   # 9 mm × 20 px/mm = 180 px

    _, binary = cv2.threshold(
        blade_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # Compute dark-pixel extent (first–last dark row) for every column
    heights = np.zeros(binary.shape[1], dtype=np.int32)
    for x in range(binary.shape[1]):
        col = binary[:, x]
        dark = np.where(col > 0)[0]
        if len(dark) >= 5:
            heights[x] = int(dark[-1] - dark[0])

    # Short-circuit: if no column exceeds the blade height limit,
    # the bow is not in the crop.
    if int(heights.max()) <= max_blade_h_px:
        return blade_crop, blade_gray

    # Find first x where 5 consecutive columns all measure ≤ max_blade_h_px
    # (robustness: one noisy column won't trigger a premature trim)
    consecutive = 0
    blade_start = 0
    for x in range(len(heights)):
        if 0 < heights[x] <= max_blade_h_px:
            consecutive += 1
            if consecutive >= 5:
                blade_start = x - 4   # back up to the start of the run
                break
        else:
            consecutive = 0

    if blade_start <= 0:
        return blade_crop, blade_gray  # couldn't find the transition

    return blade_crop[:, blade_start:], blade_gray[:, blade_start:]
