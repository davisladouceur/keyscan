/**
 * camera.js — getUserMedia camera stream management.
 *
 * Requests the rear camera (environment-facing), starts the video stream,
 * and exposes capturePhoto() which grabs a JPEG blob from the current frame.
 */

'use strict';

let mediaStream = null;
let _torchOn     = false;   // current torch state

/**
 * Start the camera and attach it to the video element.
 * Prefers the rear-facing camera on mobile devices.
 *
 * @returns {Promise<void>}
 */
async function startCamera() {
  const video = document.getElementById('video');

  // Request rear camera first, fall back to any camera
  const constraints = {
    video: {
      facingMode: { ideal: 'environment' },
      width:  { ideal: 1920 },
      height: { ideal: 1080 },
    },
    audio: false,
  };

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
  } catch (err) {
    // Fall back to any camera if environment-facing fails
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: false,
    });
  }

  video.srcObject = mediaStream;
  await new Promise(resolve => {
    video.onloadedmetadata = resolve;
  });
  await video.play();

  // Show the torch button only if this device supports it
  _torchOn = false;
  const torchBtn = document.getElementById('btn-torch');
  if (torchBtn) {
    torchBtn.style.display = _isTorchSupported() ? 'flex' : 'none';
    torchBtn.classList.remove('torch-on');
    torchBtn.title = 'Turn on flashlight';
  }
}

/** Stop all camera tracks (torch turns off automatically with the track). */
function stopCamera() {
  _torchOn = false;
  const torchBtn = document.getElementById('btn-torch');
  if (torchBtn) {
    torchBtn.classList.remove('torch-on');
    torchBtn.style.display = 'none';
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
}

/**
 * Toggle the device flashlight / torch on or off.
 * No-op if the device does not support torch or no stream is active.
 *
 * @returns {Promise<boolean>} Resolves to the new torch state (true = on).
 */
async function toggleTorch() {
  const track = mediaStream?.getVideoTracks()[0];
  if (!track || !_isTorchSupported()) return _torchOn;

  _torchOn = !_torchOn;
  try {
    await track.applyConstraints({ advanced: [{ torch: _torchOn }] });
  } catch (err) {
    // Some browsers accept the constraint but still fail at runtime
    console.warn('Torch constraint failed:', err);
    _torchOn = false;
  }

  // Update button appearance
  const torchBtn = document.getElementById('btn-torch');
  if (torchBtn) {
    torchBtn.classList.toggle('torch-on', _torchOn);
    torchBtn.title = _torchOn ? 'Turn off flashlight' : 'Turn on flashlight';
  }

  return _torchOn;
}

/**
 * Check whether the active camera track supports the torch constraint.
 * @returns {boolean}
 */
function _isTorchSupported() {
  const track = mediaStream?.getVideoTracks()[0];
  if (!track) return false;
  // getCapabilities() may be undefined on some browsers
  const caps = track.getCapabilities?.() || {};
  return !!caps.torch;
}

/**
 * Capture the current video frame as a JPEG Blob.
 *
 * @returns {Promise<{blob: Blob, url: string}>}
 */
async function capturePhoto() {
  const video  = document.getElementById('video');
  const canvas = document.createElement('canvas');

  canvas.width  = video.videoWidth;
  canvas.height = video.videoHeight;

  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0);

  // Flash animation
  _flashAnimation();

  // Play shutter sound
  _playShutterSound();

  return new Promise(resolve => {
    canvas.toBlob(blob => {
      resolve({ blob, url: URL.createObjectURL(blob) });
    }, 'image/jpeg', 0.92);
  });
}

/** Brief white flash to confirm capture. */
function _flashAnimation() {
  const flash = document.createElement('div');
  flash.style.cssText = `
    position: fixed;
    inset: 0;
    background: white;
    opacity: 0.8;
    pointer-events: none;
    z-index: 9999;
    animation: flash-out 0.3s ease-out forwards;
  `;

  // Add keyframe dynamically
  if (!document.getElementById('flash-style')) {
    const style = document.createElement('style');
    style.id = 'flash-style';
    style.textContent = '@keyframes flash-out { to { opacity: 0; } }';
    document.head.appendChild(style);
  }

  document.body.appendChild(flash);
  setTimeout(() => flash.remove(), 350);
}

/** Synthesise a shutter click using the Web Audio API. */
function _playShutterSound() {
  try {
    const ctx  = new (window.AudioContext || window.webkitAudioContext)();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.frequency.setValueAtTime(1200, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(400, ctx.currentTime + 0.08);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.12);

    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.12);
  } catch (e) {
    // Audio is non-critical — ignore errors silently
  }
}
