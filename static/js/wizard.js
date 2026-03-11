/**
 * wizard.js — Multi-step flow manager.
 *
 * Screens (in order):
 *   1. loading        — Wait for opencv.js
 *   2. instructions   — How it works + calibration sheet download
 *   3. camera         — Live capture: 3 photos of Side A, then Side B (6 total)
 *   4. review         — Preview all photos before submitting
 *   5. analyzing      — Spinner while backend identifies blank
 *   6. confirm        — Show system prediction; user confirms or picks actual blank
 *   7. analyzing      — Spinner while backend measures bitting
 *   8. results        — Display bitting result
 */

'use strict';

// ── State ────────────────────────────────────────────────────────────────── //

const capturedPhotos = [];   // [{blob, url}, ...]
const MAX_PHOTOS     = 6;    // 3 per side

let opencvReady          = false;
let pollTimer            = null;
let selectedConfirmBlank = null;   // blank code chosen on confirm screen

// ── Initialise ────────────────────────────────────────────────────────────── //

document.addEventListener('DOMContentLoaded', () => {
  showScreen('loading');
  document.getElementById('loading-message').textContent = 'Loading camera system…';

  document.getElementById('btn-start-camera').addEventListener('click', enterCameraScreen);
  document.getElementById('btn-capture').addEventListener('click', () => triggerCapture());
  document.getElementById('btn-retake').addEventListener('click', enterCameraScreen);
  document.getElementById('btn-submit').addEventListener('click', submitPhotos);
  document.getElementById('btn-new-key').addEventListener('click', resetWizard);

  const flipBtn = document.getElementById('btn-flip-continue');
  if (flipBtn) flipBtn.addEventListener('click', _hideFlipOverlay);
});

document.addEventListener('opencv-ready', () => {
  opencvReady = true;
  setTimeout(() => showScreen('instructions'), 400);
});

setTimeout(() => {
  if (!opencvReady) {
    opencvReady = true;
    showScreen('instructions');
  }
}, 8000);

// ── Screen navigation ─────────────────────────────────────────────────────── //

function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const target = document.getElementById(`${name}-screen`);
  if (target) target.classList.add('active');
}

async function enterCameraScreen() {
  capturedPhotos.length = 0;
  _resetThumbnails();
  _hideFlipOverlay();

  showScreen('camera');

  try {
    await startCamera();
    const videoEl  = document.getElementById('video');
    const canvasEl = document.getElementById('overlay-canvas');
    if (opencvReady) startFeedbackLoop(videoEl, canvasEl);
  } catch (err) {
    alert('Camera access is required. Please allow camera permission and try again.');
    showScreen('instructions');
  }
}

function enterReviewScreen() {
  stopCamera();
  stopFeedbackLoop();
  showScreen('review');
  _renderPhotoGrid();
}

// ── Capture ───────────────────────────────────────────────────────────────── //

async function triggerCapture() {
  if (capturedPhotos.length >= MAX_PHOTOS) return;

  const { blob, url } = await capturePhoto();
  capturedPhotos.push({ blob, url });
  const idx = capturedPhotos.length - 1;

  // Update thumbnail
  const thumb = document.getElementById(`photo-thumb-${idx}`);
  if (thumb) {
    thumb.classList.add('captured');
    thumb.classList.remove('side2');  // remove dim class
    const img = document.createElement('img');
    img.src = url;
    thumb.innerHTML = '';
    thumb.appendChild(img);
  }

  // After Side A complete → show flip overlay
  if (capturedPhotos.length === 3) {
    stopFeedbackLoop();
    setTimeout(_showFlipOverlay, 500);
    return;
  }

  // After both sides complete → go to review
  if (capturedPhotos.length >= MAX_PHOTOS) {
    setTimeout(enterReviewScreen, 700);
  }
}

// ── Flip overlay ──────────────────────────────────────────────────────────── //

function _showFlipOverlay() {
  const overlay = document.getElementById('flip-overlay');
  if (overlay) overlay.classList.remove('hidden');
}

function _hideFlipOverlay() {
  const overlay = document.getElementById('flip-overlay');
  if (overlay) overlay.classList.add('hidden');

  // Resume feedback loop for Side B if camera is still running
  if (capturedPhotos.length === 3) {
    const videoEl  = document.getElementById('video');
    const canvasEl = document.getElementById('overlay-canvas');
    if (opencvReady) startFeedbackLoop(videoEl, canvasEl);
  }
}

// ── Submit (Phase A: Identify) ────────────────────────────────────────────── //

