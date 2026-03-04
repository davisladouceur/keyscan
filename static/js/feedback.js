/**
 * feedback.js — Real-time camera feedback using opencv.js
 *
 * Runs every 400ms on the live video frame. Six checks determine
 * whether the camera view is ready for capture.
 *
 * Depends on opencv.js loaded via the <script async> tag in index.html.
 * Call startFeedbackLoop() after OpenCV is ready and the video is playing.
 */

'use strict';

// ── State ────────────────────────────────────────────────────────────────── //

let cv = null;
let arucoDetector = null;   // cv.aruco_ArucoDetector instance (OpenCV 4.8 API)
let feedbackInterval = null;
let stableFrameCount = 0;
const STABLE_FRAMES_REQUIRED = 4;   // 4 × 400ms = 1.6 seconds stable
const FEEDBACK_INTERVAL_MS = 400;

// Current check results (shared with overlay.js)
window.checkResults = {
  sheet:    { status: 'pending', message: 'Waiting…' },
  angle:    { status: 'pending', message: 'Waiting…' },
  scale:    { status: 'pending', message: 'Waiting…' },
  key:      { status: 'pending', message: 'Waiting…' },
  lighting: { status: 'pending', message: 'Waiting…' },
  focus:    { status: 'pending', message: 'Waiting…' },
};

// Last detected marker corners (shared with overlay.js for corner rendering)
window.detectedCorners = null;

// ── Entry points ─────────────────────────────────────────────────────────── //

/** Called by opencv.js onload. */
function onOpenCvReady() {
  cv = window.cv;
  try {
    // OpenCV 4.8 new API — all three args are required by this JS build
    const dict         = cv.getPredefinedDictionary(cv.DICT_4X4_50);
    const params       = new cv.aruco_DetectorParameters();
    const refineParams = new cv.aruco_RefineParameters(10, 3.0, true);
    arucoDetector = new cv.aruco_ArucoDetector(dict, params, refineParams);
    console.log('ArUco detector initialised');
  } catch (e) {
    console.warn('ArUco init error:', e);
    arucoDetector = null;
  }
  // Notify wizard.js that OpenCV is ready
  document.dispatchEvent(new Event('opencv-ready'));
}

/** Start the 400ms feedback loop. */
function startFeedbackLoop(videoEl, canvasEl) {
  if (feedbackInterval) clearInterval(feedbackInterval);
  stableFrameCount = 0;

  feedbackInterval = setInterval(() => {
    if (!cv || videoEl.readyState < 2) return;
    _runFeedbackFrame(videoEl, canvasEl);
  }, FEEDBACK_INTERVAL_MS);
}

/** Stop the feedback loop (called when leaving camera screen). */
function stopFeedbackLoop() {
  if (feedbackInterval) {
    clearInterval(feedbackInterval);
    feedbackInterval = null;
  }
  stableFrameCount = 0;
}

// ── Core feedback frame ───────────────────────────────────────────────────── //

function _runFeedbackFrame(videoEl, canvasEl) {
  const ctx = canvasEl.getContext('2d');

  // Match canvas to video dimensions
  canvasEl.width  = videoEl.videoWidth  || 640;
  canvasEl.height = videoEl.videoHeight || 480;

  // Draw current frame to a hidden processing canvas at reduced resolution
  const processW = 640;
  const processH = Math.round(canvasEl.height * (processW / canvasEl.width));

  const offscreen = document.createElement('canvas');
  offscreen.width  = processW;
  offscreen.height = processH;
  const octx = offscreen.getContext('2d');
  octx.drawImage(videoEl, 0, 0, processW, processH);

  let src = null, gray = null;
  try {
    const imageData = octx.getImageData(0, 0, processW, processH);
    src  = cv.matFromImageData(imageData);
    gray = new cv.Mat();
    cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);

    // Run all 6 checks
    const sheetResult    = checkCalibrationSheet(gray);
    const angleResult    = checkCameraAngle(sheetResult.corners);
    const scaleResult    = checkScale(sheetResult.corners, processW, processH);
    const keyResult      = checkKeyPresence(gray, sheetResult.corners, processW, processH);
    const lightingResult = checkLighting(gray);
    const focusResult    = checkFocus(gray);

    const results = {
      sheet:    sheetResult,
      angle:    angleResult,
      scale:    scaleResult,
      key:      keyResult,
      lighting: lightingResult,
      focus:    focusResult,
    };

    window.checkResults = results;

    // Store corner positions scaled back to display canvas coords
    if (sheetResult.corners) {
      const scaleX = canvasEl.width  / processW;
      const scaleY = canvasEl.height / processH;
      window.detectedCorners = sheetResult.corners.map(pt => ({
        x: pt.x * scaleX,
        y: pt.y * scaleY,
      }));
    } else {
      window.detectedCorners = null;
    }

    updateUI(results);
    handleAutoCapture(results);

  } catch (err) {
    console.error('Feedback frame error:', err);
  } finally {
    if (src)  src.delete();
    if (gray) gray.delete();
  }
}

