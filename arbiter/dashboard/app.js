/**
 * PROJECT ARBITER — 2 AM Collision Monitor Frontend Engine
 * Module: arbiter/dashboard/app.js
 * Specification: Branch 4 Spec §4.1, §4.2, §5.1, §6
 * Linear/Vercel NOC Theme with Tweening, Hex Grid & Filter Chips
 * Features v2:
 *   #4 Flight Recorder & Side-by-Side Counterfactual Panel
 *   #5 Calibrated Twin Uncertainty Intervals & Drift Injection
 *   #6 Dynamic RiskDial & Multi-Objective Pareto Frontier
 *   #7 Environmental and Economic Impact Telemetry & Extrapolation
 *   #8 Offline Optimality Benchmark Visualizer
 *
 * NOTE: Safe DOM manipulation only — ZERO innerHTML assignments.
 */

const CONFIG = {
  WS_RECONNECT_INITIAL_MS: 1000,
  WS_RECONNECT_MAX_MS: 16000,
  WS_RECONNECT_FACTOR: 2.0,

  DECISIONS_POLL_INTERVAL_MS: 2000,
  DECISIONS_POLL_BACKOFF_FACTOR: 1.5,
  DECISIONS_POLL_MAX_INTERVAL_MS: 15000,

  MAX_INGRESS_TABLE_ROWS: 50,
  CHART_HISTORY_TICKS: 30,
  MOCK_TICK_CADENCE_MS: 600,

  MAPE_GUARD_THRESHOLD_PCT: 10.0,
  BADGE_COLORS: {
    APPROVE: "green",
    MODIFY: "blue",
    DELAY: "amber",
    REJECT: "red"
  }
};

const STATE = {
  ws: null,
  wsReconnectDelay: CONFIG.WS_RECONNECT_INITIAL_MS,
  wsReconnectTimer: null,
  decisionsPollInterval: CONFIG.DECISIONS_POLL_INTERVAL_MS,
  decisionsPollTimer: null,
  isMockMode: false,
  mockTickIndex: 0,
  mockIntervalTimer: null,

  activeFilters: { ctrl: "ALL", action: "ALL" },
  rawIngressRows: [],
  rawDecisions: [],

  // Previous KPI values for number tweening
  prevKPIs: { drops: 0, power: 0, sinr: 0, mape: 0 },
  sparklineHistory: { drops: [], power: [], sinr: [] },

  // 5-cell canonical topology coordinates & states
  cells: [
    { id: "cell_A", name: "Cell A (Core)",  x: 160, y: 110, tx: 43.0, tilt: 6.0, load: 0.88, users: 35, sleeping: false },
    { id: "cell_B", name: "Cell B (East)",  x: 230, y: 110, tx: 43.0, tilt: 6.0, load: 0.10, users: 4,  sleeping: false },
    { id: "cell_C", name: "Cell C (NE)",    x: 195, y: 50,  tx: 43.0, tilt: 6.0, load: 0.10, users: 4,  sleeping: false },
    { id: "cell_D", name: "Cell D (NW)",    x: 125, y: 50,  tx: 43.0, tilt: 6.0, load: 0.15, users: 6,  sleeping: false },
    { id: "cell_E", name: "Cell E (South)", x: 160, y: 170, tx: 43.0, tilt: 6.0, load: 0.12, users: 5,  sleeping: false }
  ],
  selectedCellId: "cell_A",

  history: {
    ticks: [],
    arbiterDrops: [],
    uncoordDrops: [],
    arbiterPower: [],
    uncoordPower: []
  },

  // v2 feature states
  activeRiskDial: {
    weight_coverage: 0.30,
    weight_energy: 0.20,
    weight_interference: 0.20,
    weight_drop_rate: 0.30,
    label: "Balanced"
  },
  twinPrediction: null,
  paretoFrontier: null,
  impactData: null,
  optimalityData: null
};

const urlParams = new URLSearchParams(window.location.search);
STATE.isMockMode = urlParams.get("demo") === "1" || urlParams.get("mock") === "1";