async function submitPhotos() {
  showScreen('analyzing');
  const statusEl = document.getElementById('poll-status');
  if (statusEl) statusEl.textContent = 'Measuring key geometry…';

  const email = document.getElementById('email-input')?.value || '';
  const form  = new FormData();
  capturedPhotos.forEach((p, i) => {
    form.append('photos', p.blob, `photo_${i}.jpg`);
  });
  if (email) form.append('email', email);

  let orderId;
  try {
    const res  = await fetch('/analyze', { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload failed');
    orderId = data.order_id;
  } catch (err) {
    _showAnalysisError(err.message);
    return;
  }

  _pollForResults(orderId);
}

// ── Polling ───────────────────────────────────────────────────────────────── //

function _pollForResults(orderId) {
  let attempts = 0;
  const MAX_ATTEMPTS = 60;
  const statusEl = document.getElementById('poll-status');

  pollTimer = setInterval(async () => {
    attempts++;
    if (statusEl) statusEl.textContent = `Checking… (${attempts}/${MAX_ATTEMPTS})`;

    if (attempts > MAX_ATTEMPTS) {
      clearInterval(pollTimer);
      _showAnalysisError('Analysis is taking longer than expected. Please try again.');
      return;
    }

    try {
      const res  = await fetch(`/orders/${orderId}`);
      const data = await res.json();

      if (data.status === 'analyzing' || data.status === 'pending') return;

      if (data.status === 'identified') {
        clearInterval(pollTimer);
        enterConfirmScreen(orderId, data.identify_result || {});
        return;
      }

      if (data.status === 'measuring') return;

      clearInterval(pollTimer);

      if (data.status === 'error' || data.status === 'rejected') {
        _showAnalysisError(data.reason || 'Analysis failed. Please try with better photos.');
        return;
      }

      showScreen('results');
      if (typeof renderResults === 'function') renderResults(data);

    } catch (err) {
      // Network error — keep polling
    }
  }, 2000);
}

// ── Confirm Screen (Phase B: Measure) + Training Mode ────────────────────── //

function enterConfirmScreen(orderId, identifyResult) {
  const candidates = identifyResult.candidates || [];
  const container  = document.getElementById('confirm-candidates');
  if (!container) return;

  if (candidates.length === 0) {
    container.innerHTML = '<p style="text-align:center;color:#888">No matching blanks found — please retake photos.</p>';
    showScreen('confirm');
    return;
  }

  const topCandidate = candidates[0];

  // ── Subtitle ──────────────────────────────────────────────────────────── //
  const subtitleEl = document.getElementById('confirm-subtitle');
  if (subtitleEl) {
    if (topCandidate.stamp_confirmed) {
      subtitleEl.textContent = 'Stamp detected on your key bow — confidence is high.';
    } else if (
      candidates.length > 1 &&
      Math.abs((candidates[0].match_score || 0) - (candidates[1].match_score || 0)) < 0.01
    ) {
      subtitleEl.textContent = 'Multiple blanks share the same blade geometry — select the one that matches your key\'s bow shape.';
    } else {
      subtitleEl.textContent = 'Based on geometric measurement of your key.';
    }
  }

  // ── System prediction badge ───────────────────────────────────────────── //
  const measurements = identifyResult.measurements || {};
  const predBadge = measurements.cut_count
    ? `<div class="system-prediction">
        System measured <strong>${measurements.cut_count} cuts</strong>,
        spacing <strong>${(measurements.approx_spacing_mm || 0).toFixed(2)}mm</strong>,
        first cut <strong>${(measurements.approx_first_cut_mm || 0).toFixed(2)}mm</strong>
        → top match: <strong>${topCandidate.blank_code}</strong>
       </div>`
    : '';

  // ── Candidate rows ────────────────────────────────────────────────────── //
  let rowsHtml = '<div class="confirm-candidate-list">';
  candidates.forEach((c, i) => {
    const isTop    = i === 0;
    const desc     = c.reference_description ? _escHtml(c.reference_description) : '';
    const cuts     = c.cut_count ? `${c.cut_count} cuts` : '';
    const matchStr = c.stamp_confirmed
      ? ''
      : (c.match_score !== undefined && c.match_score < 90
          ? `· score ${c.match_score.toFixed(2)}`
          : '· manual selection');
    const stampBadge = c.stamp_confirmed
      ? `<span class="confirm-stamp-badge">✓ Stamp read from key</span>`
      : '';

    rowsHtml += `
      <div class="confirm-candidate${isTop ? ' top-pick selected' : ''}"
           data-blank="${_escHtml(c.blank_code)}"
           onclick="_selectCandidate('${_escHtml(c.blank_code)}')">
        <div class="confirm-radio${isTop ? ' checked' : ''}"></div>
        <div class="confirm-candidate-info">
          <div class="confirm-blank-code">${_escHtml(c.blank_code)}</div>
          ${desc ? `<div class="confirm-blank-desc">${desc}</div>` : ''}
          <div class="confirm-blank-meta">${cuts}${matchStr}</div>
          ${stampBadge}
        </div>
      </div>`;
  });
  rowsHtml += '</div>';

  // ── "Other" training input ────────────────────────────────────────────── //
  const otherHtml = `
    <div class="training-other">
      <label class="training-label">Not listed? Enter actual blank code:</label>
      <input type="text" id="other-blank-input" class="other-blank-input"
             placeholder="e.g. SC4, KW10, WR3"
             oninput="_onOtherBlankInput(this.value)" />
    </div>`;

  container.innerHTML = predBadge + rowsHtml + otherHtml;

  selectedConfirmBlank = topCandidate.blank_code;

  // Wire confirm button
  const confirmBtn = document.getElementById('btn-confirm-blank');
  if (confirmBtn) {
    const fresh = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(fresh, confirmBtn);
    fresh.addEventListener('click', () => _confirmAndMeasure(orderId, topCandidate.blank_code));
  }

  showScreen('confirm');
}

function _selectCandidate(blankCode) {
  selectedConfirmBlank = blankCode;
  // Clear the "Other" text input when a card is tapped
  const otherInput = document.getElementById('other-blank-input');
  if (otherInput) otherInput.value = '';

  document.querySelectorAll('.confirm-candidate').forEach(el => {
    const isSelected = el.dataset.blank === blankCode;
    el.classList.toggle('selected', isSelected);
    const radio = el.querySelector('.confirm-radio');
    if (radio) radio.classList.toggle('checked', isSelected);
  });
}

function _onOtherBlankInput(value) {
  const trimmed = value.trim().toUpperCase();
  if (trimmed.length >= 2) {
    // Deselect all candidate cards
    document.querySelectorAll('.confirm-candidate').forEach(el => {
      el.classList.remove('selected');
      const radio = el.querySelector('.confirm-radio');
      if (radio) radio.classList.remove('checked');
    });
    selectedConfirmBlank = trimmed;
  } else if (!trimmed) {
    // Restore top candidate if input is cleared
    const topCard = document.querySelector('.confirm-candidate.top-pick');
    if (topCard) {
      topCard.classList.add('selected');
      topCard.querySelector('.confirm-radio')?.classList.add('checked');
      selectedConfirmBlank = topCard.dataset.blank;
    }
  }
}

async function _confirmAndMeasure(orderId, systemPrediction) {
  if (!selectedConfirmBlank) return;

  showScreen('analyzing');
  const statusEl = document.getElementById('poll-status');
  if (statusEl) statusEl.textContent = 'Measuring cut depths…';

  try {
    const form = new FormData();
    form.append('confirmed_blank', selectedConfirmBlank);
    // Log training data: what the system predicted vs what user selected
    if (systemPrediction && systemPrediction !== selectedConfirmBlank) {
      console.log(`[training] System predicted: ${systemPrediction}, User corrected to: ${selectedConfirmBlank}`);
      form.append('system_prediction', systemPrediction);
    }

    const res = await fetch(`/orders/${orderId}/confirm`, { method: 'POST', body: form });
    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.detail || 'Confirmation failed');
    }
  } catch (err) {
    _showAnalysisError(err.message);
    return;
  }

  _pollForResults(orderId);
}

