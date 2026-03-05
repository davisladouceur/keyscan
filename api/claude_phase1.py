"""
Phase 1 Claude API call — Visual Intelligence.

Sends 2-3 key photos to Claude and returns:
  - Blank family identification
  - Photo quality assessment
  - Rough bitting estimate
  - Confidence score
"""

import base64
import os
from pathlib import Path
from typing import Union

import anthropic

from api.prompt_config import (
    CLAUDE_MODEL,
    MAX_TOKENS_PHASE1,
    PHASE1_SYSTEM_PROMPT,
    PHASE1_USER_MESSAGE,
)

# Supported blank families — must stay in sync with db/seed.py
SUPPORTED_BLANKS = ["KW1", "SC1", "SC4", "M1", "WR5", "unknown"]

# Tool schema — enforces structured JSON output from Claude
KEY_ANALYSIS_TOOL = {
    "name": "key_analysis",
    "description": "Extract key blank identification and photo quality data from images.",
    "input_schema": {
        "type": "object",
        "properties": {
            "blank_family": {
                "type": "string",
                "enum": SUPPORTED_BLANKS,
                "description": (
                    "Identified key blank family. "
                    "If a blank code is stamped on the bow (e.g. 'SC4', 'KW1'), "
                    "use that value directly with confidence=1.0."
                ),
            },
            "blank_stamp": {
                "type": "string",
                "description": (
                    "The exact blank-family text stamped or engraved on the key bow or "
                    "shoulder (e.g. 'SC4', 'KW1', 'Kwikset'). "
                    "Empty string if no blank-family marking is found."
                ),
            },
            "manufacturer": {
                "type": "string",
                "description": "Key manufacturer name if identifiable.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "Confidence in blank family identification (0-1). "
                    "Set to 1.0 when blank_stamp provides a definitive match."
                ),
            },
            "bitting_stamp": {
                "type": "string",
                "description": (
                    "The exact numeric bitting code stamped on the key, if visible "
                    "(e.g. '35463', '214352'). Empty string if not found. "
                    "This is different from the blank family stamp."
                ),
            },
            "estimated_bitting": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Per-cut bitting code estimate as an integer array. "
                    "If bitting_stamp is present, convert it to integers here. "
                    "Empty array [] only if cuts are completely invisible."
                ),
            },
            "photo_quality": {
                "type": "string",
                "enum": ["good", "acceptable", "poor", "reject"],
                "description": "Quality of the best photo for computer vision measurement.",
            },
            "best_photo_index": {
                "type": "integer",
                "description": "0-indexed position of the best photo in the array.",
            },
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of quality issues detected (e.g. 'blur', 'glare').",
            },
        },
        "required": ["blank_family", "confidence", "photo_quality"],
    },
}


def analyze_photos(image_paths: list[Union[str, Path]]) -> dict:
    """
    Send key photos to Claude for blank identification and quality assessment.

    Args:
        image_paths: List of 2-3 paths to JPEG/PNG key photos.

    Returns:
        Dict with keys: blank_family, manufacturer, confidence, stamps_detected,
        estimated_bitting, photo_quality, best_photo_index, issues.

    Raises:
        ValueError: If no images are provided or Claude returns unexpected output.
        anthropic.APIError: On API communication failure.
    """
    if not image_paths:
        raise ValueError("At least one image path is required")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Build the message content: image blocks + text prompt
    content = []
    for path in image_paths:
        path = Path(path)
        media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        with open(path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_data,
            },
        })

    content.append({"type": "text", "text": PHASE1_USER_MESSAGE})

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS_PHASE1,
        system=PHASE1_SYSTEM_PROMPT,
        tools=[KEY_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "key_analysis"},
        messages=[{"role": "user", "content": content}],
    )

    # Extract the tool_use result
    for block in response.content:
        if block.type == "tool_use" and block.name == "key_analysis":
            result = dict(block.input)

            # ── Normalise optional fields ────────────────────────────── #
            result.setdefault("manufacturer", "")
            result.setdefault("blank_stamp", "")
            result.setdefault("bitting_stamp", "")
            result.setdefault("stamps_detected", [])
            result.setdefault("estimated_bitting", [])
            result.setdefault("best_photo_index", 0)
            result.setdefault("issues", [])

            # ── Blank-family stamp override ──────────────────────────── #
            # If Claude read a blank code directly from the key (e.g. "SC4"),
            # treat it as ground truth and force blank_family + confidence.
            blank_stamp = result["blank_stamp"].strip().upper()
            if blank_stamp in SUPPORTED_BLANKS and blank_stamp != "unknown":
                result["blank_family"] = blank_stamp
                result["confidence"] = 1.0   # stamp is definitive

            # ── Bitting stamp → estimated_bitting ───────────────────── #
            # If a numeric bitting code was stamped on the key, parse it
            # into the estimated_bitting array (overrides visual estimate).
            bitting_stamp = result["bitting_stamp"].strip()
            if bitting_stamp and not result["estimated_bitting"]:
                digits = [int(c) for c in bitting_stamp if c.isdigit()]
                if digits:
                    result["estimated_bitting"] = digits

            return result

    raise ValueError("Claude did not return a key_analysis tool call")
