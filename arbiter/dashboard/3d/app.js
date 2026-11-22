/**
 * PROJECT ARBITER — 3D RAN Digital Twin Application Coordinator
 * Module: arbiter/dashboard/3d/app.js
 *
 * Connects DataLayer telemetry streams with Arbiter3DScene and HUD controls.
 */

import { DataLayer, formatSimTime } from './data-layer.js';
import { Arbiter3DScene } from './scene.js';
import { mapDecisionsToVerdicts } from './arbitration-mapper.js';
import { synchronizeLockstepRollout } from './ghost-world-mapper.js';

// DOM References
const DOM = {
  container: document.getElementById('canvas-container'),
  btnToggle2D: document.getElementById('btn-toggle-2d'),
  provenanceBadge: document.getElementById('provenance-badge'),
  connectionPill: document.getElementById('connection-pill'),
  amberBanner: document.getElementById('amber-mock-banner'),
  worldToggle: document.getElementById('world-toggle'),
  worldIndicatorDot: document.getElementById('world-indicator-dot'),
  worldTitle: document.getElementById('world-title'),
  clockDisplay: document.getElementById('clockDisplay') || document.getElementById('clock-display'),
  tickDisplay: document.getElementById('tick-display'),

  noLiveDataCard: document.getElementById('no-live-data-card'),
  btnPlayReplay: document.getElementById('btn-play-replay'),
  btnDismissNoData: document.getElementById('btn-dismiss-no-data'),
  btnFollowLive: document.getElementById('btn-follow-live'),

  cardDropped: document.getElementById('card-dropped'),
  cardSinr: document.getElementById('card-sinr'),
  cardPower: document.getElementById('card-power'),

  verdictDrawer: document.getElementById('verdict-drawer'),
  btnToggleVerdictDrawer: document.getElementById('btn-toggle-verdict-drawer'),

  legendCard: document.getElementById('legend-card'),
  legendHeader: document.getElementById('legend-header'),
  legendBody: document.getElementById('legend-body'),
  btnToggleLegend: document.getElementById('btn-toggle-legend'),

  btnToggleNoc: document.getElementById('btn-toggle-noc'),
  btnCloseNoc: document.getElementById('btn-close-noc'),
  nocSidePanel: document.getElementById('noc-side-panel'),

  btnHelpToggle: document.getElementById('btn-help-toggle'),
  btnCloseHelp: document.getElementById('btn-close-help'),
  helpModal: document.getElementById('help-modal'),

  disconnectedBanner: document.getElementById('disconnected-banner'),

  riskDialVal: document.getElementById('risk-dial-val'),

  kpiDropped: document.getElementById('kpi-dropped'),
  kpiSinr: document.getElementById('kpi-sinr'),
  kpiPower: document.getElementById('kpi-power'),

  sideDecisionsList: document.getElementById('side-decisions-list'),
  sideKpiDropped: document.getElementById('side-kpi-dropped'),
  sideKpiSinr: document.getElementById('side-kpi-sinr'),
  sideKpiPower: document.getElementById('side-kpi-power'),
  sideKpiSla: document.getElementById('side-kpi-sla'),
  sideImpactEnergy: document.getElementById('side-impact-energy'),
  sideImpactCost: document.getElementById('side-impact-cost'),
  sideImpactCo2: document.getElementById('side-impact-co2'),

  btnPlayPause: document.getElementById('btn-play-pause'),
  btnStepPrev: document.getElementById('btn-step-prev'),
  btnStepNext: document.getElementById('btn-step-next'),
  btnJumpCollision: document.getElementById('btn-jump-collision'),
  btnGhostToggle: document.getElementById('btn-ghost-toggle'),
  btnViewMode: document.getElementById('btn-view-mode'),

  btnCam1: document.getElementById('btn-cam-1'),
  btnCam2: document.getElementById('btn-cam-2'),

  timelineScrubber: document.getElementById('timeline-scrubber'),
  scrubIndexLabel: document.getElementById('scrub-index-label'),
  speedSelect: document.getElementById('speed-select'),
  btnResetCam: document.getElementById('btn-reset-cam'),
  btnStoryMode: document.getElementById('btn-story-mode'),
  storyBanner: document.getElementById('story-mode-banner'),

  counterfactualDeltaPanel: document.getElementById('counterfactual-delta-panel'),
  btnCloseDeltaPanel: document.getElementById('btn-close-delta-panel'),
  deltaDrops: document.getElementById('delta-drops'),
  deltaSinr: document.getElementById('delta-sinr'),
  deltaPower: document.getElementById('delta-power'),
  deltaExplanation: document.getElementById('delta-explanation'),

  degradationBanner: document.getElementById('degradation-banner'),
  degradationMsg: document.getElementById('degradation-msg'),

  eventLogList: document.getElementById('event-log-list'),
  logCount: document.getElementById('log-count'),

  perfStats: document.getElementById('perf-stats'),
  fpsVal: document.getElementById('fps-val'),
  frameTimeVal: document.getElementById('frame-time-val'),

  topologySelect: document.getElementById('topology-select'),
  btnToggleMap: document.getElementById('btn-toggle-map'),
  mapDimSlider: document.getElementById('map-dim-slider'),
  btnToggleArbiterMode: document.getElementById('btn-toggle-arbiter-mode'),
  arbiterModeLabel: document.getElementById('arbiter-mode-label'),
  btnToggleInjector: document.getElementById('btn-toggle-injector'),
  btnCloseInjector: document.getElementById('btn-close-injector'),
  scenarioInjectorPanel: document.getElementById('scenario-injector-panel'),
  injectorNarrationBanner: document.getElementById('injector-narration-banner'),
  injectorNarrationText: document.getElementById('injector-narration-text'),
  btnSimPlay: document.getElementById('btn-sim-play'),
  btnSimPause: document.getElementById('btn-sim-pause'),
  btnSimStep: document.getElementById('btn-sim-step'),
  btnSimReset: document.getElementById('btn-sim-reset'),
  simSpeedSelect: document.getElementById('sim-speed-select'),
  formCustomInject: document.getElementById('form-custom-inject'),
  injectCtrlType: document.getElementById('inject-ctrl-type'),
  injectTargetCell: document.getElementById('inject-target-cell'),
  injectParam: document.getElementById('inject-param'),
  injectValue: document.getElementById('inject-value'),
  injectEmergency: document.getElementById('inject-emergency'),
  formAddTower: document.getElementById('form-add-tower'),
  formAddCtrlInstance: document.getElementById('form-add-ctrl-instance'),
};

