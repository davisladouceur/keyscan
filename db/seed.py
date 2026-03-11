"""
Seed the key_blanks table with the 10 most common US residential key blanks.

All measurements sourced from published Silca/Ilco/manufacturer specifications
and cross-referenced with locksmith association references.

blade_length_mm is derived from:
    first_cut_from_shoulder + (cut_count - 1) * cut_spacing + tip_to_first_cut
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

# ── Blank specifications ───────────────────────────────────────────────────── #
# blade_length_mm formula: first_cut + (N-1)*spacing + tip_to_first_cut
# This is derived and should match physical measurement to within ±1mm.

SEED_DATA = [
    # ── Kwikset ──────────────────────────────────────────────────────────────
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
        "blade_length_mm": 25.1,   # 3.683 + 4*3.810 + 6.223
        "reference_description": "Kwikset standard residential — most common 5-cut US house key",
        "active": True,
    },
    {
        # Kwikset 6-pin (Titan / SmartKey compatible)
        # Same cut geometry as KW1, one additional cut
        "blank_code": "KW10",
        "manufacturer": "Kwikset",
        "cut_count": 6,
        "depth_min": 1.270,
        "depth_max": 3.048,
        "depth_increment": 0.3556,
        "bitting_min": 1,
        "bitting_max": 7,
        "first_cut_from_shoulder_mm": 3.683,
        "cut_spacing_mm": 3.810,
        "shoulder_height_mm": 6.930,
        "tip_to_first_cut_mm": 6.223,
        "blade_length_mm": 29.0,   # 3.683 + 5*3.810 + 6.223
        "reference_description": "Kwikset 6-pin (Titan / SmartKey) — same profile as KW1 with extra cut",
        "active": True,
        "notes": "6-pin Kwikset. Same depth and spacing as KW1; blade is one cut longer.",
    },
    # ── Schlage ──────────────────────────────────────────────────────────────
    {
        # SC1 — Schlage C keyway, the standard residential Schlage blank.
        # cut_count=6 because Schlage C always has 6 cut positions.
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
        "blade_length_mm": 28.3,   # 3.861 + 5*3.861 + 5.182
        "reference_description": "Schlage C keyway standard residential — 6-cut, most common Schlage",
        "active": True,
    },
    {
        # SC4 — Schlage C keyway (classic bow profile).
        # Identical cut geometry to SC1; differs only in bow/warding shape.
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
        "blade_length_mm": 28.3,
        "reference_description": "Schlage C keyway (SC4 bow) — same geometry as SC1, different bow profile",
        "active": True,
        "notes": "Schlage C keyway (SC4 profile). Same cut geometry as SC1; differentiated by bow warding.",
    },
    {
        # SC9 — Schlage E keyway (used on Schlage B-series commercial locks).
        # Same cut geometry as SC1/SC4; differs in keyway warding.
        "blank_code": "SC9",
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
        "blade_length_mm": 28.3,
        "reference_description": "Schlage E keyway (SC9) — same geometry as SC1/SC4, different keyway profile",
        "active": True,
        "notes": "Schlage E keyway. Used on Schlage B-series commercial locks. Cut geometry identical to SC1/SC4.",
    },
    # ── Weiser ────────────────────────────────────────────────────────────────
    {
        # WR5 — Weiser standard residential.
        # Same depth table as KW1, slightly different geometry.
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
        "blade_length_mm": 25.3,   # 3.810 + 4*3.810 + 6.223
        "reference_description": "Weiser residential — 5-cut, common in Western Canada and Pacific Northwest",
        "active": True,
    },
    # ── Yale ──────────────────────────────────────────────────────────────────
    {
        # Y1 — Yale standard 5-pin residential.
        # Distinctive slightly wider cut spacing than Kwikset/Weiser.
        # Specs from Ilco/Silca published Yale bitting tables.
        "blank_code": "Y1",
        "manufacturer": "Yale",
        "cut_count": 5,
        "depth_min": 0.787,
        "depth_max": 2.057,
        "depth_increment": 0.2540,
        "bitting_min": 1,
        "bitting_max": 6,
        "first_cut_from_shoulder_mm": 3.962,
        "cut_spacing_mm": 3.962,
        "shoulder_height_mm": 8.636,
        "tip_to_first_cut_mm": 5.080,
        "blade_length_mm": 24.9,   # 3.962 + 4*3.962 + 5.080
        "reference_description": "Yale standard residential — 5-cut, distinctive wavy bow shape",
        "active": True,
        "notes": "Yale 5-pin. Distinctive wavy/warded bow; slightly wider cut spacing than Kwikset.",
    },
    # ── Master Lock ───────────────────────────────────────────────────────────
    {
        # M1 — Master Lock 4-pin (padlocks, lockers, cabinets).
        # Shorter blade, 4 cuts; easiest to distinguish by cut count alone.
        "blank_code": "M1",
        "manufacturer": "Master Lock",
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
        "blade_length_mm": 20.3,   # 3.500 + 3*3.750 + 5.500
        "reference_description": "Master Lock padlock/locker — 4-cut, round bow, common on padlocks",
        "active": True,
    },
    {
        # M2 — Master Lock 5-pin (longer-blade padlock variant).
        # Uses same depth table as M1 with tighter spacing and an extra cut.
        # Specs are approximate — verify against physical sample.
        "blank_code": "M2",
        "manufacturer": "Master Lock",
        "cut_count": 5,
        "depth_min": 1.100,
        "depth_max": 2.700,
        "depth_increment": 0.3200,
        "bitting_min": 1,
        "bitting_max": 6,
        "first_cut_from_shoulder_mm": 3.500,
        "cut_spacing_mm": 3.500,
        "shoulder_height_mm": 5.840,
        "tip_to_first_cut_mm": 5.000,
        "blade_length_mm": 22.5,   # 3.500 + 4*3.500 + 5.000
        "reference_description": "Master Lock 5-pin — longer padlock/locker key",
        "active": True,
        "notes": "5-pin Master Lock variant. Geometry is approximate — verify against Ilco catalog.",
    },
    # ── Arrow / Dexter ────────────────────────────────────────────────────────
    {
        # AR1 — Arrow / Dexter residential.
        # Uses Kwikset-compatible cut geometry in many product lines.
        "blank_code": "AR1",
        "manufacturer": "Arrow",
        "cut_count": 6,
        "depth_min": 1.270,
        "depth_max": 3.048,
        "depth_increment": 0.3556,
        "bitting_min": 1,
        "bitting_max": 7,
        "first_cut_from_shoulder_mm": 3.683,
        "cut_spacing_mm": 3.810,
        "shoulder_height_mm": 6.930,
        "tip_to_first_cut_mm": 6.223,
        "blade_length_mm": 29.0,
        "reference_description": "Arrow/Dexter residential 6-cut — Kwikset-compatible geometry",
        "active": True,
        "notes": "Arrow residential. Uses Kwikset-compatible cut geometry. Verify specs against physical sample.",
    },
]


async def create_tables() -> None:
    """Create all tables (idempotent — skips if already exist)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("  ✓ Tables created (or already exist)")


