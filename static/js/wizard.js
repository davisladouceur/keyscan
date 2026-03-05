/**
 * wizard.js — Multi-step flow manager.
 *
 * Screens (in order):
 *   1. loading        — Wait for opencv.js
 *   2. instructions   — How it works + calibration sheet download
 *   3. blank          — User selects key blank type (KW1/SC1/SC4/M1/WR5 or auto)
 *   4. camera         — Live capture with real-time feedback (3 photos)
 *   5. review         — Preview all 3 photos before submitting
 *   6. analyzing      — Show spinner while backend processes
 *   7. results        — Display bitting result
 */

'use strict';

// ── State ────────────────────────────────────────────────────────────────── //

const capturedPhotos = [];   // [{blob, url}, ...]
const MAX_PHOTOS = 3;
let opencvReady  = false;
let pollTimer    = null;
let selectedBlank = null;    // blank code string (e.g. 'SC4'), '' = auto-detect, null = nothing chosen yet

// ── Initialise ────────────────────────────────────────────────────────────── //

document.addEventListener('DOMContentLoaded', () => {
  // Show loading screen until OpenCV is ready
  showScreen('loading');
  document.getElementById('loading-message').textContent = 'Loading camera system…';

  // Wire up buttons
  document.getElementById('btn-start-camera').addEventListener('click', enterBlankScreen);
  document.getElementById('btn-blank-continue').addEventListener('click', enterCameraScreen);
  document.getElementById('btn-capture').addEventListener('click', () => triggerCapture());
  document.getElementById('btn-retake').addEventListener('click', enterCameraScreen);
  document.getElementById('btn-submit').addEventListener('click', submitPhotos);
  document.getElementById('btn-new-key').addEventListener('click', resetWizard);

  // Wire up blank selection cards
  document.querySelectorAll('.blank-card').forEach(card => {
    card.addEventListener('click', () => {
      document.querySelectorAll('.blank-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      selectedBlank = card.dataset.blank;  // '' for auto-detect, 'SC4' etc. for explicit
      document.getElementById('btn-blank-continue').disabled = false;
    });
  });
});

document.addEventListener('opencv-ready', () => {
  opencvReady = true;
  // Short delay so OpenCV internal init can complete
  setTimeout(() => showScreen('instructions'), 400);
});

// If opencv.js fails to load after 8 seconds, continue anyway (feedback won't work)
setTimeout(() => {
  if (!opencvReady) {
    opencvReady = true; // Mark ready so the app still works
    showScreen('instructions');
  }
}, 8000);

// ── Screen navigation ─────────────────────────────────────────────────────── //

function showScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const target = document.getElementById(`${name}-screen`);
  if (target) target.classList.add('active');
}

function enterBlankScreen() {
  selectedBlank = null;
  document.querySelectorAll('.blank-card').forEach(c => c.classList.remove('selected'));
  document.getElementById('btn-blank-continue').disabled = true;
  showScreen('blank');
}

async function enterCameraScreen() {
  capturedPhotos.length = 0;
  _resetThumbnails();

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

/** Called by feedback.js auto-capture OR the manual capture button. */
async function triggerCapture() {
  if (capturedPhotos.length >= MAX_PHOTOS) return;

  const { blob, url } = await capturePhoto();
  capturedPhotos.push({ blob, url });

  // Update thumbnail
  const thumb = document.getElementById(`photo-thumb-${capturedPhotos.length - 1}`);
  if (thumb) {
    thumb.classList.add('captured');
    const img = document.createElement('img');
    img.src = url;
    thumb.innerHTML = '';
    thumb.appendChild(img);
  }

  if (capturedPhotos.length >= MAX_PHOTOS) {
    // Small delay so user sees the last thumbnail flash
    setTimeout(enterReviewScreen, 700);
  }
}

// ── Submit ────────────────────────────────────────────────────────────────── //

async function submitPhotos() {
  showScreen('analyzing');

  const email = document.getElementById('email-input')?.value || '';
  const form  = new FormData();
  capturedPhotos.forEach((p, i) => {
    form.append('photos', p.blob, `photo_${i}.jpg`);
  });
  if (email) form.append('email', email);
  // Pass the explicitly selected blank (non-empty string means user chose it)
  if (selectedBlank) form.append('blank_family', selectedBlank);

  let orderId;
  try {
    const res  = await fetch('/analyze', { method: 'POST', body: form });
    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || 'Upload failed');
    }

    orderId = data.order_id;
  } catch (err) {
    _showAnalysisError(err.message);
    return;
  }

  // Poll for results
  _pollForResults(orderId);
}

function _pollForResults(orderId) {
  let attempts = 0;
  const MAX_ATTEMPTS = 60;  // 60 × 2s = 2 minutes max

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

      if (data.status === 'analyzing' || data.status === 'pending') return; // Still working

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
  if (pollTimer) clearInterval(pollTimer);
  showScreen('instructions');
}

// ── Helpers ───────────────────────────────────────────────────────────────── //

function _resetThumbnails() {
  for (let i = 0; i < MAX_PHOTOS; i++) {
    const thumb = document.getElementById(`photo-thumb-${i}`);
    if (thumb) {
      thumb.className = 'photo-thumb empty';
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
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
