/**
 * PROJECT ARBITER — Ghost World & Digital Twin Uncertainty Mapper
 * Module: arbiter/dashboard/3d/ghost-world-mapper.js
 *
 * Pure functions mapping:
 * - Twin uncertainty intervals to 3D shell radii and calibration states.
 * - Multi-tick sequential plans to ghost holographic step markers.
 * - Side-by-side / overlay lockstep playback synchronization and KPI deltas.
 * - Reverse search for collision index in frame ring buffers.
 * Fully testable in Node.js (node --test) without WebGL or DOM dependencies.
 */

export const NOMINAL_UNCERTAINTY_WIDTH_DB = 1.2; // Nominal 90% conformal interval width
export const BASE_SHELL_RADIUS = 35.0; // Base tower shell radius in meters

/**
 * Computes uncertainty shell radius from twin interval width.
 * Formula:
 *   radius = baseRadius * (1.0 + clamp((width - nominal) / nominal, 0.0, 2.5) * 0.45)
 * If uncalibrated, radius swells by an additional +25% and pulses.
 */
export function computeUncertaintyShellRadius(
  baseRadius = BASE_SHELL_RADIUS,
  intervalWidth = NOMINAL_UNCERTAINTY_WIDTH_DB,
  isCalibrated = true
) {
  const w = Math.max(0.1, Number(intervalWidth) || NOMINAL_UNCERTAINTY_WIDTH_DB);
  const expansionRatio = Math.max(0.0, Math.min(2.5, (w - NOMINAL_UNCERTAINTY_WIDTH_DB) / NOMINAL_UNCERTAINTY_WIDTH_DB));
  let radius = baseRadius * (1.0 + expansionRatio * 0.45);

  if (!isCalibrated) {
    radius *= 1.25; // Swell when uncalibrated
  }
  return Number(radius.toFixed(2));
}

/**
 * Maps twin interval data to per-cell uncertainty shell descriptors.
 */
export function mapTwinIntervalsToShells(twinData, cells = []) {
  if (!twinData || !cells || cells.length === 0) {
    return [];
  }

  const isCalibrated = Boolean(twinData.calibrated);
  const intervals = twinData.intervals || [];
  const sinrInterval = intervals.find((i) => i.kpi === "mean_sinr_db") || intervals[0] || {
    mean: 9.5,
    lower: 8.9,
    upper: 10.1,
  };

  const width = Math.max(0.2, (sinrInterval.upper || 10.1) - (sinrInterval.lower || 8.9));
  const shellRadius = computeUncertaintyShellRadius(BASE_SHELL_RADIUS, width, isCalibrated);

  return cells.map((cell) => ({
    type: "TWIN_UNCERTAINTY_SHELL",
    cellId: cell.id,
    x: Number(cell.x) || 0,
    y: Number(cell.y) || 0,
    radius: shellRadius,
    intervalWidth: Number(width.toFixed(2)),
    isCalibrated,
    badgeText: isCalibrated ? `CALIBRATED (±${(width / 2).toFixed(2)} dB)` : "UNCALIBRATED (GUARD ACTIVE)",
    colorHex: isCalibrated ? 0x38bdf8 : 0xf59e0b,
    isPuffedOut: width > NOMINAL_UNCERTAINTY_WIDTH_DB * 1.5 || !isCalibrated,
  }));
}

/**
 * Maps multi-tick plan steps to ghost icon descriptors.
 */
export function mapPlanStepsToGhosts(plans = []) {
  if (!plans || plans.length === 0) return [];

  const ghosts = [];
  for (const plan of plans) {
    const steps = plan.steps || [];
    for (const step of steps) {
      const status = String(step.status || "PENDING").toUpperCase();
      const param = String(step.parameter || "");

      let iconType = "PARAM";
      if (param.includes("SLEEP")) iconType = "SLEEP_MOON";
      else if (param.includes("OFFLOAD")) iconType = "OFFLOAD_TRANSFER";
      else if (param.includes("POWER")) iconType = "POWER_BOOST";

      ghosts.push({
        type: "PLAN_GHOST",
        planId: plan.plan_id || "plan-1",
        stepIndex: step.step_index || 0,
        targetCellId: step.target_cell_id || "",
        parameter: param,
        value: Number(step.value) || 0,
        precondition: step.precondition || "NONE",
        status, // PENDING, EXECUTED, EXPIRED
        iconType,
        tickOffset: Number(step.tick_offset) || 0,
        opacity: status === "PENDING" ? 0.65 : (status === "EXECUTED" ? 1.0 : 0.25),
        colorHex: status === "EXECUTED" ? 0x10b981 : (status === "EXPIRED" ? 0xef4444 : 0x38bdf8),
      });
    }
  }
  return ghosts;
}

/**
 * Synchronizes lockstep rollout frames between ARBITER and Counterfactual worlds.
 */
export function synchronizeLockstepRollout(arbiterFrames = [], counterfactualFrames = []) {
  const count = Math.min(arbiterFrames.length, counterfactualFrames.length);
  const synchronizedTicks = [];

  for (let i = 0; i < count; i++) {
    const arb = arbiterFrames[i];
    const cf = counterfactualFrames[i];

    const arbKpis = arb.kpis || {};
    const cfKpis = cf.kpis || {};

    const dropsArb = arbKpis.dropped_users || 0;
    const dropsCf = cfKpis.dropped_users || 0;
    const sinrArb = Number(arbKpis.mean_sinr_db) || 0;
    const sinrCf = Number(cfKpis.mean_sinr_db) || 0;
    const energyArb = Number(arbKpis.energy_w) || 0;
    const energyCf = Number(cfKpis.energy_w) || 0;

    const deltaDrops = dropsArb - dropsCf;
    const deltaSinr = Number((sinrArb - sinrCf).toFixed(2));
    const deltaEnergy = Number((energyArb - energyCf).toFixed(2));

    let explanation = `ARBITER maintained ${dropsArb} dropped users vs ${dropsCf} without coordination.`;
    if (dropsCf > dropsArb) {
      explanation = `Applying the requests as-is would have dropped ${dropsCf - dropsArb} more users and degraded mean SINR by ${Math.abs(deltaSinr).toFixed(2)} dB.`;
    }

    synchronizedTicks.push({
      index: i,
      tick: arb.tick !== undefined ? arb.tick : i,
      simTimeMs: arb.sim_time_ms || 0,
      arbiterFrame: arb,
      counterfactualFrame: cf,
      deltas: {
        droppedUsers: deltaDrops,
        meanSinrDb: deltaSinr,
        energyW: deltaEnergy,
      },
      explanation,
    });
  }

  return synchronizedTicks;
}

/**
 * Scans frame ring buffer for the latest collision tick index.
 */
export function findLastCollisionFrameIndex(frameBuffer = []) {
  if (!frameBuffer || frameBuffer.length === 0) return 0;

  for (let i = frameBuffer.length - 1; i >= 0; i--) {
    const f = frameBuffer[i];
    if (
      (f.proposals && f.proposals.length > 0) ||
      (f.conflicts && f.conflicts.length > 0) ||
      (f.kpis && f.kpis.dropped_users > 0)
    ) {
      return i;
    }
  }
  return 0;
}
