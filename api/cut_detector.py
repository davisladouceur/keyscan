"""
Detect individual key cuts (valleys) along the blade edge profile.

Scans the top edge of the oriented blade image to produce a 1D depth
profile, then uses scipy peak detection to locate cut valleys.
"""

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from scipy import signal


@dataclass
class DetectedCut:
    position_px: int    # X position of cut centre in the blade crop
    valley_depth_px: float   # Depth from shoulder baseline to valley bottom
    prominence: float   # scipy peak prominence score (higher = cleaner cut)
    width_px: float     # Width of the cut valley in pixels


def detect_cuts(blade_gray: np.ndarray, expected_cut_count: int) -> list[DetectedCut]:
    """
    Find cut valleys in the blade's top edge profile.

    Args:
        blade_gray:          Grayscale blade crop (shoulder on left).
        expected_cut_count:  From the blank spec (e.g. 5 for KW1).

    Returns:
        List of DetectedCut, sorted left-to-right (shoulder to tip).
        Returns fewer than expected_cut_count if not all cuts are visible.
    """
    profile = _extract_edge_profile(blade_gray)
    smoothed = _smooth_profile(profile)

    # Convert to depth-from-top: invert so cuts appear as peaks
    inverted = -smoothed

    # Dynamic minimum prominence: 10% of the profile range, at least 3 px
    profile_range = smoothed.max() - smoothed.min()
    min_prominence = max(3.0, profile_range * 0.10)

    # Minimum distance between cuts (half of expected spacing)
    # Typical cut spacing is ~75 px (3.81mm × 20 px/mm)
    min_distance = max(20, len(profile) // (expected_cut_count * 2 + 2))

    peaks, properties = signal.find_peaks(
        inverted,
        prominence=min_prominence,
        width=4,
        distance=min_distance,
    )

    # Sort by prominence and take the top expected_cut_count
    if len(peaks) > expected_cut_count:
        prominences = properties["prominences"]
        top_idx = np.argsort(prominences)[-expected_cut_count:]
        peaks = peaks[top_idx]
        properties = {k: v[top_idx] for k, v in properties.items()}

    # Sort by position (left to right)
    sort_order = np.argsort(peaks)
    peaks = peaks[sort_order]
    for k in properties:
        properties[k] = properties[k][sort_order]

    # Compute baseline (uncut metal height) as the median of the full profile
    baseline_y = float(np.median(smoothed))

    cuts = []
    for i, peak_pos in enumerate(peaks):
        valley_y = float(smoothed[peak_pos])
        depth_px = baseline_y - valley_y  # positive = deeper cut
        cuts.append(DetectedCut(
            position_px=int(peak_pos),
            valley_depth_px=max(0.0, depth_px),
            prominence=float(properties["prominences"][i]),
            width_px=float(properties["widths"][i]),
        ))

    return cuts


def _extract_edge_profile(blade_gray: np.ndarray) -> np.ndarray:
    """
    Build a 1D profile representing the blade's top edge height at each x.

    Scans each column of the blade crop and finds the first dark pixel
    (the blade edge). Returns array of y-positions (lower y = shallower cut).
    """
    height, width = blade_gray.shape
    profile = np.zeros(width, dtype=np.float32)

    # Threshold to separate blade (dark) from background (white)
    _, binary = cv2.threshold(blade_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Scan top 60% of the crop for the blade edge
    scan_height = int(height * 0.6)

    for x in range(width):
        col = binary[:scan_height, x]
        blade_pixels = np.where(col > 0)[0]
        if len(blade_pixels) > 0:
            profile[x] = float(blade_pixels[0])  # topmost dark pixel
        else:
            # No blade found in this column — interpolate later
            profile[x] = np.nan

    # Interpolate NaN gaps (missing blade pixels)
    nans = np.isnan(profile)
    if nans.any() and not nans.all():
        x_good = np.where(~nans)[0]
        y_good = profile[~nans]
        profile[nans] = np.interp(np.where(nans)[0], x_good, y_good)
    elif nans.all():
        profile[:] = height / 4  # fallback if no blade found

    return profile


def _smooth_profile(profile: np.ndarray) -> np.ndarray:
    """
    Apply Savitzky-Golay smoothing to reduce noise while preserving cut shapes.

    Window length is adaptive to profile length; must be odd and > polyorder.
    """
    n = len(profile)
    window = min(21, n if n % 2 == 1 else n - 1)
    if window < 5:
        return profile
    return signal.savgol_filter(profile, window_length=window, polyorder=3)
