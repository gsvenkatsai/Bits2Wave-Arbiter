/**
 * PROJECT ARBITER — 2 AM Collision Monitor Frontend
 * Module: arbiter/dashboard/app.js
 * Specification: Branch 4 Spec §4.1, §4.2, §5.1, §6
 */

// ---------------------------------------------------------------------------
// ASSUMPTIONS & CENTRAL CONFIGURATION
// ---------------------------------------------------------------------------
const CONFIG = {
  // WebSocket reconnect exponential backoff
  WS_RECONNECT_INITIAL_MS: 1000,
  WS_RECONNECT_MAX_MS: 16000,
  WS_RECONNECT_FACTOR: 2.0,

  // REST polling for audit decisions
  DECISIONS_POLL_INTERVAL_MS: 2000,
  DECISIONS_POLL_BACKOFF_FACTOR: 1.5,
  DECISIONS_POLL_MAX_INTERVAL_MS: 15000,

  // Buffer and history bounds
  MAX_INGRESS_TABLE_ROWS: 50,
  CHART_HISTORY_TICKS: 30,

  // Mock mode tick cadence (?mock=1)
  MOCK_TICK_CADENCE_MS: 500,

  // Twin MAPE safety guard threshold (shared/constants.py line 6: 10.0%)
  MAPE_GUARD_THRESHOLD_PCT: 10.0,

  // Badge colors from Spec §5.1 pages 6-7
  BADGE_COLORS: {
    APPROVE: "green",
    MODIFY: "blue",
    DELAY: "amber",
    REJECT: "red"
  }
};

// ---------------------------------------------------------------------------
// APPLICATION STATE
// ---------------------------------------------------------------------------
const STATE = {
  ws: null,
  wsReconnectDelay: CONFIG.WS_RECONNECT_INITIAL_MS,
  wsReconnectTimer: null,
  decisionsPollInterval: CONFIG.DECISIONS_POLL_INTERVAL_MS,
  decisionsPollTimer: null,
  isMockMode: false,
  mockTickIndex: 0,
  mockIntervalTimer: null,

  // Time-series buffers for dual-line charts
  history: {
    ticks: [],
    arbiterDrops: [],
    uncoordDrops: [],
    arbiterPower: [],
    uncoordPower: []
  }
};

// Check query param ?mock=1
const urlParams = new URLSearchParams(window.location.search);
STATE.isMockMode = urlParams.get("mock") === "1";

// ---------------------------------------------------------------------------
// DOM REFERENCES
// ---------------------------------------------------------------------------
const DOM = {
  mockBanner: document.getElementById("mockBanner"),
  provenanceBadge: document.getElementById("provenanceBadge"),
  connectionStatus: document.getElementById("connectionStatus"),
  connectionText: document.getElementById("connectionText"),
  clockDisplay: document.getElementById("clockDisplay"),

  kpiDroppedUsers: document.getElementById("kpiDroppedUsers"),
  kpiTotalPower: document.getElementById("kpiTotalPower"),
  kpiMeanSinr: document.getElementById("kpiMeanSinr"),
  kpiTwinMape: document.getElementById("kpiTwinMape"),

  ingressTickCounter: document.getElementById("ingressTickCounter"),
  ingressTableBody: document.getElementById("ingressTableBody"),
  cellStateContainer: document.getElementById("cellStateContainer"),
  decisionsTableBody: document.getElementById("decisionsTableBody"),

  chartDroppedUsers: document.getElementById("chartDroppedUsers"),
  chartPowerKw: document.getElementById("chartPowerKw"),
  chartDroppedNote: document.getElementById("chartDroppedNote"),
  chartPowerNote: document.getElementById("chartPowerNote")
};

// ---------------------------------------------------------------------------
// SAFE DOM HELPERS (No innerHTML with raw server data)
// ---------------------------------------------------------------------------
function clearChildren(element) {
  while (element.firstChild) {
    element.removeChild(element.firstChild);
  }
}

function createTextCell(text) {
  const td = document.createElement("td");
  td.textContent = String(text ?? "—");
  return td;
}

