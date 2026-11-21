/**
 * PROJECT ARBITER — 3D Visualization Data Layer
 * Module: arbiter/dashboard/3d/data-layer.js
 *
 * Responsibilities:
 * - Unified 600-frame ring buffer for both live telemetry and replay.
 * - Deterministic status state machine (LIVE, REPLAY, NO LIVE DATA, DISCONNECTED).
 * - Live WebSocket ingestion of native "frame" packets (/api/v1/ws/live).
 * - Degraded fallback for TICK_UPDATE messages (never invent fake cells/users).
 * - Replay stream with metadata provenance tooltip (commit SHA, seed, timestamp).
 * - Timeline scrubber synchronization with follow-live resumption.
 * - Node.js testable (node --test) without DOM or WebSocket dependencies.
 */

import {
  synchronizeLockstepRollout,
  findLastCollisionFrameIndex,
} from './ghost-world-mapper.js';

/**
 * Format simulation epoch milliseconds into discrete clock representation:
 * "SIM hh:mm:ss.mmm" (UTC), or "SIM --" if timestamp is missing/invalid.
 * Strictly no Date.now() fallback.
 */
export function formatSimTime(epochMs) {
  if (typeof epochMs !== 'number' || isNaN(epochMs) || epochMs <= 0) {
    return 'SIM --';
  }
  const d = new Date(epochMs);
  const h = String(d.getUTCHours()).padStart(2, '0');
  const m = String(d.getUTCMinutes()).padStart(2, '0');
  const s = String(d.getUTCSeconds()).padStart(2, '0');
  const ms = String(d.getUTCMilliseconds()).padStart(3, '0');
  return `SIM ${h}:${m}:${s}.${ms}`;
}

export class FrameRingBuffer {
  /**
   * Fixed-capacity circular ring buffer for simulation frames.
   * @param {number} capacity - Maximum frames retained (default: 600).
   */
  constructor(capacity = 600) {
    if (capacity <= 0) {
      throw new RangeError('Capacity must be positive integer');
    }
    this.capacity = capacity;
    this.buffer = [];
  }

  push(frame) {
    if (this.buffer.length >= this.capacity) {
      this.buffer.shift();
    }
    this.buffer.push(frame);
  }

  get length() {
    return this.buffer.length;
  }

  count() {
    return this.buffer.length;
  }

  getLatest() {
    return this.buffer.length > 0 ? this.buffer[this.buffer.length - 1] : null;
  }

  get(index) {
    return this.buffer[index];
  }

  getAll() {
    return [...this.buffer];
  }

  clear() {
    this.buffer = [];
  }
}

export class DataLayer {
  constructor(options = {}) {
    this.capacity = options.capacity || 600;
    this.ringBuffer = new FrameRingBuffer(this.capacity);
    this.listeners = new Map();
    this.ws = null;
    this.wsConnected = false;

    // Status state machine variables
    this.status = 'disconnected';
    this.statusMessage = 'Initialized';
    this.statusMeta = null;
    this.lastFrameTimestamp = 0;
    this.lastTick = -1;
    this.isTickAdvancing = false;
    this.reconnectCountdown = 0;
    this.reconnectTimer = null;
    this.statusCheckInterval = null;

    this.latestFrame = null;
    this.currentFrameIndex = -1;
    this.followLive = true;

    // Detect demo and replay query parameters
    const searchParams = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
    this.isDemo = (
      options.demo !== undefined
        ? Boolean(options.demo)
        : Boolean(searchParams?.get('demo') === '1')
    );
    this.isReplay = (
      options.replay !== undefined
        ? Boolean(options.replay)
        : (Boolean(searchParams?.get('replay') === '1') || this.isDemo)
    );

    this.wsUrl = options.wsUrl || null;
    this.selectedWorld = options.world || 'arbiter';
    this.replayData = { arbiter: [], counterfactual: [] };
    this.replayIndex = 0;
    this.replayTimer = null;
    this.replaySpeed = this.isDemo ? 0.25 : 1.0;
    this.isPlaying = true;
    this.tickIntervalMs = 100;
    this.riskDialLabel = options.riskDial || 'BALANCED (0.30/0.20/0.20/0.30)';
    this.counterfactualRollout = null;
    this.replayMeta = null;

    if (typeof setInterval !== 'undefined') {
      this.startStatusMonitor();
    }
  }

