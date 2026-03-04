"""
Create, update, and retrieve orders in PostgreSQL.

All database writes go through this module so the rest of the codebase
never needs to import ORM models directly.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update

from db.models import Order
from db.session import get_session


async def create_order(customer_email: Optional[str] = None) -> str:
    """
    Create a new order in 'pending' status.

    Returns:
        The new order UUID as a string.
    """
    order_id = uuid.uuid4()
    async with get_session() as session:
        order = Order(
            id=order_id,
            status="pending",
            customer_email=customer_email,
        )
        session.add(order)
    return str(order_id)


async def update_order_status(order_id: str, status: str) -> None:
    """Update the status field of an existing order."""
    async with get_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == uuid.UUID(order_id))
            .values(status=status)
        )


async def save_pipeline_results(
    order_id: str,
    blank_code: str,
    bitting: list[int],
    cnc_instruction: str,
    phase1_result: dict,
    opencv_result: dict,
    phase3_result: dict,
    overall_confidence: float,
    human_review: bool,
) -> None:
    """
    Persist the full pipeline output to the order row.

    Called by the Celery task once all three pipeline phases are complete.
    """
    status = "review_required" if human_review else "approved"

    async with get_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == uuid.UUID(order_id))
            .values(
                status=status,
                blank_code=blank_code,
                bitting=bitting,
                cnc_instruction=cnc_instruction,
                phase1_result=phase1_result,
                opencv_result=opencv_result,
                phase3_result=phase3_result,
                overall_confidence=overall_confidence,
                human_review=human_review,
            )
        )


async def get_order(order_id: str) -> Optional[dict]:
    """
    Retrieve a single order by UUID.

    Returns a plain dict, or None if not found.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )
        order = result.scalar_one_or_none()

    if order is None:
        return None

    return _order_to_dict(order)


async def get_review_queue() -> list[dict]:
    """
    Return all orders pending human review, oldest first.
    """
    async with get_session() as session:
        result = await session.execute(
            select(Order)
            .where(Order.human_review == True)
            .where(Order.status == "review_required")
            .order_by(Order.created_at)
        )
        orders = result.scalars().all()

    return [_order_to_dict(o) for o in orders]


async def approve_order(order_id: str, reviewer_notes: Optional[str] = None) -> None:
    """Mark an order as approved by a human reviewer."""
    async with get_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == uuid.UUID(order_id))
            .values(
                status="approved",
                human_review=False,
                reviewer_notes=reviewer_notes,
            )
        )


async def correct_order(
    order_id: str,
    corrected_bitting: list[int],
    reviewer_notes: Optional[str] = None,
) -> None:
    """Apply a manual bitting correction and approve the order."""
    from api.cnc_generator import generate_cnc_instruction

    async with get_session() as session:
        # Need blank_code to regenerate CNC instruction
        result = await session.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )
        order = result.scalar_one_or_none()

    if order is None:
        raise ValueError(f"Order {order_id} not found")

    cnc = generate_cnc_instruction(order.blank_code, corrected_bitting)

    async with get_session() as session:
        await session.execute(
            update(Order)
            .where(Order.id == uuid.UUID(order_id))
            .values(
                status="approved",
                bitting=corrected_bitting,
                cnc_instruction=cnc["standard"],
                human_review=False,
                reviewer_notes=reviewer_notes,
            )
        )


def _order_to_dict(order: Order) -> dict:
    """Convert an ORM Order to a plain dict for API responses."""
    return {
        "id": str(order.id),
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "blank_code": order.blank_code,
        "bitting": order.bitting,
        "cnc_instruction": order.cnc_instruction,
        "overall_confidence": float(order.overall_confidence) if order.overall_confidence else None,
        "human_review": order.human_review,
        "reviewer_notes": order.reviewer_notes,
        "customer_email": order.customer_email,
        "phase1_result": order.phase1_result,
        "opencv_result": order.opencv_result,
        "phase3_result": order.phase3_result,
    }
