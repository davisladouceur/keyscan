"""
Generate the KeyScan calibration sheet PDF.

The sheet has 4 ArUco markers (DICT_4X4_50, IDs 0-3) at precisely known
positions. Users print this once at 100% scale and place their key on it
for every scan. The OpenCV pipeline uses the markers to correct perspective
and establish the mm/pixel scale.

Sheet: A5 (148mm x 210mm) at 300 DPI
"""

import json
import os
import sys
from pathlib import Path

import cv2
import cv2.aruco as aruco
import numpy as np
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

# --------------------------------------------------------------------------- #
# Sheet geometry constants (all in mm)
# --------------------------------------------------------------------------- #
SHEET_W_MM = 148.0
SHEET_H_MM = 210.0
MARKER_SIZE_MM = 20.0
INSET_MM = 10.0
DPI = 300

# Key placement zone (centred on sheet, 90mm x 50mm)
ZONE_W_MM = 90.0
ZONE_H_MM = 50.0
ZONE_X_MM = (SHEET_W_MM - ZONE_W_MM) / 2   # 29.0
ZONE_Y_MM = (SHEET_H_MM - ZONE_H_MM) / 2   # 80.0


def mm_to_px(mm_value: float) -> int:
    """Convert millimetres to pixels at the working DPI."""
    return int(mm_value * DPI / 25.4)


def mm_to_pt(mm_value: float) -> float:
    """Convert millimetres to PDF points (1 pt = 1/72 inch)."""
    return mm_value * mm  # reportlab's mm unit is already in points


def generate_aruco_marker_png(marker_id: int, size_px: int) -> np.ndarray:
    """Render a single ArUco marker as a numpy uint8 image."""
    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    marker_img = np.zeros((size_px, size_px), dtype=np.uint8)
    # generateImageMarker replaced older drawMarker API in OpenCV 4.7+
    aruco.generateImageMarker(aruco_dict, marker_id, size_px, marker_img, 1)
    return marker_img


def save_marker_positions(output_dir: Path) -> dict:
    """
    Save the known physical marker corner positions in mm.

    The OpenCV pipeline loads this JSON to build the reference homography.
    Positions are the top-left corner of each marker, then we compute centres.
    """
    positions = {
        # marker_id: {"top_left_mm": [x, y], "center_mm": [cx, cy]}
        "0": {
            "corner": "top-left",
            "top_left_mm": [INSET_MM, INSET_MM],
            "center_mm": [INSET_MM + MARKER_SIZE_MM / 2,
                          INSET_MM + MARKER_SIZE_MM / 2],
        },
        "1": {
            "corner": "top-right",
            "top_left_mm": [SHEET_W_MM - INSET_MM - MARKER_SIZE_MM, INSET_MM],
            "center_mm": [SHEET_W_MM - INSET_MM - MARKER_SIZE_MM / 2,
                          INSET_MM + MARKER_SIZE_MM / 2],
        },
        "2": {
            "corner": "bottom-left",
            "top_left_mm": [INSET_MM, SHEET_H_MM - INSET_MM - MARKER_SIZE_MM],
            "center_mm": [INSET_MM + MARKER_SIZE_MM / 2,
                          SHEET_H_MM - INSET_MM - MARKER_SIZE_MM / 2],
        },
        "3": {
            "corner": "bottom-right",
            "top_left_mm": [SHEET_W_MM - INSET_MM - MARKER_SIZE_MM,
                            SHEET_H_MM - INSET_MM - MARKER_SIZE_MM],
            "center_mm": [SHEET_W_MM - INSET_MM - MARKER_SIZE_MM / 2,
                          SHEET_H_MM - INSET_MM - MARKER_SIZE_MM / 2],
        },
    }
    out_path = output_dir / "marker_positions.json"
    with open(out_path, "w") as f:
        json.dump(positions, f, indent=2)
    print(f"  ✓ marker_positions.json written to {out_path}")
    return positions


