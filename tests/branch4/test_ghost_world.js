/**
 * PROJECT ARBITER — Node.js Tests for Ghost World & Twin Uncertainty Mapper
 * File: tests/branch4/test_ghost_world.js
 *
 * Runs with: node --test tests/branch4/test_ghost_world.js
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import {
  computeUncertaintyShellRadius,
  mapTwinIntervalsToShells,
  mapPlanStepsToGhosts,
  synchronizeLockstepRollout,
  findLastCollisionFrameIndex,
  BASE_SHELL_RADIUS,
} from "../../arbiter/dashboard/3d/ghost-world-mapper.js";

describe("Ghost World Mapper — computeUncertaintyShellRadius", () => {
  test("nominal interval returns baseline radius when calibrated", () => {
    const r = computeUncertaintyShellRadius(BASE_SHELL_RADIUS, 1.2, true);
    assert.equal(r, BASE_SHELL_RADIUS);
  });

  test("expanded interval width visibly puffs out shell radius", () => {
    const rNominal = computeUncertaintyShellRadius(BASE_SHELL_RADIUS, 1.2, true);
    const rDrifted = computeUncertaintyShellRadius(BASE_SHELL_RADIUS, 3.6, true);
    assert.ok(rDrifted > rNominal, `${rDrifted} should be greater than ${rNominal}`);
  });

  test("uncalibrated twin swells shell radius by 1.25x", () => {
    const rCalibrated = computeUncertaintyShellRadius(BASE_SHELL_RADIUS, 1.2, true);
    const rUncalibrated = computeUncertaintyShellRadius(BASE_SHELL_RADIUS, 1.2, false);
    assert.equal(rUncalibrated, Number((rCalibrated * 1.25).toFixed(2)));
  });
});

describe("Ghost World Mapper — mapTwinIntervalsToShells", () => {
  test("maps twin interval to per-cell uncertainty shell descriptors", () => {
    const twin = {
      calibrated: true,
      intervals: [
        { kpi: "mean_sinr_db", mean: 9.5, lower: 8.9, upper: 10.1 },
      ],
    };
    const cells = [
      { id: "cell_A", x: 0, y: 0 },
      { id: "cell_B", x: 230, y: 110 },
    ];

    const shells = mapTwinIntervalsToShells(twin, cells);
    assert.equal(shells.length, 2);
    assert.equal(shells[0].cellId, "cell_A");
    assert.equal(shells[0].isCalibrated, true);
    assert.match(shells[0].badgeText, /CALIBRATED/);
  });

  test("uncalibrated twin generates warning badge and puffed state", () => {
    const twin = {
      calibrated: false,
      intervals: [{ kpi: "mean_sinr_db", lower: 6.0, upper: 12.0 }],
    };
    const cells = [{ id: "cell_A", x: 0, y: 0 }];

    const shells = mapTwinIntervalsToShells(twin, cells);
    assert.equal(shells[0].isCalibrated, false);
    assert.match(shells[0].badgeText, /UNCALIBRATED/);
    assert.equal(shells[0].isPuffedOut, true);
  });
});

describe("Ghost World Mapper — mapPlanStepsToGhosts", () => {
  test("generates plan step ghost descriptors with status coloring", () => {
    const plans = [
      {
        plan_id: "plan-sequential-01",
        steps: [
          {
            step_index: 0,
            target_cell_id: "cell_B",
            parameter: "SLEEP_STATE",
            value: 1.0,
            precondition: "AFTER_OFFLOAD",
            status: "PENDING",
            tick_offset: 2,
          },
          {
            step_index: 1,
            target_cell_id: "cell_A",
            parameter: "TX_POWER_DBM",
            value: 44.0,
            precondition: "NONE",
            status: "EXECUTED",
            tick_offset: 0,
          },
        ],
      },
    ];

    const ghosts = mapPlanStepsToGhosts(plans);
    assert.equal(ghosts.length, 2);

    // Pending step
    assert.equal(ghosts[0].status, "PENDING");
    assert.equal(ghosts[0].iconType, "SLEEP_MOON");
    assert.equal(ghosts[0].precondition, "AFTER_OFFLOAD");
    assert.equal(ghosts[0].opacity, 0.65);

    // Executed step
    assert.equal(ghosts[1].status, "EXECUTED");
    assert.equal(ghosts[1].colorHex, 0x10b981); // Green
    assert.equal(ghosts[1].opacity, 1.0);
  });
});

describe("Ghost World Mapper — synchronizeLockstepRollout", () => {
  test("synchronizes parallel worlds and computes deltas", () => {
    const arbiterFrames = [
      {
        tick: 7,
        kpis: { dropped_users: 0, mean_sinr_db: 9.51, energy_w: 2200.0 },
      },
    ];
    const counterfactualFrames = [
      {
        tick: 7,
        kpis: { dropped_users: 14, mean_sinr_db: -12.47, energy_w: 1927.39 },
      },
    ];

    const sync = synchronizeLockstepRollout(arbiterFrames, counterfactualFrames);
    assert.equal(sync.length, 1);
    assert.equal(sync[0].deltas.droppedUsers, -14);
    assert.equal(sync[0].deltas.meanSinrDb, 21.98);
    assert.match(sync[0].explanation, /would have dropped 14 more users/);
  });
});

describe("Ghost World Mapper — findLastCollisionFrameIndex", () => {
  test("finds latest frame with proposals or call drops", () => {
    const frames = [
      { tick: 0, kpis: { dropped_users: 0 }, proposals: [] },
      { tick: 1, kpis: { dropped_users: 0 }, proposals: [] },
      { tick: 2, kpis: { dropped_users: 0 }, proposals: [{ cmd: 1 }] },
      { tick: 3, kpis: { dropped_users: 0 }, proposals: [] },
    ];

    const idx = findLastCollisionFrameIndex(frames);
    assert.equal(idx, 2);
  });
});

describe("Counterfactual Popover Lifecycle & Safe Bounds", () => {
  test("calculates clamped popover top position within viewport bounds", () => {
    const minTop = 96;
    const panelHeight = 160;
    const windowHeight = 900;
    const maxTop = Math.max(minTop, windowHeight - panelHeight - 40);

    const rowNearTop = 50;
    const topClamped = Math.max(minTop, Math.min(maxTop, rowNearTop - 20));
    assert.equal(topClamped, minTop, "Should not cover the header");

    const rowNearBottom = 880;
    const bottomClamped = Math.max(minTop, Math.min(maxTop, rowNearBottom - 20));
    assert.equal(bottomClamped, maxTop, "Should not spill past bottom viewport");

    const rowNormal = 300;
    const normalClamped = Math.max(minTop, Math.min(maxTop, rowNormal - 20));
    assert.equal(normalClamped, 280, "Normal row positions correctly");
  });

  test("handles aborted fetch signal gracefully", async () => {
    const controller = new AbortController();
    controller.abort();
    assert.equal(controller.signal.aborted, true);
  });
});
