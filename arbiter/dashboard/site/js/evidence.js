/**
 * PROJECT ARBITER — Evidence & Quantitative Proofs Loader
 * Module: arbiter/dashboard/site/js/evidence.js
 *
 * Provides deterministic helpers to load evidence.json and team.json,
 * formatting values with real units or rendering "--" when missing.
 * Strictly zero invented numbers or hardcoded claims.
 */

/**
 * Format metric value with units, or return "--" when absent/missing.
 * @param {Object|null} metric - The metric object from evidence.json
 * @param {Object} [options] - Formatting options (decimals, showUnit)
 * @returns {string} Formatted value or "--"
 */
export function formatMetricValue(metric, options = {}) {
  if (!metric || metric.value === undefined || metric.value === null) {
    return '--';
  }
  const val = metric.value;
  if (typeof val === 'number') {
    if (isNaN(val)) return '--';
    const decimals = options.decimals !== undefined ? options.decimals : (Number.isInteger(val) ? 0 : 2);
    const formattedNum = val.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    if (options.showUnit && metric.unit) {
      return `${formattedNum} ${metric.unit}`;
    }
    return formattedNum;
  }
  return String(val);
}

/**
 * Format metric unit, or return "" when absent.
 * @param {Object|null} metric
 * @returns {string}
 */
export function formatMetricUnit(metric) {
  if (!metric || !metric.unit) return '';
  return String(metric.unit);
}

/**
 * Get metric status chip label ("measured" or "target").
 * @param {Object|null} metric
 * @returns {"measured"|"target"}
 */
export function getMetricStatus(metric) {
  if (!metric || !metric.status) return 'target';
  return metric.status.toLowerCase() === 'measured' ? 'measured' : 'target';
}

/**
 * Safely retrieve a metric by key from the evidence store.
 * @param {Object|null} evidence - The root evidence object
 * @param {string} key - The metric dictionary key
 * @returns {Object|null}
 */
export function getMetric(evidence, key) {
  if (!evidence || !evidence.metrics || !evidence.metrics[key]) {
    return null;
  }
  return evidence.metrics[key];
}

/**
 * Load evidence data via fetch. Works offline relative to current page.
 * @param {string} [path]
 * @returns {Promise<Object>}
 */
export async function loadEvidence(path = './data/evidence.json') {
  try {
    const res = await fetch(path);
    if (!res.ok) {
      throw new Error(`Failed to load evidence: HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    console.warn('[Evidence] Could not load evidence.json, falling back to empty state:', err);
    return { metrics: {} };
  }
}

/**
 * Load team data via fetch.
 * @param {string} [path]
 * @returns {Promise<Object>}
 */
export async function loadTeam(path = './data/team.json') {
  try {
    const res = await fetch(path);
    if (!res.ok) {
      throw new Error(`Failed to load team: HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    console.warn('[Evidence] Could not load team.json:', err);
    return { members: [] };
  }
}

/**
 * Populate DOM stat tiles that contain `data-metric="<key>"`.
 * Populates `.stat-value`, `.stat-unit`, and `.stat-chip`.
 * If metric is missing, safely populates value with "--".
 * @param {HTMLElement|Document} root
 * @param {Object} evidence
 */
export function populateEvidenceDOM(root, evidence) {
  if (!root || !root.querySelectorAll) return;
  const elements = root.querySelectorAll('[data-metric]');
  elements.forEach((el) => {
    const key = el.getAttribute('data-metric');
    const metric = getMetric(evidence, key);

    const valEl = el.querySelector('.stat-val') || el.querySelector('.stat-value');
    const unitEl = el.querySelector('.stat-unit');
    const chipEl = el.querySelector('.stat-chip');

    if (valEl) {
      valEl.textContent = formatMetricValue(metric);
    }
    if (unitEl) {
      unitEl.textContent = formatMetricUnit(metric);
    }
    if (chipEl) {
      const status = getMetricStatus(metric);
      chipEl.textContent = status.toUpperCase();
      chipEl.className = `stat-chip chip-${status}`;
    }
  });
}