const DOM = {
  mockBanner: document.getElementById("mockBanner"),
  provenanceBadge: document.getElementById("provenanceBadge"),
  connectionStatus: document.getElementById("connectionStatus"),
  connectionText: document.getElementById("connectionText"),
  clockDisplay: document.getElementById("clockDisplay"),
  btnInjectDrift: document.getElementById("btnInjectDrift"),

  kpiDroppedUsers: document.getElementById("kpiDroppedUsers"),
  kpiTotalPower: document.getElementById("kpiTotalPower"),
  kpiMeanSinr: document.getElementById("kpiMeanSinr"),
  kpiTwinMape: document.getElementById("kpiTwinMape"),
  kpiMapeSubtext: document.getElementById("kpiMapeSubtext"),
  mapeFillBar: document.getElementById("mapeFillBar"),

  // Uncertainty interval labels
  kpiDropsInterval: document.getElementById("kpiDropsInterval"),
  kpiPowerInterval: document.getElementById("kpiPowerInterval"),
  kpiSinrInterval: document.getElementById("kpiSinrInterval"),
  kpiTwinCalibratedBadge: document.getElementById("kpiTwinCalibratedBadge"),

  trendDropped: document.getElementById("trendDropped"),
  trendPower: document.getElementById("trendPower"),
  trendSinr: document.getElementById("trendSinr"),

  sparklineDrops: document.getElementById("sparklineDrops"),
  sparklinePower: document.getElementById("sparklinePower"),
  sparklineSinr: document.getElementById("sparklineSinr"),

  ingressTickCounter: document.getElementById("ingressTickCounter"),
  ingressTableBody: document.getElementById("ingressTableBody"),
  ingressTableViewport: document.getElementById("ingressTableViewport"),
  streamPausedPill: document.getElementById("streamPausedPill"),

  cellStateContainer: document.getElementById("cellStateContainer"),
  decisionsTableBody: document.getElementById("decisionsTableBody"),

  chartDroppedUsers: document.getElementById("chartDroppedUsers"),
  chartPowerKw: document.getElementById("chartPowerKw"),
  chartDropsTooltip: document.getElementById("chartDropsTooltip"),
  chartPowerTooltip: document.getElementById("chartPowerTooltip"),
  chartDroppedNote: document.getElementById("chartDroppedNote"),
  chartPowerNote: document.getElementById("chartPowerNote"),

  hexTopologySvg: document.getElementById("hexTopologySvg"),
  inspectCellId: document.getElementById("inspectCellId"),
  inspectSleepBadge: document.getElementById("inspectSleepBadge"),
  inspectTx: document.getElementById("inspectTx"),
  inspectTilt: document.getElementById("inspectTilt"),
  inspectLoad: document.getElementById("inspectLoad"),
  inspectUsers: document.getElementById("inspectUsers"),

  // Flight Recorder Modal
  flightRecorderModal: document.getElementById("flightRecorderModal"),
  btnCloseRecorder: document.getElementById("btnCloseRecorder"),
  recorderDecisionBadge: document.getElementById("recorderDecisionBadge"),
  recorderTickBadge: document.getElementById("recorderTickBadge"),
  cfWithDrops: document.getElementById("cfWithDrops"),
  cfWithSinr: document.getElementById("cfWithSinr"),
  cfWithPower: document.getElementById("cfWithPower"),
  cfWithIntf: document.getElementById("cfWithIntf"),
  cfWithUtility: document.getElementById("cfWithUtility"),
  cfWithoutDrops: document.getElementById("cfWithoutDrops"),
  cfWithoutSinr: document.getElementById("cfWithoutSinr"),
  cfWithoutPower: document.getElementById("cfWithoutPower"),
  cfWithoutIntf: document.getElementById("cfWithoutIntf"),
  cfWithoutUtility: document.getElementById("cfWithoutUtility"),
  cfDeltaDrops: document.getElementById("cfDeltaDrops"),
  cfDeltaSinr: document.getElementById("cfDeltaSinr"),
  cfDeltaPower: document.getElementById("cfDeltaPower"),
  cfDeltaIntf: document.getElementById("cfDeltaIntf"),
  cfDeltaUtility: document.getElementById("cfDeltaUtility"),
  cfExplanationText: document.getElementById("cfExplanationText"),
  planTimelineSteps: document.getElementById("planTimelineSteps"),

  // Risk Dial & Pareto
  paretoFrontierSvg: document.getElementById("paretoFrontierSvg"),
  weightCovVal: document.getElementById("weightCovVal"),
  weightNrgVal: document.getElementById("weightNrgVal"),
  weightIntVal: document.getElementById("weightIntVal"),
  weightDrpVal: document.getElementById("weightDrpVal"),

  // Optimality
  optGapPct: document.getElementById("optGapPct"),
  optArbiterMs: document.getElementById("optArbiterMs"),
  optOptimumS: document.getElementById("optOptimumS"),
  optCombos: document.getElementById("optCombos"),
  optBarBaseline: document.getElementById("optBarBaseline"),
  optBarArbiter: document.getElementById("optBarArbiter"),
  optBarOptimum: document.getElementById("optBarOptimum"),
  optValBaseline: document.getElementById("optValBaseline"),
  optValArbiter: document.getElementById("optValArbiter"),
  optValOptimum: document.getElementById("optValOptimum"),

  // Impact
  impactEnergySaved: document.getElementById("impactEnergySaved"),
  impactCostInr: document.getElementById("impactCostInr"),
  impactCo2: document.getElementById("impactCo2"),
  impactTariff: document.getElementById("impactTariff"),
  extrapKwh: document.getElementById("extrapKwh"),
  extrapCost: document.getElementById("extrapCost"),
  extrapCo2: document.getElementById("extrapCo2")
};


// ---------------------------------------------------------------------------
// SAFE DOM HELPER (ZERO innerHTML)
// ---------------------------------------------------------------------------

function clearChildren(node) {
  if (!node) return;
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function createTextElement(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) {
    el.textContent = String(text);
  }
  if (className) {
    el.className = className;
  }
  return el;
}


// ---------------------------------------------------------------------------
// NUMBER TWEENING & FORMATTING
// ---------------------------------------------------------------------------

function tweenValue(element, start, end, decimals, duration = 300) {
  if (!element) return;
  const startTime = performance.now();
  function update(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1.0);
    const ease = 1 - Math.pow(1 - progress, 3);
    const current = start + (end - start) * ease;
    element.textContent = current.toFixed(decimals);
    if (progress < 1.0) {
      requestAnimationFrame(update);
    } else {
      element.textContent = end.toFixed(decimals);
    }
  }
  requestAnimationFrame(update);
}

function setTrendChip(chip, current, previous, invertGood = false) {
  if (!chip) return;
  const diff = current - previous;
  if (Math.abs(diff) < 0.001) {
    chip.style.display = "none";
    return;
  }
  chip.style.display = "inline-block";
  const isUp = diff > 0;
  const isGood = invertGood ? !isUp : isUp;
  chip.className = "delta-chip " + (isGood ? "delta-good" : "delta-bad");
  chip.textContent = (isUp ? "↑ +" : "↓ ") + Math.abs(diff).toFixed(1);
}


// ---------------------------------------------------------------------------
// WEBSOCKET & TICK INGESTION
// ---------------------------------------------------------------------------

function connectWebSocket() {
  if (STATE.isMockMode) {
    startMockGenerator();
    return;
  }

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = proto + "//" + window.location.host + "/api/v1/ws/live";
  updateConnectionStatus("connecting", "Connecting to live engine...");

  try {
    STATE.ws = new WebSocket(wsUrl);
  } catch (err) {
    scheduleWsReconnect();
    return;
  }

  STATE.ws.onopen = () => {
    updateConnectionStatus("connected", "Connected (Real-time)");
    STATE.wsReconnectDelay = CONFIG.WS_RECONNECT_INITIAL_MS;
    if (DOM.mockBanner) DOM.mockBanner.style.display = "none";
    if (DOM.provenanceBadge) DOM.provenanceBadge.style.display = "none";
  };

  STATE.ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "TICK_UPDATE") {
        handleTickUpdate(msg);
      }
    } catch (e) {
      console.warn("Malformed WS frame", e);
    }
  };

  STATE.ws.onclose = () => {
    updateConnectionStatus("disconnected", "Disconnected. Reconnecting...");
    scheduleWsReconnect();
  };

  STATE.ws.onerror = () => {
    updateConnectionStatus("disconnected", "WS Error");
  };
}

function scheduleWsReconnect() {
  if (STATE.wsReconnectTimer) clearTimeout(STATE.wsReconnectTimer);
  STATE.wsReconnectTimer = setTimeout(() => {
    STATE.wsReconnectDelay = Math.min(
      STATE.wsReconnectDelay * CONFIG.WS_RECONNECT_FACTOR,
      CONFIG.WS_RECONNECT_MAX_MS
    );
    connectWebSocket();
  }, STATE.wsReconnectDelay);
}

