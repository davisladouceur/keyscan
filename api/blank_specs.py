"""
Helper to load key blank specifications from the database.

Returns plain Python dicts so callers don't need to import ORM models.
"""

from typing import Optional
from sqlalchemy import select
from db.models import KeyBlank
from db.session import get_session


async def get_blank_spec(blank_code: str) -> Optional[dict]:
    """
    Return the specification dict for a blank_code, or None if not found.

    Example return value:
    {
        "blank_code": "KW1",
        "manufacturer": "Kwikset",
        "cut_count": 5,
        "depth_min": 1.270,
        "depth_max": 3.048,
        "depth_increment": 0.3556,
        "bitting_min": 1,
        "bitting_max": 7,
        "first_cut_from_shoulder_mm": 3.683,
        "cut_spacing_mm": 3.810,
        "shoulder_height_mm": 6.930,
        "tip_to_first_cut_mm": 6.223,
    }
    """
    async with get_session() as session:
        result = await session.execute(
            select(KeyBlank).where(
                KeyBlank.blank_code == blank_code,
                KeyBlank.active == True,
            )
        )
        blank = result.scalar_one_or_none()

    if blank is None:
        return None

    return _blank_to_dict(blank)


def _blank_to_dict(blank) -> dict:
    return {
        "blank_code": blank.blank_code,
        "manufacturer": blank.manufacturer,
        "cut_count": blank.cut_count,
        "depth_min": float(blank.depth_min),
        "depth_max": float(blank.depth_max),
        "depth_increment": float(blank.depth_increment),
        "bitting_min": blank.bitting_min,
        "bitting_max": blank.bitting_max,
        "first_cut_from_shoulder_mm": float(blank.first_cut_from_shoulder_mm or 0),
        "cut_spacing_mm": float(blank.cut_spacing_mm or 0),
        "shoulder_height_mm": float(blank.shoulder_height_mm or 0),
        "tip_to_first_cut_mm": float(blank.tip_to_first_cut_mm or 0),
        "blade_length_mm": float(blank.blade_length_mm) if blank.blade_length_mm else None,
        "reference_description": blank.reference_description or "",
    }


async def get_all_blanks() -> list[dict]:
    """Return all active blank specifications."""
    async with get_session() as session:
        result = await session.execute(
            select(KeyBlank).where(KeyBlank.active == True).order_by(KeyBlank.id)
        )
        blanks = result.scalars().all()

    return [_blank_to_dict(b) for b in blanks]


async def get_blanks_by_cut_count(cut_count: int) -> list[dict]:
    """Return all active blanks with the specified number of cuts."""
    async with get_session() as session:
        result = await session.execute(
            select(KeyBlank).where(
                KeyBlank.active == True,
                KeyBlank.cut_count == cut_count,
            ).order_by(KeyBlank.id)
        )
        blanks = result.scalars().all()

    return [_blank_to_dict(b) for b in blanks]