// ── Individual checks ─────────────────────────────────────────────────────── //

function checkCalibrationSheet(gray) {
  if (!arucoDetector) {
    return { status: 'warn', message: 'OpenCV loading…', corners: null };
  }

  let cornersVec = null, idsVec = null, rejectedVec = null;
  try {
    cornersVec   = new cv.MatVector();
    idsVec       = new cv.Mat();
    rejectedVec  = new cv.MatVector();

    // OpenCV 4.8 instance method (replaces old cv.detectMarkers free function)
    arucoDetector.detectMarkers(gray, cornersVec, idsVec, rejectedVec);

    const count = idsVec.rows;
    if (count < 4) {
      return {
        status:  count === 0 ? 'fail' : 'warn',
        message: count === 0 ? 'No calibration sheet' : `Sheet: ${count}/4 markers`,
        corners: null,
      };
    }

    // Extract centre of each marker
    const ids = [];
    for (let i = 0; i < idsVec.rows; i++) ids.push(idsVec.intAt(i, 0));

    if (!ids.includes(0) || !ids.includes(1) || !ids.includes(2) || !ids.includes(3)) {
      return { status: 'warn', message: 'Wrong marker IDs', corners: null };
    }

    const corners = ids.map((id, i) => {
      const c = cornersVec.get(i);
      const cx = (c.floatAt(0, 0) + c.floatAt(0, 2) + c.floatAt(0, 4) + c.floatAt(0, 6)) / 4;
      const cy = (c.floatAt(0, 1) + c.floatAt(0, 3) + c.floatAt(0, 5) + c.floatAt(0, 7)) / 4;
      c.delete();
      return { id, x: cx, y: cy };
    });

    return { status: 'pass', message: 'Sheet detected', corners };

  } catch (e) {
    return { status: 'fail', message: 'Detection error', corners: null };
  } finally {
    if (cornersVec)  cornersVec.delete();
    if (idsVec)      idsVec.delete();
    if (rejectedVec) rejectedVec.delete();
  }
}

function checkCameraAngle(corners) {
  if (!corners) return { status: 'fail', message: 'Need sheet first' };

  const byId = {};
  corners.forEach(c => { byId[c.id] = c; });
  if (!byId[0] || !byId[1]) return { status: 'warn', message: 'Checking angle…' };

  const dx = byId[1].x - byId[0].x;
  const dy = byId[1].y - byId[0].y;
  const angleDeg = Math.abs(Math.atan2(Math.abs(dy), Math.abs(dx)) * 180 / Math.PI);

  if (angleDeg < 10)  return { status: 'pass', message: 'Angle good' };
  if (angleDeg < 20)  return { status: 'warn', message: 'Hold camera straighter' };
  return { status: 'fail', message: 'Angle too steep — camera directly above' };
}

function checkScale(corners, frameW, frameH) {
  if (!corners) return { status: 'fail', message: 'Need sheet first' };

  const byId = {};
  corners.forEach(c => { byId[c.id] = c; });
  if (!byId[0] || !byId[1] || !byId[2]) return { status: 'warn', message: 'Checking distance…' };

  // Estimate sheet width in pixels
  const dx = byId[1].x - byId[0].x;
  const dy = byId[1].y - byId[0].y;
  const sheetWidthPx = Math.sqrt(dx * dx + dy * dy);

  const frameWidthPx = frameW;
  const ratio = sheetWidthPx / frameWidthPx;

  if (ratio < 0.35) return { status: 'fail', message: 'Move closer to the sheet' };
  if (ratio > 0.95) return { status: 'fail', message: 'Move back a little' };
  return { status: 'pass', message: 'Distance good' };
}