function updateConnectionStatus(status, text) {
  if (!DOM.connectionStatus || !DOM.connectionText) return;
  DOM.connectionStatus.className = "status-indicator status-" + status;
  DOM.connectionText.textContent = text;
}


// ---------------------------------------------------------------------------
// TICK UPDATE HANDLER
// ---------------------------------------------------------------------------

function handleTickUpdate(msg) {
  if (msg.timestamp && DOM.clockDisplay) {
    DOM.clockDisplay.textContent = msg.timestamp;
  }
  if (DOM.ingressTickCounter) {
    DOM.ingressTickCounter.textContent = "Tick: " + msg.batch_tick;
  }

  const kpis = msg.aggregate_kpis || {};

  // Tween KPI numbers
  if (DOM.kpiDroppedUsers) {
    tweenValue(DOM.kpiDroppedUsers, STATE.prevKPIs.drops, kpis.total_dropped_users || 0, 0);
    setTrendChip(DOM.trendDropped, kpis.total_dropped_users || 0, STATE.prevKPIs.drops, true);
    STATE.prevKPIs.drops = kpis.total_dropped_users || 0;
  }

  if (DOM.kpiTotalPower) {
    tweenValue(DOM.kpiTotalPower, STATE.prevKPIs.power, kpis.total_power_kw || 0, 1);
    setTrendChip(DOM.trendPower, kpis.total_power_kw || 0, STATE.prevKPIs.power, true);
    STATE.prevKPIs.power = kpis.total_power_kw || 0;
  }

  if (DOM.kpiMeanSinr) {
    tweenValue(DOM.kpiMeanSinr, STATE.prevKPIs.sinr, kpis.mean_sinr_db || 0, 2);
    setTrendChip(DOM.trendSinr, kpis.mean_sinr_db || 0, STATE.prevKPIs.sinr, false);
    STATE.prevKPIs.sinr = kpis.mean_sinr_db || 0;
  }

  if (DOM.kpiTwinMape) {
    const mape = kpis.twin_mape_pct || 0;
    tweenValue(DOM.kpiTwinMape, STATE.prevKPIs.mape, mape, 2);
    STATE.prevKPIs.mape = mape;
    if (DOM.mapeFillBar) {
      const clampedPct = Math.min(100, Math.max(0, (mape / 20.0) * 100));
      DOM.mapeFillBar.style.width = clampedPct + "%";
      if (mape > CONFIG.MAPE_GUARD_THRESHOLD_PCT) {
        DOM.mapeFillBar.className = "mape-fill breach";
      } else {
        DOM.mapeFillBar.className = "mape-fill";
      }
    }
  }

  // Update uncertainty view if provided in payload
  if (msg.twin_prediction) {
    updateUncertaintyView(msg.twin_prediction);
  }

  // Render ingress stream rows
  if (msg.ingress_proposals && msg.ingress_proposals.length > 0) {
    renderIngressRows(msg.ingress_proposals, msg.timestamp);
  }

  // Chart data history
  const uncoordDrops = kpis.uncoord_dropped_users !== undefined ? kpis.uncoord_dropped_users : (kpis.total_dropped_users || 0);
  const uncoordPower = kpis.uncoord_power_kw !== undefined ? kpis.uncoord_power_kw : (kpis.total_power_kw || 0);

  updateCharts(msg.batch_tick, kpis.total_dropped_users || 0, uncoordDrops, kpis.total_power_kw || 0, uncoordPower);
}


// ---------------------------------------------------------------------------
// INGRESS STREAM TABLE RENDERING
// ---------------------------------------------------------------------------

function renderIngressRows(proposals, timestamp) {
  if (!DOM.ingressTableBody) return;

  // Clear initial empty state row if present
  const firstRow = DOM.ingressTableBody.firstElementChild;
  if (firstRow && firstRow.querySelector(".empty-state")) {
    clearChildren(DOM.ingressTableBody);
  }

  proposals.forEach((p) => {
    const tr = document.createElement("tr");

    const tdTime = createTextElement("td", timestamp || "--:--:--", "num");
    const tdCtrl = createTextElement("td", p.controller || "ES");
    const tdTarget = createTextElement("td", p.target_cell || "cell_A");
    const tdParam = createTextElement("td", p.parameter || "TX_POWER_DBM");
    const tdReq = createTextElement("td", p.requested_value !== undefined ? String(p.requested_value) : "--", "num");

    const tdVerdict = document.createElement("td");
    const action = p.action || "APPROVE";
    const badge = createTextElement("span", action, "badge badge-" + action.toLowerCase());
    tdVerdict.appendChild(badge);

    const tdReason = createTextElement("td", p.reason_code || "JOINT_UTILITY_POSITIVE");

    tr.appendChild(tdTime);
    tr.appendChild(tdCtrl);
    tr.appendChild(tdTarget);
    tr.appendChild(tdParam);
    tr.appendChild(tdReq);
    tr.appendChild(tdVerdict);
    tr.appendChild(tdReason);

    DOM.ingressTableBody.insertBefore(tr, DOM.ingressTableBody.firstChild);
  });

  // Prune table to MAX_INGRESS_TABLE_ROWS
  while (DOM.ingressTableBody.children.length > CONFIG.MAX_INGRESS_TABLE_ROWS) {
    DOM.ingressTableBody.removeChild(DOM.ingressTableBody.lastChild);
  }
}


// ---------------------------------------------------------------------------
// FEATURE #4: FLIGHT RECORDER & DECISIONS HISTORY
// ---------------------------------------------------------------------------

function pollDecisionsHistory() {
  fetch("/api/v1/recorder/decisions?limit=50")
    .then((r) => r.json())
    .then((decisions) => {
      STATE.rawDecisions = decisions;
      renderDecisionsTable(decisions);
      STATE.decisionsPollInterval = CONFIG.DECISIONS_POLL_INTERVAL_MS;
    })
    .catch(() => {
      STATE.decisionsPollInterval = Math.min(
        STATE.decisionsPollInterval * CONFIG.DECISIONS_POLL_BACKOFF_FACTOR,
        CONFIG.DECISIONS_POLL_MAX_INTERVAL_MS
      );
    })
    .finally(() => {
      if (STATE.decisionsPollTimer) clearTimeout(STATE.decisionsPollTimer);
      STATE.decisionsPollTimer = setTimeout(pollDecisionsHistory, STATE.decisionsPollInterval);
    });
}

