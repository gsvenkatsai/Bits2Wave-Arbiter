/**
 * PROJECT ARBITER — Operations Monitor Hub & Live Engine Prober
 * Module: arbiter/dashboard/site/js/monitor.js
 */

import { initNavbar, injectIcons } from './components.js';

/**
 * Pure function determining the engine status from health and frame probes.
 * Never reports 'ENGINE LIVE' unless frames are strictly advancing.
 *
 * @param {Object} params
 * @param {boolean} params.serverHealthy - True if /api/v1/health returned 200 OK
 * @param {number|null} params.previousTick - Tick from previous probe
 * @param {number|null} params.currentTick - Tick from current probe
 * @returns {'ENGINE LIVE' | 'ENGINE IDLE' | 'SERVER OFFLINE'}
 */
export function determineEngineStatus({ serverHealthy, previousTick, currentTick }) {
  if (!serverHealthy) {
    return 'SERVER OFFLINE';
  }
  if (
    typeof currentTick === 'number' &&
    typeof previousTick === 'number' &&
    currentTick > previousTick
  ) {
    return 'ENGINE LIVE';
  }
  return 'ENGINE IDLE';
}

/**
 * Format status description string.
 * @param {'ENGINE LIVE' | 'ENGINE IDLE' | 'SERVER OFFLINE'} status
 * @param {number|null} currentTick
 * @returns {string}
 */
export function getStatusDescription(status, currentTick) {
  switch (status) {
    case 'ENGINE LIVE':
      return `Discrete-event simulator actively ticking • Current Sim Tick: ${currentTick}`;
    case 'ENGINE IDLE':
      return `Server up and healthy • No active simulation frames streaming (Tick: ${currentTick ?? 0})`;
    case 'SERVER OFFLINE':
    default:
      return 'Unable to reach API server at 127.0.0.1:8000 • Start uvicorn server to connect';
  }
}

// Client-side prober loop when loaded in browser
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    initNavbar();
    injectIcons();

    let previousTick = null;
    const statusTextEl = document.getElementById('engine-status-text');
    const statusDescEl = document.getElementById('engine-status-desc');
    const statusDotEl = document.getElementById('engine-status-dot');

    async function probeEngine() {
      let serverHealthy = false;
      let currentTick = null;

      try {
        const healthRes = await fetch('/api/v1/health', { cache: 'no-store' });
        if (healthRes.ok) {
          serverHealthy = true;
          try {
            const frameRes = await fetch('/api/v1/frame/latest', { cache: 'no-store' });
            if (frameRes.ok) {
              const frameData = await frameRes.json();
              if (frameData && typeof frameData.tick === 'number') {
                currentTick = frameData.tick;
              }
            }
          } catch {
            // Frame endpoint may be empty or idle
          }
        }
      } catch {
        serverHealthy = false;
      }

      const status = determineEngineStatus({ serverHealthy, previousTick, currentTick });
      previousTick = currentTick;

      if (statusTextEl) {
        statusTextEl.textContent = status;
      }
      if (statusDescEl) {
        statusDescEl.textContent = getStatusDescription(status, currentTick);
      }
      if (statusDotEl) {
        statusDotEl.className = 'status-indicator-dot';
        if (status === 'ENGINE LIVE') statusDotEl.classList.add('dot-live');
        else if (status === 'ENGINE IDLE') statusDotEl.classList.add('dot-idle');
        else statusDotEl.classList.add('dot-offline');
      }
    }

    // Initial probe and periodic 2.5s poll
    probeEngine();
    setInterval(probeEngine, 2500);
  });
}