def build_sheet_image() -> np.ndarray:
    """
    Compose a full-resolution PNG of the calibration sheet (300 DPI).
    This is used for the PDF embed and for OpenCV detection tests.
    """
    sheet_w = mm_to_px(SHEET_W_MM)
    sheet_h = mm_to_px(SHEET_H_MM)
    marker_px = mm_to_px(MARKER_SIZE_MM)

    # White background
    sheet = np.ones((sheet_h, sheet_w), dtype=np.uint8) * 255

    # Marker top-left pixel positions (x, y) in image coordinates
    # Note: image y-axis is top-down, same as mm coordinates here
    marker_origins_px = {
        0: (mm_to_px(INSET_MM), mm_to_px(INSET_MM)),
        1: (mm_to_px(SHEET_W_MM - INSET_MM - MARKER_SIZE_MM),
            mm_to_px(INSET_MM)),
        2: (mm_to_px(INSET_MM),
            mm_to_px(SHEET_H_MM - INSET_MM - MARKER_SIZE_MM)),
        3: (mm_to_px(SHEET_W_MM - INSET_MM - MARKER_SIZE_MM),
            mm_to_px(SHEET_H_MM - INSET_MM - MARKER_SIZE_MM)),
    }

    for marker_id, (ox, oy) in marker_origins_px.items():
        marker_img = generate_aruco_marker_png(marker_id, marker_px)
        sheet[oy:oy + marker_px, ox:ox + marker_px] = marker_img

    # Draw key placement zone (dashed rectangle in light grey)
    zone_x1 = mm_to_px(ZONE_X_MM)
    zone_y1 = mm_to_px(ZONE_Y_MM)
    zone_x2 = zone_x1 + mm_to_px(ZONE_W_MM)
    zone_y2 = zone_y1 + mm_to_px(ZONE_H_MM)

    # Draw dashed border — dark colour, thick, long dashes so it's clearly
    # visible when printed on a home printer
    dark = 30          # near-black (was 180 = light grey)
    dash_on = 25       # px "on"  (was 15)
    dash_off = 10      # px "off" (was 10)
    border_px = 5      # line thickness in pixels (was 2)

    def draw_dashed_hline(img, y, x1, x2, color, on, off):
        x = x1
        draw = True
        while x < x2:
            end = min(x + (on if draw else off), x2)
            if draw:
                img[y, x:end] = color
            x = end
            draw = not draw

    def draw_dashed_vline(img, x, y1, y2, color, on, off):
        y = y1
        draw = True
        while y < y2:
            end = min(y + (on if draw else off), y2)
            if draw:
                img[y:end, x] = color
            y = end
            draw = not draw

    for thickness in range(border_px):
        draw_dashed_hline(sheet, zone_y1 + thickness, zone_x1, zone_x2,
                          dark, dash_on, dash_off)
        draw_dashed_hline(sheet, zone_y2 - thickness, zone_x1, zone_x2,
                          dark, dash_on, dash_off)
        draw_dashed_vline(sheet, zone_x1 + thickness, zone_y1, zone_y2,
                          dark, dash_on, dash_off)
        draw_dashed_vline(sheet, zone_x2 - thickness, zone_y1, zone_y2,
                          dark, dash_on, dash_off)

    # Solid corner brackets — extra visual anchor for placement
    bracket_len = mm_to_px(8)   # 8mm long solid lines at each corner
    bracket_t   = border_px + 2
    corners = [
        (zone_x1, zone_y1, +1, +1),   # top-left
        (zone_x2, zone_y1, -1, +1),   # top-right
        (zone_x1, zone_y2, +1, -1),   # bottom-left
        (zone_x2, zone_y2, -1, -1),   # bottom-right
    ]
    for cx, cy, dx, dy in corners:
        # horizontal arm
        x0 = min(cx, cx + dx * bracket_len)
        x1b = max(cx, cx + dx * bracket_len)
        for t in range(bracket_t):
            sheet[cy + dy * t, x0:x1b] = dark
        # vertical arm
        y0 = min(cy, cy + dy * bracket_len)
        y1b = max(cy, cy + dy * bracket_len)
        for t in range(bracket_t):
            sheet[y0:y1b, cx + dx * t] = dark

    # "PLACE KEY HERE" label centred just above the placement zone
    label = "PLACE KEY HERE"
    font       = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 1.0
    thickness_text = 2
    (tw, th), _ = cv2.getTextSize(label, font, font_scale, thickness_text)
    label_x = (zone_x1 + zone_x2 - tw) // 2
    label_y = zone_y1 - mm_to_px(4)   # 4mm above the box
    cv2.putText(sheet, label, (label_x, label_y),
                font, font_scale, dark, thickness_text, cv2.LINE_AA)

    return sheet


