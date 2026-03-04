"""
Phase 3 Claude API call — Bitting Validation.

Cross-checks the OpenCV-measured bitting against the Phase 1 visual estimate
and produces a final validated bitting array with human-review flag.
"""

import os

import anthropic

from api.prompt_config import (
    CLAUDE_MODEL,
    MAX_TOKENS_PHASE3,
    PHASE3_SYSTEM_PROMPT,
)

# Tool schema for structured validation output
BITTING_VALIDATION_TOOL = {
    "name": "bitting_validation",
    "description": "Validate OpenCV bitting measurements against visual estimate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "final_bitting": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Final recommended bitting array.",
            },
            "human_review": {
                "type": "boolean",
                "description": "True if this order should go to the human review queue.",
            },
            "overall_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Overall confidence in the final bitting (0-1).",
            },
            "flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of validation issues or reasons for human review.",
            },
            "cut_validations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "position": {"type": "integer"},
                        "opencv_code": {"type": "integer"},
                        "phase1_code": {"type": ["integer", "null"]},
                        "final_code": {"type": "integer"},
                        "agreement": {"type": "boolean"},
                        "ambiguous": {"type": "boolean"},
                    },
                    "required": ["position", "opencv_code", "final_code", "agreement"],
                },
                "description": "Per-cut validation details.",
            },
        },
        "required": ["final_bitting", "human_review", "overall_confidence", "flags"],
    },
}


def validate_bitting(
    blank_family: str,
    opencv_bitting: list[int],
    phase1_estimate: list[int],
    opencv_confidence: float,
) -> dict:
    """
    Ask Claude to validate the OpenCV-measured bitting.

    Args:
        blank_family:       Identified blank (e.g. 'KW1').
        opencv_bitting:     Bitting array from the OpenCV pipeline.
        phase1_estimate:    Rough estimate from Claude Phase 1.
        opencv_confidence:  Overall confidence score from confidence_scorer.

    Returns:
        Dict with keys: final_bitting, human_review, overall_confidence, flags,
        cut_validations.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build per-cut comparison text
    if phase1_estimate and len(phase1_estimate) == len(opencv_bitting):
        cut_lines = []
        for i, (oc, pc) in enumerate(zip(opencv_bitting, phase1_estimate)):
            diff = abs(oc - pc)
            flag = " ← DISCREPANCY" if diff > 1 else (" ← check" if diff == 1 else "")
            cut_lines.append(f"  Cut {i+1}: OpenCV={oc}, Phase1={pc}{flag}")
        cut_comparison = "\n".join(cut_lines)
    else:
        cut_comparison = f"  OpenCV only: {opencv_bitting}\n  Phase 1 estimate not available or wrong length."

    prompt = f"""\
Blank family: {blank_family}
OpenCV measured bitting: {opencv_bitting}
Phase 1 estimated bitting: {phase1_estimate if phase1_estimate else 'not available'}
OpenCV overall confidence: {opencv_confidence:.2f}

Cut-by-cut comparison:
{cut_comparison}

Please validate:
1. Are all OpenCV bitting values within the legal range for {blank_family}?
2. For cuts where Phase 1 and OpenCV disagree by more than 1 — flag them as ambiguous.
3. Determine the final bitting array (trust OpenCV when confidence is high).
4. Set human_review = true if confidence < 0.85 or any cut is ambiguous or out of range.
"""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS_PHASE3,
        system=PHASE3_SYSTEM_PROMPT,
        tools=[BITTING_VALIDATION_TOOL],
        tool_choice={"type": "tool", "name": "bitting_validation"},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "bitting_validation":
            result = dict(block.input)
            result.setdefault("cut_validations", [])
            return result

    raise ValueError("Claude did not return a bitting_validation tool call")
