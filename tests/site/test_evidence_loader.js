/**
 * tests/site/test_evidence_loader.js
 * Unit tests for arbiter/dashboard/site/js/evidence.js using Node.js test runner.
 *
 * Verifies:
 * 1. Missing or undefined metrics deterministically render "--" with no invented claims.
 * 2. Proper number formatting with specified decimal precision and units.
 * 3. Status chip classification ('measured' vs 'target').
 * 4. DOM population fallback when keys are absent.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatMetricValue,
  formatMetricUnit,
  getMetricStatus,
  getMetric,
  populateEvidenceDOM,
} from '../../arbiter/dashboard/site/js/evidence.js';

test('formatMetricValue — missing or absent values render "--"', () => {
  assert.equal(formatMetricValue(null), '--');
  assert.equal(formatMetricValue(undefined), '--');
  assert.equal(formatMetricValue({}), '--');
  assert.equal(formatMetricValue({ value: undefined }), '--');
  assert.equal(formatMetricValue({ value: null }), '--');
  assert.equal(formatMetricValue({ value: NaN }), '--');
});

test('formatMetricValue — formats numbers accurately with decimals and units', () => {
  assert.equal(formatMetricValue({ value: 3.55, unit: '%' }), '3.55');
  assert.equal(formatMetricValue({ value: 14, unit: 'users' }), '14');
  assert.equal(formatMetricValue({ value: 0, unit: 'drops' }), '0');
  assert.equal(formatMetricValue({ value: 3.82, unit: 'ms' }, { showUnit: true }), '3.82 ms');
  assert.equal(formatMetricValue({ value: 10, unit: 'ms' }, { decimals: 1, showUnit: true }), '10.0 ms');
});

test('formatMetricUnit — returns unit string or empty string', () => {
  assert.equal(formatMetricUnit(null), '');
  assert.equal(formatMetricUnit({}), '');
  assert.equal(formatMetricUnit({ unit: 'ms' }), 'ms');
  assert.equal(formatMetricUnit({ unit: '%' }), '%');
});

test('getMetricStatus — classifies measured vs target status chips', () => {
  assert.equal(getMetricStatus(null), 'target');
  assert.equal(getMetricStatus({}), 'target');
  assert.equal(getMetricStatus({ status: 'measured' }), 'measured');
  assert.equal(getMetricStatus({ status: 'MEASURED' }), 'measured');
  assert.equal(getMetricStatus({ status: 'target' }), 'target');
  assert.equal(getMetricStatus({ status: 'TARGET' }), 'target');
});

test('getMetric — retrieves metric dictionary entries safely', () => {
  const evidence = {
    metrics: {
      optimality_gap: { value: 3.55, unit: '%', status: 'measured' },
    },
  };

  assert.deepEqual(getMetric(evidence, 'optimality_gap'), {
    value: 3.55,
    unit: '%',
    status: 'measured',
  });
  assert.equal(getMetric(evidence, 'non_existent_key'), null);
  assert.equal(getMetric(null, 'optimality_gap'), null);
});

test('populateEvidenceDOM — renders real values or "--" for missing metrics into DOM nodes', () => {
  // Lightweight mock of DOM elements
  function createMockElement(metricKey) {
    const valEl = { textContent: '' };
    const unitEl = { textContent: '' };
    const chipEl = { textContent: '', className: '' };

    return {
      getAttribute: (attr) => (attr === 'data-metric' ? metricKey : null),
      querySelector: (selector) => {
        if (selector === '.stat-val' || selector === '.stat-value') return valEl;
        if (selector === '.stat-unit') return unitEl;
        if (selector === '.stat-chip') return chipEl;
        return null;
      },
      valEl,
      unitEl,
      chipEl,
    };
  }

  const elPresent = createMockElement('optimality_gap');
  const elMissing = createMockElement('uncollected_telemetry_metric');

  const mockRoot = {
    querySelectorAll: (selector) => {
      if (selector === '[data-metric]') return [elPresent, elMissing];
      return [];
    },
  };

  const evidence = {
    metrics: {
      optimality_gap: { value: 3.55, unit: '%', status: 'measured' },
    },
  };

  populateEvidenceDOM(mockRoot, evidence);

  // Present metric formatted
  assert.equal(elPresent.valEl.textContent, '3.55');
  assert.equal(elPresent.unitEl.textContent, '%');
  assert.equal(elPresent.chipEl.textContent, 'MEASURED');
  assert.equal(elPresent.chipEl.className, 'stat-chip chip-measured');

  // Missing metric safely renders "--"
  assert.equal(elMissing.valEl.textContent, '--');
  assert.equal(elMissing.unitEl.textContent, '');
  assert.equal(elMissing.chipEl.textContent, 'TARGET');
  assert.equal(elMissing.chipEl.className, 'stat-chip chip-target');
});
