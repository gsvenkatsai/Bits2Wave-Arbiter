/**
 * tests/site/test_monitor_status.js
 *
 * Unit tests for the monitor engine status logic using Node's native test runner (node --test).
 * Validates the three mutually exclusive states:
 * 1. SERVER OFFLINE: server health probe failed
 * 2. ENGINE IDLE: server healthy but frames are not advancing
 * 3. ENGINE LIVE: server healthy and simulation frames are advancing (currentTick > previousTick)
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { determineEngineStatus, getStatusDescription, formatFooterStats, getMockBannerText } from '../../arbiter/dashboard/site/js/monitor.js';

test('determineEngineStatus — returns "SERVER OFFLINE" when server is unhealthy', () => {
  const result1 = determineEngineStatus({ serverHealthy: false, previousTick: null, currentTick: null });
  assert.equal(result1, 'SERVER OFFLINE');

  const result2 = determineEngineStatus({ serverHealthy: false, previousTick: 10, currentTick: 20 });
  assert.equal(result2, 'SERVER OFFLINE');

  const desc = getStatusDescription(result1, null);
  assert.match(desc, /Unable to reach API server/);
});

test('determineEngineStatus — returns "ENGINE IDLE" when server is up but frames are stationary or null', () => {
  // Case A: Initial connection with no frames
  const result1 = determineEngineStatus({ serverHealthy: true, previousTick: null, currentTick: null });
  assert.equal(result1, 'ENGINE IDLE');

  // Case B: Tick is zero and unchanged
  const result2 = determineEngineStatus({ serverHealthy: true, previousTick: 0, currentTick: 0 });
  assert.equal(result2, 'ENGINE IDLE');

  // Case C: Simulation paused (tick remains stationary at 45)
  const result3 = determineEngineStatus({ serverHealthy: true, previousTick: 45, currentTick: 45 });
  assert.equal(result3, 'ENGINE IDLE');

  const desc = getStatusDescription(result2, 0);
  assert.match(desc, /No active simulation frames streaming/);
});

test('determineEngineStatus — returns "ENGINE LIVE" ONLY when frames are strictly advancing', () => {
  // Case A: Normal tick advance from 1 to 2
  const result1 = determineEngineStatus({ serverHealthy: true, previousTick: 1, currentTick: 2 });
  assert.equal(result1, 'ENGINE LIVE');

  // Case B: Multi-tick batch advance from 50 to 55
  const result2 = determineEngineStatus({ serverHealthy: true, previousTick: 50, currentTick: 55 });
  assert.equal(result2, 'ENGINE LIVE');

  const desc = getStatusDescription(result1, 2);
  assert.match(desc, /Discrete-event simulator actively ticking/);
});

test('determineEngineStatus — never shows "ENGINE LIVE" on backward scrub or stationary ticks', () => {
  // Scrubbing backward in ring buffer
  const resultBackward = determineEngineStatus({ serverHealthy: true, previousTick: 30, currentTick: 20 });
  assert.equal(resultBackward, 'ENGINE IDLE', 'Backward scrubbing must not be classified as live');

  // First frame arrival without previous tick
  const resultFirst = determineEngineStatus({ serverHealthy: true, previousTick: null, currentTick: 10 });
  assert.equal(resultFirst, 'ENGINE IDLE', 'Single static frame must not report live until advancement is confirmed');
});

test('formatFooterStats — extracts test count and git sha from evidence or defaults to "--"', () => {
  // Case A: Missing or empty evidence
  const emptyRes = formatFooterStats(null);
  assert.deepEqual(emptyRes, { testCount: '--', gitSha: '--' });

  // Case B: Partial evidence (missing test count)
  const partialRes = formatFooterStats({ git_commit: '1cbfd2b614f9fa1d6e109518f16286d401c3cf7a' });
  assert.deepEqual(partialRes, { testCount: '--', gitSha: '1cbfd2b' });

  // Case C: Full evidence
  const fullRes = formatFooterStats({
    git_commit: '1cbfd2b614f9fa1d6e109518f16286d401c3cf7a',
    metrics: { suite_test_count: { value: 535 } }
  });
  assert.deepEqual(fullRes, { testCount: '535', gitSha: '1cbfd2b' });
});

test('getMockBannerText — returns persistent amber banner text for scripted twin and mock sink', () => {
  assert.equal(getMockBannerText(null), null);
  assert.equal(getMockBannerText({ twin_kind: 'real', sink: 'queue' }), null);

  assert.equal(getMockBannerText({ twin_kind: 'scripted', sink: 'queue' }), 'SCRIPTED TWIN (TEST MOCK)');
  assert.equal(getMockBannerText({ twin_kind: 'real', sink: 'mock' }), 'MOCK PROPOSAL SINK');
  assert.equal(
    getMockBannerText({ twin_kind: 'scripted', sink: 'mock' }),
    'SCRIPTED TWIN (TEST MOCK) • MOCK PROPOSAL SINK'
  );
});