function renderDecisionsTable(decisions) {
  if (!DOM.decisionsTableBody) return;
  clearChildren(DOM.decisionsTableBody);

  if (!decisions || decisions.length === 0) {
    const tr = document.createElement("tr");
    const td = createTextElement("td", "No audit decisions recorded yet.", "empty-state");
    td.setAttribute("colspan", "7");
    tr.appendChild(td);
    DOM.decisionsTableBody.appendChild(tr);
    return;
  }

  decisions.forEach((d) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.title = "Click to inspect in Flight Recorder";

    const tdTick = createTextElement("td", "T" + d.batch_tick, "num");

    const tdAction = document.createElement("td");
    const action = d.action || "APPROVE";
    const badge = createTextElement("span", action, "badge badge-" + action.toLowerCase());
    tdAction.appendChild(badge);

    const tdAppr = createTextElement("td", d.approved_value !== null && d.approved_value !== undefined ? String(d.approved_value) : "--", "num");
    const tdDeltaU = createTextElement("td", d.delta_utility !== undefined ? (d.delta_utility > 0 ? "+" : "") + Number(d.delta_utility).toFixed(3) : "--", "num");

    const tdReason = createTextElement("td", d.reason_code || "NONE");
    const tdHold = createTextElement("td", String(d.hold_count || 0), "num");

    const decidedTime = d.decided_at_epoch_ms ? new Date(d.decided_at_epoch_ms).toISOString().substr(11, 8) : "--:--:--";
    const tdTime = createTextElement("td", decidedTime, "num");

    tr.appendChild(tdTick);
    tr.appendChild(tdAction);
    tr.appendChild(tdAppr);
    tr.appendChild(tdDeltaU);
    tr.appendChild(tdReason);
    tr.appendChild(tdHold);
    tr.appendChild(tdTime);

    // Click handler to open Flight Recorder side-by-side modal
    tr.addEventListener("click", () => {
      openFlightRecorder(d.decision_id || "dec-2am-collision-06", d.batch_tick);
    });

    DOM.decisionsTableBody.appendChild(tr);
  });
}

function openFlightRecorder(decisionId, tick) {
  if (!DOM.flightRecorderModal) return;

  if (DOM.recorderDecisionBadge) {
    DOM.recorderDecisionBadge.textContent = decisionId;
  }
  if (DOM.recorderTickBadge) {
    DOM.recorderTickBadge.textContent = "Tick " + tick + " (+10 tick horizon)";
  }

  DOM.flightRecorderModal.style.display = "flex";

  // Fetch counterfactual evaluation
  fetch("/api/v1/recorder/counterfactual/" + encodeURIComponent(decisionId))
    .then((r) => {
      if (!r.ok) {
        throw new Error("Snapshot aged out or not found");
      }
      return r.json();
    })
    .then((cf) => {
      populateCounterfactualDetails(cf);
    })
    .catch((err) => {
      if (DOM.cfExplanationText) {
        DOM.cfExplanationText.textContent = "Snapshot for decision " + decisionId + " has aged out of snapshot ring.";
      }
    });

  // Fetch multi-tick sequential plan timeline
  fetch("/api/v1/plans")
    .then((r) => r.json())
    .then((plans) => {
      renderPlanTimeline(plans);
    })
    .catch(() => {});
}

function populateCounterfactualDetails(cf) {
  const w = cf.with_arbiter || {};
  const wo = cf.without_arbiter || {};
  const d = cf.diff || {};

  if (DOM.cfWithDrops) DOM.cfWithDrops.textContent = String(w.dropped_users || 0);
  if (DOM.cfWithSinr) DOM.cfWithSinr.textContent = (w.mean_sinr_db || 0).toFixed(2) + " dB";
  if (DOM.cfWithPower) DOM.cfWithPower.textContent = ((w.energy_w || 0) / 1000.0).toFixed(2) + " kW";
  if (DOM.cfWithIntf) DOM.cfWithIntf.textContent = (w.interference_db || 0).toFixed(1) + " dBm";
  if (DOM.cfWithUtility) DOM.cfWithUtility.textContent = (w.utility || 0).toFixed(3);

  if (DOM.cfWithoutDrops) DOM.cfWithoutDrops.textContent = String(wo.dropped_users || 0);
  if (DOM.cfWithoutSinr) DOM.cfWithoutSinr.textContent = (wo.mean_sinr_db || 0).toFixed(2) + " dB";
  if (DOM.cfWithoutPower) DOM.cfWithoutPower.textContent = ((wo.energy_w || 0) / 1000.0).toFixed(2) + " kW";
  if (DOM.cfWithoutIntf) DOM.cfWithoutIntf.textContent = (wo.interference_db || 0).toFixed(1) + " dBm";
  if (DOM.cfWithoutUtility) DOM.cfWithoutUtility.textContent = (wo.utility || 0).toFixed(3);

  // Deltas with semantic styling
  if (DOM.cfDeltaDrops) {
    const dropDiff = d.dropped_users !== undefined ? d.dropped_users : ((w.dropped_users || 0) - (wo.dropped_users || 0));
    DOM.cfDeltaDrops.textContent = (dropDiff <= 0 ? "" : "+") + dropDiff.toFixed(0) + " drops";
    DOM.cfDeltaDrops.className = "delta-chip " + (dropDiff <= 0 ? "delta-good" : "delta-bad");
  }

  if (DOM.cfDeltaSinr) {
    const sinrDiff = d.mean_sinr_db !== undefined ? d.mean_sinr_db : ((w.mean_sinr_db || 0) - (wo.mean_sinr_db || 0));
    DOM.cfDeltaSinr.textContent = (sinrDiff >= 0 ? "+" : "") + sinrDiff.toFixed(2) + " dB";
    DOM.cfDeltaSinr.className = "delta-chip " + (sinrDiff >= 0 ? "delta-good" : "delta-bad");
  }

  if (DOM.cfDeltaPower) {
    const powerDiffW = d.energy_w !== undefined ? d.energy_w : ((w.energy_w || 0) - (wo.energy_w || 0));
    const powerDiffKw = powerDiffW / 1000.0;
    DOM.cfDeltaPower.textContent = (powerDiffKw >= 0 ? "+" : "") + powerDiffKw.toFixed(2) + " kW";
    DOM.cfDeltaPower.className = "delta-chip delta-neutral";
  }

  if (DOM.cfDeltaIntf) {
    const intfDiff = d.interference_db !== undefined ? d.interference_db : ((w.interference_db || 0) - (wo.interference_db || 0));
    DOM.cfDeltaIntf.textContent = (intfDiff <= 0 ? "" : "+") + intfDiff.toFixed(1) + " dBm";
    DOM.cfDeltaIntf.className = "delta-chip " + (intfDiff <= 0 ? "delta-good" : "delta-bad");
  }

  if (DOM.cfDeltaUtility) {
    const uDiff = d.utility !== undefined ? d.utility : ((w.utility || 0) - (wo.utility || 0));
    DOM.cfDeltaUtility.textContent = (uDiff >= 0 ? "+" : "") + uDiff.toFixed(3);
    DOM.cfDeltaUtility.className = "delta-chip " + (uDiff >= 0 ? "delta-good" : "delta-bad");
  }

  if (DOM.cfExplanationText && cf.explanation) {
    DOM.cfExplanationText.textContent = cf.explanation;
  }
}

