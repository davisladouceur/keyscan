"""
Geometry-driven key blank candidate matching.

Instead of asking an AI to visually identify a key blank "from nothing",
this module uses physical measurements extracted from the photo to query
the database for blanks whose geometry matches.

Matching works by:
  1. Filtering to blanks with exactly the measured cut_count
  2. Scoring each candidate by weighted distance from measured geometry
  3. Returning the top N candidates sorted best-first (lowest score)

A perfect match scores 0.0. Blanks whose first_cut or spacing are more
than HARD_LIMIT_MM away are excluded (hardware detection error range).
"""

from api.blank_specs import get_blanks_by_cut_count

# Maximum tolerable error in mm before a blank is excluded from candidates.
# Shoulder detection can be off by ~1mm, so we're generous.
HARD_LIMIT_FIRST_CUT_MM = 1.5   # ±1.5 mm on first cut position
HARD_LIMIT_SPACING_MM    = 0.8   # ±0.8 mm on cut spacing

# Score weighting — first_cut position is the most distinctive per-blank metric,
# so it gets higher weight than spacing (many blanks share similar spacings).
WEIGHT_FIRST_CUT  = 2.0
WEIGHT_SPACING    = 1.5
WEIGHT_BLADE_LEN  = 0.5   # only used when blade_length_mm is provided


async def match_blank_candidates(
    cut_count: int,
    approx_spacing_mm: float,
    approx_first_cut_mm: float,
    blade_length_mm: float | None = None,
    max_results: int = 3,
) -> list[dict]:
    """
    Return up to `max_results` blank candidates that best match the measured geometry.

    Each returned dict contains all blank spec fields plus:
      "match_score"     — lower is better (0.0 = perfect match)
      "match_details"   — human-readable breakdown of why this scored as it did

    Args:
        cut_count:           Number of cuts detected by peak analysis.
        approx_spacing_mm:   Mean centre-to-centre distance between adjacent cuts (mm).
        approx_first_cut_mm: Distance from shoulder to first cut (mm).
        blade_length_mm:     Optional total blade length shoulder-to-tip (mm).
        max_results:         Maximum number of candidates to return.
    """
    # Pull all blanks that share this cut count
    candidates = await get_blanks_by_cut_count(cut_count)

    scored = []
    for blank in candidates:
        first_cut = blank["first_cut_from_shoulder_mm"]
        spacing   = blank["cut_spacing_mm"]

        # Skip blanks with missing geometry data
        if first_cut == 0 or spacing == 0:
            continue

        first_cut_err = abs(first_cut - approx_first_cut_mm)
        spacing_err   = abs(spacing   - approx_spacing_mm)

        # Hard-limit exclusion
        if first_cut_err > HARD_LIMIT_FIRST_CUT_MM:
            continue
        if spacing_err > HARD_LIMIT_SPACING_MM:
            continue

        # Weighted score
        score = (
            first_cut_err * WEIGHT_FIRST_CUT
            + spacing_err  * WEIGHT_SPACING
        )

        # Optional blade length term
        blade_err = None
        if blade_length_mm and blank["blade_length_mm"]:
            blade_err = abs(blank["blade_length_mm"] - blade_length_mm)
            score += blade_err * WEIGHT_BLADE_LEN

        # Build human-readable detail string
        details = (
            f"first_cut Δ{first_cut_err:.2f}mm  "
            f"spacing Δ{spacing_err:.2f}mm"
        )
        if blade_err is not None:
            details += f"  blade Δ{blade_err:.1f}mm"

        result = dict(blank)
        result["match_score"]   = round(score, 4)
        result["match_details"] = details
        scored.append(result)

    # Sort best-first
    scored.sort(key=lambda x: x["match_score"])
    return scored[:max_results]


def select_best_candidate(candidates: list[dict], stamp_override: str | None = None) -> dict | None:
    """
    Pick the single best blank from a candidate list.

    If `stamp_override` is provided (e.g. "SC4" read from the key bow stamp),
    and a matching candidate exists, return that one with confidence=1.0.
    Otherwise return the top-scored candidate, or None if the list is empty.
    """
    if not candidates:
        return None

    # Stamp is ground truth — always wins
    if stamp_override:
        stamp_upper = stamp_override.upper()
        for c in candidates:
            if c["blank_code"] == stamp_upper:
                result = dict(c)
                result["match_score"]   = 0.0
                result["match_details"] = f"Stamp '{stamp_override}' confirmed"
                result["stamp_confirmed"] = True
                return result

    best = candidates[0]
    best["stamp_confirmed"] = False
    return best