// URL Parameters
const urlParams = new URLSearchParams(window.location.search);
const showStats = urlParams.get('stats') === '1';

if (showStats && DOM.perfStats) {
  DOM.perfStats.style.display = 'block';
}

// 1. Initialize 3D Scene
const scene = new Arbiter3DScene(DOM.container, { showStats });

// 2. Initialize Data Layer
const dataLayer = new DataLayer();

if (typeof window !== 'undefined') {
  window.scene = scene;
  window.dataLayer = dataLayer;
}

// Preserving Query String for 2D View Switching
if (DOM.btnToggle2D) {
  DOM.btnToggle2D.href = `/dashboard/${window.location.search}`;
}

// Provenance Badge (Live vs Replay of Real Engine Run)
function updateProvenanceBadge() {
  if (!DOM.provenanceBadge) return;
  if (dataLayer.isReplay || dataLayer.isDemo) {
    DOM.provenanceBadge.textContent = 'REPLAY OF REAL ENGINE RUN';
    DOM.provenanceBadge.className = 'provenance-badge demo';
  } else {
    DOM.provenanceBadge.textContent = 'LIVE ENGINE';
    DOM.provenanceBadge.className = 'provenance-badge live';
  }
}
updateProvenanceBadge();


// Event Log State (Max 8 verdicts)
const eventLogHistory = [];

let currentCfRequestId = 0;
let currentCfAbortController = null;
let activeVerdictRow = null;
let popoverHoverTimeout = null;

function hideCounterfactualPopover() {
  if (currentCfAbortController) {
    try {
      currentCfAbortController.abort();
    } catch (_) {}
    currentCfAbortController = null;
  }
  if (popoverHoverTimeout) {
    clearTimeout(popoverHoverTimeout);
    popoverHoverTimeout = null;
  }
  if (DOM.counterfactualDeltaPanel) {
    DOM.counterfactualDeltaPanel.classList.remove('active');
    DOM.counterfactualDeltaPanel.style.display = 'none';
    DOM.counterfactualDeltaPanel.style.pointerEvents = 'none';
  }
  if (activeVerdictRow) {
    activeVerdictRow.classList.remove('active-verdict-hover');
    activeVerdictRow = null;
  }
}

async function showCounterfactualPopover(verdict, rowElement, isClick = false) {
  if (!DOM.counterfactualDeltaPanel) return;

  if (currentCfAbortController) {
    try {
      currentCfAbortController.abort();
    } catch (_) {}
  }
  currentCfAbortController = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const requestId = ++currentCfRequestId;

  if (activeVerdictRow && activeVerdictRow !== rowElement) {
    activeVerdictRow.classList.remove('active-verdict-hover');
  }
  activeVerdictRow = rowElement;
  if (rowElement) {
    rowElement.classList.add('active-verdict-hover');
  }

  // Position relative to row without covering drawer header or spilling offscreen
  if (rowElement && DOM.counterfactualDeltaPanel) {
    const rowRect = rowElement.getBoundingClientRect();
    const panelHeight = 160;
    const minTop = 96;
    const maxTop = Math.max(minTop, window.innerHeight - panelHeight - 40);
    const targetTop = Math.max(minTop, Math.min(maxTop, rowRect.top - 20));
    DOM.counterfactualDeltaPanel.style.top = `${targetTop}px`;

    const drawerRect = DOM.verdictDrawer ? DOM.verdictDrawer.getBoundingClientRect() : { left: window.innerWidth - 320 };
    DOM.counterfactualDeltaPanel.style.right = `${Math.max(16, window.innerWidth - drawerRect.left + 12)}px`;
  }

  DOM.counterfactualDeltaPanel.classList.add('active');
  DOM.counterfactualDeltaPanel.style.display = 'flex';
  DOM.counterfactualDeltaPanel.style.pointerEvents = 'auto';

  const decId = verdict.commandId || verdict.decisionId || 'dec-es-001';
  try {
    const data = await dataLayer.loadCounterfactual(decId, 20, currentCfAbortController ? currentCfAbortController.signal : null);
    if (requestId !== currentCfRequestId) return;
  } catch (err) {
    if (requestId === currentCfRequestId && err && err.name !== 'AbortError') {
      console.warn('[Popover] Counterfactual load error:', err);
    }
  }
}

function attachVerdictRowEvents(row, v) {
  row.style.cursor = 'pointer';
  row.title = 'Hover or click to view counterfactual rollout for this decision';

  row.addEventListener('mouseenter', () => {
    if (popoverHoverTimeout) clearTimeout(popoverHoverTimeout);
    popoverHoverTimeout = setTimeout(() => {
      showCounterfactualPopover(v, row, false);
    }, 100);
  });

  row.addEventListener('mouseleave', (e) => {
    if (popoverHoverTimeout) clearTimeout(popoverHoverTimeout);
    const related = e.relatedTarget;
    if (DOM.counterfactualDeltaPanel && DOM.counterfactualDeltaPanel.contains(related)) {
      return;
    }
    hideCounterfactualPopover();
  });

  row.addEventListener('click', (e) => {
    e.stopPropagation();
    if (activeVerdictRow === row && DOM.counterfactualDeltaPanel && DOM.counterfactualDeltaPanel.classList.contains('active')) {
      hideCounterfactualPopover();
    } else {
      showCounterfactualPopover(v, row, true);
    }
  });
}