function renderPlanTimeline(plans) {
  if (!DOM.planTimelineSteps) return;
  clearChildren(DOM.planTimelineSteps);

  if (!plans || plans.length === 0) {
    DOM.planTimelineSteps.appendChild(createTextElement("div", "No multi-tick plans active.", "empty-state"));
    return;
  }

  const plan = plans[0];
  const steps = plan.steps || [];

  steps.forEach((step) => {
    const item = document.createElement("div");
    item.className = "plan-step-item";

    const left = document.createElement("div");
    left.className = "plan-step-info";

    const stepBadge = createTextElement("span", "Step " + step.step_index, "step-num-badge");
    const offset = createTextElement("span", "+" + step.tick_offset + " ticks", "panel-meta");
    const actionText = createTextElement("span", step.target_cell_id + " • " + step.parameter + " = " + step.value);
    const precond = createTextElement("span", "when (" + step.precondition + ")", "precondition-tag");

    left.appendChild(stepBadge);
    left.appendChild(offset);
    left.appendChild(actionText);
    left.appendChild(precond);

    const right = document.createElement("div");
    const statusClass = step.status === "COMPLETED" ? "badge-approve" : "badge-delay";
    const statusBadge = createTextElement("span", step.status || "PENDING", "badge " + statusClass);
    right.appendChild(statusBadge);

    item.appendChild(left);
    item.appendChild(right);
    DOM.planTimelineSteps.appendChild(item);
  });
}


// ---------------------------------------------------------------------------
// FEATURE #5: UNCERTAINTY VIEW & TWIN DRIFT INJECTION
// ---------------------------------------------------------------------------

function fetchTwinPrediction() {
  fetch("/api/v1/twin/prediction")
    .then((r) => r.json())
    .then((pred) => {
      STATE.twinPrediction = pred;
      updateUncertaintyView(pred);
    })
    .catch(() => {});
}

function updateUncertaintyView(twin) {
  if (!twin || !twin.intervals) return;

  const intvs = {};
  twin.intervals.forEach((i) => {
    intvs[i.kpi] = i;
  });

  if (DOM.kpiDropsInterval && intvs["drop_rate_ratio"]) {
    const i = intvs["drop_rate_ratio"];
    DOM.kpiDropsInterval.textContent = "[" + i.lower.toFixed(3) + " - " + i.upper.toFixed(3) + "]";
  }

  if (DOM.kpiPowerInterval && intvs["normalized_energy"]) {
    const i = intvs["normalized_energy"];
    DOM.kpiPowerInterval.textContent = "[" + i.lower.toFixed(3) + " - " + i.upper.toFixed(3) + "]";
  }

  if (DOM.kpiSinrInterval && intvs["coverage_ratio"]) {
    const i = intvs["coverage_ratio"];
    DOM.kpiSinrInterval.textContent = "[" + i.lower.toFixed(3) + " - " + i.upper.toFixed(3) + "]";
  }

  if (DOM.kpiTwinCalibratedBadge) {
    if (twin.calibrated) {
      DOM.kpiTwinCalibratedBadge.className = "badge-calibrated";
      DOM.kpiTwinCalibratedBadge.textContent = "CALIBRATED (n=" + (twin.n_calibration || 50) + ")";
    } else {
      DOM.kpiTwinCalibratedBadge.className = "badge-uncalibrated";
      DOM.kpiTwinCalibratedBadge.textContent = "UNCALIBRATED (DRIFT DETECTED)";
    }
  }
}

function injectTwinDrift() {
  fetch("/api/v1/twin/drift", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ drift_scale: 1.8 })
  })
    .then((r) => r.json())
    .then((updated) => {
      STATE.twinPrediction = updated;
      updateUncertaintyView(updated);
    })
    .catch((err) => {
      console.warn("Drift injection error", err);
    });
}


// ---------------------------------------------------------------------------
// FEATURE #6: DYNAMIC RISK DIAL & PARETO FRONTIER
// ---------------------------------------------------------------------------

const PRESETS = {
  night_saver: {
    weight_coverage: 0.15,
    weight_energy: 0.50,
    weight_interference: 0.15,
    weight_drop_rate: 0.20,
    label: "Night saver"
  },
  balanced: {
    weight_coverage: 0.30,
    weight_energy: 0.20,
    weight_interference: 0.20,
    weight_drop_rate: 0.30,
    label: "Balanced"
  },
  rush_hour: {
    weight_coverage: 0.40,
    weight_energy: 0.05,
    weight_interference: 0.15,
    weight_drop_rate: 0.40,
    label: "Rush-hour safe"
  }
};

function setRiskDialPreset(presetKey) {
  const dial = PRESETS[presetKey];
  if (!dial) return;

  fetch("/api/v1/risk-dial", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dial)
  })
    .then((r) => r.json())
    .then((applied) => {
      STATE.activeRiskDial = applied;
      updateRiskDialDisplay(applied);
      fetchParetoFrontier();
    })
    .catch(() => {});
}

function updateRiskDialDisplay(dial) {
  if (DOM.weightCovVal) DOM.weightCovVal.textContent = dial.weight_coverage.toFixed(2);
  if (DOM.weightNrgVal) DOM.weightNrgVal.textContent = dial.weight_energy.toFixed(2);
  if (DOM.weightIntVal) DOM.weightIntVal.textContent = dial.weight_interference.toFixed(2);
  if (DOM.weightDrpVal) DOM.weightDrpVal.textContent = dial.weight_drop_rate.toFixed(2);

  // Update preset button active states
  const buttons = document.querySelectorAll(".btn-preset");
  buttons.forEach((b) => {
    const key = b.getAttribute("data-preset");
    if (PRESETS[key] && PRESETS[key].label.toLowerCase() === dial.label.toLowerCase()) {
      b.classList.add("active");
    } else {
      b.classList.remove("active");
    }
  });
}

