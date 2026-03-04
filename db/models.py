"""
SQLAlchemy async ORM models for KeyScan.

Two tables:
  - key_blanks  : mechanical specifications for each supported key blank
  - orders      : one row per customer submission, tracks the full pipeline
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, Numeric,
    String, Text, ARRAY, ForeignKey
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class KeyBlank(Base):
    __tablename__ = "key_blanks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    blank_code = Column(String(20), unique=True, nullable=False)
    manufacturer = Column(String(100))
    cut_count = Column(Integer, nullable=False)

    # Depth range and increment (all in mm)
    depth_min = Column(Numeric(5, 3), nullable=False)
    depth_max = Column(Numeric(5, 3), nullable=False)
    depth_increment = Column(Numeric(5, 4), nullable=False)

    # Bitting code range (integer codes)
    bitting_min = Column(Integer, nullable=False)
    bitting_max = Column(Integer, nullable=False)

    # Physical geometry (mm) — used by OpenCV pipeline for alignment
    first_cut_from_shoulder_mm = Column(Numeric(5, 3))
    cut_spacing_mm = Column(Numeric(5, 3))
    shoulder_height_mm = Column(Numeric(5, 3))
    tip_to_first_cut_mm = Column(Numeric(5, 3))

    active = Column(Boolean, default=True)
    notes = Column(Text)


class Order(Base):
    __tablename__ = "orders"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Status flow: pending → analyzing → review_required → approved → sent_to_cnc
    status = Column(String(30), default="pending", nullable=False)

    blank_code = Column(String(20), ForeignKey("key_blanks.blank_code"))
    bitting = Column(ARRAY(Integer))
    cnc_instruction = Column(Text)

    # Raw pipeline outputs stored as JSON blobs for debugging / review
    phase1_result = Column(JSONB)
    opencv_result = Column(JSONB)
    phase3_result = Column(JSONB)

    overall_confidence = Column(Numeric(3, 2))
    human_review = Column(Boolean, default=False)
    reviewer_notes = Column(Text)

    customer_email = Column(String(255))
    shipping_address = Column(JSONB)
