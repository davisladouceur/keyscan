/**
 * results.js — Display the analysis results on the results screen.
 *
 * Renders:
 *  - Key blank identification + manufacturer
 *  - Bitting code display
 *  - Per-cut confidence bars
 *  - Human review notice if flagged
 *  - CNC instruction string
 */

'use strict';

/**
 * Render the analysis results into #results-content.
 *
 * @param {Object} data - API response from GET /orders/{id}
 */
function renderResults(data) {
  const content = document.getElementById('results-content');
  if (!content) return;

  const {
    blank_code,
    bitting,
    overall_confidence,
    human_review,
    phase1_result,
    phase3_result,
  } = data;

  const manufacturer = phase1_result?.manufacturer || blank_code;
  const confidence   = overall_confidence || 0;
  const flags        = phase3_result?.flags || [];
  const cutDetails   = data.phase3_result?.cut_validations || _buildFallbackCuts(bitting);

  const html = `
    <!-- Key identification card -->
    <div class="result-card">
      <div class="result-header">
        <div class="result-icon">🔑</div>
        <div>
          <div class="result-title">${_escHtml(blank_code || 'Unknown')} — ${_escHtml(manufacturer)}</div>
          <div class="result-subtitle">Key blank identified with ${_pct(confidence)} confidence</div>
        </div>
      </div>

      <div class="bitting-display">${(bitting || []).join(' ')}</div>
      <div class="result-subtitle" style="text-align:center; margin-bottom: 12px;">Bitting code</div>

      <!-- Per-cut confidence bars -->
      <div class="cut-bars">
        ${_renderCutBars(cutDetails, bitting)}
      </div>
    </div>

    <!-- CNC instruction card -->
    <div class="result-card">
      <div class="result-title" style="margin-bottom: 8px;">CNC Instruction</div>
      <div style="font-family: monospace; font-size: 18px; color: #1A7A4A; font-weight: 700;">
        ${_escHtml(blank_code)},${(bitting || []).join('')}
      </div>
      <p style="font-size: 13px; color: #888; margin-top: 6px;">
        Share this code with your locksmith or key cutting service.
      </p>
    </div>

    <!-- Human review notice -->
    ${human_review ? `
    <div class="review-notice">
      <strong>⚠️ Human Review Required</strong><br>
      One or more cuts were ambiguous. Our team will review your order before cutting.
      ${flags.length ? `<br><br>${flags.map(f => `• ${_escHtml(f)}`).join('<br>')}` : ''}
    </div>
    ` : `
    <div class="result-card" style="border: 2px solid #1A7A4A;">
      <div style="color: #1A7A4A; font-weight: 700;">✓ Auto-approved</div>
      <p style="margin-top: 4px;">All cuts measured with high confidence. Ready for CNC cutting.</p>
    </div>
    `}
  `;

  content.innerHTML = html;
}

// ── Helpers ───────────────────────────────────────────────────────────────── //

function _renderCutBars(cutDetails, bitting) {
  if (!bitting || !bitting.length) return '<p>No bitting data</p>';

  return bitting.map((code, i) => {
    const detail = cutDetails[i];
    const conf   = detail?.confidence ?? 0.5;
    const cls    = conf >= 0.8 ? 'conf-high' : conf >= 0.5 ? 'conf-mid' : 'conf-low';

    return `
      <div class="confidence-bar-row">
        <div class="confidence-bar-label">Cut ${i + 1}</div>
        <div class="confidence-bar-track">
          <div class="confidence-bar-fill ${cls}" style="width:${Math.round(conf * 100)}%"></div>
        </div>
        <div class="confidence-bar-value" style="color: #333;">${code}</div>
      </div>
    `;
  }).join('');
}

function _buildFallbackCuts(bitting) {
  if (!bitting) return [];
  return bitting.map((code, i) => ({
    position: i + 1,
    final_code: code,
    confidence: 0.75,
  }));
}

function _pct(val) {
  return `${Math.round((val || 0) * 100)}%`;
}

function _escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