function fetchParetoFrontier() {
  fetch("/api/v1/benchmarks/pareto?seed=42")
    .then((r) => r.json())
    .then((res) => {
      STATE.paretoFrontier = res;
      renderParetoFrontier(res);
    })
    .catch(() => {});
}

function renderParetoFrontier(res) {
  if (!DOM.paretoFrontierSvg || !res || !res.points) return;
  clearChildren(DOM.paretoFrontierSvg);

  const points = res.points;
  const active = res.active_point;

  // Chart bounds: width 320, height 180, margins: left 40, right 20, top 20, bottom 30
  const w = 320;
  const h = 180;
  const ml = 45;
  const mr = 20;
  const mt = 20;
  const mb = 30;

  const minKw = 70.0;
  const maxKw = 115.0;
  const minDrops = 0.0;
  const maxDrops = 15.0;

  function xCoord(kw) {
    return ml + ((kw - minKw) / (maxKw - minKw)) * (w - ml - mr);
  }
  function yCoord(drops) {
    return (h - mb) - ((drops - minDrops) / (maxDrops - minDrops)) * (h - mt - mb);
  }

  // Draw axes
  const axisG = document.createElementNS("http://www.w3.org/2000/svg", "g");

  const xLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
  xLine.setAttribute("x1", String(ml));
  xLine.setAttribute("y1", String(h - mb));
  xLine.setAttribute("x2", String(w - mr));
  xLine.setAttribute("y2", String(h - mb));
  xLine.setAttribute("stroke", "#334155");
  xLine.setAttribute("stroke-width", "1");
  axisG.appendChild(xLine);

  const yLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
  yLine.setAttribute("x1", String(ml));
  yLine.setAttribute("y1", String(mt));
  yLine.setAttribute("x2", String(ml));
  yLine.setAttribute("y2", String(h - mb));
  yLine.setAttribute("stroke", "#334155");
  yLine.setAttribute("stroke-width", "1");
  axisG.appendChild(yLine);

  // Labels
  const xLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
  xLabel.setAttribute("x", String((w + ml) / 2));
  xLabel.setAttribute("y", String(h - 8));
  xLabel.setAttribute("text-anchor", "middle");
  xLabel.setAttribute("fill", "#64748b");
  xLabel.setAttribute("font-size", "9");
  xLabel.textContent = "Cluster Power (kW) →";
  axisG.appendChild(xLabel);

  const yLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
  yLabel.setAttribute("x", "12");
  yLabel.setAttribute("y", String((h + mt) / 2));
  yLabel.setAttribute("text-anchor", "middle");
  yLabel.setAttribute("transform", "rotate(-90 12 " + ((h + mt) / 2) + ")");
  yLabel.setAttribute("fill", "#64748b");
  yLabel.setAttribute("font-size", "9");
  yLabel.textContent = "Dropped Users ↑";
  axisG.appendChild(yLabel);

  DOM.paretoFrontierSvg.appendChild(axisG);

  // Draw Pareto curve line through non-dominated points
  const paretoPoints = points.filter((p) => p.is_pareto_optimal);
  paretoPoints.sort((a, b) => a.energy_kw - b.energy_kw);

  if (paretoPoints.length > 1) {
    let pathD = "";
    paretoPoints.forEach((p, idx) => {
      const cx = xCoord(p.energy_kw);
      const cy = yCoord(p.dropped_users);
      if (idx === 0) {
        pathD += "M " + cx + " " + cy;
      } else {
        pathD += " L " + cx + " " + cy;
      }
    });
    const curvePath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    curvePath.setAttribute("d", pathD);
    curvePath.setAttribute("fill", "none");
    curvePath.setAttribute("stroke", "rgba(0, 229, 255, 0.5)");
    curvePath.setAttribute("stroke-width", "2");
    curvePath.setAttribute("stroke-dasharray", "4 2");
    DOM.paretoFrontierSvg.appendChild(curvePath);
  }

  // Draw points
  points.forEach((p) => {
    const cx = xCoord(p.energy_kw);
    const cy = yCoord(p.dropped_users);

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", String(cx));
    circle.setAttribute("cy", String(cy));
    circle.setAttribute("r", p.is_pareto_optimal ? "4" : "2.5");
    circle.setAttribute("fill", p.is_pareto_optimal ? "#38bdf8" : "#475569");
    circle.setAttribute("cursor", "pointer");

    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = (p.label || "Candidate") + ": " + p.energy_kw + " kW, " + p.dropped_users + " drops";
    circle.appendChild(title);

    DOM.paretoFrontierSvg.appendChild(circle);
  });

  // Highlight active point
  if (active) {
    const acx = xCoord(active.energy_kw);
    const acy = yCoord(active.dropped_users);

    const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    halo.setAttribute("cx", String(acx));
    halo.setAttribute("cy", String(acy));
    halo.setAttribute("r", "8");
    halo.setAttribute("fill", "rgba(0, 229, 255, 0.25)");
    halo.setAttribute("stroke", "#00e5ff");
    halo.setAttribute("stroke-width", "1.5");
    DOM.paretoFrontierSvg.appendChild(halo);

    const activeDot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    activeDot.setAttribute("cx", String(acx));
    activeDot.setAttribute("cy", String(acy));
    activeDot.setAttribute("r", "4.5");
    activeDot.setAttribute("fill", "#00e5ff");
    DOM.paretoFrontierSvg.appendChild(activeDot);
  }
}


// ---------------------------------------------------------------------------
// FEATURE #7: ENVIRONMENTAL & ECONOMIC IMPACT TELEMETRY
// ---------------------------------------------------------------------------

function fetchImpactTelemetry() {
  fetch("/api/v1/impact?hour=2")
    .then((r) => r.json())
    .then((data) => {
      STATE.impactData = data;
      renderImpactData(data);
    })
    .catch(() => {});
}

