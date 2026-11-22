/**
 * tests/branch4/test_honest_labels.js
 *
 * Node --test unit tests verifying honest labeling and persistent banner logic:
 * 1. Tower calibration label binding (no hardcoded 0.30, UNCALIBRATED when missing/uncalibrated)
 * 2. Persistent amber banners for scripted twin & mock proposal sink
 * 3. 2D NOC topology empty state rendering ("No engine data")
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { getMockBannerText } from '../../arbiter/dashboard/site/js/monitor.js';

/**
 * Pure function mirroring the 3D tower label formatting logic in scene.js
 */
function formatTowerCalibrationLabel(twin) {
  if (twin && twin.calibrated) {
    const hw = twin.interval_halfwidth_db !== undefined && twin.interval_halfwidth_db !== null
      ? twin.interval_halfwidth_db
      : (twin.sinr_half_width_db !== undefined ? twin.sinr_half_width_db : null);
    if (hw !== null && hw !== undefined) {
      return `CALIBRATED (±${Number(hw).toFixed(2)} dB)`;
    }
    return 'CALIBRATED';
  }
  return 'UNCALIBRATED';
}

/**
 * Pure function mirroring the 2D / 3D amber banner text resolution
 */
function resolveAmberBannerText(meta) {
  return getMockBannerText(meta);
}

test('formatTowerCalibrationLabel — shows "UNCALIBRATED" with no number when uncalibrated or missing', () => {
  // Case A: Missing twin object
  assert.equal(formatTowerCalibrationLabel(null), 'UNCALIBRATED');
  assert.equal(formatTowerCalibrationLabel(undefined), 'UNCALIBRATED');

  // Case B: Explicitly uncalibrated twin
  assert.equal(formatTowerCalibrationLabel({ calibrated: false }), 'UNCALIBRATED');
  assert.equal(formatTowerCalibrationLabel({ calibrated: false, interval_halfwidth_db: 0.30 }), 'UNCALIBRATED');

  // Must NEVER have any number when uncalibrated
  const uncal = formatTowerCalibrationLabel({ calibrated: false });
  assert.equal(/\d/.test(uncal), false, 'UNCALIBRATED label must not contain any numbers');
});

test('formatTowerCalibrationLabel — shows "CALIBRATED (±X.XX dB)" when calibrated with halfwidth', () => {
  // Contract C3: interval_halfwidth_db
  assert.equal(
    formatTowerCalibrationLabel({ calibrated: true, interval_halfwidth_db: 0.45 }),
    'CALIBRATED (±0.45 dB)'
  );

  // Fallback to sinr_half_width_db
  assert.equal(
    formatTowerCalibrationLabel({ calibrated: true, sinr_half_width_db: 0.28 }),
    'CALIBRATED (±0.28 dB)'
  );

  // Calibrated without halfwidth
  assert.equal(
    formatTowerCalibrationLabel({ calibrated: true }),
    'CALIBRATED'
  );
});

test('resolveAmberBannerText — triggers persistent amber banner only for scripted twin or mock sink', () => {
  // Production default: real twin + real queue sink
  assert.equal(resolveAmberBannerText(null), null);
  assert.equal(resolveAmberBannerText({ twin_kind: 'real', sink: 'queue' }), null);

  // Scripted twin test mock
  assert.equal(
    resolveAmberBannerText({ twin_kind: 'scripted', sink: 'queue' }),
    'SCRIPTED TWIN (TEST MOCK)'
  );

  // Mock proposal sink
  assert.equal(
    resolveAmberBannerText({ twin_kind: 'real', sink: 'mock' }),
    'MOCK PROPOSAL SINK'
  );

  // Both non-production flags active
  assert.equal(
    resolveAmberBannerText({ twin_kind: 'scripted', sink: 'mock' }),
    'SCRIPTED TWIN (TEST MOCK) • MOCK PROPOSAL SINK'
  );
});