function checkKeyPresence(gray, corners, frameW, frameH) {
  if (!corners) return { status: 'fail', message: 'Need sheet first' };

  // Estimate approximate centre of the key zone based on marker positions
  const byId = {};
  corners.forEach(c => { byId[c.id] = c; });
  if (!byId[0] || !byId[1] || !byId[2] || !byId[3]) {
    return { status: 'warn', message: 'Place key on sheet' };
  }

  const cx = (byId[0].x + byId[1].x + byId[2].x + byId[3].x) / 4;
  const cy = (byId[0].y + byId[1].y + byId[2].y + byId[3].y) / 4;

  // Crop a small region around the centre
  const zoneW = Math.round(frameW * 0.3);
  const zoneH = Math.round(frameH * 0.2);
  const x0 = Math.max(0, Math.round(cx - zoneW / 2));
  const y0 = Math.max(0, Math.round(cy - zoneH / 2));
  const x1 = Math.min(gray.cols - 1, x0 + zoneW);
  const y1 = Math.min(gray.rows - 1, y0 + zoneH);

  let zone = null, thresh = null;
  try {
    const rect = new cv.Rect(x0, y0, x1 - x0, y1 - y0);
    zone   = gray.roi(rect);
    thresh = new cv.Mat();
    cv.threshold(zone, thresh, 0, 255, cv.THRESH_BINARY_INV + cv.THRESH_OTSU);

    const darkPixels = cv.countNonZero(thresh);
    const totalPixels = (x1 - x0) * (y1 - y0);
    const darkRatio = darkPixels / totalPixels;

    if (darkRatio > 0.08) return { status: 'pass', message: 'Key detected' };
    return { status: 'warn', message: 'Place key in rectangle' };

  } finally {
    if (zone)   zone.delete();
    if (thresh) thresh.delete();
  }
}

function checkLighting(gray) {
  const mean = cv.mean(gray);
  const brightness = mean[0];

  if (brightness < 60)  return { status: 'warn', message: 'Too dark — move to better light' };
  if (brightness > 210) return { status: 'warn', message: 'Too bright — avoid direct light' };
  return { status: 'pass', message: 'Lighting good' };
}

function checkFocus(gray) {
  let laplacian = null;
  try {
    laplacian = new cv.Mat();
    cv.Laplacian(gray, laplacian, cv.CV_64F);

    // Variance of Laplacian — higher = sharper
    const mean   = new cv.Mat();
    const stddev = new cv.Mat();
    cv.meanStdDev(laplacian, mean, stddev);
    const variance = Math.pow(stddev.doubleAt(0, 0), 2);
    mean.delete();
    stddev.delete();

    if (variance < 50)  return { status: 'warn', message: 'Hold still — blurry' };
    return { status: 'pass', message: 'Sharp focus' };

  } finally {
    if (laplacian) laplacian.delete();
  }
}

// ── UI updates ────────────────────────────────────────────────────────────── //

const CHECK_NAMES = ['sheet', 'angle', 'scale', 'key', 'lighting', 'focus'];

function updateUI(results) {
  let passCount = 0;

  CHECK_NAMES.forEach(name => {
    const r   = results[name];
    const el  = document.getElementById(`check-${name}`);
    if (!el) return;

    el.className = `check-item ${r.status}`;
    el.querySelector('.check-label').textContent = r.message || name;
    if (r.status === 'pass') passCount++;
  });

  // Progress bar
  const bar = document.getElementById('progress-bar');
  if (bar) {
    const pct = (passCount / CHECK_NAMES.length) * 100;
    bar.style.width = pct + '%';
    bar.style.background = passCount === CHECK_NAMES.length ? '#1A7A4A' : '#2E86C1';
  }

  // Capture button gate
  const btn = document.getElementById('btn-capture');
  const msg = document.getElementById('capture-message');
  if (btn) {
    const allPass = passCount === CHECK_NAMES.length;
    btn.disabled = !allPass;
    if (msg) {
      msg.textContent = allPass
        ? 'Ready — tap to capture'
        : `${passCount}/${CHECK_NAMES.length} checks passing`;
    }
  }

  // Draw overlay
  if (typeof drawOverlay === 'function') {
    drawOverlay();
  }
}

// ── Auto-capture ──────────────────────────────────────────────────────────── //

function handleAutoCapture(results) {
  const allPass = CHECK_NAMES.every(n => results[n].status === 'pass');

  if (allPass) {
    stableFrameCount++;
    const remaining = STABLE_FRAMES_REQUIRED - stableFrameCount;
    _showCountdown(remaining > 0 ? remaining : 0);

    if (stableFrameCount >= STABLE_FRAMES_REQUIRED) {
      stableFrameCount = 0;
      _hideCountdown();
      // Trigger capture via wizard.js
      if (typeof triggerCapture === 'function') triggerCapture();
    }
  } else {
    stableFrameCount = 0;
    _hideCountdown();
  }
}

function _showCountdown(n) {
  const overlay = document.getElementById('countdown-overlay');
  const num     = document.getElementById('countdown-number');
  if (!overlay || !num) return;
  overlay.classList.remove('hidden');
  num.textContent = n > 0 ? n : '📸';
}

function _hideCountdown() {
  const overlay = document.getElementById('countdown-overlay');
  if (overlay) overlay.classList.add('hidden');
}