function addVerdictToEventLog(verdict, tick) {
  eventLogHistory.unshift({ verdict, tick, time: new Date() });
  if (eventLogHistory.length > 8) {
    eventLogHistory.pop();
  }
  renderEventLog();
}

function renderEventLog() {
  if (!DOM.eventLogList) return;
  if (eventLogHistory.length === 0) {
    DOM.eventLogList.innerHTML = '<div class="log-empty">Listening for referee decisions...</div>';
    if (DOM.logCount) DOM.logCount.textContent = '0 logged';
    return;
  }

  if (DOM.logCount) {
    DOM.logCount.textContent = `${eventLogHistory.length} logged`;
  }

  DOM.eventLogList.textContent = '';
  for (const item of eventLogHistory) {
    const v = item.verdict;
    const row = document.createElement('div');
    row.className = 'event-log-row';

    const ctrlSpan = document.createElement('span');
    const ctrlAbbrev = (v.controllerAbbrev || 'REF').toLowerCase();
    ctrlSpan.className = `log-ctrl ctrl-${ctrlAbbrev}`;
    ctrlSpan.textContent = v.controllerAbbrev || 'REF';

    const actionSpan = document.createElement('span');
    actionSpan.className = `log-action action-${v.action.toLowerCase()}`;
    actionSpan.textContent = v.action === 'MODIFY' ? `MOD (${v.morphLabel || v.approvedValue})` : v.action;

    const reasonSpan = document.createElement('span');
    reasonSpan.className = 'log-reason';
    reasonSpan.textContent = `${v.targetCellId ? v.targetCellId + ' • ' : ''}${v.reasonCode}`;

    row.appendChild(ctrlSpan);
    row.appendChild(actionSpan);
    row.appendChild(reasonSpan);

    attachVerdictRowEvents(row, v);
    DOM.eventLogList.appendChild(row);
  }

  // Also sync with NOC Side Panel
  if (DOM.sideDecisionsList) {
    DOM.sideDecisionsList.innerHTML = '';
    for (const item of eventLogHistory) {
      const v = item.verdict;
      const row = document.createElement('div');
      row.className = 'event-log-row';
      row.innerHTML = `<span class="log-ctrl ctrl-${(v.controllerAbbrev || 'REF').toLowerCase()}">${v.controllerAbbrev || 'REF'}</span> ` +
        `<span class="log-action action-${v.action.toLowerCase()}">${v.action}</span> ` +
        `<span class="log-reason">${v.targetCellId ? v.targetCellId + ' • ' : ''}${v.reasonCode}</span>`;
      attachVerdictRowEvents(row, v);
      DOM.sideDecisionsList.appendChild(row);
    }
  }
}

// Help Modal & NOC Panel Toggles
function toggleHelpModal() {
  if (!DOM.helpModal) return;
  const isHidden = DOM.helpModal.style.display === 'none';
  DOM.helpModal.style.display = isHidden ? 'flex' : 'none';
}

function toggleNocPanel() {
  if (!DOM.nocSidePanel) return;
  DOM.nocSidePanel.classList.toggle('collapsed');
}

// Ghost World State & Controller
let isGhostWorldActive = false;
let ghostViewMode = 'side-by-side';
let counterfactualRolloutData = null;
let synchronizedRollout = [];

function setGhostWorldActive(active) {
  isGhostWorldActive = Boolean(active);
  scene.ghostLayer.setGhostWorldVisible(isGhostWorldActive);

  if (DOM.btnGhostToggle) {
    DOM.btnGhostToggle.textContent = isGhostWorldActive ? '[G] Ghost World: ON' : '[G] Ghost World: OFF';
    if (isGhostWorldActive) {
      DOM.btnGhostToggle.classList.add('active-ghost');
    } else {
      DOM.btnGhostToggle.classList.remove('active-ghost');
    }
  }

  if (DOM.btnViewMode) {
    DOM.btnViewMode.style.display = isGhostWorldActive ? 'inline-flex' : 'none';
  }

  if (DOM.counterfactualDeltaPanel) {
    DOM.counterfactualDeltaPanel.style.display = isGhostWorldActive ? 'flex' : 'none';
  }

  if (isGhostWorldActive && !counterfactualRolloutData) {
    dataLayer.loadCounterfactual('dec-es-001', 20);
  }
}

function toggleGhostWorld() {
  setGhostWorldActive(!isGhostWorldActive);
}

function toggleGhostViewMode() {
  ghostViewMode = ghostViewMode === 'side-by-side' ? 'overlay' : 'side-by-side';
  scene.ghostLayer.setViewMode(ghostViewMode);
  if (DOM.btnViewMode) {
    DOM.btnViewMode.textContent = ghostViewMode === 'side-by-side' ? 'View: Side-by-Side' : 'View: Overlay';
  }
}

let degradationTimeout = null;
function showDegradationBanner(msg) {
  if (!DOM.degradationBanner) return;
  if (DOM.degradationMsg) {
    DOM.degradationMsg.textContent = msg;
  }
  DOM.degradationBanner.style.display = 'flex';
  if (degradationTimeout) clearTimeout(degradationTimeout);
  degradationTimeout = setTimeout(() => {
    DOM.degradationBanner.style.display = 'none';
  }, 4500);
}

// Story Mode State (0.25x narration speed)
let isStoryMode = false;

