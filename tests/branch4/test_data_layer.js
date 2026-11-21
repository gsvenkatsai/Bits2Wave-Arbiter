/**
 * PROJECT ARBITER — Node.js Unit Tests for 3D Data Layer
 * File: tests/branch4/test_data_layer.js
 *
 * Runs with: node --test tests/branch4/test_data_layer.js
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { FrameRingBuffer, DataLayer, formatSimTime } from "../../arbiter/dashboard/3d/data-layer.js";

describe("FrameRingBuffer", () => {
  test("maintains fixed capacity and drops oldest frames", () => {
    const ring = new FrameRingBuffer(5);
    assert.equal(ring.capacity, 5);
    assert.equal(ring.length, 0);

    for (let i = 0; i < 7; i++) {
      ring.push({ tick: i, type: "frame" });
    }

    assert.equal(ring.length, 5);
    assert.equal(ring.get(0).tick, 2);
    assert.equal(ring.getLatest().tick, 6);
  });

  test("rejects non-positive capacity", () => {
    assert.throws(() => new FrameRingBuffer(0), RangeError);
    assert.throws(() => new FrameRingBuffer(-10), RangeError);
  });

  test("getAll returns a copy of stored frames", () => {
    const ring = new FrameRingBuffer(10);
    ring.push({ tick: 10 });
    ring.push({ tick: 11 });

    const all = ring.getAll();
    assert.equal(all.length, 2);
    assert.equal(all[0].tick, 10);
    assert.equal(all[1].tick, 11);

    ring.clear();
    assert.equal(ring.length, 0);
  });
});

describe("DataLayer", () => {
  test("initializes with 600 default capacity", () => {
    const dl = new DataLayer();
    assert.equal(dl.capacity, 600);
    assert.equal(dl.ringBuffer.capacity, 600);
    assert.equal(dl.selectedWorld, "arbiter");
  });

  test("pushFrame updates latestFrame and fires listener", () => {
    const dl = new DataLayer();
    let received = null;
    dl.on("frame", (frame) => {
      received = frame;
    });

    const sampleFrame = {
      type: "frame",
      tick: 42,
      sim_time_ms: 1732154404200,
      world: "arbiter",
      cells: [],
      users: [],
      kpis: { dropped_users: 0, mean_sinr_db: 12.5, energy_w: 2200 },
    };

    dl.pushFrame(sampleFrame);
    assert.deepEqual(received, sampleFrame);
    assert.deepEqual(dl.getLatestFrame(), sampleFrame);
    assert.equal(dl.getHistory().length, 1);
  });

  test("world switching updates world and notifies listeners", () => {
    const dl = new DataLayer();
    let changeEvent = null;
    dl.on("world_change", (evt) => {
      changeEvent = evt;
    });

    dl.setWorld("counterfactual");
    assert.equal(dl.selectedWorld, "counterfactual");
    assert.deepEqual(changeEvent, { world: "counterfactual" });

    assert.throws(() => dl.setWorld("invalid_world"), /Invalid world/);
  });

  test("replay preloaded frames playback and looping", () => {
    const dl = new DataLayer({ replay: true });
    const frames = [
      { tick: 1, world: "arbiter", kpis: { dropped_users: 0 } },
      { tick: 2, world: "arbiter", kpis: { dropped_users: 0 } },
      { tick: 3, world: "arbiter", kpis: { dropped_users: 0 } },
    ];
    dl.loadReplayFrames({ arbiter: frames, counterfactual: frames });

    assert.equal(dl.replayData.arbiter.length, 3);
    dl.seek(1);
    assert.equal(dl.getLatestFrame().tick, 2);
    dl.seek(2);
    assert.equal(dl.getLatestFrame().tick, 3);
  });

  test("jumpToLastCollision jumps to latest collision frame", () => {
    const dl = new DataLayer({ replay: true });
    const frames = [
      { tick: 0, proposals: [] },
      { tick: 1, proposals: [{ command_id: "cmd-1" }] },
      { tick: 2, proposals: [] },
    ];
    dl.loadReplayFrames({ arbiter: frames, counterfactual: frames });

    let jumpedEvt = null;
    dl.on("jump_collision", (e) => {
      jumpedEvt = e;
    });

    const idx = dl.jumpToLastCollision();
    assert.equal(idx, 1);
    assert.equal(jumpedEvt.index, 1);
    assert.equal(dl.getLatestFrame().tick, 1);
  });

  test("setRiskDial updates active label and notifies listeners", () => {
    const dl = new DataLayer();
    assert.equal(dl.riskDialLabel, "BALANCED (0.30/0.20/0.20/0.30)");

    let changed = null;
    dl.on("risk_dial_change", (e) => {
      changed = e;
    });

    dl.setRiskDial("SAFETY_FIRST (0.50/0.10/0.20/0.20)");
    assert.equal(dl.riskDialLabel, "SAFETY_FIRST (0.50/0.10/0.20/0.20)");
    assert.equal(changed.label, "SAFETY_FIRST (0.50/0.10/0.20/0.20)");
  });

  test("status state machine covers DISCONNECTED, NO LIVE DATA, LIVE, and REPLAY states", () => {
    const dl = new DataLayer();
    dl.stopStatusMonitor(); // control evaluation deterministically in test

    // 1. Initial / Disconnected state
    dl.wsConnected = false;
    dl.evaluateStatus();
    assert.equal(dl.status, "disconnected");
    assert.ok(dl.statusMessage.includes("Disconnected"));

    // 2. Connected but no frames for 3 seconds -> NO LIVE DATA
    dl.wsConnected = true;
    dl.lastFrameTimestamp = 0;
    dl.evaluateStatus();
    assert.equal(dl.status, "no_live_data");
    assert.equal(dl.statusMessage, "NO LIVE DATA");

    // Also with elapsed time >= 3000ms
    dl.lastFrameTimestamp = 10000;
    dl.evaluateStatus(13500); // 3500ms later
    assert.equal(dl.status, "no_live_data");

    // 3. Frame received within 2s AND tick advancing -> LIVE
    dl.lastFrameTimestamp = 20000;
    dl.isTickAdvancing = true;
    dl.evaluateStatus(21000); // 1000ms elapsed
    assert.equal(dl.status, "live");
    assert.equal(dl.statusMessage, "LIVE ENGINE");
    // Verify forbidden string constraint
    assert.notEqual(dl.statusMessage, "LIVE ENGINE CONNECTED");

    // 4. Replay state with metadata tooltip
    dl.isReplay = true;
    dl.replayMeta = {
      git_commit: "a187901623c1f55ae2d6511df6c43a49943c521a",
      seed: 42,
      generated_at: "2026-11-21T17:51:22.808057+00:00",
    };
    dl.evaluateStatus();
    assert.equal(dl.status, "replay");
    assert.equal(dl.statusMessage, "REPLAY OF REAL ENGINE RUN");
    assert.ok(dl.statusMeta.tooltip.includes("a187901"));
    assert.ok(dl.statusMeta.tooltip.includes("42"));
  });

  test("adaptTickUpdateToFrame maps real aggregate_kpis fields and never creates cells/users", () => {
    const dl = new DataLayer();
    dl.stopStatusMonitor();

    const tickUpdate = {
      type: "TICK_UPDATE",
      batch_tick: 49,
      timestamp_epoch_ms: 1732154404900,
      ingress_proposals: [{ command_id: "cmd-es-001" }],
      arbitration_badges: [{ decision_id: "dec-es-001" }],
      aggregate_kpis: {
        total_dropped_users: 14,
        mean_sinr_db: 8.75,
        total_power_kw: 2.2,
      },
    };

    const frame = dl.adaptTickUpdateToFrame(tickUpdate);

    // Assert real field mapping
    assert.equal(frame.tick, 49);
    assert.equal(frame.sim_time_ms, 1732154404900);
    assert.equal(frame.world, "kpi_only");
    assert.equal(frame.kpis.dropped_users, 14);
    assert.equal(frame.kpis.mean_sinr_db, 8.75);
    assert.equal(frame.kpis.power_kw, 2.2);
    assert.equal(frame.kpis.energy_w, 2200.0);

    // Assert never invents cells or users
    assert.deepEqual(frame.cells, []);
    assert.deepEqual(frame.users, []);

    // Assert proposals & badges preserved
    assert.equal(frame.proposals.length, 1);
    assert.equal(frame.decisions.length, 1);

    // Test missing timestamp strictly yields null, never Date.now()
    const noTsUpdate = {
      type: "TICK_UPDATE",
      batch_tick: 1,
      aggregate_kpis: { total_dropped_users: 0 },
    };
    const noTsFrame = dl.adaptTickUpdateToFrame(noTsUpdate);
    assert.equal(noTsFrame.sim_time_ms, null);
  });

  test("timeline scrubber range comes from buffer length and scrubbing selects right frame", () => {
    const dl = new DataLayer();
    dl.stopStatusMonitor();

    // Empty buffer initially
    assert.equal(dl.ringBuffer.length, 0);

    // Push 5 frames
    for (let t = 0; t < 5; t++) {
      dl.pushFrame({
        type: "frame",
        tick: t,
        sim_time_ms: 1732154400000 + t * 100,
        world: "arbiter",
        cells: [],
        users: [],
        kpis: { dropped_users: 0, mean_sinr_db: 10.0, energy_w: 2200 },
      });
    }

    assert.equal(dl.ringBuffer.length, 5);
    assert.equal(dl.followLive, true);
    assert.equal(dl.getLatestFrame().tick, 4);

    // Scrub back to tick 2
    dl.seek(2);
    assert.equal(dl.followLive, false);
    assert.equal(dl.getLatestFrame().tick, 2);

    // New frame arrives while scrubbing: added to buffer, but active view stays at scrubbed frame
    dl.pushFrame({
      type: "frame",
      tick: 5,
      sim_time_ms: 1732154400500,
      world: "arbiter",
      cells: [],
      users: [],
      kpis: { dropped_users: 0, mean_sinr_db: 10.0, energy_w: 2200 },
    });
    assert.equal(dl.ringBuffer.length, 6);
    assert.equal(dl.followLive, false);
    assert.equal(dl.getLatestFrame().tick, 2);

    // Click "Follow live" to resume tail
    dl.setFollowLive(true);
    assert.equal(dl.followLive, true);
    assert.equal(dl.getLatestFrame().tick, 5);
  });

  test("formatSimTime strictly formats UTC timestamp and shows SIM -- when invalid/missing", () => {
    // Exact 02:00:00.000 UTC
    assert.equal(formatSimTime(1732154400000), "SIM 02:00:00.000");
    // With 456 milliseconds
    assert.equal(formatSimTime(1732154400456), "SIM 02:00:00.456");

    // Invalid / missing inputs strictly show "SIM --"
    assert.equal(formatSimTime(null), "SIM --");
    assert.equal(formatSimTime(undefined), "SIM --");
    assert.equal(formatSimTime(0), "SIM --");
    assert.equal(formatSimTime(-500), "SIM --");
    assert.equal(formatSimTime(NaN), "SIM --");
    assert.equal(formatSimTime("invalid"), "SIM --");
  });
});