function createActionBadge(badgeType, labelText) {
  const span = document.createElement("span");
  span.className = "badge";
  const typeUpper = String(badgeType || "").toUpperCase();

  if (typeUpper === "APPROVE") {
    span.classList.add("badge-approve");
  } else if (typeUpper === "MODIFY") {
    span.classList.add("badge-modify");
  } else if (typeUpper === "DELAY") {
    span.classList.add("badge-delay");
  } else if (typeUpper === "REJECT") {
    span.classList.add("badge-reject");
  } else if (typeUpper === "SYNTHETIC") {
    span.classList.add("badge-synthetic");
  }
  span.textContent = labelText || typeUpper || "ACTION";
  return span;
}

// ---------------------------------------------------------------------------
// KPI CARD UPDATER
// ---------------------------------------------------------------------------
function updateAggregateKPIs(kpis) {
  if (!kpis) return;

  if (kpis.total_dropped_users !== undefined) {
    DOM.kpiDroppedUsers.textContent = Number(kpis.total_dropped_users).toLocaleString();
  }
  if (kpis.total_power_kw !== undefined) {
    DOM.kpiTotalPower.textContent = Number(kpis.total_power_kw).toFixed(2);
  }
  if (kpis.mean_sinr_db !== undefined) {
    DOM.kpiMeanSinr.textContent = Number(kpis.mean_sinr_db).toFixed(1);
  }
  if (kpis.twin_mape_pct !== undefined) {
    const mapeVal = Number(kpis.twin_mape_pct);
    DOM.kpiTwinMape.textContent = mapeVal.toFixed(1) + "%";
    // Threshold warning color
    if (mapeVal >= CONFIG.MAPE_GUARD_THRESHOLD_PCT) {
      DOM.kpiTwinMape.style.color = "var(--badge-red-fg)";
    } else {
      DOM.kpiTwinMape.style.color = "var(--badge-green-fg)";
    }
  }
}

// ---------------------------------------------------------------------------
// LIVE INGRESS TABLE UPDATER
// ---------------------------------------------------------------------------
function appendIngressRow(timestamp, proposal, badge) {
  // Remove placeholder on first row
  const firstRow = DOM.ingressTableBody.querySelector(".empty-state");
  if (firstRow && firstRow.parentElement) {
    firstRow.parentElement.remove();
  }

  const tr = document.createElement("tr");

  // Time
  tr.appendChild(createTextCell(timestamp));

  // Controller
  tr.appendChild(createTextCell(proposal ? proposal.controller : "—"));

  // Target Cell
  tr.appendChild(createTextCell(proposal ? proposal.target : "—"));

  // Parameter
  tr.appendChild(createTextCell(proposal ? proposal.param : "—"));

  // Requested Value
  tr.appendChild(createTextCell(proposal ? proposal.value : "—"));

  // Action Badge (Spec §5.1)
  const tdBadge = document.createElement("td");
  if (badge) {
    tdBadge.appendChild(createActionBadge(badge.badge_type, badge.label));
  } else {
    tdBadge.textContent = "—";
  }
  tr.appendChild(tdBadge);

  // Reason
  tr.appendChild(createTextCell(badge ? badge.reason : "—"));

  // Prepend to show newest at top (scrolling table)
  DOM.ingressTableBody.insertBefore(tr, DOM.ingressTableBody.firstChild);

  // Prune table bounds
  while (DOM.ingressTableBody.children.length > CONFIG.MAX_INGRESS_TABLE_ROWS) {
    DOM.ingressTableBody.removeChild(DOM.ingressTableBody.lastChild);
  }
}