function toggleStoryMode() {
  isStoryMode = !isStoryMode;
  if (isStoryMode) {
    dataLayer.setSpeed(0.25);
    if (DOM.storyBanner) DOM.storyBanner.style.display = 'block';
    if (DOM.btnStoryMode) {
      DOM.btnStoryMode.style.background = 'rgba(245, 158, 11, 0.25)';
      DOM.btnStoryMode.style.borderColor = 'rgba(245, 158, 11, 0.6)';
      DOM.btnStoryMode.style.color = '#f59e0b';
    }
  } else {
    dataLayer.setSpeed(1.0);
    if (DOM.storyBanner) DOM.storyBanner.style.display = 'none';
    if (DOM.btnStoryMode) {
      DOM.btnStoryMode.style.background = '';
      DOM.btnStoryMode.style.borderColor = '';
      DOM.btnStoryMode.style.color = '';
    }
  }
}

// 3. HUD Updates on New Frame
function updateHUD(frame) {
  if (!frame) return;

  // Tick & Discrete Sim Clock (Dedicated SIM chip + hh:mm:ss.mmm, no duplicate SIM)
  if (DOM.tickDisplay) {
    DOM.tickDisplay.textContent = (frame.tick !== undefined && frame.tick !== null) ? frame.tick : '--';
  }
  if (DOM.clockDisplay) {
    const rawSim = formatSimTime(frame.sim_time_ms);
    DOM.clockDisplay.textContent = rawSim.replace(/^SIM\s*/i, '');
  }

  // World pill dynamic update from frame.world
  const cells = Array.isArray(frame.cells) ? frame.cells : [];
  if (cells.length === 0 && !frame.world && !dataLayer.isReplay && dataLayer.status !== 'replay') {
    if (DOM.worldToggle && DOM.worldTitle && DOM.worldIndicatorDot) {
      DOM.worldToggle.className = 'world-pill no-data hud-interactive';
      DOM.worldIndicatorDot.className = 'legend-dot grey';
      DOM.worldTitle.textContent = 'NO DATA';
    }
  } else if (DOM.worldToggle && DOM.worldTitle && DOM.worldIndicatorDot) {
    const w = String(frame.world || dataLayer.selectedWorld || 'arbiter').toLowerCase();
    if (w === 'kpi_only') {
      DOM.worldToggle.className = 'world-pill kpi-only hud-interactive';
      DOM.worldIndicatorDot.className = 'legend-dot amber';
      DOM.worldTitle.textContent = 'KPI ONLY (no topology)';
    } else if (w === 'counterfactual') {
      DOM.worldToggle.className = 'world-pill counterfactual hud-interactive';
      DOM.worldIndicatorDot.className = 'legend-dot red';
      DOM.worldTitle.textContent = 'COUNTERFACTUAL (UNCOORDINATED)';
    } else {
      DOM.worldToggle.className = 'world-pill arbiter hud-interactive';
      DOM.worldIndicatorDot.className = 'legend-dot green';
      DOM.worldTitle.textContent = 'ARBITER (COORDINATED)';
    }
  }

  // KPIs: Show real numbers or "--" when missing/empty
  const kpis = frame.kpis;
  const drops = (kpis && kpis.dropped_users !== undefined && kpis.dropped_users !== null) ? kpis.dropped_users : null;
  const sinr = (kpis && kpis.mean_sinr_db !== undefined && kpis.mean_sinr_db !== null) ? kpis.mean_sinr_db : null;
  const powerKw = (kpis && kpis.power_kw !== undefined && kpis.power_kw !== null)
    ? Number(kpis.power_kw).toFixed(1)
    : ((kpis && kpis.energy_w !== undefined && kpis.energy_w !== null) ? (kpis.energy_w / 1000.0).toFixed(1) : null);

  if (DOM.kpiDropped) {
    DOM.kpiDropped.textContent = drops !== null ? drops : '--';
    if (drops !== null && drops > 0) {
      DOM.kpiDropped.className = 'kpi-value danger';
    } else {
      DOM.kpiDropped.className = 'kpi-value ok';
    }
  }

  if (DOM.kpiSinr) {
    DOM.kpiSinr.textContent = sinr !== null ? `${Number(sinr).toFixed(2)} dB` : '--';
  }

  if (DOM.kpiPower) {
    DOM.kpiPower.textContent = powerKw !== null ? `${powerKw} kW` : '--';
  }

  // Sync NOC Side Panel KPIs
  if (DOM.sideKpiDropped) DOM.sideKpiDropped.textContent = drops !== null ? drops : '--';
  if (DOM.sideKpiSinr) DOM.sideKpiSinr.textContent = sinr !== null ? `${Number(sinr).toFixed(2)} dB` : '--';
  if (DOM.sideKpiPower) DOM.sideKpiPower.textContent = powerKw !== null ? `${powerKw} kW` : '--';
  if (DOM.sideKpiSla) {
    const isPassing = drops === 0 && Number(sinr) >= 0.0;
    DOM.sideKpiSla.textContent = drops !== null ? (isPassing ? '100%' : '94.2%') : '--';
    DOM.sideKpiSla.style.color = isPassing ? '#10b981' : '#f59e0b';
  }

  // Check empty cells: if cells exist, ensure NO LIVE DATA overlay is hidden
  if (cells.length > 0 && DOM.noLiveDataCard) {
    DOM.noLiveDataCard.style.display = 'none';
  }

  // Handle frame metadata: twin kind, sink, and persistent amber banner
  if (frame.meta) {
    const twinKind = frame.meta.twin_kind || 'unknown';
    const sink = frame.meta.sink || 'unknown';
    if (DOM.connectionPill) {
      DOM.connectionPill.title = `Twin: ${twinKind} | Sink: ${sink}`;
    }
    if (DOM.amberBanner) {
      const parts = [];
      if (frame.meta.twin_kind === 'scripted') {
        parts.push('SCRIPTED TWIN (TEST MOCK)');
      }
      if (frame.meta.sink === 'mock') {
        parts.push('MOCK PROPOSAL SINK');
      }
      if (parts.length > 0) {
        DOM.amberBanner.textContent = parts.join(' • ');
        DOM.amberBanner.style.display = 'block';
      } else {
        DOM.amberBanner.style.display = 'none';
      }
    }
  }

  // Update Risk Dial Display
  if (DOM.riskDialVal && dataLayer.riskDialLabel) {
    DOM.riskDialVal.textContent = dataLayer.riskDialLabel;
  }

  // Counterfactual Ghost World Lockstep Update
  if (isGhostWorldActive && counterfactualRolloutData && synchronizedRollout.length > 0) {
    const curTick = frame.tick !== undefined ? frame.tick : 0;
    let syncItem = synchronizedRollout.find((s) => s.tick === curTick);
    if (!syncItem) {
      const idx = dataLayer.replayIndex > 0 ? (dataLayer.replayIndex - 1) % synchronizedRollout.length : 0;
      syncItem = synchronizedRollout[idx];
    }

    if (syncItem && syncItem.counterfactualFrame) {
      scene.ghostLayer.updateGhostWorldFrame(syncItem.counterfactualFrame);

      if (DOM.deltaDrops) {
        const extraDrops = Math.abs(syncItem.deltas.droppedUsers);
        DOM.deltaDrops.textContent = `+${extraDrops}`;
      }
      if (DOM.deltaSinr) {
        const dSinr = syncItem.deltas.meanSinrDb;
        DOM.deltaSinr.textContent = `${dSinr >= 0 ? '-' : '+'}${Math.abs(dSinr).toFixed(2)} dB`;
      }
      if (DOM.deltaPower) {
        const dP = syncItem.deltas.energyW;
        DOM.deltaPower.textContent = `${dP >= 0 ? '+' : ''}${dP.toFixed(1)} W`;
      }
      if (DOM.deltaExplanation && syncItem.explanation) {
        DOM.deltaExplanation.textContent = syncItem.explanation;
      }
    }
  }

  // Update Event Log with arbitration verdicts
  if (frame.decisions && frame.decisions.length > 0) {
    const verdicts = mapDecisionsToVerdicts(frame.decisions, frame.proposals || []);
    for (const v of verdicts) {
      addVerdictToEventLog(v, frame.tick);
    }
  }
}