  /**
   * Subscribe to event notifications.
   * Supported events: 'frame', 'status', 'world_change', 'replay_end', 'seek', 'counterfactual_loaded'.
   */
  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event).add(callback);
    return () => this.off(event, callback);
  }

  off(event, callback) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).delete(callback);
    }
  }

  emit(event, data) {
    if (this.listeners.has(event)) {
      for (const cb of this.listeners.get(event)) {
        try {
          cb(data);
        } catch (e) {
          console.error(`[DataLayer] Error in ${event} listener:`, e);
        }
      }
    }
  }

  updateStatus(status, message, meta = null) {
    const changed = this.status !== status || this.statusMessage !== message;
    this.status = status;
    this.statusMessage = message;
    this.statusMeta = meta;
    if (changed) {
      this.emit('status', { status, message, meta });
    }
  }

  /**
   * Status state machine evaluator:
   * - LIVE: Socket open AND frame received within 2 s AND tick advancing.
   * - REPLAY: "REPLAY OF REAL ENGINE RUN" with metadata SHA/seed/date tooltip.
   * - NO LIVE DATA: Socket open, but no frames for >= 3 s.
   * - DISCONNECTED: Auto-reconnect with countdown.
   * Constraint: Connected status is verified from live stream activity.
   */
  evaluateStatus(now = Date.now()) {
    if (this.isReplay) {
      const tooltip = this.replayMeta
        ? `Commit: ${this.replayMeta.git_commit?.slice(0, 7) || 'latest'} | Seed: ${this.replayMeta.seed || 42} | Date: ${this.replayMeta.generated_at || 'offline'}`
        : 'Replay of real engine execution';
      this.updateStatus('replay', 'REPLAY OF REAL ENGINE RUN', { tooltip, meta: this.replayMeta });
      return;
    }

    if (!this.wsConnected) {
      const msg = this.reconnectCountdown > 0
        ? `Disconnected. Reconnecting in ${this.reconnectCountdown}s...`
        : 'Disconnected';
      this.updateStatus('disconnected', msg);
      return;
    }

    const elapsedMs = this.lastFrameTimestamp > 0 ? (now - this.lastFrameTimestamp) : Infinity;

    if (this.lastFrameTimestamp === 0 || elapsedMs >= 3000) {
      this.updateStatus('no_live_data', 'NO LIVE DATA');
    } else if (elapsedMs <= 2000 && this.isTickAdvancing) {
      this.updateStatus('live', 'LIVE ENGINE');
    }
  }

  startStatusMonitor() {
    if (this.statusCheckInterval) clearInterval(this.statusCheckInterval);
    this.statusCheckInterval = setInterval(() => {
      this.evaluateStatus(Date.now());
    }, 500);
    if (this.statusCheckInterval.unref) {
      this.statusCheckInterval.unref();
    }
  }

  stopStatusMonitor() {
    if (this.statusCheckInterval) {
      clearInterval(this.statusCheckInterval);
      this.statusCheckInterval = null;
    }
  }

  pushFrame(frame) {
    if (!frame) return;

    // Track tick advancement
    if (frame.tick !== undefined) {
      if (this.lastTick !== -1 && frame.tick > this.lastTick) {
        this.isTickAdvancing = true;
      }
      this.lastTick = frame.tick;
    }
    this.lastFrameTimestamp = Date.now();

    // Single unified frame store: ring buffer of 600
    this.ringBuffer.push(frame);

    if (this.followLive) {
      this.currentFrameIndex = this.ringBuffer.length - 1;
      this.latestFrame = frame;
      this.emit('frame', frame);
      this.emit('seek', {
        index: this.currentFrameIndex,
        total: this.ringBuffer.length,
        followLive: true,
      });
    }

    this.evaluateStatus(this.lastFrameTimestamp);
  }

  getLatestFrame() {
    return this.latestFrame;
  }

  getHistory() {
    return this.ringBuffer.getAll();
  }

  seek(index) {
    const totalFrames = this.ringBuffer.length;
    if (totalFrames === 0) return;

    this.followLive = false;
    this.currentFrameIndex = Math.max(0, Math.min(totalFrames - 1, Math.floor(index)));
    this.replayIndex = this.currentFrameIndex;
    const frame = this.ringBuffer.get(this.currentFrameIndex);
    if (frame) {
      this.latestFrame = frame;
      this.emit('frame', frame);
      this.emit('seek', {
        index: this.currentFrameIndex,
        total: totalFrames,
        followLive: false,
      });
    }
  }

  setFollowLive(enable = true) {
    this.followLive = Boolean(enable);
    if (this.followLive && this.ringBuffer.length > 0) {
      this.currentFrameIndex = this.ringBuffer.length - 1;
      const frame = this.ringBuffer.get(this.currentFrameIndex);
      if (frame) {
        this.latestFrame = frame;
        this.emit('frame', frame);
      }
      this.emit('seek', {
        index: this.currentFrameIndex,
        total: this.ringBuffer.length,
        followLive: true,
      });
    }
  }

  setWorld(world) {
    if (world !== 'arbiter' && world !== 'counterfactual') {
      throw new Error(`Invalid world: ${world}`);
    }
    this.selectedWorld = world;
    this.emit('world_change', { world });

    // In replay mode, update current frame immediately to matched tick
    if (this.isReplay && this.replayData[world]?.length > 0) {
      const frames = this.replayData[world];
      const targetFrame = frames[this.replayIndex] || frames[0];
      if (targetFrame) {
        this.pushFrame(targetFrame);
      }
    }
  }

  loadReplayFrames(data, meta = null) {
    if (!data) return;
    if (Array.isArray(data)) {
      this.replayData.arbiter = data;
      this.replayData.counterfactual = data;
    } else {
      this.replayData.arbiter = data.arbiter || [];
      this.replayData.counterfactual = data.counterfactual || [];
    }
    this.replayMeta = meta;
    this.replayIndex = 0;

    // Load replay frames into unified 600-frame ring buffer
    this.ringBuffer.clear();
    const activeFrames = this.replayData[this.selectedWorld];
    if (activeFrames && activeFrames.length > 0) {
      for (const f of activeFrames) {
        this.ringBuffer.push(f);
      }
      this.currentFrameIndex = 0;
      this.latestFrame = activeFrames[0];
      this.emit('frame', this.latestFrame);
    }
  }

  startReplayLoop() {
    if (this.replayTimer) {
      clearInterval(this.replayTimer);
    }
    this.isReplay = true;
    this.evaluateStatus();

    const interval = Math.max(20, Math.round(this.tickIntervalMs / this.replaySpeed));
    this.replayTimer = setInterval(() => {
      if (!this.isPlaying) return;

      const activeFrames = this.replayData[this.selectedWorld];
      if (!activeFrames || activeFrames.length === 0) return;

      if (this.replayIndex >= activeFrames.length) {
        if (this.isDemo) {
          // Demo loops indefinitely
          this.replayIndex = 0;
        } else {
          this.replayIndex = 0;
        }
        this.emit('replay_end', {});
      }

      const frame = activeFrames[this.replayIndex];
      this.replayIndex++;
      if (frame) {
        this.pushFrame(frame);
      }
    }, interval);
    if (this.replayTimer.unref) {
      this.replayTimer.unref();
    }
  }

  stopReplayLoop() {
    if (this.replayTimer) {
      clearInterval(this.replayTimer);
      this.replayTimer = null;
    }
  }

  play() {
    this.isPlaying = true;
  }

  pause() {
    this.isPlaying = false;
  }

  togglePlay() {
    this.isPlaying = !this.isPlaying;
    return this.isPlaying;
  }

  setSpeed(speedMultiplier) {
    this.replaySpeed = Math.max(0.1, Math.min(10.0, speedMultiplier));
    if (this.isReplay && this.replayTimer) {
      this.startReplayLoop();
    }
  }

  jumpToLastCollision() {
    const frames = this.ringBuffer.getAll();
    const targetIndex = findLastCollisionFrameIndex(frames);
    if (targetIndex >= 0) {
      this.seek(targetIndex);
    }
    this.emit('jump_collision', { index: targetIndex });
    return targetIndex;
  }

  setRiskDial(label) {
    this.riskDialLabel = String(label);
    this.emit('risk_dial_change', { label: this.riskDialLabel });
  }

  async loadCounterfactual(decisionId = 'dec-es-001', horizon = 20) {
    let host = 'localhost:8000';
    let proto = 'http:';
    if (typeof window !== 'undefined') {
      proto = window.location.protocol;
      host = window.location.host;
    }
    const apiUrl = `${proto}//${host}/api/v1/recorder/counterfactual_frames/${encodeURIComponent(decisionId)}?horizon=${horizon}`;

    if (typeof fetch !== 'undefined') {
      try {
        const resp = await fetch(apiUrl);
        if (resp.ok) {
          const data = await resp.json();
          this.counterfactualRollout = data;
          this.emit('counterfactual_loaded', data);
          return data;
        }
      } catch (err) {
        console.warn('[DataLayer] Live counterfactual API call failed, attempting local fixture fallback:', err);
      }

      // Offline fallback to recorded counterfactual fixture
      try {
        const localResp = await fetch('./data/counterfactual_2am.json');
        if (localResp.ok) {
          const localData = await localResp.json();
          this.counterfactualRollout = localData;
          this.emit('counterfactual_loaded', localData);
          return localData;
        }
      } catch (fallbackErr) {
        console.warn('[DataLayer] Bundled counterfactual fixture fetch failed:', fallbackErr);
      }
    }

    this.emit('counterfactual_error', {
      decisionId,
      message: 'No counterfactual data for this decision',
    });
    return null;
  }

  connect() {
    if (this.isReplay) {
      this.connectReplay();
    } else {
      this.connectLive();
    }
  }

  async loadReplayData() {
    this.isReplay = true;
    this.disconnectLiveWs();

    let meta = null;
    if (typeof fetch !== 'undefined') {
      try {
        const metaResp = await fetch('./data/frames_2am.meta.json');
        if (metaResp.ok) {
          meta = await metaResp.json();
          this.replayMeta = meta;
        }
      } catch (e) {
        // Meta fetch failure non-fatal
      }

      try {
        const resp = await fetch('./data/frames_2am.json');
        if (resp.ok) {
          const data = await resp.json();
          this.loadReplayFrames(data, meta);
          this.startReplayLoop();
          return;
        }
      } catch (e) {
        console.warn('[DataLayer] Failed to fetch ./data/frames_2am.json:', e);
      }
    }

    if (this.replayData[this.selectedWorld]?.length > 0) {
      this.startReplayLoop();
    }
  }

  connectReplay() {
    this.updateStatus('connecting', 'Starting recorded replay stream...');

    let wsHost = 'localhost:8000';
    let proto = 'ws:';
    if (typeof window !== 'undefined') {
      proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsHost = window.location.host;
    }
    const wsUrl = this.wsUrl || `${proto}//${wsHost}/ws/replay`;

    let wsAttempted = false;
    if (typeof WebSocket !== 'undefined') {
      try {
        wsAttempted = true;
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
          this.wsConnected = true;
          this.evaluateStatus();
        };

        this.ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'frame') {
              this.pushFrame(data);
            } else if (data.arbiter && data.counterfactual) {
              this.loadReplayFrames(data);
              this.startReplayLoop();
            }
          } catch (e) {
            console.warn('[DataLayer] Malformed WS frame:', e);
          }
        };

        this.ws.onerror = () => {
          this.loadReplayData();
        };

        this.ws.onclose = () => {
          this.loadReplayData();
        };
      } catch (err) {
        this.loadReplayData();
      }
    }

    if (!wsAttempted) {
      this.loadReplayData();
    }
  }

  connectLive() {
    this.isReplay = false;
    let wsHost = 'localhost:8000';
    let proto = 'ws:';
    if (typeof window !== 'undefined') {
      proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsHost = window.location.host;
    }
    const wsUrl = this.wsUrl || `${proto}//${wsHost}/api/v1/ws/live`;
    this.updateStatus('connecting', `Connecting to ${wsUrl}...`);

    if (typeof WebSocket === 'undefined') {
      this.updateStatus('disconnected', 'WebSocket not available');
      return;
    }

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = async () => {
        this.wsConnected = true;
        this.reconnectCountdown = 0;
        this.evaluateStatus();

        // Optional probe for latest full frame if available
        try {
          if (typeof fetch !== 'undefined') {
            const resp = await fetch('/api/v1/frame/latest');
            if (resp.ok) {
              const latest = await resp.json();
              if (latest && Array.isArray(latest.cells) && latest.cells.length > 0) {
                this.pushFrame(latest);
              }
            }
          }
        } catch (e) {}
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'frame') {
            this.lastNativeFrameTimestamp = Date.now();
            this.pushFrame(data);
          } else if (data.type === 'TICK_UPDATE') {
            // Degraded fallback ONLY when no native frame stream is arriving
            const now = Date.now();
            const hasRecentNativeFrame = this.lastNativeFrameTimestamp && (now - this.lastNativeFrameTimestamp < 3000);
            if (!hasRecentNativeFrame) {
              const frame = this.adaptTickUpdateToFrame(data);
              if (frame) {
                this.pushFrame(frame);
              }
            }
          }
        } catch (e) {
          console.warn('[DataLayer] Malformed frame payload:', e);
        }
      };

      this.ws.onclose = () => {
        this.wsConnected = false;
        this.startReconnectCountdown();
      };

      this.ws.onerror = () => {
        this.wsConnected = false;
        this.startReconnectCountdown();
      };
    } catch (e) {
      this.wsConnected = false;
      this.startReconnectCountdown();
    }
  }

  startReconnectCountdown() {
    if (this.isReplay) return;
    if (this.reconnectTimer) clearInterval(this.reconnectTimer);

    this.reconnectCountdown = 2;
    this.evaluateStatus();

    this.reconnectTimer = setInterval(() => {
      this.reconnectCountdown--;
      if (this.reconnectCountdown <= 0) {
        clearInterval(this.reconnectTimer);
        this.reconnectTimer = null;
        this.connectLive();
      } else {
        this.evaluateStatus();
      }
    }, 1000);
    if (this.reconnectTimer.unref) {
      this.reconnectTimer.unref();
    }
  }

  disconnectLiveWs() {
    if (this.reconnectTimer) {
      clearInterval(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {}
      this.ws = null;
    }
    this.wsConnected = false;
  }

  /**
   * Adaptive fallback for legacy TICK_UPDATE payloads.
   * Maps aggregate_kpis to real KPI fields, never invents cells or users,
   * sets world to "kpi_only", and never falls back to Date.now().
   */
  adaptTickUpdateToFrame(tickUpdate) {
    const kpis = tickUpdate.aggregate_kpis || {};
    const droppedUsers = (
      kpis.total_dropped_users !== undefined
        ? kpis.total_dropped_users
        : (kpis.dropped_users !== undefined ? kpis.dropped_users : 0)
    );
    const meanSinr = (
      kpis.mean_sinr_db !== undefined
        ? kpis.mean_sinr_db
        : 0.0
    );
    const totalPowerKw = (
      kpis.total_power_kw !== undefined
        ? kpis.total_power_kw
        : 0.0
    );

    return {
      type: 'frame',
      tick: tickUpdate.batch_tick !== undefined ? tickUpdate.batch_tick : 0,
      sim_time_ms: tickUpdate.timestamp_epoch_ms || null,
      scenario: '2_AM_COLLISION',
      world: 'kpi_only',
      cells: [],
      users: [],
      proposals: tickUpdate.ingress_proposals || [],
      decisions: tickUpdate.arbitration_badges || [],
      conflicts: [],
      plans: [],
      twin: null,
      kpis: {
        dropped_users: droppedUsers,
        mean_sinr_db: meanSinr,
        power_kw: totalPowerKw,
        energy_w: totalPowerKw * 1000.0,
      },
    };
  }

  disconnect() {
    this.stopReplayLoop();
    this.disconnectLiveWs();
    this.stopStatusMonitor();
    this.updateStatus('disconnected', 'Disconnected');
  }
}