// ── Error ─────────────────────────────────────────────────────────────────── //

function _showAnalysisError(message) {
  showScreen('results');
  const content = document.getElementById('results-content');
  if (content) {
    content.innerHTML = `
      <div class="error-card">
        <h2>Something went wrong</h2>
        <p>${_escHtml(message)}</p>
        <p>Please take new photos with better lighting and try again.</p>
      </div>
    `;
  }
}

// ── Reset ─────────────────────────────────────────────────────────────────── //

function resetWizard() {
  capturedPhotos.length = 0;
  selectedConfirmBlank = null;
  if (pollTimer) clearInterval(pollTimer);
  showScreen('instructions');
}

// ── Helpers ───────────────────────────────────────────────────────────────── //

function _resetThumbnails() {
  for (let i = 0; i < MAX_PHOTOS; i++) {
    const thumb = document.getElementById(`photo-thumb-${i}`);
    if (thumb) {
      thumb.className = `photo-thumb empty${i >= 3 ? ' side2' : ''}`;
      thumb.textContent = (i + 1).toString();
    }
  }
}

function _renderPhotoGrid() {
  const grid = document.getElementById('photo-grid');
  if (!grid) return;
  grid.innerHTML = capturedPhotos
    .map(p => `<img src="${p.url}" alt="Key photo" />`)
    .join('');
}

function _escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
