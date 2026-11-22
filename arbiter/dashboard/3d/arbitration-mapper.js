/**
 * PROJECT ARBITER — Arbitration Event & Animation Mapper
 * Module: arbiter/dashboard/3d/arbitration-mapper.js
 *
 * Pure functions mapping frame proposals, decisions, conflicts, and user
 * state deltas to deterministic 3D animation descriptors.
 * Fully testable in Node.js (node --test) without WebGL or DOM dependencies.
 */

/**
 * Viewport HUD bounding boxes (header, KPI row, verdict drawer, legend, bottom bar).
 * Exported as constants and helper for screen projection assertions and layout alignment.
 */
export const HUD_RECTANGLES = {
  header: (w, h) => ({ name: "header", x: 0, y: 0, w, h: 85 }),
  kpi_row: (w, h) => ({ name: "kpi_row", x: 20, y: 85, w: 480, h: 80 }),
  verdict_drawer: (w, h) => ({ name: "verdict_drawer", x: w - 320, y: 96, w: 300, h: 270 }),
  legend_collapsed: (w, h) => ({ name: "legend", x: 20, y: h - 50, w: 120, h: 30 }),
  legend_expanded: (w, h) => ({ name: "legend", x: 20, y: h - 280, w: 260, h: 200 }),
  bottom_bar: (w, h) => ({ name: "bottom_bar", x: 0, y: h - 85, w, h: 85 }),
};

export function getHudRectangles(width, height, options = {}) {
  const legendCollapsed = options.legendCollapsed !== undefined ? options.legendCollapsed : true;
  return [
    HUD_RECTANGLES.header(width, height),
    HUD_RECTANGLES.kpi_row(width, height),
    HUD_RECTANGLES.verdict_drawer(width, height),
    legendCollapsed ? HUD_RECTANGLES.legend_collapsed(width, height) : HUD_RECTANGLES.legend_expanded(width, height),
    HUD_RECTANGLES.bottom_bar(width, height),
  ];
}

/**
 * Compute controller node positions evenly spaced along a ring elevated above
 * the cluster bounds, keeping them clear of the top-left HUD quadrant and right-edge verdict drawer.
 */
export function computeControllerRingPositions(bounds = { minX: -250, maxX: 500, minZ: -500, maxZ: 433.01 }) {
  const minX = Number(bounds.minX !== undefined ? bounds.minX : -250);
  const maxX = Number(bounds.maxX !== undefined ? bounds.maxX : 500);
  const minZ = Number(bounds.minZ !== undefined ? bounds.minZ : (bounds.minY !== undefined ? bounds.minY : -500));
  const maxZ = Number(bounds.maxZ !== undefined ? bounds.maxZ : (bounds.maxY !== undefined ? bounds.maxY : 433.01));

  const centerX = (minX + maxX) / 2;
  const centerZ = (minZ + maxZ) / 2;
  const spanX = Math.abs(maxX - minX);
  const spanZ = Math.abs(maxZ - minZ);
  const clusterRadius = Math.max(spanX, spanZ) / 2;

  const R = Math.max(320, clusterRadius * 0.70); // Elevated & outside cluster bounds
  const H = 140; // Elevated above cluster
  const shiftZ = Math.max(160, clusterRadius * 0.38);
  const baseAngle = 80 * Math.PI / 180;

  const ctrlKeys = ["ENERGY_SAVING", "LOAD_BALANCING", "COVERAGE_CAPACITY", "PUBLIC_SAFETY"];
  const positions = {};
  ctrlKeys.forEach((key, i) => {
    const angle = baseAngle + i * (Math.PI / 2);
    positions[key] = {
      x: Math.round(centerX + R * Math.cos(angle)),
      y: H,
      z: Math.round(centerZ + shiftZ + R * Math.sin(angle)),
    };
  });
  return positions;
}

const DEFAULT_RING_POSITIONS = computeControllerRingPositions();

