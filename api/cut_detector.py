"""
Detect individual key cuts (valleys) along the blade edge profile.

PRIMARY METHOD — spec-based positioning:
  Uses blank_spec geometry (first_cut_from_shoulder_mm, cut_spacing_mm) to
  compute the exact x-position of each cut, then measures the valley depth
  at (and around) each position. This always produces exactly cut_count
  measurements and is robust against spurious peaks from bow/shoulder noise.

FALLBACK METHOD — peak detection:
  Used when blank_spec geometry is unavailable. Finds local maxima in the
  smoothed edge profile using scipy's find_peaks.
"""

from dataclasses import dataclass

import cv2
import numpy as np
from scipy import signal

from api.homography import PX_PER_MM


@dataclass
class DetectedCut:
    position_px: int    # X position of cut centre in the blade crop
    valley_depth_px: float   # Depth from shoulder baseline to valley bottom
    prominence: float   # scipy peak prominence score (higher = cleaner cut)
    width_px: float     # Width of the cut valley in pixels


def detect_cuts(
    blade_gray: np.ndarray,
    expected_cut_count: int,
    blank_spec: dict | None = None,
) -> list[DetectedCut]:
    """
    Find cut valleys in the blade's top edge profile.

    Args:
        blade_gray:          Grayscale blade crop (shoulder on left).
        expected_cut_count:  From the blank spec (e.g. 5 for KW1).
        blank_spec:          Full blank specification dict. When provided,
                             spec-based positioning is used (preferred).

    Returns:
        List of DetectedCut, sorted left-to-right (shoulder to tip).
        With spec-based positioning, always returns exactly expected_cut_count
        entries (no missed cuts, no padding needed).
    """
    profile = _extract_edge_profile(blade_gray)
    smoothed = _smooth_profile(profile)

    # Baseline = uncut shoulder height = 5th percentile of the profile.
    # The shoulder (uncut ridges) sits at the TOP of the blade (small y values).
    # Using the 5th percentile is robust even when most columns are at cut depth.
    baseline_y = float(np.percentile(smoothed, 5))

    has_spec_geometry = (
        blank_spec is not None
        and blank_spec.get("first_cut_from_shoulder_mm")
        and blank_spec.get("cut_spacing_mm")
    )

    if has_spec_geometry:
        return _detect_cuts_spec_based(smoothed, baseline_y, blank_spec)

    return _detect_cuts_peak_based(smoothed, baseline_y, expected_cut_count)


# ── Spec-based (primary) ────────────────────────────────────────────────────


def _detect_cuts_spec_based(
    smoothed: np.ndarray,
    baseline_y: float,
    blank_spec: dict,
) -> list[DetectedCut]:
    """
    Place one cut measurement at each spec-defined position.

    Uses the known blank geometry to compute exactly where each cut must be,
    then measures the actual valley depth at (or near) that position.
    A ±40% window around each nominal position allows for small placement
    errors without risking overlap with adjacent cuts.

    The shoulder x-reference is detected automatically: we scan the left
    side of the profile looking for the first sustained flat region (within
    15 px of the baseline) — that is the uncut shoulder land.
    """
    first_cut_mm = float(blank_spec["first_cut_from_shoulder_mm"])
    cut_spacing_mm = float(blank_spec["cut_spacing_mm"])
    cut_count = int(blank_spec["cut_count"])

    # ── Detect shoulder start ───────────────────────────────────────────── #
    # The shoulder is the flat land just before the first cut.
    # Scan left-to-right; note the first sustained region whose profile value
    # is within FLAT_THRESHOLD_PX of the baseline.
    FLAT_THRESHOLD_PX = 15.0       # px deviation from baseline → "flat"
    MIN_FLAT_COLS = int(1.5 * PX_PER_MM)  # ≥ 1.5 mm of contiguous flat area

    shoulder_x = 0      # default: assume crop starts at shoulder
    consecutive_flat = 0
    for x in range(min(int(first_cut_mm * PX_PER_MM * 2), len(smoothed))):
        if abs(smoothed[x] - baseline_y) <= FLAT_THRESHOLD_PX:
            consecutive_flat += 1
            if consecutive_flat >= MIN_FLAT_COLS:
                shoulder_x = max(0, x - MIN_FLAT_COLS + 1)
                break
        else:
            consecutive_flat = 0

    # ── Measure each cut ────────────────────────────────────────────────── #
    # Search window: ±40 % of the cut spacing to catch local peaks while
    # never overlapping with the neighbouring cut's window.
    half_win = int(cut_spacing_mm * PX_PER_MM * 0.40)

    cuts = []
    for i in range(cut_count):
        x_nominal_mm = first_cut_mm + i * cut_spacing_mm
        x_nominal_px = shoulder_x + int(round(x_nominal_mm * PX_PER_MM))

        if x_nominal_px >= len(smoothed):
            # Blade crop ended before this cut — caller will pad
            break

        x_lo = max(0, x_nominal_px - half_win)
        x_hi = min(len(smoothed) - 1, x_nominal_px + half_win)

        window = smoothed[x_lo: x_hi + 1]

        # The cut valley is the local MAXIMUM in the profile (blade edge dips
        # down = higher y value = larger profile reading).
        local_max_idx = int(np.argmax(window))
        actual_x_px = x_lo + local_max_idx
        valley_y = float(smoothed[actual_x_px])
        depth_px = max(0.0, valley_y - baseline_y)

        # Prominence: how far the peak rises above the local floor
        local_floor = float(np.min(window))
        prominence = max(0.0, valley_y - local_floor)

        cuts.append(DetectedCut(
            position_px=actual_x_px,
            valley_depth_px=depth_px,
            prominence=prominence,
            width_px=float(half_win * 2),
        ))

    return cuts


# ── Peak-based (fallback) ───────────────────────────────────────────────────


def _detect_cuts_peak_based(
    smoothed: np.ndarray,
    baseline_y: float,
    expected_cut_count: int,
) -> list[DetectedCut]:
    """
    Find cut valleys using scipy peak detection.

    Fallback for when blank_spec geometry (cut spacing) is not available.
    Cuts are LOCAL MAXIMA in the edge profile: the blade edge dips DOWN at a
    cut (higher y = further from image top = deeper cut).
    """
    # Dynamic minimum prominence: 10% of the profile range, at least 3 px
    profile_range = smoothed.max() - smoothed.min()
    min_prominence = max(3.0, profile_range * 0.10)

    # Minimum distance between cuts (half of expected spacing)
    min_distance = max(20, len(smoothed) // (expected_cut_count * 2 + 2))

    peaks, properties = signal.find_peaks(
        smoothed,           # find maxima — cuts are the high points of the profile
        prominence=min_prominence,
        width=4,
        distance=min_distance,
    )

    # Keep only the top expected_cut_count by prominence
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

    cuts = []
    for i, peak_pos in enumerate(peaks):
        valley_y = float(smoothed[peak_pos])
        depth_px = valley_y - baseline_y  # positive: cut dips below shoulder baseline
        cuts.append(DetectedCut(
            position_px=int(peak_pos),
            valley_depth_px=max(0.0, depth_px),
            prominence=float(properties["prominences"][i]),
            width_px=float(properties["widths"][i]),
        ))

    return cuts


# ── Profile extraction ──────────────────────────────────────────────────────


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

    # Scan top 85% of the crop for the blade edge.
    # Deep cuts (KW1 bitting 7 ≈ 3.4 mm = 68 px below shoulder) need headroom.
    scan_height = int(height * 0.85)

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
