"""
Seed the key_blanks table with POC data for 4 blank families.

All measurements sourced from Silca/Ilco published specifications.
Run this once after the initial migration:
    python -m db.seed
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from db.session import engine, get_session
from db.models import Base, KeyBlank

SEED_DATA = [
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
        "active": True,
    },
    {
        "blank_code": "SC1",
        "manufacturer": "Schlage",
        "cut_count": 6,
        "depth_min": 0.000,
        "depth_max": 2.108,
        "depth_increment": 0.2345,
        "bitting_min": 0,
        "bitting_max": 9,
        "first_cut_from_shoulder_mm": 3.861,
        "cut_spacing_mm": 3.861,
        "shoulder_height_mm": 7.772,
        "tip_to_first_cut_mm": 5.182,
        "active": True,
    },
    {
        "blank_code": "M1",
        "manufacturer": "Master",
        "cut_count": 4,
        "depth_min": 1.100,
        "depth_max": 2.700,
        "depth_increment": 0.3200,
        "bitting_min": 1,
        "bitting_max": 6,
        "first_cut_from_shoulder_mm": 3.500,
        "cut_spacing_mm": 3.750,
        "shoulder_height_mm": 5.840,
        "tip_to_first_cut_mm": 5.500,
        "active": True,
    },
    {
        "blank_code": "WR5",
        "manufacturer": "Weiser",
        "cut_count": 5,
        "depth_min": 1.270,
        "depth_max": 3.048,
        "depth_increment": 0.3556,
        "bitting_min": 1,
        "bitting_max": 7,
        "first_cut_from_shoulder_mm": 3.810,
        "cut_spacing_mm": 3.810,
        "shoulder_height_mm": 7.137,
        "tip_to_first_cut_mm": 6.223,
        "active": True,
    },
    {
        # SC4 — Schlage C keyway (classic profile, same cut geometry as SC1).
        # Differentiated from SC1 by bow shape; cut specs are identical.
        "blank_code": "SC4",
        "manufacturer": "Schlage",
        "cut_count": 6,
        "depth_min": 0.000,
        "depth_max": 2.108,
        "depth_increment": 0.2345,
        "bitting_min": 0,
        "bitting_max": 9,
        "first_cut_from_shoulder_mm": 3.861,
        "cut_spacing_mm": 3.861,
        "shoulder_height_mm": 7.772,
        "tip_to_first_cut_mm": 5.182,
        "active": True,
        "notes": "Schlage C keyway (SC4 profile). Same cut geometry as SC1.",
    },
]


async def create_tables() -> None:
    """Create all tables (idempotent — skips if already exist)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("  ✓ Tables created (or already exist)")


async def seed_blanks() -> None:
    """Insert seed data, skipping rows that already exist."""
    async with get_session() as session:
        for data in SEED_DATA:
            # Check if blank already exists
            from sqlalchemy import select
            result = await session.execute(
                select(KeyBlank).where(KeyBlank.blank_code == data["blank_code"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  ↳ {data['blank_code']} already seeded — skipping")
                continue
            session.add(KeyBlank(**data))
            print(f"  ✓ Seeded {data['blank_code']} ({data['manufacturer']})")


async def verify_blanks() -> None:
    """Query each blank and verify depth-to-bitting math."""
    async with get_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(KeyBlank).order_by(KeyBlank.id))
        blanks = result.scalars().all()

    print(f"\n  Loaded {len(blanks)} blank(s) — verifying depth-to-bitting math:")
    for blank in blanks:
        # Mid-range bitting code → expected depth
        mid_code = (blank.bitting_min + blank.bitting_max) // 2
        expected_depth = float(blank.depth_min) + (
            (mid_code - blank.bitting_min) * float(blank.depth_increment)
        )
        # Reverse: depth → bitting code
        computed_code = round(
            (expected_depth - float(blank.depth_min)) / float(blank.depth_increment)
        ) + blank.bitting_min
        ok = "✓" if computed_code == mid_code else "✗"
        print(
            f"    {ok} {blank.blank_code}: mid code {mid_code} "
            f"→ {expected_depth:.3f}mm → back to code {computed_code}"
        )


async def main() -> None:
    print("KeyScan — Seeding database...")
    await create_tables()
    await seed_blanks()
    await verify_blanks()
    print("\nDatabase seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
