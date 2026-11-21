/**
 * PROJECT ARBITER — Node.js Tests for Arbitration Mapper
 * File: tests/branch4/test_arbitration_mapper.js
 *
 * Runs with: node --test tests/branch4/test_arbitration_mapper.js
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../../arbiter/dashboard/3d/vendor/three.module.js";
import {
  CONTROLLER_SPECS,
  HUD_RECTANGLES,
  getHudRectangles,
  computeControllerRingPositions,
  mapProposalsToPackets,
  mapConflictsToEdges,
  mapDecisionsToVerdicts,
  detectUserHandovers,
  formatParamValue,
} from "../../arbiter/dashboard/3d/arbitration-mapper.js";

describe("Arbitration Mapper — formatParamValue", () => {
  test("formats parameters into clean readable strings", () => {
    assert.equal(formatParamValue("SLEEP_STATE", 1.0), "SLEEP 1.0 (OFF)");
    assert.equal(formatParamValue("SLEEP_STATE", 0.0), "SLEEP 0.0 (WAKE)");
    assert.equal(formatParamValue("OFFLOAD_RATIO", 0.40), "OFFLOAD 40%");
    assert.equal(formatParamValue("TX_POWER_DBM", 46.0), "TX 46.0 dBm");
    assert.equal(formatParamValue("TILT_DEG", 8.0), "TILT 8.0°");
  });
});

describe("Arbitration Mapper — mapProposalsToPackets", () => {
  test("maps proposals to packet descriptors with controller colors and positions", () => {
    const proposals = [
      {
        command_id: "cmd-es-001",
        controller_type: "ENERGY_SAVING",
        target_cell_id: "cell_B",
        parameter: "SLEEP_STATE",
        requested_value: 1.0,
      },
      {
        command_id: "cmd-lb-002",
        controller_type: "LOAD_BALANCING",
        target_cell_id: "cell_B",
        parameter: "OFFLOAD_RATIO",
        requested_value: 0.40,
      },
      {
        command_id: "cmd-cc-003",
        controller_type: "COVERAGE_CAPACITY",
        target_cell_id: "cell_A",
        parameter: "TX_POWER_DBM",
        requested_value: 46.0,
      },
    ];

    const packets = mapProposalsToPackets(proposals);
    assert.equal(packets.length, 3);

    // ES packet check
    assert.equal(packets[0].commandId, "cmd-es-001");
    assert.equal(packets[0].controllerAbbrev, "ES");
    assert.equal(packets[0].targetCellId, "cell_B");
    assert.equal(packets[0].label, "SLEEP 1.0 (OFF)");
    assert.equal(packets[0].color, "#10b981");

    // LB packet check
    assert.equal(packets[1].commandId, "cmd-lb-002");
    assert.equal(packets[1].controllerAbbrev, "LB");
    assert.equal(packets[1].label, "OFFLOAD 40%");
    assert.equal(packets[1].color, "#38bdf8");

    // CC packet check
    assert.equal(packets[2].commandId, "cmd-cc-003");
    assert.equal(packets[2].controllerAbbrev, "CC");
    assert.equal(packets[2].label, "TX 46.0 dBm");
  });
});

describe("Arbitration Mapper — mapConflictsToEdges", () => {
  test("creates conflict arc descriptors between competing command packets", () => {
    const packets = [
      { commandId: "cmd-es-001", controllerAbbrev: "ES" },
      { commandId: "cmd-lb-002", controllerAbbrev: "LB" },
    ];
    const conflicts = [
      { a: "cmd-es-001", b: "cmd-lb-002", type: "SLEEP_WHILE_RECEIVING" },
    ];

    const edges = mapConflictsToEdges(conflicts, packets);
    assert.equal(edges.length, 1);
    assert.equal(edges[0].sourceCommandId, "cmd-es-001");
    assert.equal(edges[0].targetCommandId, "cmd-lb-002");
    assert.equal(edges[0].conflictType, "SLEEP_WHILE_RECEIVING");
    assert.equal(edges[0].label, "SLEEP WHILE RECEIVING");
  });
});

describe("Arbitration Mapper — mapDecisionsToVerdicts", () => {
  test("generates verdicts with morph labels and reason codes", () => {
    const proposals = [
      {
        command_id: "cmd-es-001",
        controller_type: "ENERGY_SAVING",
        parameter: "SLEEP_STATE",
        requested_value: 1.0,
      },
      {
        command_id: "cmd-lb-002",
        controller_type: "LOAD_BALANCING",
        parameter: "OFFLOAD_RATIO",
        requested_value: 0.40,
      },
      {
        command_id: "cmd-ps-004",
        controller_type: "PUBLIC_SAFETY",
        parameter: "TX_POWER_DBM",
        requested_value: 46.0,
      },
    ];

    const decisions = [
      {
        decision_id: "dec-es-001",
        command_id: "cmd-es-001",
        action: "DELAY",
        approved_value: 0.0,
        reason_code: "FLOOR_BREACH",
        hold_count: 1,
      },
      {
        decision_id: "dec-lb-002",
        command_id: "cmd-lb-002",
        action: "MODIFY",
        approved_value: 0.15,
        reason_code: "PARETO_OPTIMAL",
        hold_count: 0,
      },
      {
        decision_id: "dec-ps-004",
        command_id: "cmd-ps-004",
        action: "REJECT",
        approved_value: 43.0,
        reason_code: "UNCERTAINTY_GUARD",
        hold_count: 0,
      },
    ];

    const verdicts = mapDecisionsToVerdicts(decisions, proposals);
    assert.equal(verdicts.length, 3);

    // DELAY check
    assert.equal(verdicts[0].action, "DELAY");
    assert.equal(verdicts[0].reasonCode, "FLOOR_BREACH");
    assert.equal(verdicts[0].holdCount, 1);

    // MODIFY check (0.40 -> 0.15)
    assert.equal(verdicts[1].action, "MODIFY");
    assert.equal(verdicts[1].morphLabel, "40% -> 15%");
    assert.equal(verdicts[1].approvedValue, 0.15);

    // REJECT check
    assert.equal(verdicts[2].action, "REJECT");
    assert.equal(verdicts[2].reasonCode, "UNCERTAINTY_GUARD");
  });
});

describe("Arbitration Mapper — detectUserHandovers", () => {
  test("detects real cell handover transitions between consecutive frames", () => {
    const prevUsers = [
      { id: "ue_0001", cell: "cell_A", x: 100, y: 120, dropped: false },
      { id: "ue_0002", cell: "cell_A", x: 105, y: 125, dropped: false },
      { id: "ue_0003", cell: "cell_B", x: 220, y: 110, dropped: false },
    ];
    const currUsers = [
      // ue_0001 moved from cell_A to cell_B (handover)
      { id: "ue_0001", cell: "cell_B", x: 110, y: 120, dropped: false },
      // ue_0002 remained at cell_A
      { id: "ue_0002", cell: "cell_A", x: 106, y: 126, dropped: false },
      // ue_0003 remained at cell_B
      { id: "ue_0003", cell: "cell_B", x: 220, y: 110, dropped: false },
    ];

    const handovers = detectUserHandovers(prevUsers, currUsers);
    assert.equal(handovers.length, 1);
    assert.equal(handovers[0].userId, "ue_0001");
    assert.equal(handovers[0].sourceCell, "cell_A");
    assert.equal(handovers[0].targetCell, "cell_B");
  });

  test("ignores dropped users during handover detection", () => {
    const prevUsers = [{ id: "ue_0001", cell: "cell_A", dropped: false }];
    const currUsers = [{ id: "ue_0001", cell: "cell_B", dropped: true }];

    const handovers = detectUserHandovers(prevUsers, currUsers);
    assert.equal(handovers.length, 0);
  });
});

describe("Controller Nodes — Screen Projections & HUD Obstruction", () => {
  const resolutions = [
    { w: 1440, h: 900 },
    { w: 1920, h: 1080 },
    { w: 2880, h: 1800 },
  ];

  const presets = [
    { name: "Preset 1 (Full Cluster)", pos: new THREE.Vector3(0, 680, 850), target: new THREE.Vector3(0, 0, 0) },
    { name: "Preset 2 (Collision Close-up)", pos: new THREE.Vector3(115, 140, 320), target: new THREE.Vector3(115, 20, 55) },
  ];

  function pointInRect(px, py, r) {
    return px >= r.x && px <= (r.x + r.w) && py >= r.y && py <= (r.y + r.h);
  }

  test("HUD_RECTANGLES constants and getHudRectangles are exported properly", () => {
    assert.ok(HUD_RECTANGLES.header);
    assert.ok(HUD_RECTANGLES.kpi_row);
    assert.ok(HUD_RECTANGLES.verdict_drawer);
    assert.ok(HUD_RECTANGLES.legend_collapsed);
    assert.ok(HUD_RECTANGLES.legend_expanded);
    assert.ok(HUD_RECTANGLES.bottom_bar);

    const rects = getHudRectangles(1920, 1080);
    assert.equal(rects.length, 5);
    // Verdict drawer is on the right edge
    const verdictRect = rects.find((r) => r.name === "verdict_drawer");
    assert.ok(verdictRect);
    assert.equal(verdictRect.x, 1920 - 320);
  });

  test("computeControllerRingPositions creates evenly spaced ring above cluster bounds", () => {
    const ring = computeControllerRingPositions({ minX: -250, maxX: 500, minZ: -500, maxZ: 433.01 });
    assert.ok(ring.ENERGY_SAVING);
    assert.ok(ring.LOAD_BALANCING);
    assert.ok(ring.COVERAGE_CAPACITY);
    assert.ok(ring.PUBLIC_SAFETY);

    // Assert elevation is above ground/cluster towers
    assert.equal(ring.ENERGY_SAVING.y, 140);
    assert.equal(ring.LOAD_BALANCING.y, 140);
    assert.equal(ring.COVERAGE_CAPACITY.y, 140);
    assert.equal(ring.PUBLIC_SAFETY.y, 140);
  });

  test("controller nodes never fall inside any HUD rectangle at 1440x900, 1920x1080, and 2880x1800 in both camera presets", () => {
    const controllers = Object.values(CONTROLLER_SPECS);
    assert.equal(controllers.length, 4);

    for (const preset of presets) {
      for (const res of resolutions) {
        const camera = new THREE.PerspectiveCamera(45, res.w / res.h, 10, 8000);
        camera.position.copy(preset.pos);
        camera.lookAt(preset.target);
        camera.updateMatrixWorld();
        camera.updateProjectionMatrix();

        const hudRects = getHudRectangles(res.w, res.h);

        for (const ctrl of controllers) {
          const worldPos = new THREE.Vector3(ctrl.defaultPos.x, ctrl.defaultPos.y, ctrl.defaultPos.z);
          const v = worldPos.clone().project(camera);

          const isInsideFrustum = v.z >= -1 && v.z <= 1;
          const sx = (v.x + 1) / 2 * res.w;
          const sy = (1 - v.y) / 2 * res.h;
          const isOnScreen = isInsideFrustum && sx >= 0 && sx <= res.w && sy >= 0 && sy <= res.h;

          if (isOnScreen) {
            // Must not fall in any HUD rectangle
            for (const rect of hudRects) {
              const inside = pointInRect(sx, sy, rect);
              assert.equal(
                inside,
                false,
                `Controller ${ctrl.abbrev} fell inside HUD rect '${rect.name}' at ${res.w}x${res.h} in ${preset.name} (screen: ${sx.toFixed(1)}, ${sy.toFixed(1)})`
              );
            }

            // In Preset 1 (Full Cluster Overview), must also stay off the screen's top-left quadrant
            if (preset.name.includes("Preset 1")) {
              const isTopLeft = sx < (res.w / 2) && sy < (res.h / 2);
              assert.equal(
                isTopLeft,
                false,
                `Controller ${ctrl.abbrev} fell inside top-left quadrant at ${res.w}x${res.h} in ${preset.name} (screen: ${sx.toFixed(1)}, ${sy.toFixed(1)})`
              );
            }
          }
        }
      }
    }
  });
});