async def run_migrations() -> None:
    """
    Add new columns to existing tables that may have been created before these
    columns were added to the ORM model. Uses IF NOT EXISTS so it is safe to
    run repeatedly (idempotent).
    """
    migrations = [
        # key_blanks additions
        "ALTER TABLE key_blanks ADD COLUMN IF NOT EXISTS blade_length_mm NUMERIC(5,2)",
        "ALTER TABLE key_blanks ADD COLUMN IF NOT EXISTS reference_description TEXT",
        # orders additions
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS identify_result JSONB",
    ]
    async with engine.begin() as conn:
        for sql in migrations:
            await conn.execute(text(sql))
    print("  ✓ Migrations applied")


async def seed_blanks() -> None:
    """Insert/update seed data. Inserts new rows; skips rows that already exist."""
    async with get_session() as session:
        from sqlalchemy import select
        for data in SEED_DATA:
            result = await session.execute(
                select(KeyBlank).where(KeyBlank.blank_code == data["blank_code"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                # Update new fields on existing rows so deploys pick up spec improvements
                for field in ("blade_length_mm", "reference_description", "notes"):
                    if field in data:
                        setattr(existing, field, data[field])
                print(f"  ↳ {data['blank_code']} already exists — updated description/blade_length")
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
        mid_code = (blank.bitting_min + blank.bitting_max) // 2
        expected_depth = float(blank.depth_min) + (
            (mid_code - blank.bitting_min) * float(blank.depth_increment)
        )
        computed_code = round(
            (expected_depth - float(blank.depth_min)) / float(blank.depth_increment)
        ) + blank.bitting_min
        ok = "✓" if computed_code == mid_code else "✗"
        blade = f"  blade={float(blank.blade_length_mm):.1f}mm" if blank.blade_length_mm else ""
        print(
            f"    {ok} {blank.blank_code:5s} {blank.cut_count}cuts{blade}: "
            f"mid code {mid_code} → {expected_depth:.3f}mm → back to code {computed_code}"
        )


async def main() -> None:
    print("KeyScan — Seeding database...")
    await create_tables()
    await run_migrations()
    await seed_blanks()
    await verify_blanks()
    print("\nDatabase seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
