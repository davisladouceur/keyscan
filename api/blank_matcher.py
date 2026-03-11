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

If no candidates match the exact cut_count (peak detection sometimes
misses or adds one cut), a ±1 cut_count fallback is tried with an
added penalty of 1.0 to keep exact matches ranked above fallbacks.
"""

from __future__ import annotations

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

# Penalty added to scores from ±1 cut_count fallback candidates.
# Keeps exact-count matches ranked above off-by-one matches.
CUT_COUNT_MISMATCH_PENALTY = 1.0


def _score_blank_list(
    blanks: list[dict],
    approx_spacing_mm: float,
    approx_first_cut_mm: float,
    blade_length_mm: float | None,
    extra_penalty: float = 0.0,
) -> list[dict]:
    """Score a list of blank specs against measured geometry. Returns scored dicts."""
    scored = []
    for blank in blanks:
        first_cut = blank["first_cut_from_shoulder_mm"]
        spacing   = blank["cut_spacing_mm"]

        if first_cut == 0 or spacing == 0:
            continue

        first_cut_err = abs(first_cut - approx_first_cut_mm)
        spacing_err   = abs(spacing   - approx_spacing_mm)

        # Hard-limit exclusion
        if first_cut_err > HARD_LIMIT_FIRST_CUT_MM:
            print(f"[matcher]   {blank['blank_code']}: excluded — first_cut_err={first_cut_err:.3f}mm > {HARD_LIMIT_FIRST_CUT_MM}")
            continue
        if spacing_err > HARD_LIMIT_SPACING_MM:
            print(f"[matcher]   {blank['blank_code']}: excluded — spacing_err={spacing_err:.3f}mm > {HARD_LIMIT_SPACING_MM}")
            continue

        score = (
            first_cut_err * WEIGHT_FIRST_CUT
            + spacing_err  * WEIGHT_SPACING
            + extra_penalty
        )

        blade_err = None
        if blade_length_mm and blank["blade_length_mm"]:
            blade_err = abs(blank["blade_length_mm"] - blade_length_mm)
            score += blade_err * WEIGHT_BLADE_LEN

        details = (
            f"first_cut Δ{first_cut_err:.2f}mm  "
            f"spacing Δ{spacing_err:.2f}mm"
        )
        if blade_err is not None:
            details += f"  blade Δ{blade_err:.1f}mm"
        if extra_penalty > 0:
            details += f"  [±1 cut fallback, penalty {extra_penalty}]"

        print(f"[matcher]   {blank['blank_code']}: score={score:.4f} ({details})")
        result = dict(blank)
        result["match_score"]   = round(score, 4)
        result["match_details"] = details
        scored.append(result)

    return scored


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

    If no exact cut_count matches pass the hard limits, automatically retries
    with cut_count ±1 (adding CUT_COUNT_MISMATCH_PENALTY) so a missed peak
    does not cause a completely wrong blank family to win.

    Args:
        cut_count:           Number of cuts detected by peak analysis.
        approx_spacing_mm:   Mean centre-to-centre distance between adjacent cuts (mm).
        approx_first_cut_mm: Distance from shoulder to first cut (mm).
        blade_length_mm:     Optional total blade length shoulder-to-tip (mm).
        max_results:         Maximum number of candidates to return.
    """
    print(
        f"[matcher] Matching: cut_count={cut_count}, "
        f"spacing={approx_spacing_mm:.3f}mm, "
        f"first_cut={approx_first_cut_mm:.3f}mm, "
        f"blade={blade_length_mm:.1f}mm" if blade_length_mm else
        f"[matcher] Matching: cut_count={cut_count}, "
        f"spacing={approx_spacing_mm:.3f}mm, "
        f"first_cut={approx_first_cut_mm:.3f}mm"
    )

    # ── Exact cut_count match ────────────────────────────────────────────── #
    exact_blanks = await get_blanks_by_cut_count(cut_count)
    print(f"[matcher] Exact cut_count={cut_count}: {len(exact_blanks)} blanks in DB")
    scored = _score_blank_list(exact_blanks, approx_spacing_mm, approx_first_cut_mm, blade_length_mm)

    # ── ±1 cut count fallback (peak detector sometimes misses one cut) ───── #
    # Always include ±1 candidates so a missed peak doesn't hide the right family.
    # They carry a penalty so exact matches still rank higher.
    fallback_counts = [c for c in [cut_count - 1, cut_count + 1] if c >= 4]
    for fb_count in fallback_counts:
        fb_blanks = await get_blanks_by_cut_count(fb_count)
        if not fb_blanks:
            continue
        print(f"[matcher] Fallback cut_count={fb_count}: {len(fb_blanks)} blanks, penalty={CUT_COUNT_MISMATCH_PENALTY}")
        fb_scored = _score_blank_list(
            fb_blanks, approx_spacing_mm, approx_first_cut_mm, blade_length_mm,
            extra_penalty=CUT_COUNT_MISMATCH_PENALTY,
        )
        scored.extend(fb_scored)

    # Sort best-first; de-duplicate by blank_code (keep lowest score)
    seen = {}
    for c in sorted(scored, key=lambda x: x["match_score"]):
        if c["blank_code"] not in seen:
            seen[c["blank_code"]] = c
    scored = list(seen.values())

    print(f"[matcher] Final candidates: {[(c['blank_code'], c['match_score']) for c in scored[:max_results]]}")
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