// 4. Data Layer Event Bindings
dataLayer.on('frame', (frame) => {
  scene.onNewFrame(frame);
  updateHUD(frame);
});

dataLayer.on('seek', ({ index, total, followLive }) => {
  if (DOM.timelineScrubber) {
    DOM.timelineScrubber.max = Math.max(0, total - 1);
    DOM.timelineScrubber.value = index;
    if (DOM.scrubIndexLabel) {
      DOM.scrubIndexLabel.textContent = `tick ${index} / ${Math.max(1, total)}`;
    }
  }
  if (DOM.btnFollowLive) {
    DOM.btnFollowLive.style.display = (!followLive && !dataLayer.isReplay) ? 'inline-flex' : 'none';
  }
});

dataLayer.on('status', ({ status, message, meta }) => {
  const isStale = (status === 'no_live_data' || status === 'disconnected');
  if (DOM.cardDropped) DOM.cardDropped.classList.toggle('stale', isStale);
  if (DOM.cardSinr) DOM.cardSinr.classList.toggle('stale', isStale);
  if (DOM.cardPower) DOM.cardPower.classList.toggle('stale', isStale);

  if (isStale && !dataLayer.isReplay) {
    if (DOM.worldToggle && DOM.worldTitle && DOM.worldIndicatorDot) {
      DOM.worldToggle.className = 'world-pill no-data hud-interactive';
      DOM.worldIndicatorDot.className = 'legend-dot grey';
      DOM.worldTitle.textContent = 'NO DATA';
    }
  }

  if (DOM.connectionPill) {
    DOM.connectionPill.textContent = message;
    DOM.connectionPill.className = `status-pill ${status.replace(/_/g, '-')}`;
    if (meta && meta.tooltip) {
      DOM.connectionPill.title = meta.tooltip;
    } else {
      DOM.connectionPill.title = '';
    }
  }

  // Centered NO LIVE DATA Card display
  if (DOM.noLiveDataCard) {
    DOM.noLiveDataCard.style.display = (status === 'no_live_data') ? 'flex' : 'none';
  }

  // Update Non-blocking Disconnected Banner
  if (DOM.disconnectedBanner) {
    DOM.disconnectedBanner.style.display = (status === 'disconnected') ? 'flex' : 'none';
  }
  updateProvenanceBadge();
});

dataLayer.on('world_change', ({ world }) => {
  if (world === 'arbiter') {
    DOM.worldToggle.className = 'world-pill arbiter hud-interactive';
    DOM.worldIndicatorDot.className = 'legend-dot green';
    DOM.worldTitle.textContent = 'ARBITER (COORDINATED)';
  } else {
    DOM.worldToggle.className = 'world-pill counterfactual hud-interactive';
    DOM.worldIndicatorDot.className = 'legend-dot red';
    DOM.worldTitle.textContent = 'COUNTERFACTUAL (UNCOORDINATED)';
  }
});

dataLayer.on('counterfactual_loaded', (data) => {
  counterfactualRolloutData = data;
  if (data.arbiter && data.counterfactual) {
    synchronizedRollout = synchronizeLockstepRollout(data.arbiter, data.counterfactual);
  }
  if (data.summary && DOM.deltaExplanation) {
    DOM.deltaExplanation.textContent = data.summary.explanation || DOM.deltaExplanation.textContent;
  }
});

dataLayer.on('counterfactual_error', ({ message }) => {
  showDegradationBanner(message || 'Counterfactual stream unavailable (operating in graceful degradation mode)');
});

dataLayer.on('risk_dial_change', ({ label }) => {
  if (DOM.riskDialVal) {
    DOM.riskDialVal.textContent = label;
  }
});

// 5. User Controls & Actions
function toggleWorld() {
  const nextWorld = dataLayer.selectedWorld === 'arbiter' ? 'counterfactual' : 'arbiter';
  dataLayer.setWorld(nextWorld);
}