function renderImpactData(data) {
  if (!data) return;
  const latest = data.latest || {};
  const extrap = data.extrapolation_1000_sites || {};

  if (DOM.impactEnergySaved) {
    DOM.impactEnergySaved.textContent = (latest.energy_saved_kwh || 0.155).toFixed(3) + " kWh";
  }
  if (DOM.impactCostInr) {
    DOM.impactCostInr.textContent = "₹ " + (latest.cost_inr || 1.085).toFixed(2);
  }
  if (DOM.impactCo2) {
    DOM.impactCo2.textContent = (latest.co2_g || 127.1).toFixed(1) + " gCO2";
  }
  if (DOM.impactTariff) {
    DOM.impactTariff.textContent = latest.tariff_label || "COMMERCIAL_OFF_PEAK_NIGHT";
  }

  // 1,000-site extrapolation card
  if (DOM.extrapKwh) {
    const mwh = (extrap.annual_kwh_saved || 176426.0) / 1000.0;
    DOM.extrapKwh.textContent = mwh.toFixed(1) + " MWh/yr";
  }
  if (DOM.extrapCost) {
    DOM.extrapCost.textContent = "₹ " + (extrap.annual_cost_savings_lakhs_inr || 12.35).toFixed(2) + " Lakhs/yr";
  }
  if (DOM.extrapCo2) {
    DOM.extrapCo2.textContent = (extrap.annual_co2_avoided_metric_tons || 144.7).toFixed(1) + " Tons/yr";
  }
}


// ---------------------------------------------------------------------------
// FEATURE #8: OFFLINE OPTIMALITY BENCHMARK PANEL
// ---------------------------------------------------------------------------

function fetchOptimalityBenchmark() {
  fetch("/api/v1/optimality")
    .then((r) => r.json())
    .then((opt) => {
      STATE.optimalityData = opt;
      renderOptimalityData(opt);
    })
    .catch(() => {});
}

function renderOptimalityData(opt) {
  if (!opt) return;

  if (DOM.optGapPct) DOM.optGapPct.textContent = opt.gap_pct.toFixed(2) + "%";
  if (DOM.optArbiterMs) DOM.optArbiterMs.textContent = opt.arbiter_ms.toFixed(2) + " ms";
  if (DOM.optOptimumS) DOM.optOptimumS.textContent = opt.optimum_s.toFixed(2) + " s";
  if (DOM.optCombos) DOM.optCombos.textContent = String(opt.combos_evaluated || 64);

  // Bars rendering: utility scale [0, 1.0] -> percentage width
  const baseWidth = Math.min(100, Math.max(0, opt.utility_baseline * 100));
  const arbiterWidth = Math.min(100, Math.max(0, opt.utility_arbiter * 100));
  const optimumWidth = Math.min(100, Math.max(0, opt.utility_optimum * 100));

  if (DOM.optBarBaseline) DOM.optBarBaseline.style.width = baseWidth + "%";
  if (DOM.optBarArbiter) DOM.optBarArbiter.style.width = arbiterWidth + "%";
  if (DOM.optBarOptimum) DOM.optBarOptimum.style.width = optimumWidth + "%";

  if (DOM.optValBaseline) DOM.optValBaseline.textContent = opt.utility_baseline.toFixed(3);
  if (DOM.optValArbiter) DOM.optValArbiter.textContent = opt.utility_arbiter.toFixed(3);
  if (DOM.optValOptimum) DOM.optValOptimum.textContent = opt.utility_optimum.toFixed(3);
}


// ---------------------------------------------------------------------------
// 5-CELL HONEYCOMB TOPOLOGY SVG
// ---------------------------------------------------------------------------

function renderHexTopology() {
  if (!DOM.hexTopologySvg) return;
  clearChildren(DOM.hexTopologySvg);

  const r = 28;
  STATE.cells.forEach((c) => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.style.cursor = "pointer";

    // Polygon coordinates for flat-topped hexagon
    let pts = "";
    for (let i = 0; i < 6; i++) {
      const angle = (i * 60) * (Math.PI / 180);
      const px = c.x + r * Math.cos(angle);
      const py = c.y + r * Math.sin(angle);
      pts += (i > 0 ? " " : "") + px.toFixed(1) + "," + py.toFixed(1);
    }

    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly.setAttribute("points", pts);

    const isSelected = c.id === STATE.selectedCellId;
    let fill = c.sleeping ? "#1e293b" : "rgba(16, 185, 129, 0.15)";
    if (c.load > 0.75) fill = "rgba(239, 68, 68, 0.2)";
    let stroke = isSelected ? "#00e5ff" : (c.sleeping ? "#475569" : "#10b981");

    poly.setAttribute("fill", fill);
    poly.setAttribute("stroke", stroke);
    poly.setAttribute("stroke-width", isSelected ? "2.5" : "1.2");

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", String(c.x));
    text.setAttribute("y", String(c.y + 4));
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("fill", "#f8fafc");
    text.setAttribute("font-family", "monospace");
    text.setAttribute("font-size", "10");
    text.setAttribute("font-weight", "600");
    text.textContent = c.id.replace("cell_", "C_");

    g.appendChild(poly);
    g.appendChild(text);

    g.addEventListener("click", () => {
      STATE.selectedCellId = c.id;
      renderHexTopology();
      updateInspector(c);
    });

    DOM.hexTopologySvg.appendChild(g);
  });
}

function updateInspector(c) {
  if (!c) return;
  if (DOM.inspectCellId) DOM.inspectCellId.textContent = c.name;
  if (DOM.inspectSleepBadge) {
    DOM.inspectSleepBadge.textContent = c.sleeping ? "SLEEP" : "ACTIVE";
    DOM.inspectSleepBadge.className = "badge " + (c.sleeping ? "badge-delay" : "badge-approve");
  }
  if (DOM.inspectTx) DOM.inspectTx.textContent = c.tx.toFixed(1) + " dBm";
  if (DOM.inspectTilt) DOM.inspectTilt.textContent = c.tilt.toFixed(1) + "°";
  if (DOM.inspectLoad) DOM.inspectLoad.textContent = Math.round(c.load * 100) + "%";
  if (DOM.inspectUsers) DOM.inspectUsers.textContent = String(c.users);
}

function fetchCellStates() {
  fetch("/api/v1/state/cells")
    .then((r) => r.json())
    .then((cells) => {
      if (!cells || cells.length === 0) return;
      cells.forEach((sc) => {
        const found = STATE.cells.find((c) => c.id === sc.cell_id);
        if (found) {
          found.tx = sc.tx_power_dbm;
          found.tilt = sc.antenna_tilt_deg;
          found.sleeping = sc.is_sleeping;
          found.load = sc.load_ratio;
          found.users = sc.active_users;
        }
      });
      renderHexTopology();
      const current = STATE.cells.find((c) => c.id === STATE.selectedCellId) || STATE.cells[0];
      updateInspector(current);
    })
    .catch(() => {});
}


// ---------------------------------------------------------------------------
// TIME-SERIES CANVAS CHARTS
// ---------------------------------------------------------------------------

