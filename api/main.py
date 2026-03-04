"""
KeyScan FastAPI application.

Endpoints:
  POST /analyze              — Submit photos, kick off analysis pipeline
  GET  /calibration-sheet.pdf — Serve printable calibration PDF
  GET  /blanks               — List supported key blank specs
  GET  /orders/{id}          — Get order status + results
  POST /orders/{id}/approve  — Admin: approve order as-is
  POST /orders/{id}/correct  — Admin: override bitting before CNC
  GET  /admin/review-queue   — Admin: orders needing human review
  GET  /health               — Health check for deployment monitoring
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

import secrets

from api.blank_specs import get_all_blanks
from api.order_manager import (
    approve_order,
    correct_order,
    create_order,
    get_order,
    get_review_queue,
)
from api.analyze_task import run_analysis
from contextlib import asynccontextmanager


async def _startup():
    """Seed the database and generate calibration sheet on first boot."""
    import sys
    from pathlib import Path as P

    # Seed key blank data (idempotent — skips rows that already exist)
    try:
        from db.seed import create_tables, seed_blanks
        await create_tables()
        await seed_blanks()
        print("✓ Database seeded")
    except Exception as e:
        print(f"⚠ Database seed skipped: {e}")

    # Generate calibration sheet if not already present
    try:
        pdf_path = P(__file__).parent.parent / "static" / "calibration_sheet.pdf"
        if not pdf_path.exists():
            sys.path.insert(0, str(P(__file__).parent.parent))
            from scripts.generate_calibration_sheet import main as gen_sheet
            gen_sheet()
            print("✓ Calibration sheet generated")
    except Exception as e:
        print(f"⚠ Calibration sheet generation skipped: {e}")


@asynccontextmanager
async def lifespan(app):
    await _startup()
    yield


# ── App setup ─────────────────────────────────────────────────────────────── #

app = FastAPI(
    title="KeyScan API",
    description="AI-powered key duplication — image to bitting pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
STATIC_DIR = Path(__file__).parent.parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Upload directory for temporary photo storage
UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Admin auth
security = HTTPBasic()
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "changeme")


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    """HTTP Basic Auth for admin endpoints."""
    correct_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ── Endpoints ─────────────────────────────────────────────────────────────── #

@app.get("/health")
async def health():
    """Liveness probe for deployment monitoring."""
    return {"status": "ok", "service": "keyscan-api"}


@app.get("/blanks")
async def list_blanks():
    """Return all supported key blank families with their specifications."""
    blanks = await get_all_blanks()
    return {"blanks": blanks}


@app.get("/calibration-sheet.pdf")
async def calibration_sheet():
    """
    Serve the printable calibration sheet PDF.

    The PDF is generated once at startup if it doesn't exist.
    """
    pdf_path = STATIC_DIR / "calibration_sheet.pdf"
    if not pdf_path.exists():
        # Generate on first request
        from scripts.generate_calibration_sheet import main as gen_sheet
        gen_sheet()
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename="keyscan_calibration_sheet.pdf",
    )


@app.post("/analyze")
async def analyze(
    photos: list[UploadFile] = File(..., description="2-3 JPEG photos of the key"),
    email: Optional[str] = Form(None, description="Customer email (optional)"),
):
    """
    Accept key photos, create an order, and dispatch the analysis pipeline.

    Returns immediately with the order ID. The client should poll
    GET /orders/{id} to check status.
    """
    if len(photos) < 2:
        raise HTTPException(
            status_code=422,
            detail="At least 2 photos are required for accurate measurement",
        )
    if len(photos) > 3:
        raise HTTPException(
            status_code=422,
            detail="Maximum 3 photos accepted",
        )

    # Validate file types and sizes
    saved_paths = []
    order_dir = UPLOAD_DIR / str(uuid.uuid4())
    order_dir.mkdir(parents=True)

    for photo in photos:
        if photo.content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid file type: {photo.content_type}. JPEG or PNG required.",
            )
        content = await photo.read()
        if len(content) > 5 * 1024 * 1024:  # 5MB limit
            raise HTTPException(
                status_code=422,
                detail=f"Photo {photo.filename} exceeds 5MB limit",
            )
        ext = Path(photo.filename).suffix or ".jpg"
        dest = order_dir / f"photo_{len(saved_paths)}{ext}"
        dest.write_bytes(content)
        saved_paths.append(str(dest))

    # Create order in DB
    order_id = await create_order(customer_email=email)

    # Dispatch Celery task (non-blocking)
    run_analysis.delay(order_id, saved_paths, email)

    return {
        "order_id": order_id,
        "status": "analyzing",
        "message": "Photos received. Poll GET /orders/{order_id} for results.",
    }


@app.get("/orders/{order_id}")
async def get_order_status(order_id: str):
    """Retrieve order status and analysis results."""
    order = await get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.post("/orders/{order_id}/approve")
async def approve(
    order_id: str,
    reviewer_notes: Optional[str] = Form(None),
    _admin: str = Depends(verify_admin),
):
    """Admin: approve an order in the review queue as-is."""
    order = await get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    await approve_order(order_id, reviewer_notes)
    return {"order_id": order_id, "status": "approved"}


@app.post("/orders/{order_id}/correct")
async def correct(
    order_id: str,
    corrected_bitting: str = Form(..., description="Comma-separated bitting codes, e.g. '3,5,2,6,4'"),
    reviewer_notes: Optional[str] = Form(None),
    _admin: str = Depends(verify_admin),
):
    """Admin: manually override bitting before sending to CNC."""
    order = await get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    try:
        bitting = [int(x.strip()) for x in corrected_bitting.split(",")]
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="corrected_bitting must be comma-separated integers, e.g. '3,5,2,6,4'",
        )

    await correct_order(order_id, bitting, reviewer_notes)
    return {"order_id": order_id, "status": "approved", "corrected_bitting": bitting}


@app.get("/admin/review-queue")
async def review_queue(_admin: str = Depends(verify_admin)):
    """Admin: return all orders pending human review."""
    orders = await get_review_queue()
    return {"orders": orders, "count": len(orders)}


@app.get("/admin")
async def admin_page():
    """Serve the human review queue admin UI."""
    review_html = Path(__file__).parent.parent / "frontend" / "review.html"
    return FileResponse(str(review_html), media_type="text/html")


@app.get("/")
async def index():
    """Serve the main web app."""
    index_html = Path(__file__).parent.parent / "frontend" / "index.html"
    return FileResponse(str(index_html), media_type="text/html")