export const CONTROLLER_SPECS = {
  ENERGY_SAVING: {
    abbrev: "ES",
    name: "Energy Saving",
    color: "#10b981", // Green
    colorHex: 0x10b981,
    defaultPos: { ...DEFAULT_RING_POSITIONS.ENERGY_SAVING },
    associatedCell: "cell_C",
  },
  LOAD_BALANCING: {
    abbrev: "LB",
    name: "Load Balancing",
    color: "#38bdf8", // Blue
    colorHex: 0x38bdf8,
    defaultPos: { ...DEFAULT_RING_POSITIONS.LOAD_BALANCING },
    associatedCell: "cell_D",
  },
  COVERAGE_CAPACITY: {
    abbrev: "CC",
    name: "Coverage & Capacity",
    color: "#f59e0b", // Amber
    colorHex: 0xf59e0b,
    defaultPos: { ...DEFAULT_RING_POSITIONS.COVERAGE_CAPACITY },
    associatedCell: "cell_E",
  },
  PUBLIC_SAFETY: {
    abbrev: "PS",
    name: "Public Safety",
    color: "#ef4444", // Red
    colorHex: 0xef4444,
    defaultPos: { ...DEFAULT_RING_POSITIONS.PUBLIC_SAFETY },
    associatedCell: "cell_B",
  },
};

export const ACTION_COLORS = {
  APPROVE: "#10b981", // Green
  MODIFY: "#f59e0b",  // Amber
  DELAY: "#eab308",   // Yellow-amber
  REJECT: "#ef4444",  // Red
};

/**
 * Format parameter requested/approved values into human-readable labels.
 */
export function formatParamValue(param, value) {
  const p = String(param || "").toUpperCase();
  const val = Number(value);

  if (p.includes("SLEEP")) {
    return val >= 0.5 ? "SLEEP 1.0 (OFF)" : "SLEEP 0.0 (WAKE)";
  }
  if (p.includes("OFFLOAD")) {
    return `OFFLOAD ${Math.round(val * 100)}%`;
  }
  if (p.includes("POWER")) {
    return `TX ${val.toFixed(1)} dBm`;
  }
  if (p.includes("TILT")) {
    return `TILT ${val.toFixed(1)}°`;
  }
  return `${p} ${val}`;
}

/**
 * Map frame proposals to proposal packet animation descriptors.
 */
export function mapProposalsToPackets(proposals = []) {
  return proposals.map((prop) => {
    const ctrlKey = String(prop.controller_type || "ENERGY_SAVING").toUpperCase();
    const ctrlSpec = CONTROLLER_SPECS[ctrlKey] || CONTROLLER_SPECS.ENERGY_SAVING;

    return {
      type: "PROPOSAL_PACKET",
      commandId: String(prop.command_id || ""),
      controllerType: ctrlKey,
      controllerAbbrev: ctrlSpec.abbrev,
      controllerName: ctrlSpec.name,
      color: ctrlSpec.color,
      colorHex: ctrlSpec.colorHex,
      sourcePos: { ...ctrlSpec.defaultPos },
      targetCellId: String(prop.target_cell_id || ""),
      parameter: String(prop.parameter || ""),
      requestedValue: Number(prop.requested_value) || 0,
      label: formatParamValue(prop.parameter, prop.requested_value),
      emergency: Boolean(prop.emergency_flag),
      holdCount: Number(prop.hold_count) || 0,
    };
  });
}

/**
 * Map frame conflict edges to conflict arc animation descriptors.
 */
export function mapConflictsToEdges(conflicts = [], packets = []) {
  const packetMap = new Map(packets.map((p) => [p.commandId, p]));

  return conflicts.map((c, idx) => {
    const cmdA = String(c.a || "");
    const cmdB = String(c.b || "");
    const packetA = packetMap.get(cmdA);
    const packetB = packetMap.get(cmdB);

    return {
      type: "CONFLICT_ARC",
      conflictId: `conflict-${idx}-${cmdA}-${cmdB}`,
      sourceCommandId: cmdA,
      targetCommandId: cmdB,
      sourceController: packetA ? packetA.controllerAbbrev : "CMD_A",
      targetController: packetB ? packetB.controllerAbbrev : "CMD_B",
      conflictType: String(c.type || "COLLISION"),
      color: "#ef4444",
      label: String(c.type || "COLLISION").replace(/_/g, " "),
    };
  });
}

/**
 * Map frame decisions to verdict animation descriptors.
 */
