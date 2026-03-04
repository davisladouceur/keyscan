/**
 * overlay.js — Draw real-time visual feedback on the camera canvas.
 *
 * Renders:
 *  - Coloured squares at each ArUco marker corner (green=detected, red=missing)
 *  - Key placement rectangle (pulsing while waiting, solid green when key present)
 *  - Status badge colours reflecting check results
 */

'use strict';

const OVERLAY_COLORS = {
  pass: '#2ECC71',
  warn: '#E67E22',
  fail: '#E74C3C',
  pending: '#95A5A6',
};

let pulsePhase = 0;  // Drives the placement rectangle pulse animation

/** Called by feedback.js after each frame is processed. */
function drawOverlay() {
  const canvas = document.getElementById('overlay-canvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  pulsePhase = (pulsePhase + 0.08) % (Math.PI * 2);

  _drawMarkerCorners(ctx, canvas);
  _drawPlacementRectangle(ctx, canvas);
}

/** Draw coloured squares at detected ArUco marker positions. */
function _drawMarkerCorners(ctx, canvas) {
  const corners = window.detectedCorners;
  const CORNER_SIZE = 24;
  const CORNER_IDS = [0, 1, 2, 3];
  const SCREEN_POSITIONS = [
    { x: CORNER_SIZE * 0.5,            y: CORNER_SIZE * 0.5 },             // top-left
    { x: canvas.width - CORNER_SIZE * 1.5,  y: CORNER_SIZE * 0.5 },        // top-right
    { x: CORNER_SIZE * 0.5,            y: canvas.height - CORNER_SIZE * 1.5 }, // bottom-left
    { x: canvas.width - CORNER_SIZE * 1.5,  y: canvas.height - CORNER_SIZE * 1.5 }, // bottom-right
  ];

  CORNER_IDS.forEach((id, i) => {
    const detected = corners && corners.find(c => c.id === id);
    const color    = detected ? OVERLAY_COLORS.pass : OVERLAY_COLORS.fail;
    const pos      = detected
      ? { x: detected.x - CORNER_SIZE / 2, y: detected.y - CORNER_SIZE / 2 }
      : { x: SCREEN_POSITIONS[i].x - CORNER_SIZE / 2, y: SCREEN_POSITIONS[i].y - CORNER_SIZE / 2 };

    ctx.strokeStyle = color;
    ctx.lineWidth   = 3;
    ctx.strokeRect(pos.x, pos.y, CORNER_SIZE, CORNER_SIZE);

    // Small ID label
    ctx.fillStyle  = color;
    ctx.font       = '10px monospace';
    ctx.fillText(id.toString(), pos.x + CORNER_SIZE + 2, pos.y + CORNER_SIZE / 2 + 4);
  });
}

/** Draw the key placement rectangle in the centre of the view. */
function _drawPlacementRectangle(ctx, canvas) {
  const results = window.checkResults || {};
  const keyPass = results.key && results.key.status === 'pass';
  const sheetPass = results.sheet && results.sheet.status === 'pass';

  if (!sheetPass) return; // Only draw if sheet is detected

  // Derive approximate rectangle position from detected corners or fallback to centre
  let rectX, rectY, rectW, rectH;

  const corners = window.detectedCorners;
  if (corners && corners.length === 4) {
    const xs = corners.map(c => c.x);
    const ys = corners.map(c => c.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);

    const sheetW = maxX - minX;
    const sheetH = maxY - minY;

    // Key zone is roughly centre 60% width × 24% height of the sheet
    rectW = sheetW * 0.61;
    rectH = sheetH * 0.24;
    rectX = minX + (sheetW - rectW) / 2;
    rectY = minY + (sheetH - rectH) / 2;
  } else {
    rectW = canvas.width  * 0.55;
    rectH = canvas.height * 0.20;
    rectX = (canvas.width  - rectW) / 2;
    rectY = (canvas.height - rectH) / 2;
  }

  // Pulsing opacity when waiting, solid when key is detected
  const alpha = keyPass
    ? 1.0
    : 0.6 + 0.4 * Math.sin(pulsePhase);

  const color = keyPass ? OVERLAY_COLORS.pass : '#FFFFFF';

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth   = 2.5;
  ctx.setLineDash([8, 6]);
  ctx.strokeRect(rectX, rectY, rectW, rectH);
  ctx.setLineDash([]);

  // Label inside rectangle
  ctx.fillStyle  = color;
  ctx.font       = '13px -apple-system, sans-serif';
  ctx.textAlign  = 'center';
  const label = keyPass ? '✓ Key detected' : 'Place key here →';
  ctx.fillText(label, rectX + rectW / 2, rectY + rectH / 2 + 5);
  ctx.restore();
}