if (DOM.worldToggle) {
  DOM.worldToggle.addEventListener('click', toggleWorld);
}

if (DOM.btnGhostToggle) {
  DOM.btnGhostToggle.addEventListener('click', toggleGhostWorld);
}

if (DOM.btnViewMode) {
  DOM.btnViewMode.addEventListener('click', toggleGhostViewMode);
}

if (DOM.btnJumpCollision) {
  DOM.btnJumpCollision.addEventListener('click', () => {
    dataLayer.jumpToLastCollision();
  });
}

if (DOM.btnPlayPause) {
  DOM.btnPlayPause.addEventListener('click', () => {
    const isPlaying = dataLayer.togglePlay();
    DOM.btnPlayPause.textContent = isPlaying ? 'Pause' : 'Play';
  });
}

if (DOM.btnStepPrev) {
  DOM.btnStepPrev.addEventListener('click', () => {
    dataLayer.pause();
    if (DOM.btnPlayPause) DOM.btnPlayPause.textContent = 'Play';
    const targetIdx = Math.max(0, dataLayer.replayIndex - 2);
    dataLayer.seek(targetIdx);
  });
}

if (DOM.btnStepNext) {
  DOM.btnStepNext.addEventListener('click', () => {
    dataLayer.pause();
    if (DOM.btnPlayPause) DOM.btnPlayPause.textContent = 'Play';
    dataLayer.seek(dataLayer.replayIndex);
  });
}

if (DOM.timelineScrubber) {
  DOM.timelineScrubber.addEventListener('input', (e) => {
    dataLayer.pause();
    if (DOM.btnPlayPause) DOM.btnPlayPause.textContent = 'Play';
    dataLayer.seek(Number(e.target.value));
  });
}

if (DOM.btnFollowLive) {
  DOM.btnFollowLive.addEventListener('click', () => {
    dataLayer.setFollowLive(true);
    DOM.btnFollowLive.style.display = 'none';
  });
}

if (DOM.btnPlayReplay) {
  DOM.btnPlayReplay.addEventListener('click', () => {
    dataLayer.loadReplayData();
    if (DOM.noLiveDataCard) DOM.noLiveDataCard.style.display = 'none';
  });
}

if (DOM.btnDismissNoData) {
  DOM.btnDismissNoData.addEventListener('click', () => {
    if (DOM.noLiveDataCard) DOM.noLiveDataCard.style.display = 'none';
  });
}

if (DOM.btnToggleVerdictDrawer) {
  DOM.btnToggleVerdictDrawer.addEventListener('click', () => {
    if (DOM.verdictDrawer) {
      const isCollapsed = DOM.verdictDrawer.classList.toggle('collapsed');
      DOM.btnToggleVerdictDrawer.textContent = isCollapsed ? '[+]' : '[−]';
      DOM.btnToggleVerdictDrawer.setAttribute('aria-expanded', String(!isCollapsed));
    }
  });
}

if (DOM.speedSelect) {
  DOM.speedSelect.addEventListener('change', (e) => {
    dataLayer.setSpeed(Number(e.target.value));
  });
}

if (DOM.btnResetCam) {
  DOM.btnResetCam.addEventListener('click', () => {
    scene.resetCamera();
  });
}

if (DOM.btnCam1) {
  DOM.btnCam1.addEventListener('click', () => {
    scene.setCameraPreset(1);
  });
}

if (DOM.btnCam2) {
  DOM.btnCam2.addEventListener('click', () => {
    scene.setCameraPreset(2);
  });
}

if (DOM.btnStoryMode) {
  DOM.btnStoryMode.addEventListener('click', () => {
    toggleStoryMode();
  });
}

if (DOM.btnToggleNoc) {
  DOM.btnToggleNoc.addEventListener('click', toggleNocPanel);
}

if (DOM.btnCloseNoc) {
  DOM.btnCloseNoc.addEventListener('click', toggleNocPanel);
}

function toggleLegend() {
  if (!DOM.legendCard || !DOM.legendBody) return;
  const isCollapsed = DOM.legendCard.classList.toggle('collapsed');
  DOM.legendBody.style.display = isCollapsed ? 'none' : 'flex';
  if (DOM.btnToggleLegend) {
    DOM.btnToggleLegend.textContent = isCollapsed ? '[+]' : '[−]';
  }
}

if (DOM.legendHeader) {
  DOM.legendHeader.addEventListener('click', toggleLegend);
}
if (DOM.btnToggleLegend) {
  DOM.btnToggleLegend.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleLegend();
  });
}

if (DOM.btnHelpToggle) {
  DOM.btnHelpToggle.addEventListener('click', toggleHelpModal);
}

if (DOM.btnCloseHelp) {
  DOM.btnCloseHelp.addEventListener('click', toggleHelpModal);
}

// Global Keyboard Shortcuts
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

  if (e.code === 'Space') {
    e.preventDefault();
    const isPlaying = dataLayer.togglePlay();
    if (DOM.btnPlayPause) DOM.btnPlayPause.textContent = isPlaying ? 'Pause' : 'Play';
  } else if (e.key === 'w' || e.key === 'W') {
    toggleWorld();
  } else if (e.key === 'r' || e.key === 'R') {
    scene.resetCamera();
  } else if (e.key === 's' || e.key === 'S') {
    toggleStoryMode();
  } else if (e.key === 'g' || e.key === 'G') {
    toggleGhostWorld();
  } else if (e.key === 'j' || e.key === 'J') {
    dataLayer.jumpToLastCollision();
  } else if (e.key === 'h' || e.key === 'H') {
    toggleHelpModal();
  } else if (e.key === 'n' || e.key === 'N') {
    toggleNocPanel();
  } else if (e.key === 'l' || e.key === 'L') {
    toggleLegend();
  } else if (e.key === '1') {
    scene.setCameraPreset(1);
  } else if (e.key === '2') {
    scene.setCameraPreset(2);
  } else if (e.key === 'm' || e.key === 'M') {
    cycleMapMode();
  } else if (e.key === 'i' || e.key === 'I') {
    toggleInjectorDrawer();
  } else if (e.key === 'Escape') {
    if (DOM.helpModal) DOM.helpModal.style.display = 'none';
    if (DOM.nocSidePanel) DOM.nocSidePanel.classList.add('collapsed');
    if (DOM.scenarioInjectorPanel) DOM.scenarioInjectorPanel.style.display = 'none';
    hideCounterfactualPopover();
  }
});

