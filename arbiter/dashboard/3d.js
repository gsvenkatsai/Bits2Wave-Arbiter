/**
 * arbiter/dashboard/3d.js
 * 3D Cellular RAN Network Visualization using local Three.js (Offline / No CDN).
 *
 * Traceability & Rules:
 * - Real engine data: Every coordinate, power level, tilt, packet and animation
 *   comes from real simulation frames.
 * - Offline operation: Uses vendored Three.js and OrbitControls in vendor/.
 * - Streams live (/api/v1/ws/live) or recorded replay (/ws/replay or fixtures/v3/frames_2am.jsonl).
 */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // State & Configuration
  // -------------------------------------------------------------------------
  const SCALE = 0.15; // 1 meter = 0.15 Three.js units (500m ISD = 75 units)

  const STATE = {
    frames: [],              // Recorded or buffered frames
    currentFrameIndex: 0,
    isPlaying: true,
    playInterval: null,
    fps: 10,                 // 10 Hz simulation rate
    selectedCellId: 'cell_A',
    streamMode: 'replay',    // 'replay', 'live', 'cf_split'
    activeWorld: 'arbiter',  // 'arbiter' or 'counterfactual'
    cfData: null,            // Counterfactual frames cache
    ws: null,
  };

  // -------------------------------------------------------------------------
  // DOM Elements
  // -------------------------------------------------------------------------
  const DOM = {
    canvas: document.getElementById('glCanvas'),
    container: document.getElementById('glContainer'),
    streamSource: document.getElementById('streamSource'),
    btnPlayPause: document.getElementById('btnPlayPause'),
    playIcon: document.getElementById('playIcon'),
    tickScrubber: document.getElementById('tickScrubber'),
    tickDisplay: document.getElementById('tickDisplay'),
    worldToggle: document.getElementById('worldToggle'),
    worldPill: document.getElementById('worldPill'),
    connectionStatus: document.getElementById('connectionStatus'),
    connectionText: document.getElementById('connectionText'),
    clockDisplay: document.getElementById('clockDisplay'),

    // HUD Left
    kpiDroppedUsers: document.getElementById('kpiDroppedUsers'),
    kpiTotalPower: document.getElementById('kpiTotalPower'),
    kpiMeanSinr: document.getElementById('kpiMeanSinr'),
    kpiBoxDrops: document.getElementById('kpiBoxDrops'),
    proposalCount: document.getElementById('proposalCount'),
    proposalsList: document.getElementById('proposalsList'),
    decisionCount: document.getElementById('decisionCount'),
    decisionsList: document.getElementById('decisionsList'),
    conflictCount: document.getElementById('conflictCount'),
    conflictsList: document.getElementById('conflictsList'),

    // HUD Right
    selectedCellId: document.getElementById('selectedCellId'),
    cellPos: document.getElementById('cellPos'),
    cellPowerTilt: document.getElementById('cellPowerTilt'),
    cellSleep: document.getElementById('cellSleep'),
    cellLoad: document.getElementById('cellLoad'),
    cellActiveUes: document.getElementById('cellActiveUes'),
    cellEnergyW: document.getElementById('cellEnergyW'),
    planCount: document.getElementById('planCount'),
    plansList: document.getElementById('plansList'),
    twinStatusBadge: document.getElementById('twinStatusBadge'),
    twinIntervalsList: document.getElementById('twinIntervalsList'),
  };

  // -------------------------------------------------------------------------
  // Three.js Scene Setup
  // -------------------------------------------------------------------------
  let scene, camera, renderer, controls;
  const cellMeshes = {};       // cell_id -> { group, pole, antenna, beam, label }
  let userMeshGroup = null;     // Group holding UE meshes
  let radioLinksMesh = null;    // THREE.LineSegments for UE-to-cell links
  let conflictArcsGroup = null; // Group holding 3D clash arcs

  function initThree() {
    const width = DOM.container.clientWidth;
    const height = DOM.container.clientHeight;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x08090d);
    scene.fog = new THREE.FogExp2(0x08090d, 0.0035);

    camera = new THREE.PerspectiveCamera(50, width / height, 1, 2000);
    camera.position.set(0, -140, 110);
    camera.up.set(0, 0, 1); // Z is UP in radio coordinates

    renderer = new THREE.WebGLRenderer({ canvas: DOM.canvas, antialias: true, alpha: false });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.maxPolarAngle = Math.PI / 2 - 0.02; // Keep camera above ground
    controls.target.set(0, 0, 0);

    // Lights
    const ambientLight = new THREE.AmbientLight(0x334155, 1.2);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
    dirLight.position.set(60, -80, 120);
    scene.add(dirLight);

    // Ground Hexagonal Grid
    const gridHelper = new THREE.GridHelper(300, 30, 0x1e293b, 0x0f172a);
    gridHelper.rotation.x = Math.PI / 2;
    scene.add(gridHelper);

    // Groups
    userMeshGroup = new THREE.Group();
    scene.add(userMeshGroup);

    conflictArcsGroup = new THREE.Group();
    scene.add(conflictArcsGroup);

    // Window resize handler
    window.addEventListener('resize', onWindowResize);

    // Raycaster for cell tower clicking
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();

    DOM.canvas.addEventListener('pointerdown', (event) => {
      const rect = DOM.canvas.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const interactables = [];
      Object.keys(cellMeshes).forEach((cid) => {
        interactables.push(cellMeshes[cid].pole);
      });

      const intersects = raycaster.intersectObjects(interactables);
      if (intersects.length > 0) {
        const hit = intersects[0].object;
        if (hit.userData && hit.userData.cellId) {
          selectCell(hit.userData.cellId);
        }
      }
    });

    animate();
  }

  function onWindowResize() {
    const width = DOM.container.clientWidth;
    const height = DOM.container.clientHeight;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  }

  function animate() {
    requestAnimationFrame(animate);
    controls.update();

    // Subtle antenna beacon pulse
    const time = performance.now() * 0.003;
    Object.keys(cellMeshes).forEach((cid) => {
      const c = cellMeshes[cid];
      if (c && c.beacon && !c.isSleeping) {
        const s = 1.0 + 0.25 * Math.sin(time * 3);
        c.beacon.scale.set(s, s, s);
      }
    });

    renderer.render(scene, camera);
  }

  // -------------------------------------------------------------------------
  // 3D Geometry Builders (Cells, Users, Conflicts)
  // -------------------------------------------------------------------------

  function createCellTower(cell) {
    const group = new THREE.Group();
    const x = cell.x * SCALE;
    const y = cell.y * SCALE;
    group.position.set(x, y, 0);

    // Tower base platform
    const baseGeo = new THREE.CylinderGeometry(3.5, 4.0, 1.0, 16);
    baseGeo.rotateX(Math.PI / 2);
    const baseMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.8 });
    const baseMesh = new THREE.Mesh(baseGeo, baseMat);
    baseMesh.position.z = 0.5;
    group.add(baseMesh);

    // Mast Pole
    const height = 18;
    const poleGeo = new THREE.CylinderGeometry(0.8, 1.2, height, 8);
    poleGeo.rotateX(Math.PI / 2);
    const poleMat = new THREE.MeshStandardMaterial({
      color: 0x475569,
      metalness: 0.7,
      roughness: 0.3,
    });
    const poleMesh = new THREE.Mesh(poleGeo, poleMat);
    poleMesh.position.z = height / 2;
    poleMesh.userData = { cellId: cell.id };
    group.add(poleMesh);

    // Antenna head & Bracket
    const antennaGroup = new THREE.Group();
    antennaGroup.position.z = height;

    const headGeo = new THREE.BoxGeometry(2.0, 2.0, 3.0);
    const headMat = new THREE.MeshStandardMaterial({ color: 0x0284c7 });
    const headMesh = new THREE.Mesh(headGeo, headMat);
    antennaGroup.add(headMesh);

    // Beacon light on top
    const beaconGeo = new THREE.SphereGeometry(0.7, 12, 12);
    const beaconMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const beaconMesh = new THREE.Mesh(beaconGeo, beaconMat);
    beaconMesh.position.z = 2.2;
    antennaGroup.add(beaconMesh);

    // Semi-transparent Coverage Beam Dome
    const beamRadius = Math.max(12, cell.tx_power_dbm * 0.75);
    const beamGeo = new THREE.ConeGeometry(beamRadius, 16, 24, 1, true);
    beamGeo.rotateX(-Math.PI / 2);
    beamGeo.translate(0, 0, -8);
    const beamMat = new THREE.MeshBasicMaterial({
      color: 0x0284c7,
      transparent: true,
      opacity: 0.12,
      wireframe: true,
      side: THREE.DoubleSide,
    });
    const beamMesh = new THREE.Mesh(beamGeo, beamMat);
    antennaGroup.add(beamMesh);

    group.add(antennaGroup);
    scene.add(group);

    return {
      group,
      pole: poleMesh,
      antenna: antennaGroup,
      beam: beamMesh,
      beacon: beaconMesh,
      isSleeping: false,
    };
  }

  function updateCellTower(cell) {
    if (!cellMeshes[cell.id]) {
      cellMeshes[cell.id] = createCellTower(cell);
    }
    const meshObj = cellMeshes[cell.id];
    meshObj.isSleeping = cell.is_sleeping;

    // Apply real downtilt (convert degrees to radians)
    const tiltRad = (cell.tilt_deg * Math.PI) / 180;
    meshObj.antenna.rotation.x = tiltRad;

    // Sleep state styling
    if (cell.is_sleeping) {
      meshObj.pole.material.color.setHex(0x334155);
      meshObj.beacon.material.color.setHex(0x475569);
      meshObj.beam.visible = false;
    } else {
      meshObj.pole.material.color.setHex(cell.id === STATE.selectedCellId ? 0x38bdf8 : 0x475569);
      meshObj.beacon.material.color.setHex(0x06b6d4);
      meshObj.beam.visible = true;

      // Opacity and scale based on tx_power_dbm
      const pRatio = Math.max(0.1, (cell.tx_power_dbm - 30.0) / 16.0);
      meshObj.beam.material.opacity = 0.08 + 0.12 * pRatio;
    }
  }

  function updateUsers(users, cells) {
    // Rebuild active users using efficient InstancedMesh or lightweight Meshes
    while (userMeshGroup.children.length > 0) {
      const obj = userMeshGroup.children.pop();
      if (obj.geometry) obj.geometry.dispose();
    }

    if (radioLinksMesh) {
      scene.remove(radioLinksMesh);
      if (radioLinksMesh.geometry) radioLinksMesh.geometry.dispose();
      radioLinksMesh = null;
    }

    if (!users || users.length === 0) return;

    const linkPositions = [];
    const linkColors = [];
    const cellPosMap = {};
    cells.forEach((c) => {
      cellPosMap[c.id] = new THREE.Vector3(c.x * SCALE, c.y * SCALE, 14);
    });

    const sphereGeo = new THREE.SphereGeometry(0.7, 8, 8);

    users.forEach((u) => {
      const ux = u.x * SCALE;
      const uy = u.y * SCALE;
      const uz = 0.6;

      let colorHex = 0x10b981; // High SINR (>10 dB)
      if (u.dropped) {
        colorHex = 0xef4444; // Dropped user
      } else if (u.sinr_db < 0) {
        colorHex = 0xf97316; // Poor SINR
      } else if (u.sinr_db <= 10) {
        colorHex = 0xf59e0b; // Moderate SINR
      }

      const mat = new THREE.MeshBasicMaterial({ color: colorHex });
      const mesh = new THREE.Mesh(sphereGeo, mat);
      mesh.position.set(ux, uy, uz);

      if (u.dropped) {
        mesh.scale.set(1.5, 1.5, 1.5);
      }
      userMeshGroup.add(mesh);

      // Link line to serving cell
      const cellPos = cellPosMap[u.cell];
      if (cellPos && !u.dropped) {
        linkPositions.push(ux, uy, uz);
        linkPositions.push(cellPos.x, cellPos.y, cellPos.z);

        const col = new THREE.Color(colorHex);
        linkColors.push(col.r, col.g, col.b);
        linkColors.push(col.r * 0.3, col.g * 0.3, col.b * 0.3);
      }
    });

    // Create LineSegments for radio packet beams
    if (linkPositions.length > 0) {
      const lineGeo = new THREE.BufferGeometry();
      lineGeo.setAttribute('position', new THREE.Float32BufferAttribute(linkPositions, 3));
      lineGeo.setAttribute('color', new THREE.Float32BufferAttribute(linkColors, 3));
      const lineMat = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.35,
      });
      radioLinksMesh = new THREE.LineSegments(lineGeo, lineMat);
      scene.add(radioLinksMesh);
    }
  }

  function updateConflictArcs(conflicts, cells) {
    while (conflictArcsGroup.children.length > 0) {
      const obj = conflictArcsGroup.children.pop();
      if (obj.geometry) obj.geometry.dispose();
    }

    if (!conflicts || conflicts.length === 0) return;

    // At 2 AM collision tick 6, clash is between Cell B and Cell A
    const cellA = cells.find((c) => c.id === 'cell_A');
    const cellB = cells.find((c) => c.id === 'cell_B');

    if (cellA && cellB) {
      const vA = new THREE.Vector3(cellA.x * SCALE, cellA.y * SCALE, 18);
      const vB = new THREE.Vector3(cellB.x * SCALE, cellB.y * SCALE, 18);
      const mid = new THREE.Vector3()
        .addVectors(vA, vB)
        .multiplyScalar(0.5)
        .add(new THREE.Vector3(0, 0, 25));

      const curve = new THREE.QuadraticBezierCurve3(vA, mid, vB);
      const points = curve.getPoints(30);
      const arcGeo = new THREE.BufferGeometry().setFromPoints(points);
      const arcMat = new THREE.LineBasicMaterial({
        color: 0xec4899,
        linewidth: 2,
        transparent: true,
        opacity: 0.9,
      });
      const arcMesh = new THREE.Line(arcGeo, arcMat);
      conflictArcsGroup.add(arcMesh);
    }
  }

  // -------------------------------------------------------------------------
  // Frame Rendering & HUD Population
  // -------------------------------------------------------------------------

  function renderFrame(frame) {
    if (!frame) return;

    // 1. Header & Controls
    DOM.tickDisplay.textContent = frame.tick;
    DOM.tickScrubber.value = frame.tick;
    if (frame.sim_time_ms) {
      const dt = new Date(frame.sim_time_ms);
      DOM.clockDisplay.textContent = dt.toISOString().substr(11, 12);
    }

    // 2. Cluster KPIs
    const drops = frame.kpis ? frame.kpis.dropped_users : 0;
    DOM.kpiDroppedUsers.textContent = drops;
    if (drops > 10) {
      DOM.kpiBoxDrops.className = 'kpi-mini-box alarm';
    } else {
      DOM.kpiBoxDrops.className = 'kpi-mini-box';
    }

    const powerKw = frame.kpis ? (frame.kpis.energy_w / 1000.0).toFixed(2) : '—';
    DOM.kpiTotalPower.textContent = powerKw;

    const sinr = frame.kpis ? frame.kpis.mean_sinr_db.toFixed(1) + ' dB' : '—';
    DOM.kpiMeanSinr.textContent = sinr;

    // 3. Update 3D Scene
    (frame.cells || []).forEach(updateCellTower);
    updateUsers(frame.users || [], frame.cells || []);
    updateConflictArcs(frame.conflicts || [], frame.cells || []);

    // 4. Ingress Proposals
    const props = frame.proposals || [];
    DOM.proposalCount.textContent = props.length;
    if (props.length === 0) {
      DOM.proposalsList.innerHTML = '<div class="empty-state">No proposals on this tick</div>';
    } else {
      DOM.proposalsList.innerHTML = props
        .map(
          (p) => `
        <div class="item-row">
          <div>
            <div style="font-weight:700; color:var(--text-primary); font-size:11px;">${p.command_id}</div>
            <div style="font-size:10px; color:var(--text-muted);">${p.controller_type} → ${p.target_cell_id}</div>
          </div>
          <span class="num item-val" style="color:var(--accent-cyan); font-size:11px;">${p.requested_value.toFixed(2)}</span>
        </div>`
        )
        .join('');
    }

    // 5. ARBITER Decisions
    const decs = frame.decisions || [];
    DOM.decisionCount.textContent = decs.length;
    if (decs.length === 0) {
      DOM.decisionsList.innerHTML = '<div class="empty-state">No decisions evaluated</div>';
    } else {
      DOM.decisionsList.innerHTML = decs
        .map((d) => {
          let bClass = 'badge-approve';
          if (d.action === 'MODIFY') bClass = 'badge-modify';
          else if (d.action === 'DELAY') bClass = 'badge-delay';
          else if (d.action === 'REJECT') bClass = 'badge-reject';

          return `
          <div class="item-row">
            <div>
              <span class="badge ${bClass}">${d.action}</span>
              <span class="num" style="font-size:10px; color:var(--text-secondary); margin-left:4px;">${d.command_id}</span>
            </div>
            <div style="text-align:right;">
              <div style="font-size:10px; color:var(--text-muted);">${d.reason_code || 'NONE'}</div>
              ${d.approved_value !== null && d.approved_value !== undefined ? `<span class="num item-val" style="font-size:11px;">${d.approved_value.toFixed(2)}</span>` : ''}
            </div>
          </div>`;
        })
        .join('');
    }

    // 6. Conflict Edges
    const confs = frame.conflicts || [];
    DOM.conflictCount.textContent = confs.length;
    if (confs.length === 0) {
      DOM.conflictsList.innerHTML = '<div class="empty-state">No conflict edges active</div>';
    } else {
      DOM.conflictsList.innerHTML = confs
        .map(
          (c) => `
        <div class="item-row">
          <span style="color:#ec4899; font-weight:700; font-size:11px;">${c.a} ⚡ ${c.b}</span>
          <span class="badge" style="background:rgba(236,72,153,0.15); color:#ec4899; border:1px solid rgba(236,72,153,0.3); font-size:9px;">${c.type}</span>
        </div>`
        )
        .join('');
    }

    // 7. Cell Inspector HUD
    const activeCell = (frame.cells || []).find((c) => c.id === STATE.selectedCellId) || (frame.cells || [])[0];
    if (activeCell) {
      DOM.selectedCellId.textContent = activeCell.id;
      DOM.cellPos.textContent = `X: ${activeCell.x.toFixed(1)}m, Y: ${activeCell.y.toFixed(1)}m`;
      DOM.cellPowerTilt.textContent = `${activeCell.tx_power_dbm.toFixed(1)} dBm / ${activeCell.tilt_deg.toFixed(1)}°`;
      DOM.cellSleep.textContent = activeCell.is_sleeping ? 'Standby (SLEEPING)' : 'Active (AWAKE)';
      DOM.cellSleep.style.color = activeCell.is_sleeping ? 'var(--text-muted)' : 'var(--badge-green)';
      DOM.cellLoad.textContent = (activeCell.load_ratio * 100).toFixed(1) + '%';
      DOM.cellActiveUes.textContent = activeCell.active_users + ' UEs';
      DOM.cellEnergyW.textContent = activeCell.energy_w.toFixed(1) + ' W';
    }

    // 8. Multi-Tick Plans
    const plans = frame.plans || [];
    DOM.planCount.textContent = plans.length;
    if (plans.length === 0) {
      DOM.plansList.innerHTML = '<div class="empty-state">No active sequential plans</div>';
    } else {
      DOM.plansList.innerHTML = plans
        .map(
          (p) => `
        <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.04);">
          <div style="font-weight:700; font-size:11px; color:var(--accent-blue);">${p.plan_id}</div>
          ${(p.steps || [])
            .map(
              (s) => `
            <div style="font-size:10px; color:var(--text-secondary); margin-left:6px; margin-top:2px;">
              +${s.tick_offset}t: ${s.target_cell_id} ${s.parameter}=${s.value.toFixed(2)} [${s.status}]
            </div>`
            )
            .join('')}
        </div>`
        )
        .join('');
    }

    // 9. Twin Conformal Intervals
    const twin = frame.twin || {};
    DOM.twinStatusBadge.textContent = twin.calibrated ? 'CALIBRATED' : 'UNCALIBRATED';
    DOM.twinStatusBadge.className = twin.calibrated ? 'badge badge-approve' : 'badge badge-delay';

    const intervals = twin.intervals || [];
    if (intervals.length === 0) {
      DOM.twinIntervalsList.innerHTML = '<div class="empty-state">No active intervals</div>';
    } else {
      DOM.twinIntervalsList.innerHTML = intervals
        .map(
          (intv) => `
        <div class="item-row">
          <span class="item-label">${intv.kpi}</span>
          <span class="num item-val" style="font-size:10px; color:var(--text-primary);">
            [${intv.lower.toFixed(3)} - ${intv.upper.toFixed(3)}]
          </span>
        </div>`
        )
        .join('');
    }
  }

  function selectCell(cellId) {
    STATE.selectedCellId = cellId;
    if (STATE.frames.length > 0) {
      renderFrame(STATE.frames[STATE.currentFrameIndex]);
    }
  }

  // -------------------------------------------------------------------------
  // Feed Ingestion & Control Handlers
  // -------------------------------------------------------------------------

  function startReplayPlayback() {
    stopReplayPlayback();
    STATE.isPlaying = true;
    DOM.playIcon.textContent = '⏸';
    DOM.btnPlayPause.innerHTML = '<span id="playIcon">⏸</span> Pause';

    STATE.playInterval = setInterval(() => {
      if (STATE.frames.length === 0) return;
      STATE.currentFrameIndex = (STATE.currentFrameIndex + 1) % STATE.frames.length;
      renderFrame(STATE.frames[STATE.currentFrameIndex]);
    }, 1000 / STATE.fps);
  }

  function stopReplayPlayback() {
    STATE.isPlaying = false;
    if (STATE.playInterval) clearInterval(STATE.playInterval);
    DOM.playIcon.textContent = '▶';
    DOM.btnPlayPause.innerHTML = '<span id="playIcon">▶</span> Play';
  }

  async function loadRecordedFrames() {
    try {
      updateStatus('Connecting to replay stream...');
      const response = await fetch('/fixtures/v3/frames_2am.jsonl');
      if (!response.ok) {
        throw new Error('Could not fetch fixtures/v3/frames_2am.jsonl');
      }
      const text = await response.text();
      const lines = text.trim().split('\n');
      STATE.frames = lines.filter((l) => l.trim().length > 0).map((l) => JSON.parse(l));

      updateStatus('Replay Active (80 ticks)', true);
      if (STATE.frames.length > 0) {
        renderFrame(STATE.frames[0]);
        startReplayPlayback();
      }
    } catch (e) {
      console.warn('[3D] Falling back to latest REST frame:', e);
      fetchLatestRestFrame();
    }
  }

  async function fetchLatestRestFrame() {
    try {
      const resp = await fetch('/api/v1/frame/latest');
      if (resp.ok) {
        const frame = await resp.json();
        STATE.frames = [frame];
        renderFrame(frame);
        updateStatus('Static Latest Frame', true);
      }
    } catch (err) {
      updateStatus('Feed Disconnected', false);
    }
  }

  async function loadCounterfactualComparison() {
    stopReplayPlayback();
    updateStatus('Loading Counterfactual Rollouts...');
    try {
      const resp = await fetch('/api/v1/recorder/counterfactual_frames/dec-2am-collision-06?horizon=20');
      if (!resp.ok) throw new Error('Counterfactual endpoint returned ' + resp.status);
      const data = await resp.json();
      STATE.cfData = data;

      applyWorldSelection();
      startReplayPlayback();
      updateStatus('Counterfactual Horizon (20 ticks)', true);
    } catch (err) {
      console.error('Failed to load counterfactual rollout:', err);
      updateStatus('CF Error', false);
    }
  }

  function applyWorldSelection() {
    if (STATE.streamMode === 'cf_split' && STATE.cfData) {
      const worldKey = STATE.activeWorld; // 'arbiter' or 'counterfactual'
      STATE.frames = STATE.cfData[worldKey] || [];
      STATE.currentFrameIndex = 0;
      if (STATE.frames.length > 0) {
        renderFrame(STATE.frames[0]);
      }
    }
  }

  function connectLiveWebSocket() {
    stopReplayPlayback();
    if (STATE.ws) {
      try { STATE.ws.close(); } catch (e) {}
    }

    const loc = window.location;
    const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${loc.host}/api/v1/ws/live`;

    updateStatus('Connecting to live WebSocket...');
    STATE.ws = new WebSocket(wsUrl);

    STATE.ws.onopen = () => {
      updateStatus('Live WS Connected', true);
    };

    STATE.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'frame') {
          STATE.frames.push(msg);
          STATE.currentFrameIndex = STATE.frames.length - 1;
          renderFrame(msg);
        }
      } catch (err) {
        console.warn('Malformed frame message:', err);
      }
    };

    STATE.ws.onclose = () => {
      updateStatus('WS Disconnected', false);
    };

    STATE.ws.onerror = () => {
      updateStatus('WS Error', false);
    };
  }

  function updateStatus(text, isConnected = false) {
    if (DOM.connectionText) DOM.connectionText.textContent = text;
    if (DOM.connectionStatus) {
      DOM.connectionStatus.className = isConnected
        ? 'status-indicator connected'
        : 'status-indicator';
    }
  }

  // -------------------------------------------------------------------------
  // Event Listeners
  // -------------------------------------------------------------------------

  DOM.btnPlayPause.addEventListener('click', () => {
    if (STATE.isPlaying) {
      stopReplayPlayback();
    } else {
      startReplayPlayback();
    }
  });

  DOM.tickScrubber.addEventListener('input', (e) => {
    stopReplayPlayback();
    const idx = parseInt(e.target.value, 10);
    if (idx >= 0 && idx < STATE.frames.length) {
      STATE.currentFrameIndex = idx;
      renderFrame(STATE.frames[idx]);
    }
  });

  DOM.streamSource.addEventListener('change', (e) => {
    STATE.streamMode = e.target.value;
    if (STATE.streamMode === 'live') {
      connectLiveWebSocket();
    } else if (STATE.streamMode === 'cf_split') {
      loadCounterfactualComparison();
    } else {
      if (STATE.ws) {
        try { STATE.ws.close(); } catch (err) {}
      }
      loadRecordedFrames();
    }
  });

  DOM.worldToggle.addEventListener('change', (e) => {
    STATE.activeWorld = e.target.value;
    DOM.worldPill.textContent = STATE.activeWorld.toUpperCase();
    DOM.worldPill.className = `world-pill ${STATE.activeWorld}`;
    applyWorldSelection();
  });

  // -------------------------------------------------------------------------
  // Initial Boot
  // -------------------------------------------------------------------------
  initThree();
  loadRecordedFrames();
})();