// ---------------------------------------------------------------------------
// DUAL-LINE CHARTS (Native HTML5 Canvas — No External Libraries)
// ---------------------------------------------------------------------------
function renderCanvasLineChart(canvas, seriesA, seriesB, labelA, labelB, colorA, colorB) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;

  // Clear
  ctx.clearRect(0, 0, width, height);

  const n = seriesA.length;
  if (n < 2) {
    ctx.fillStyle = "#64748b";
    ctx.font = "11px monospace";
    ctx.fillText("Buffering time-series ticks...", 20, height / 2);
    return;
  }

  // Determine scale
  let allVals = [...seriesA];
  if (seriesB && seriesB.length > 0) {
    allVals = allVals.concat(seriesB);
  }
  const minVal = 0;
  const maxVal = Math.max(...allVals, 1.0) * 1.15;

  // Draw grid lines
  ctx.strokeStyle = "#1e293b";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let y = 0; y < height; y += height / 4) {
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
  }
  ctx.stroke();

  // Helper to map index & val to canvas x, y
  const padLeft = 35;
  const padRight = 10;
  const padBottom = 15;
  const padTop = 10;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const getX = (idx) => padLeft + (idx / (CONFIG.CHART_HISTORY_TICKS - 1)) * plotW;
  const getY = (val) => padTop + plotH - ((val - minVal) / (maxVal - minVal)) * plotH;

  // Axis labels
  ctx.fillStyle = "#64748b";
  ctx.font = "9px monospace";
  ctx.fillText(maxVal.toFixed(1), 4, padTop + 8);
  ctx.fillText("0", 12, height - 4);

  // Draw Series A (ARBITER)
  ctx.strokeStyle = colorA;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < n; i++) {
    const x = getX(i);
    const y = getY(seriesA[i]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Draw Series B (Uncoordinated) if present
  if (seriesB && seriesB.length > 0) {
    ctx.strokeStyle = colorB;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < seriesB.length; i++) {
      const x = getX(i);
      const y = getY(seriesB[i]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
}

function updateCharts(batchTick, arbiterDrops, uncoordDrops, arbiterPower, uncoordPower) {
  // Push to circular history
  STATE.history.ticks.push(batchTick);
  STATE.history.arbiterDrops.push(arbiterDrops);
  if (uncoordDrops !== null) STATE.history.uncoordDrops.push(uncoordDrops);
  STATE.history.arbiterPower.push(arbiterPower);
  if (uncoordPower !== null) STATE.history.uncoordPower.push(uncoordPower);

  // Trim to max ticks
  if (STATE.history.ticks.length > CONFIG.CHART_HISTORY_TICKS) {
    STATE.history.ticks.shift();
    STATE.history.arbiterDrops.shift();
    if (STATE.history.uncoordDrops.length > 0) STATE.history.uncoordDrops.shift();
    STATE.history.arbiterPower.shift();
    if (STATE.history.uncoordPower.length > 0) STATE.history.uncoordPower.shift();
  }

  // Render Dropped Users
  renderCanvasLineChart(
    DOM.chartDroppedUsers,
    STATE.history.arbiterDrops,
    STATE.isMockMode ? STATE.history.uncoordDrops : [],
    "ARBITER",
    "Uncoordinated",
    "#10b981",
    "#ef4444"
  );

  // Render Power kW
  renderCanvasLineChart(
    DOM.chartPowerKw,
    STATE.history.arbiterPower,
    STATE.isMockMode ? STATE.history.uncoordPower : [],
    "ARBITER",
    "Uncoordinated",
    "#10b981",
    "#ef4444"
  );

  // Source Provenance Note (Spec §4.2 vs live stream)
  if (STATE.isMockMode) {
    DOM.chartDroppedNote.textContent = "Data Source: MOCK / SYNTHETIC generator active (?mock=1)";
    DOM.chartPowerNote.textContent = "Data Source: MOCK / SYNTHETIC generator active (?mock=1)";
  } else {
    // Exact spec compliance: WebSocket stream §4.2 only broadcasts ARBITER live telemetry.
    DOM.chartDroppedNote.textContent = "Uncoordinated baseline: UNSPECIFIED (no live baseline stream in §4.2 WS contract)";
    DOM.chartPowerNote.textContent = "Uncoordinated baseline: UNSPECIFIED (no live baseline stream in §4.2 WS contract)";
  }
}

// ---------------------------------------------------------------------------
// WEBSOCKET CLIENT (Auto-reconnect with Exponential Backoff)
// ---------------------------------------------------------------------------
function connectWebSocket() {
  if (STATE.isMockMode) {
    startMockGenerator();
    return;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/api/v1/ws/live`;

  updateConnectionStatus("reconnecting", "Connecting to live stream...");

  try {
    STATE.ws = new WebSocket(wsUrl);
  } catch (err) {
    scheduleReconnect();
    return;
  }

  STATE.ws.onopen = () => {
    STATE.wsReconnectDelay = CONFIG.WS_RECONNECT_INITIAL_MS;
    updateConnectionStatus("connected", "Live WebSocket Connected");
  };

  STATE.ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleTickUpdate(msg);
    } catch (parseErr) {
      console.error("Failed to parse WS tick update", parseErr);
    }
  };

  STATE.ws.onerror = () => {
    // Handled in onclose
  };

  STATE.ws.onclose = () => {
    updateConnectionStatus("disconnected", `Disconnected — Reconnecting in ${(STATE.wsReconnectDelay / 1000).toFixed(0)}s`);
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (STATE.wsReconnectTimer) {
    clearTimeout(STATE.wsReconnectTimer);
  }
  STATE.wsReconnectTimer = setTimeout(() => {
    STATE.wsReconnectDelay = Math.min(
      STATE.wsReconnectDelay * CONFIG.WS_RECONNECT_FACTOR,
      CONFIG.WS_RECONNECT_MAX_MS
    );
    connectWebSocket();
  }, STATE.wsReconnectDelay);
}

function updateConnectionStatus(type, label) {
  DOM.connectionStatus.className = "status-indicator";
  if (type === "connected") {
    DOM.connectionStatus.classList.add("status-connected");
  } else if (type === "reconnecting") {
    DOM.connectionStatus.classList.add("status-reconnecting");
  } else {
    DOM.connectionStatus.classList.add("status-disconnected");
  }
  DOM.connectionText.textContent = label;
}

// ---------------------------------------------------------------------------
// TICK UPDATE PROCESSOR
// ---------------------------------------------------------------------------
function handleTickUpdate(msg) {
  if (!msg || msg.type !== "TICK_UPDATE") return;

  if (msg.timestamp) {
    DOM.clockDisplay.textContent = msg.timestamp;
  }
  if (msg.batch_tick !== undefined) {
    DOM.ingressTickCounter.textContent = `Tick: #${msg.batch_tick}`;
  }

  // Update top KPIs
  if (msg.aggregate_kpis) {
    updateAggregateKPIs(msg.aggregate_kpis);

    // Update charts with live ARBITER values
    const arbDrop = msg.aggregate_kpis.total_dropped_users || 0;
    const arbPower = msg.aggregate_kpis.total_power_kw || 0;
    updateCharts(msg.batch_tick || 0, arbDrop, null, arbPower, null);
  }

  // Ingress & Badges
  const proposals = msg.ingress_proposals || [];
  const badges = msg.arbitration_badges || [];
  const count = Math.max(proposals.length, badges.length);

  if (count > 0) {
    for (let i = 0; i < count; i++) {
      appendIngressRow(msg.timestamp, proposals[i], badges[i]);
    }
  }
}

// ---------------------------------------------------------------------------
// CELL STATE FETCHER (GET /api/v1/state/cells)
// Handles "Awaiting Branch 3" when [] returned
// ---------------------------------------------------------------------------
async function fetchCellStates() {
  try {
    const res = await fetch("/api/v1/state/cells");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const cells = await res.json();
    renderCellStates(cells);
  } catch (err) {
    renderCellStatesError(err);
  }
}

function renderCellStates(cells) {
  clearChildren(DOM.cellStateContainer);

  if (!cells || cells.length === 0) {
    // Explicit "Awaiting Branch 3" info card per user instructions
    const box = document.createElement("div");
    box.className = "info-box-awaiting";

    const title = document.createElement("div");
    title.className = "title";
    title.textContent = "ℹ️ Cell Topology: Awaiting Branch 3";

    const desc = document.createElement("div");
    desc.className = "desc";
    desc.textContent = "Actuator & digital twin physical storage layers are not yet merged. GET /api/v1/state/cells is in STUB mode.";

    box.appendChild(title);
    box.appendChild(desc);
    DOM.cellStateContainer.appendChild(box);
    return;
  }

  // If populated, render cell grid
  const grid = document.createElement("div");
  grid.style.display = "grid";
  grid.style.gridTemplateColumns = "repeat(auto-fit, minmax(180px, 1fr))";
  grid.style.gap = "10px";

  cells.forEach((cell) => {
    const card = document.createElement("div");
    card.style.background = "rgba(0, 0, 0, 0.2)";
    card.style.border = "1px solid var(--border-subtle)";
    card.style.borderRadius = "4px";
    card.style.padding = "10px";
    card.style.fontSize = "0.75rem";
    card.style.fontFamily = "monospace";

    const cellId = document.createElement("div");
    cellId.style.fontWeight = "bold";
    cellId.textContent = `Cell: ${cell.cell_id}`;
    card.appendChild(cellId);

    const tx = document.createElement("div");
    tx.textContent = `Tx Power: ${cell.current_tx_power_dbm} dBm`;
    card.appendChild(tx);

    const tilt = document.createElement("div");
    tilt.textContent = `Tilt: ${cell.antenna_tilt_deg}°`;
    card.appendChild(tilt);

    const load = document.createElement("div");
    load.textContent = `Load Ratio: ${(cell.load_ratio * 100).toFixed(0)}%`;
    card.appendChild(load);

    grid.appendChild(card);
  });

  DOM.cellStateContainer.appendChild(grid);
}

function renderCellStatesError(err) {
  clearChildren(DOM.cellStateContainer);
  const div = document.createElement("div");
  div.style.color = "var(--badge-red-fg)";
  div.style.fontSize = "0.8rem";
  div.textContent = `Failed to fetch cell states: ${err.message}`;
  DOM.cellStateContainer.appendChild(div);
}

// ---------------------------------------------------------------------------
// DECISIONS AUDIT HISTORY POLLER (GET /api/v1/decisions/history?limit=50)
// Pauses when document.hidden, backs off on fetch error
// ---------------------------------------------------------------------------
async function pollDecisionsHistory() {
  if (document.hidden) {
    // Paused while hidden
    return;
  }

  try {
    const res = await fetch("/api/v1/decisions/history?limit=50");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const decisions = await res.json();
    renderDecisionsTable(decisions);

    // Reset backoff on success
    STATE.decisionsPollInterval = CONFIG.DECISIONS_POLL_INTERVAL_MS;
  } catch (err) {
    // Backoff on fetch error
    STATE.decisionsPollInterval = Math.min(
      STATE.decisionsPollInterval * CONFIG.DECISIONS_POLL_BACKOFF_FACTOR,
      CONFIG.DECISIONS_POLL_MAX_INTERVAL_MS
    );
  } finally {
    scheduleNextDecisionsPoll();
  }
}

function scheduleNextDecisionsPoll() {
  if (STATE.decisionsPollTimer) {
    clearTimeout(STATE.decisionsPollTimer);
  }
  STATE.decisionsPollTimer = setTimeout(pollDecisionsHistory, STATE.decisionsPollInterval);
}

function renderDecisionsTable(decisions) {
  clearChildren(DOM.decisionsTableBody);

  if (!decisions || decisions.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.className = "empty-state";
    td.textContent = "No audit decisions recorded yet.";
    tr.appendChild(td);
    DOM.decisionsTableBody.appendChild(tr);
    return;
  }

  decisions.forEach((d) => {
    const tr = document.createElement("tr");

    // Tick
    tr.appendChild(createTextCell(`#${d.batch_tick}`));

    // Action Badge
    const tdAction = document.createElement("td");
    tdAction.appendChild(createActionBadge(d.action, d.action));
    tr.appendChild(tdAction);

    // Approved Value
    tr.appendChild(createTextCell(d.approved_value !== null ? Number(d.approved_value).toFixed(1) : "—"));

    // Delta Utility
    const tdDelta = createTextCell(d.delta_utility !== undefined ? Number(d.delta_utility).toFixed(2) : "—");
    if (d.delta_utility > 0) tdDelta.style.color = "var(--badge-green-fg)";
    tr.appendChild(tdDelta);

    // Reason
    tr.appendChild(createTextCell(d.reason_code));

    // Hold count
    tr.appendChild(createTextCell(d.hold_count !== undefined ? d.hold_count : "0"));

    // Decided At Epoch
    const dateStr = d.decided_at_epoch_ms ? new Date(d.decided_at_epoch_ms).toISOString().substring(11, 23) : "—";
    tr.appendChild(createTextCell(dateStr));

    DOM.decisionsTableBody.appendChild(tr);
  });
}

// ---------------------------------------------------------------------------
// VISIBILITY CHANGE LISTENER
// Pauses polling when hidden, resumes immediately when visible
// ---------------------------------------------------------------------------
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (STATE.decisionsPollTimer) {
      clearTimeout(STATE.decisionsPollTimer);
      STATE.decisionsPollTimer = null;
    }
  } else {
    // Visible again: trigger immediate poll
    pollDecisionsHistory();
    fetchCellStates();
  }
});