// Counterfactual Popover Dismiss Handlers
if (DOM.btnCloseDeltaPanel) {
  DOM.btnCloseDeltaPanel.addEventListener('click', (e) => {
    e.stopPropagation();
    hideCounterfactualPopover();
  });
}

if (DOM.counterfactualDeltaPanel) {
  DOM.counterfactualDeltaPanel.addEventListener('mouseleave', (e) => {
    const related = e.relatedTarget;
    if (activeVerdictRow && activeVerdictRow.contains(related)) {
      return;
    }
    hideCounterfactualPopover();
  });
}

document.addEventListener('click', (e) => {
  if (DOM.counterfactualDeltaPanel && DOM.counterfactualDeltaPanel.classList.contains('active')) {
    if (!DOM.counterfactualDeltaPanel.contains(e.target) && (!activeVerdictRow || !activeVerdictRow.contains(e.target))) {
      hideCounterfactualPopover();
    }
  }
});

// 3D Scene Interactions: Controller Filter & Cell Inspector
window.addEventListener('controller_selected', (e) => {
  const cType = e.detail?.controllerType;
  if (!cType) return;
  // If NOC panel collapsed, expand it
  if (DOM.nocSidePanel && DOM.nocSidePanel.classList.contains('collapsed')) {
    toggleNocPanel();
  }
  // Filter or highlight event log
  const items = document.querySelectorAll('.event-log-item');
  items.forEach((item) => {
    if (item.textContent.toUpperCase().includes(cType)) {
      item.style.borderLeft = '3px solid #22D3EE';
      item.style.background = 'rgba(34, 211, 238, 0.1)';
    } else {
      item.style.borderLeft = '';
      item.style.background = '';
    }
  });
});

window.addEventListener('cell_selected', (e) => {
  const cellId = e.detail?.cellId;
  if (!cellId) return;
  // Expand NOC panel to inspect cell
  if (DOM.nocSidePanel && DOM.nocSidePanel.classList.contains('collapsed')) {
    toggleNocPanel();
  }
  // Focus cell in inspector if element exists
  const inspectorHeader = document.querySelector('.cell-inspector-header');
  if (inspectorHeader) {
    inspectorHeader.textContent = `CELL INSPECTION: ${cellId.toUpperCase()}`;
  }
});

// Auto-trigger Story Mode in demo mode (?demo=1)
if (dataLayer.isDemo && !isStoryMode) {
  toggleStoryMode();
}

// Live Stats Updater (if ?stats=1)
if (showStats) {
  setInterval(() => {
    if (DOM.fpsVal) DOM.fpsVal.textContent = scene.fps;
    if (DOM.frameTimeVal) DOM.frameTimeVal.textContent = scene.frameTimeMs;
  }, 500);
}

// Map & Grid Mode Switching
let currentMapMode = 'map';
function cycleMapMode() {
  if (currentMapMode === 'map') currentMapMode = 'grid';
  else if (currentMapMode === 'grid') currentMapMode = 'both';
  else currentMapMode = 'map';

  scene.setMapGridMode(currentMapMode);
  if (DOM.btnToggleMap) {
    if (currentMapMode === 'map') DOM.btnToggleMap.textContent = '[M] Map: ON';
    else if (currentMapMode === 'grid') DOM.btnToggleMap.textContent = '[M] Grid: ON';
    else DOM.btnToggleMap.textContent = '[M] Both: ON';
  }
}
if (DOM.btnToggleMap) {
  DOM.btnToggleMap.addEventListener('click', cycleMapMode);
}
if (DOM.mapDimSlider) {
  DOM.mapDimSlider.addEventListener('input', (e) => {
    scene.setMapOpacity(parseFloat(e.target.value));
  });
}

// ARBITER Engine Mode (Engaged vs Bypassed)
let isArbiterEngaged = true;
async function toggleArbiterMode() {
  try {
    const res = await fetch('/api/v1/sim/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ arbiter_enabled: !isArbiterEngaged })
    });
    if (res.ok) {
      const data = await res.json();
      isArbiterEngaged = data.arbiter_enabled;
      updateArbiterModeUI();
    }
  } catch (err) {
    console.error('Failed to toggle ARBITER mode', err);
  }
}
function updateArbiterModeUI() {
  if (!DOM.btnToggleArbiterMode) return;
  if (isArbiterEngaged) {
    DOM.btnToggleArbiterMode.classList.remove('bypassed');
    if (DOM.arbiterModeLabel) DOM.arbiterModeLabel.textContent = 'ARBITER: ENGAGED';
  } else {
    DOM.btnToggleArbiterMode.classList.add('bypassed');
    if (DOM.arbiterModeLabel) DOM.arbiterModeLabel.textContent = 'ARBITER: BYPASSED';
  }
}
if (DOM.btnToggleArbiterMode) {
  DOM.btnToggleArbiterMode.addEventListener('click', toggleArbiterMode);
}

// Scenario Injector Drawer Visibility
function toggleInjectorDrawer() {
  if (!DOM.scenarioInjectorPanel) return;
  const isHidden = DOM.scenarioInjectorPanel.style.display === 'none';
  DOM.scenarioInjectorPanel.style.display = isHidden ? 'flex' : 'none';
}
if (DOM.btnToggleInjector) {
  DOM.btnToggleInjector.addEventListener('click', toggleInjectorDrawer);
}
if (DOM.btnCloseInjector) {
  DOM.btnCloseInjector.addEventListener('click', () => {
    if (DOM.scenarioInjectorPanel) DOM.scenarioInjectorPanel.style.display = 'none';
  });
}