export function mapDecisionsToVerdicts(decisions = [], proposals = []) {
  const propMap = new Map(proposals.map((p) => [String(p.command_id), p]));

  return decisions.map((dec) => {
    const cmdId = String(dec.command_id || "");
    const prop = propMap.get(cmdId);
    const action = String(dec.action || "APPROVE").toUpperCase();
    const reqVal = prop ? Number(prop.requested_value) : Number(dec.approved_value);
    const appVal = Number(dec.approved_value);
    const reasonCode = String(dec.reason_code || "NONE");

    let morphLabel = null;
    if (action === "MODIFY") {
      const p = prop ? prop.parameter : "";
      if (p.includes("OFFLOAD")) {
        morphLabel = `${Math.round(reqVal * 100)}% -> ${Math.round(appVal * 100)}%`;
      } else if (p.includes("POWER")) {
        morphLabel = `${reqVal.toFixed(0)} -> ${appVal.toFixed(0)} dBm`;
      } else {
        morphLabel = `${reqVal} -> ${appVal}`;
      }
    }

    return {
      type: "VERDICT_ANIMATION",
      decisionId: String(dec.decision_id || ""),
      commandId: cmdId,
      action,
      actionColor: ACTION_COLORS[action] || "#38bdf8",
      targetCellId: prop ? String(prop.target_cell_id) : "",
      parameter: prop ? String(prop.parameter) : "",
      requestedValue: reqVal,
      approvedValue: appVal,
      reasonCode,
      holdCount: Number(dec.hold_count) || 0,
      deltaUtility: Number(dec.delta_utility) || 0,
      morphLabel,
      controllerType: prop ? prop.controller_type : "ES",
      controllerAbbrev: prop ? (CONTROLLER_SPECS[prop.controller_type]?.abbrev || "CTRL") : "CTRL",
    };
  });
}

/**
 * Detect real user handover transitions between two consecutive frames.
 */
export function detectUserHandovers(prevUsers = [], currUsers = []) {
  if (!prevUsers || !currUsers || prevUsers.length === 0 || currUsers.length === 0) {
    return [];
  }

  const prevMap = new Map();
  for (const u of prevUsers) {
    const cell = u.cell || u.attached_cell_id || "";
    prevMap.set(String(u.id || u.ue_id), cell);
  }

  const handovers = [];
  for (const u of currUsers) {
    const id = String(u.id || u.ue_id);
    const currCell = u.cell || u.attached_cell_id || "";
    const prevCell = prevMap.get(id);

    if (prevCell && currCell && prevCell !== currCell && !u.dropped && !u.is_dropped) {
      handovers.push({
        type: "USER_HANDOVER",
        userId: id,
        sourceCell: prevCell,
        targetCell: currCell,
        x: Number(u.x || u.x_coord) || 0,
        y: Number(u.y || u.y_coord) || 0,
      });
    }
  }

  return handovers;
}

/**
 * Map load balancing or handover proposals to an offload arrow descriptor between cells.
 */
export function mapOffloadArrow(proposals = [], decisions = []) {
  const lbProposal = proposals.find(
    (p) => p.controller_type === "LOAD_BALANCING" && (p.parameter === "OFFLOAD_RATIO" || p.param === "OFFLOAD_RATIO")
  );
  const lbDecision = decisions.find(
    (d) => d.action === "MODIFY" || d.action === "APPROVE"
  );

  if (lbProposal || lbDecision) {
    const sourceCell = (lbProposal && lbProposal.source_cell_id) || "cell_A";
    const targetCell = (lbProposal && lbProposal.target_cell_id) || (lbDecision && lbDecision.target_cell_id) || "cell_B";
    const ratio = (lbDecision && lbDecision.approved_value !== undefined)
      ? Number(lbDecision.approved_value)
      : (lbProposal ? Number(lbProposal.requested_value || 0.4) : 0.1528);

    return {
      type: "OFFLOAD_ARROW",
      sourceCell,
      targetCell,
      offloadRatio: ratio,
      label: `OFFLOAD ${(ratio * 100).toFixed(1)}% (${sourceCell} -> ${targetCell})`,
      color: "#60A5FA",
    };
  }

  return null;
}