// ---------------------------------------------------------------------------
// MOCK TELEMETRY GENERATOR (?mock=1)
// ---------------------------------------------------------------------------
function startMockGenerator() {
  DOM.mockBanner.style.display = "block";
  DOM.provenanceBadge.style.display = "inline-block";
  DOM.provenanceBadge.textContent = "SYNTHETIC";
  updateConnectionStatus("connected", "MOCK GENERATOR ACTIVE (?mock=1)");

  const controllers = ["ES", "LB", "CC", "PS"];
  const targetCells = ["cell_A", "cell_B", "cell_C"];
  const params = ["SLEEP_STATE", "OFFLOAD_RATIO", "TX_POWER_DBM", "TILT_DEG"];
  const badgeTemplates = [
    { badge_type: "APPROVE", label: "APPROVE", color: "green", reason: "NONE" },
    { badge_type: "MODIFY", label: "MODIFY (15%)", color: "blue", reason: "HEADROOM" },
    { badge_type: "DELAY", label: "DELAY (hold 2)", color: "amber", reason: "ACTIVE_HO" },
    { badge_type: "REJECT", label: "REJECT (cyclic)", color: "red", reason: "CYCLIC_PINGPONG" }
  ];

  STATE.mockIntervalTimer = setInterval(() => {
    STATE.mockTickIndex++;
    const now = new Date();
    const timeStr = now.toTimeString().split(" ")[0] + "." + String(now.getMilliseconds()).padStart(3, "0");

    const idx = STATE.mockTickIndex % 4;
    const mockProposal = {
      controller: controllers[idx],
      target: targetCells[idx % 3],
      param: params[idx],
      value: (idx + 1) * 2.5
    };
    const mockBadge = badgeTemplates[idx];

    // Compute mock dual series
    const baseDrops = 5 + Math.sin(STATE.mockTickIndex * 0.2) * 3;
    const arbDrops = Math.max(0, baseDrops * 0.6);
    const uncoordDrops = Math.max(0, baseDrops * 1.4);

    const basePower = 2.4 + Math.cos(STATE.mockTickIndex * 0.1) * 0.5;
    const arbPower = basePower * 0.9;
    const uncoordPower = basePower * 1.15;

    const mockMsg = {
      type: "TICK_UPDATE",
      batch_tick: STATE.mockTickIndex,
      timestamp: timeStr,
      ingress_proposals: [mockProposal],
      arbitration_badges: [mockBadge],
      aggregate_kpis: {
        total_dropped_users: Math.round(arbDrops),
        total_power_kw: Number(arbPower.toFixed(2)),
        mean_sinr_db: Number((14.0 + Math.sin(STATE.mockTickIndex * 0.1) * 2.0).toFixed(1)),
        twin_mape_pct: Number((4.5 + Math.sin(STATE.mockTickIndex * 0.3) * 1.2).toFixed(1))
      }
    };

    DOM.clockDisplay.textContent = timeStr;
    DOM.ingressTickCounter.textContent = `Tick: #${STATE.mockTickIndex} (MOCK)`;
    updateAggregateKPIs(mockMsg.aggregate_kpis);
    appendIngressRow(timeStr, mockProposal, mockBadge);
    updateCharts(STATE.mockTickIndex, arbDrops, uncoordDrops, arbPower, uncoordPower);
  }, CONFIG.MOCK_TICK_CADENCE_MS);
}

// ---------------------------------------------------------------------------
// INITIALIZATION
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
  fetchCellStates();
  pollDecisionsHistory();
});