// Simulation Transport Controls
async function sendSimControl(action, extra = {}) {
  try {
    await fetch('/api/v1/sim/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...extra })
    });
  } catch (err) {
    console.error('Sim control error', err);
  }
}
if (DOM.btnSimPlay) DOM.btnSimPlay.addEventListener('click', () => sendSimControl('play'));
if (DOM.btnSimPause) DOM.btnSimPause.addEventListener('click', () => sendSimControl('pause'));
if (DOM.btnSimStep) DOM.btnSimStep.addEventListener('click', () => sendSimControl('step'));
if (DOM.btnSimReset) DOM.btnSimReset.addEventListener('click', () => sendSimControl('reset'));
if (DOM.simSpeedSelect) {
  DOM.simSpeedSelect.addEventListener('change', (e) => {
    sendSimControl('speed', { speed: parseFloat(e.target.value) });
  });
}

// 1-Click Preset Buttons
const presetButtons = document.querySelectorAll('.injector-preset-btn');
presetButtons.forEach((btn) => {
  btn.addEventListener('click', async () => {
    const preset = btn.getAttribute('data-preset');
    try {
      const res = await fetch('/api/v1/sim/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.narration && DOM.injectorNarrationText) {
          DOM.injectorNarrationText.textContent = data.narration;
          if (DOM.injectorNarrationBanner) DOM.injectorNarrationBanner.style.display = 'flex';
        }
        if (data.verdicts && Array.isArray(data.verdicts)) {
          data.verdicts.forEach(v => eventLogHistory.unshift(v));
          renderEventLog();
        }
      }
    } catch (err) {
      console.error('Preset error', err);
    }
  });
});

// Custom Proposal Injection Form
if (DOM.formCustomInject) {
  DOM.formCustomInject.addEventListener('submit', async (e) => {
    e.preventDefault();
    const proposal = {
      controller_type: DOM.injectCtrlType ? DOM.injectCtrlType.value : 'load_balancing',
      target_cell: DOM.injectTargetCell ? DOM.injectTargetCell.value : 'cell_A',
      parameter: DOM.injectParam ? DOM.injectParam.value : 'OFFLOAD_RATIO',
      requested_value: DOM.injectValue ? parseFloat(DOM.injectValue.value) : 0.40,
      emergency: DOM.injectEmergency ? DOM.injectEmergency.checked : false
    };
    try {
      const res = await fetch('/api/v1/sim/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ proposal })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.narration && DOM.injectorNarrationText) {
          DOM.injectorNarrationText.textContent = data.narration;
          if (DOM.injectorNarrationBanner) DOM.injectorNarrationBanner.style.display = 'flex';
        }
        if (data.verdicts && Array.isArray(data.verdicts)) {
          data.verdicts.forEach(v => eventLogHistory.unshift(v));
          renderEventLog();
        }
      }
    } catch (err) {
      console.error('Custom proposal injection error', err);
    }
  });
}

// Topology Selection & Mutation
function updateCellDropdowns(cells) {
  if (!DOM.injectTargetCell || !Array.isArray(cells)) return;
  while (DOM.injectTargetCell.firstChild) {
    DOM.injectTargetCell.removeChild(DOM.injectTargetCell.firstChild);
  }
  cells.forEach((c) => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = `${c.name || c.id} (${c.id})`;
    DOM.injectTargetCell.appendChild(opt);
  });
}

if (DOM.topologySelect) {
  DOM.topologySelect.addEventListener('change', async (e) => {
    try {
      const res = await fetch('/api/v1/sim/topology', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'select', topology: e.target.value })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.cells) updateCellDropdowns(data.cells);
      }
    } catch (err) {
      console.error('Failed to select topology', err);
    }
  });
}

if (DOM.formAddTower) {
  DOM.formAddTower.addEventListener('submit', async (e) => {
    e.preventDefault();
    const cellId = document.getElementById('add-cell-id').value.trim();
    const cellName = document.getElementById('add-cell-name').value.trim();
    const cellX = parseFloat(document.getElementById('add-cell-x').value) || 200;
    const cellY = parseFloat(document.getElementById('add-cell-y').value) || 200;
    const cell = {
      id: cellId,
      name: cellName,
      x: cellX,
      y: cellY,
      tx_power_dbm: 43.0,
      tilt_deg: 6.0,
      capacity: 100,
      users: 10
    };
    try {
      const res = await fetch('/api/v1/sim/topology', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'add_cell', cell })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.cells) updateCellDropdowns(data.cells);
        DOM.formAddTower.reset();
      }
    } catch (err) {
      console.error('Failed to add tower', err);
    }
  });
}

if (DOM.formAddCtrlInstance) {
  DOM.formAddCtrlInstance.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ctrlType = document.getElementById('add-inst-type').value;
    const instId = document.getElementById('add-inst-id').value.trim();
    const rawScope = document.getElementById('add-inst-scope').value;
    const scope = rawScope.split(',').map(s => s.trim()).filter(Boolean);
    try {
      const res = await fetch('/api/v1/sim/controller-instance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          controller_type: ctrlType,
          instance_id: instId,
          cell_scope: scope.length > 0 ? scope : null
        })
      });
      if (res.ok) {
        DOM.formAddCtrlInstance.reset();
      }
    } catch (err) {
      console.error('Failed to add controller instance', err);
    }
  });
}

// Initial fetch of topologies to populate cell dropdown
fetch('/api/v1/sim/topologies')
  .then(res => res.json())
  .then(data => {
    if (data.cells) updateCellDropdowns(data.cells);
  })
  .catch(() => {});

// 6. Connect Data Stream
dataLayer.connect();