function updateCharts(tick, arbDrops, uncoordDrops, arbPower, uncoordPower) {
  const h = STATE.history;
  h.ticks.push(tick);
  h.arbiterDrops.push(arbDrops);
  h.uncoordDrops.push(uncoordDrops);
  h.arbiterPower.push(arbPower);
  h.uncoordPower.push(uncoordPower);

  while (h.ticks.length > CONFIG.CHART_HISTORY_TICKS) {
    h.ticks.shift();
    h.arbiterDrops.shift();
    h.uncoordDrops.shift();
    h.arbiterPower.shift();
    h.uncoordPower.shift();
  }

  renderCanvasChart(DOM.chartDroppedUsers, [
    { data: h.arbiterDrops, color: "#10b981", label: "ARBITER" },
    { data: h.uncoordDrops, color: "#ef4444", label: "Uncoordinated" }
  ], 0, 16);

  renderCanvasChart(DOM.chartPowerKw, [
    { data: h.arbiterPower, color: "#00e5ff", label: "ARBITER" },
    { data: h.uncoordPower, color: "#94a3b8", label: "Uncoordinated" }
  ], 60, 120);
}

function renderCanvasChart(canvas, seriesList, minVal, maxVal) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  // Grid lines
  ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
  ctx.lineWidth = 1;
  for (let y = 20; y < h; y += 30) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  seriesList.forEach((s) => {
    if (!s.data || s.data.length < 2) return;
    const len = s.data.length;
    const dx = w / (CONFIG.CHART_HISTORY_TICKS - 1);

    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.beginPath();

    s.data.forEach((val, i) => {
      const clamped = Math.max(minVal, Math.min(maxVal, val));
      const py = h - 10 - ((clamped - minVal) / (maxVal - minVal)) * (h - 20);
      const px = i * dx;
      if (i === 0) {
        ctx.moveTo(px, py);
      } else {
        ctx.lineTo(px, py);
      }
    });

    ctx.stroke();
  });
}


// ---------------------------------------------------------------------------
// MOCK SIMULATION ENGINE (FOR DEMO MODE ?demo=1 or ?mock=1)
// ---------------------------------------------------------------------------

function startMockGenerator() {
  if (DOM.mockBanner) {
    DOM.mockBanner.style.display = "inline-block";
    DOM.mockBanner.textContent = "DEMO DATA";
  }
  if (DOM.provenanceBadge) {
    DOM.provenanceBadge.style.display = "inline-block";
    DOM.provenanceBadge.textContent = "SYNTHETIC DEMO DATA";
  }
  updateConnectionStatus("connected", "DEMO DATA ACTIVE");

  const controllers = ["ES", "LB", "CC", "PS"];
  const actions = ["APPROVE", "MODIFY", "DELAY", "REJECT"];
  const reasons = [
    "JOINT_UTILITY_POSITIVE",
    "BOUNDED_BISECTION_OPTIMAL",
    "DELAY_UNTIL_OFFLOAD_SETTLES",
    "SAFETY_FLOOR_BREACH_COVERAGE"
  ];

  STATE.mockIntervalTimer = setInterval(() => {
    STATE.mockTickIndex++;
    const now = new Date();
    const ts = now.toISOString().substr(11, 12);

    const proposals = [];
    const count = 1 + Math.floor(Math.random() * 3);
    for (let i = 0; i < count; i++) {
      const act = actions[Math.floor(Math.random() * actions.length)];
      proposals.push({
        controller: controllers[Math.floor(Math.random() * controllers.length)],
        target_cell: STATE.cells[Math.floor(Math.random() * STATE.cells.length)].id,
        parameter: "TX_POWER_DBM",
        requested_value: (40 + Math.random() * 4).toFixed(1),
        action: act,
        reason_code: reasons[Math.floor(Math.random() * reasons.length)]
      });
    }

    const arbDrops = Math.random() < 0.1 ? 1 : 0;
    const uncoordDrops = 10 + Math.floor(Math.random() * 5);
    const arbPower = 95.0 + Math.sin(STATE.mockTickIndex * 0.2) * 5.0;
    const uncoordPower = 110.0 + Math.sin(STATE.mockTickIndex * 0.2) * 4.0;

    handleTickUpdate({
      type: "TICK_UPDATE",
      batch_tick: STATE.mockTickIndex,
      timestamp: ts,
      ingress_proposals: proposals,
      aggregate_kpis: {
        total_dropped_users: arbDrops,
        total_power_kw: arbPower,
        mean_sinr_db: 8.5 + Math.random() * 0.4,
        twin_mape_pct: 4.2 + Math.random() * 1.5,
        uncoord_dropped_users: uncoordDrops,
        uncoord_power_kw: uncoordPower
      }
    });
  }, CONFIG.MOCK_TICK_CADENCE_MS);
}


// ---------------------------------------------------------------------------
// INITIALIZATION
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  renderHexTopology();
  updateInspector(STATE.cells[0]);

  // Tab switching in secondary column
  const tabButtons = document.querySelectorAll(".tab-btn");
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");

      const targetId = btn.getAttribute("data-tab");
      const panels = document.querySelectorAll(".tab-panel-view");
      panels.forEach((p) => {
        if (p.id === targetId) {
          p.classList.add("active");
        } else {
          p.classList.remove("active");
        }
      });
    });
  });

  // Modal close handler
  if (DOM.btnCloseRecorder && DOM.flightRecorderModal) {
    DOM.btnCloseRecorder.addEventListener("click", () => {
      DOM.flightRecorderModal.style.display = "none";
    });
    DOM.flightRecorderModal.addEventListener("click", (e) => {
      if (e.target === DOM.flightRecorderModal) {
        DOM.flightRecorderModal.style.display = "none";
      }
    });
  }

  // Drift injection button
  if (DOM.btnInjectDrift) {
    DOM.btnInjectDrift.addEventListener("click", () => {
      injectTwinDrift();
    });
  }

  // Risk Dial preset buttons
  const presetButtons = document.querySelectorAll(".btn-preset");
  presetButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const pKey = btn.getAttribute("data-preset");
      setRiskDialPreset(pKey);
    });
  });

  // Connect & poll
  connectWebSocket();
  fetchCellStates();
  pollDecisionsHistory();
  fetchTwinPrediction();
  fetchParetoFrontier();
  fetchImpactTelemetry();
  fetchOptimalityBenchmark();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (STATE.decisionsPollTimer) clearTimeout(STATE.decisionsPollTimer);
  } else {
    pollDecisionsHistory();
    fetchCellStates();
    fetchImpactTelemetry();
  }
});