def generate_pdf(sheet_img: np.ndarray, output_path: Path) -> None:
    """
    Embed the sheet PNG into an A5 PDF at exact physical dimensions.
    Uses reportlab — no font needed, just the image.
    """
    # Save sheet as temporary PNG
    tmp_png = output_path.parent / "_sheet_tmp.png"
    cv2.imwrite(str(tmp_png), sheet_img)

    # A5 in reportlab points
    page_w, page_h = A5  # (419.53, 595.28) points

    c = pdf_canvas.Canvas(str(output_path), pagesize=A5)

    # Draw the sheet image to fill the entire page
    c.drawImage(str(tmp_png), 0, 0, width=page_w, height=page_h)

    # Instruction text at bottom
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawCentredString(
        page_w / 2, 18,
        "Print at 100% scale — Do NOT scale to fit. One sheet per household."
    )

    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, 8, "KeyScan.com  |  Calibration Sheet v1")

    c.save()
    tmp_png.unlink()  # remove temp PNG
    print(f"  ✓ calibration_sheet.pdf written to {output_path}")


def verify_detection(sheet_img: np.ndarray) -> bool:
    """
    Run ArUco detection on the generated sheet to confirm all 4 markers
    are detectable before the PDF is distributed.
    """
    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    params = aruco.DetectorParameters()
    detector = aruco.ArucoDetector(aruco_dict, params)

    corners, ids, rejected = detector.detectMarkers(sheet_img)

    if ids is None or len(ids) < 4:
        found = len(ids) if ids is not None else 0
        print(f"  ✗ Detection failed: only {found}/4 markers detected")
        return False

    found_ids = sorted(ids.flatten().tolist())
    if found_ids != [0, 1, 2, 3]:
        print(f"  ✗ Wrong marker IDs detected: {found_ids}")
        return False

    print(f"  ✓ All 4 ArUco markers verified (IDs: {found_ids})")
    return True


def main():
    project_root = Path(__file__).parent.parent
    static_dir = project_root / "static"
    static_dir.mkdir(exist_ok=True)

    print("KeyScan — Generating calibration sheet...")

    # 1. Build the sheet PNG at 300 DPI
    sheet_img = build_sheet_image()
    png_path = static_dir / "calibration_sheet.png"
    cv2.imwrite(str(png_path), sheet_img)
    print(f"  ✓ Sheet PNG ({sheet_img.shape[1]}x{sheet_img.shape[0]}px) → {png_path}")

    # 2. Verify ArUco detection works on the generated image
    ok = verify_detection(sheet_img)
    if not ok:
        print("ERROR: ArUco detection failed. Cannot generate PDF.")
        sys.exit(1)

    # 3. Generate the PDF
    pdf_path = static_dir / "calibration_sheet.pdf"
    generate_pdf(sheet_img, pdf_path)

    # 4. Save marker positions JSON (used by OpenCV pipeline)
    positions = save_marker_positions(project_root / "api")

    print("\nCalibration sheet generation complete.")
    print(f"  PDF:  {pdf_path}")
    print(f"  PNG:  {png_path}")
    print(f"  JSON: {project_root / 'api' / 'marker_positions.json'}")


if __name__ == "__main__":
    main()
