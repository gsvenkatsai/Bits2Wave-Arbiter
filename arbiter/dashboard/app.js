/**
 * PROJECT ARBITER — 2 AM Collision Monitor Frontend Engine
 * Module: arbiter/dashboard/app.js
 * Specification: Branch 4 Spec §4.1, §4.2, §5.1, §6
 * Linear/Vercel NOC Theme with Tweening, Hex Grid & Filter Chips
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
  }
};

const urlParams = new URLSearchParams(window.location.search);
STATE.isMockMode = urlParams.get("demo") === "1" || urlParams.get("mock") === "1";

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
  kpiMapeSubtext: document.getElementById("kpiMapeSubtext"),

  trendDropped: document.getElementById("trendDropped"),
  trendPower: document.getElementById("trendPower"),
  trendSinr: document.getElementById("trendSinr"),
  trendMape: document.getElementById("trendMape"),

  ingressTickCounter: document.getElementById("ingressTickCounter"),
  ingressTableBody: document.getElementById("ingressTableBody"),
  cellStateContainer: document.getElementById("cellStateContainer"),
  decisionsTableBody: document.getElementById("decisionsTableBody"),

  chartDroppedUsers: document.getElementById("chartDroppedUsers"),
  chartPowerKw: document.getElementById("chartPowerKw"),
  chartDroppedNote: document.getElementById("chartDroppedNote"),
  chartPowerNote: document.getElementById("chartPowerNote"),

  hexTopologySvg: document.getElementById("hexTopologySvg"),
  inspectCellId: document.getElementById("inspectCellId"),
  inspectSleepBadge: document.getElementById("inspectSleepBadge"),
  inspectTx: document.getElementById("inspectTx"),
  inspectTilt: document.getElementById("inspectTilt"),
  inspectLoad: document.getElementById("inspectLoad"),
  inspectUsers: document.getElementById("inspectUsers"),
  inspectMode: document.getElementById("inspectMode"),

  btnExportIngressCsv: document.getElementById("btnExportIngressCsv"),
  btnExportDecisionsJson: document.getElementById("btnExportDecisionsJson")
};

function clearChildren(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
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

  if (typeUpper.includes("APPROVE")) span.classList.add("badge-approve");
  else if (typeUpper.includes("MODIFY")) span.classList.add("badge-modify");
  else if (typeUpper.includes("DELAY")) span.classList.add("badge-delay");
  else if (typeUpper.includes("REJECT")) span.classList.add("badge-reject");
  else if (typeUpper === "SYNTHETIC") span.classList.add("badge-synthetic");

  span.textContent = labelText || typeUpper || "ACTION";
  return span;
}

// ---------------------------------------------------------------------------
// NUMBER TWEENING ANIMATION FOR KPIS
// ---------------------------------------------------------------------------
function tweenValue(element, start, end, decimals = 0, suffix = "") {
  if (!element) return;
  const startTime = performance.now();
  const duration = 260;

  function animate(time) {
    const elapsed = time - startTime;
    const progress = Math.min(elapsed / duration, 1.0);
    const factor = 1 - Math.pow(1 - progress, 3);
    const current = start + (end - start) * factor;

    element.textContent = current.toFixed(decimals) + suffix;
    if (progress < 1.0) {
      requestAnimationFrame(animate);
    }
  }
  requestAnimationFrame(animate);
}

function setTrendIndicator(el, delta, reverse = false) {
  if (!el) return;
  if (Math.abs(delta) < 0.01) {
    el.className = "kpi-trend";
    return;
  }
  const isUp = delta > 0;
  const isBad = reverse ? !isUp : isUp;

  el.textContent = isUp ? `▲ ${Math.abs(delta).toFixed(1)}` : `▼ ${Math.abs(delta).toFixed(1)}`;
  el.className = `kpi-trend visible ${isBad ? "trend-up" : "trend-down"}`;
}

function updateAggregateKPIs(kpis) {
  if (!kpis) return;

  if (kpis.total_dropped_users !== undefined) {
    const val = Number(kpis.total_dropped_users);
    setTrendIndicator(DOM.trendDropped, val - STATE.prevKPIs.drops, false);
    tweenValue(DOM.kpiDroppedUsers, STATE.prevKPIs.drops, val, 0);
    STATE.prevKPIs.drops = val;
  }

  if (kpis.total_power_kw !== undefined) {
    const val = Number(kpis.total_power_kw);
    setTrendIndicator(DOM.trendPower, val - STATE.prevKPIs.power, false);
    tweenValue(DOM.kpiTotalPower, STATE.prevKPIs.power, val, 2);
    STATE.prevKPIs.power = val;
  }

  if (kpis.mean_sinr_db !== undefined) {
    const val = Number(kpis.mean_sinr_db);
    setTrendIndicator(DOM.trendSinr, val - STATE.prevKPIs.sinr, true);
    tweenValue(DOM.kpiMeanSinr, STATE.prevKPIs.sinr, val, 1);
    STATE.prevKPIs.sinr = val;
  }

  if (kpis.twin_mape_pct !== undefined) {
    const val = Number(kpis.twin_mape_pct);
    setTrendIndicator(DOM.trendMape, val - STATE.prevKPIs.mape, false);
    tweenValue(DOM.kpiTwinMape, STATE.prevKPIs.mape, val, 1, "%");
    STATE.prevKPIs.mape = val;

    if (val >= CONFIG.MAPE_GUARD_THRESHOLD_PCT) {
      DOM.kpiTwinMape.style.color = "var(--action-reject)";
      DOM.kpiMapeSubtext.textContent = "⚠️ Guard breach: ≥ 10.0%";
      DOM.kpiMapeSubtext.style.color = "var(--action-reject)";
    } else {
      DOM.kpiTwinMape.style.color = "var(--action-approve)";
      DOM.kpiMapeSubtext.textContent = "Guard floor < 10.0% (Safe)";
      DOM.kpiMapeSubtext.style.color = "var(--text-muted)";
    }
  }
}

// ---------------------------------------------------------------------------
// INGRESS STREAM & REAL-TIME FILTERING
// ---------------------------------------------------------------------------
function appendIngressRow(timestamp, proposal, badge) {
  const firstRow = DOM.ingressTableBody.querySelector(".empty-state");
  if (firstRow && firstRow.parentElement) firstRow.parentElement.remove();

  const item = { timestamp, proposal, badge };
  STATE.rawIngressRows.unshift(item);
  if (STATE.rawIngressRows.length > CONFIG.MAX_INGRESS_TABLE_ROWS) {
    STATE.rawIngressRows.pop();
  }

  renderIngressTable();
}

function renderIngressTable() {
  clearChildren(DOM.ingressTableBody);

  const filtered = STATE.rawIngressRows.filter(row => {
    const ctrlMatch = STATE.activeFilters.ctrl === "ALL" || (row.proposal && row.proposal.controller === STATE.activeFilters.ctrl);
    const actionMatch = STATE.activeFilters.action === "ALL" || (row.badge && row.badge.badge_type && row.badge.badge_type.toUpperCase().includes(STATE.activeFilters.action));
    return ctrlMatch && actionMatch;
  });

  if (filtered.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.className = "empty-state";
    td.textContent = "No proposals match active filters.";
    tr.appendChild(td);
    DOM.ingressTableBody.appendChild(tr);
    return;
  }

  filtered.forEach((row, index) => {
    const tr = document.createElement("tr");
    if (index === 0) tr.classList.add("row-animated");

    tr.appendChild(createTextCell(row.timestamp));
    tr.appendChild(createTextCell(row.proposal ? row.proposal.controller : "—"));
    tr.appendChild(createTextCell(row.proposal ? row.proposal.target : "—"));
    tr.appendChild(createTextCell(row.proposal ? row.proposal.param : "—"));
    tr.appendChild(createTextCell(row.proposal ? row.proposal.value : "—"));

    const tdBadge = document.createElement("td");
    if (row.badge) {
      tdBadge.appendChild(createActionBadge(row.badge.badge_type, row.badge.label));
    } else {
      tdBadge.textContent = "—";
    }
    tr.appendChild(tdBadge);
    tr.appendChild(createTextCell(row.badge ? row.badge.reason : "—"));

    // Micro-flash top row
    if (index === 0 && row.badge) {
      const bType = String(row.badge.badge_type).toUpperCase();
      if (bType.includes("APPROVE")) tr.classList.add("flash-approve");
      else if (bType.includes("MODIFY")) tr.classList.add("flash-modify");
      else if (bType.includes("DELAY")) tr.classList.add("flash-delay");
      else if (bType.includes("REJECT")) tr.classList.add("flash-reject");
      setTimeout(() => tr.classList.remove("flash-approve", "flash-modify", "flash-delay", "flash-reject"), 380);
    }

    DOM.ingressTableBody.appendChild(tr);
  });
}

// ---------------------------------------------------------------------------
// CANVAS DUAL-LINE CHARTS WITH GLOWING HEAD DOT
// ---------------------------------------------------------------------------
function renderCanvasLineChart(canvas, seriesA, seriesB, labelA, labelB, colorA, colorB) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;

  ctx.clearRect(0, 0, width, height);
  const n = seriesA.length;

  if (n < 2) {
    ctx.fillStyle = "#475569";
    ctx.font = "11px 'JetBrains Mono', monospace";
    ctx.fillText("Buffering time-series ticks...", 20, height / 2);
    return;
  }

  let allVals = [...seriesA];
  if (seriesB && seriesB.length > 0) allVals = allVals.concat(seriesB);
  const minVal = 0;
  const maxVal = Math.max(...allVals, 1.0) * 1.18;

  // Grid
  ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let y = 0; y < height; y += height / 4) {
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
  }
  ctx.stroke();

  const padLeft = 32, padRight = 12, padBottom = 16, padTop = 10;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const getX = (idx) => padLeft + (idx / (CONFIG.CHART_HISTORY_TICKS - 1)) * plotW;
  const getY = (val) => padTop + plotH - ((val - minVal) / (maxVal - minVal)) * plotH;

  // Labels
  ctx.fillStyle = "#64748b";
  ctx.font = "9px 'JetBrains Mono', monospace";
  ctx.fillText(maxVal.toFixed(1), 4, padTop + 8);
  ctx.fillText("0", 12, height - 4);

  function drawSeries(series, strokeColor, glowColor) {
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < series.length; i++) {
      const x = getX(i);
      const y = getY(series[i]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    if (series.length > 0) {
      const lastX = getX(series.length - 1);
      const lastY = getY(series[series.length - 1]);

      ctx.fillStyle = glowColor;
      ctx.beginPath();
      ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
      ctx.fill();

      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(lastX, lastY, 2, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  if (seriesB && seriesB.length > 0) {
    drawSeries(seriesB, colorB, "rgba(239, 68, 68, 0.4)");
  }
  drawSeries(seriesA, colorA, "rgba(16, 185, 129, 0.5)");
}

function updateCharts(batchTick, arbiterDrops, uncoordDrops, arbiterPower, uncoordPower) {
  STATE.history.ticks.push(batchTick);
  STATE.history.arbiterDrops.push(arbiterDrops);
  if (uncoordDrops !== null) STATE.history.uncoordDrops.push(uncoordDrops);
  STATE.history.arbiterPower.push(arbiterPower);
  if (uncoordPower !== null) STATE.history.uncoordPower.push(uncoordPower);

  if (STATE.history.ticks.length > CONFIG.CHART_HISTORY_TICKS) {
    STATE.history.ticks.shift();
    STATE.history.arbiterDrops.shift();
    if (STATE.history.uncoordDrops.length > 0) STATE.history.uncoordDrops.shift();
    STATE.history.arbiterPower.shift();
    if (STATE.history.uncoordPower.length > 0) STATE.history.uncoordPower.shift();
  }

  renderCanvasLineChart(
    DOM.chartDroppedUsers,
    STATE.history.arbiterDrops,
    STATE.history.uncoordDrops,
    "ARBITER", "Uncoordinated", "#10b981", "#ef4444"
  );

  renderCanvasLineChart(
    DOM.chartPowerKw,
    STATE.history.arbiterPower,
    STATE.history.uncoordPower,
    "ARBITER", "Uncoordinated", "#10b981", "#ef4444"
  );

  if (STATE.isMockMode) {
    DOM.chartDroppedNote.textContent = "Data Source: MOCK / SYNTHETIC generator active (?mock=1)";
    DOM.chartPowerNote.textContent = "Data Source: MOCK / SYNTHETIC generator active (?mock=1)";
  } else {
    DOM.chartDroppedNote.textContent = "Baseline: Uncoordinated reference comparator (§4.2)";
    DOM.chartPowerNote.textContent = "Baseline: Nocturnal sleep policy comparison";
  }
}

// ---------------------------------------------------------------------------
// INTERACTIVE HEXAGONAL TOPOLOGY (Panel 3)
// ---------------------------------------------------------------------------
function getHexPoints(cx, cy, r = 32) {
  const points = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 3) * i - (Math.PI / 6);
    points.push(`${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`);
  }
  return points.join(" ");
}

function renderHexTopology() {
  if (!DOM.hexTopologySvg) return;
  clearChildren(DOM.hexTopologySvg);

  STATE.cells.forEach(cell => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const isSelected = cell.id === STATE.selectedCellId;
    g.setAttribute("class", `hex-cell ${cell.sleeping ? "sleeping" : "active"} ${isSelected ? "selected" : ""}`);
    g.setAttribute("data-cell-id", cell.id);

    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    poly.setAttribute("class", "hex-cell-polygon");
    poly.setAttribute("points", getHexPoints(cell.x, cell.y, 34));
    g.appendChild(poly);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("class", "hex-label");
    label.setAttribute("x", cell.x);
    label.setAttribute("y", cell.y - 2);
    label.textContent = cell.id.replace("cell_", "C-");
    g.appendChild(label);

    const sub = document.createElementNS("http://www.w3.org/2000/svg", "text");
    sub.setAttribute("class", "hex-sublabel");
    sub.setAttribute("x", cell.x);
    sub.setAttribute("y", cell.y + 11);
    sub.textContent = cell.sleeping ? "SLEEP" : `${(cell.load * 100).toFixed(0)}%`;
    g.appendChild(sub);

    g.addEventListener("click", () => {
      STATE.selectedCellId = cell.id;
      renderHexTopology();
      updateInspector(cell);
    });

    DOM.hexTopologySvg.appendChild(g);
  });
}

function updateInspector(cell) {
  if (!cell || !DOM.inspectCellId) return;
  DOM.inspectCellId.textContent = cell.name;
  DOM.inspectSleepBadge.textContent = cell.sleeping ? "SLEEPING" : "ACTIVE";
  DOM.inspectSleepBadge.className = `badge ${cell.sleeping ? "badge-delay" : "badge-approve"}`;
  DOM.inspectTx.textContent = `${cell.tx.toFixed(1)} dBm`;
  DOM.inspectTilt.textContent = `${cell.tilt.toFixed(1)}°`;
  DOM.inspectLoad.textContent = `${(cell.load * 100).toFixed(0)}%`;
  DOM.inspectUsers.textContent = cell.users;
  DOM.inspectMode.textContent = cell.sleeping ? "Energy Saving Mode" : "Active Service";
}

// ---------------------------------------------------------------------------
// WEBSOCKET CLIENT & TICK PARSER
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
    } catch (err) {
      console.error("Failed to parse WS tick update", err);
    }
  };

  STATE.ws.onclose = () => {
    updateConnectionStatus("disconnected", `Reconnecting in ${(STATE.wsReconnectDelay / 1000).toFixed(0)}s`);
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (STATE.wsReconnectTimer) clearTimeout(STATE.wsReconnectTimer);
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
  if (type === "connected") DOM.connectionStatus.classList.add("status-connected");
  else if (type === "reconnecting") DOM.connectionStatus.classList.add("status-reconnecting");
  else DOM.connectionStatus.classList.add("status-disconnected");
  DOM.connectionText.textContent = label;
}

function handleTickUpdate(msg) {
  if (!msg || msg.type !== "TICK_UPDATE") return;

  if (msg.timestamp) DOM.clockDisplay.textContent = msg.timestamp;
  if (msg.batch_tick !== undefined) DOM.ingressTickCounter.textContent = `Tick: #${msg.batch_tick}`;

  if (msg.aggregate_kpis) {
    updateAggregateKPIs(msg.aggregate_kpis);
    const arbDrop = msg.aggregate_kpis.total_dropped_users || 0;
    const arbPower = msg.aggregate_kpis.total_power_kw || 0;
    const uncoordDrop = msg.aggregate_kpis.uncoord_dropped_users ?? null;
    const uncoordPower = msg.aggregate_kpis.uncoord_power_kw ?? null;
    updateCharts(msg.batch_tick || 0, arbDrop, uncoordDrop, arbPower, uncoordPower);
  }

  const proposals = msg.ingress_proposals || [];
  const badges = msg.arbitration_badges || [];
  const count = Math.max(proposals.length, badges.length);

  for (let i = 0; i < count; i++) {
    appendIngressRow(msg.timestamp, proposals[i], badges[i]);
  }
}

// ---------------------------------------------------------------------------
// CELL STATES REST POLLER (Awaiting Branch 3 Handling)
// ---------------------------------------------------------------------------
async function fetchCellStates() {
  try {
    const res = await fetch("/api/v1/state/cells");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const cells = await res.json();
    renderCellStates(cells);
  } catch (err) {
    renderCellStates([]);
  }
}

function renderCellStates(cells) {
  clearChildren(DOM.cellStateContainer);

  const titleEl = document.querySelector(".topology-panel .panel-title");
  if (cells && cells.length > 0) {
    if (titleEl) {
      titleEl.textContent = `Physical Cell Topology (${cells.length}-Cell Cluster)`;
    }

    cells.forEach((liveCell, idx) => {
      const match = STATE.cells.find(c => c.id === liveCell.cell_id) || STATE.cells[idx];
      if (match) {
        match.id = liveCell.cell_id;
        match.tx = liveCell.current_tx_power_dbm;
        match.tilt = liveCell.antenna_tilt_deg;
        match.load = liveCell.load_ratio;
        match.users = liveCell.active_users;
        match.sleeping = liveCell.is_sleeping;
      }
    });
  } else if (titleEl) {
    titleEl.textContent = "Physical Cell Topology (5-Cell Cluster)";
  }

  renderHexTopology();
  const selected = STATE.cells.find(c => c.id === STATE.selectedCellId) || STATE.cells[0];
  if (selected) updateInspector(selected);
}

// ---------------------------------------------------------------------------
// AUDIT DECISIONS REST POLLER
// ---------------------------------------------------------------------------
async function pollDecisionsHistory() {
  if (document.hidden) return;

  try {
    const res = await fetch("/api/v1/decisions/history?limit=50");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const decisions = await res.json();
    STATE.rawDecisions = decisions;
    renderDecisionsTable(decisions);
    STATE.decisionsPollInterval = CONFIG.DECISIONS_POLL_INTERVAL_MS;
  } catch (err) {
    STATE.decisionsPollInterval = Math.min(
      STATE.decisionsPollInterval * CONFIG.DECISIONS_POLL_BACKOFF_FACTOR,
      CONFIG.DECISIONS_POLL_MAX_INTERVAL_MS
    );
  } finally {
    if (STATE.decisionsPollTimer) clearTimeout(STATE.decisionsPollTimer);
    STATE.decisionsPollTimer = setTimeout(pollDecisionsHistory, STATE.decisionsPollInterval);
  }
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

  decisions.forEach(d => {
    const tr = document.createElement("tr");
    tr.appendChild(createTextCell(`#${d.batch_tick}`));

    const tdAction = document.createElement("td");
    tdAction.appendChild(createActionBadge(d.action, d.action));
    tr.appendChild(tdAction);

    tr.appendChild(createTextCell(d.approved_value !== null ? Number(d.approved_value).toFixed(1) : "—"));

    const tdDelta = createTextCell(d.delta_utility !== undefined ? Number(d.delta_utility).toFixed(2) : "—");
    if (d.delta_utility > 0) tdDelta.style.color = "var(--action-approve)";
    tr.appendChild(tdDelta);

    tr.appendChild(createTextCell(d.reason_code));
    tr.appendChild(createTextCell(d.hold_count !== undefined ? d.hold_count : "0"));

    const dateStr = d.decided_at_epoch_ms ? new Date(d.decided_at_epoch_ms).toISOString().substring(11, 23) : "—";
    tr.appendChild(createTextCell(dateStr));

    DOM.decisionsTableBody.appendChild(tr);
  });
}

// ---------------------------------------------------------------------------
// CLIENT-SIDE CSV & JSON EXPORTERS
// ---------------------------------------------------------------------------
function exportToCsv(filename, rows) {
  if (!rows || !rows.length) return;
  const separator = ",";
  const keys = ["timestamp", "controller", "target", "param", "value", "action", "reason"];
  const csvContent = [
    keys.join(separator),
    ...rows.map(r => [
      r.timestamp,
      r.proposal ? r.proposal.controller : "",
      r.proposal ? r.proposal.target : "",
      r.proposal ? r.proposal.param : "",
      r.proposal ? r.proposal.value : "",
      r.badge ? r.badge.badge_type : "",
      r.badge ? `"${r.badge.reason}"` : ""
    ].join(separator))
  ].join("\n");

  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function exportToJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

if (DOM.btnExportIngressCsv) {
  DOM.btnExportIngressCsv.addEventListener("click", () => {
    exportToCsv(`arbiter_ingress_${Date.now()}.csv`, STATE.rawIngressRows);
  });
}

if (DOM.btnExportDecisionsJson) {
  DOM.btnExportDecisionsJson.addEventListener("click", () => {
    exportToJson(`arbiter_decisions_${Date.now()}.json`, STATE.rawDecisions);
  });
}

// ---------------------------------------------------------------------------
// FILTER CHIP INTERACTIONS
// ---------------------------------------------------------------------------
document.querySelectorAll(".filter-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    const type = chip.getAttribute("data-filter-type");
    const val = chip.getAttribute("data-filter-val");

    document.querySelectorAll(`.filter-chip[data-filter-type="${type}"]`).forEach(c => c.classList.remove("active"));
    chip.classList.add("active");

    STATE.activeFilters[type] = val;
    renderIngressTable();
  });
});

// ---------------------------------------------------------------------------
// MOCK TELEMETRY GENERATOR (?mock=1)
// ---------------------------------------------------------------------------
function startMockGenerator() {
  DOM.mockBanner.style.display = "block";
  DOM.mockBanner.textContent = "DEMO DATA — SYNTHETIC SIMULATION (?demo=1)";
  DOM.provenanceBadge.style.display = "inline-block";
  DOM.provenanceBadge.textContent = "DEMO DATA";
  updateConnectionStatus("connected", "DEMO DATA ACTIVE");

  const controllers = ["ES", "LB", "CC", "PS"];
  const targetCells = ["cell_A", "cell_B", "cell_C", "cell_D"];
  const params = ["SLEEP_STATE", "OFFLOAD_RATIO", "TX_POWER_DBM", "TILT_DEG"];
  const badgeTemplates = [
    { badge_type: "APPROVE", label: "APPROVE", reason: "NONE" },
    { badge_type: "MODIFY",  label: "MODIFY (15%)", reason: "HEADROOM" },
    { badge_type: "DELAY",   label: "DELAY (hold 1)", reason: "ACTIVE_HO" },
    { badge_type: "REJECT",  label: "REJECT (cyclic)", reason: "CYCLIC_PINGPONG" }
  ];

  STATE.mockIntervalTimer = setInterval(() => {
    STATE.mockTickIndex++;
    const now = new Date();
    const timeStr = now.toTimeString().split(" ")[0] + "." + String(now.getMilliseconds()).padStart(3, "0");

    const idx = STATE.mockTickIndex % 4;
    const mockProposal = {
      controller: controllers[idx],
      target: targetCells[idx % targetCells.length],
      param: params[idx],
      value: Number(((idx + 1) * 2.5).toFixed(1))
    };
    const mockBadge = badgeTemplates[idx];

    const baseDrops = 6 + Math.sin(STATE.mockTickIndex * 0.18) * 3;
    const arbDrops = Math.max(0, baseDrops * 0.55);
    const uncoordDrops = Math.max(0, baseDrops * 1.45);

    const basePower = 18.2 + Math.cos(STATE.mockTickIndex * 0.12) * 2.2;
    const arbPower = basePower * 0.88;
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
        mean_sinr_db: Number((14.5 + Math.sin(STATE.mockTickIndex * 0.1) * 1.8).toFixed(1)),
        twin_mape_pct: Number((5.2 + Math.sin(STATE.mockTickIndex * 0.25) * 1.5).toFixed(1))
      }
    };

    DOM.clockDisplay.textContent = timeStr;
    DOM.ingressTickCounter.textContent = `Tick: #${STATE.mockTickIndex} (MOCK)`;
    updateAggregateKPIs(mockMsg.aggregate_kpis);
    appendIngressRow(timeStr, mockProposal, mockBadge);
    updateCharts(STATE.mockTickIndex, arbDrops, uncoordDrops, arbPower, uncoordPower);

    if (STATE.mockTickIndex % 3 === 0) {
      const c = STATE.cells[STATE.mockTickIndex % STATE.cells.length];
      c.load = Math.min(0.85, Math.max(0.1, c.load + (Math.random() - 0.5) * 0.08));
      renderHexTopology();
      if (c.id === STATE.selectedCellId) updateInspector(c);
    }
  }, CONFIG.MOCK_TICK_CADENCE_MS);
}

// ---------------------------------------------------------------------------
// INITIALIZATION
// ---------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  renderHexTopology();
  updateInspector(STATE.cells[0]);
  connectWebSocket();
  fetchCellStates();
  pollDecisionsHistory();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (STATE.decisionsPollTimer) clearTimeout(STATE.decisionsPollTimer);
  } else {
    pollDecisionsHistory();
    fetchCellStates();
  }
});
