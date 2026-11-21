/**
 * PROJECT ARBITER — 3D RAN Digital Twin Visualizer
 * Module: arbiter/dashboard/3d/scene.js
 *
 * Responsibilities:
 * - Three.js WebGL scene, lighting, dark NOC ground plane & hex grid.
 * - Cell towers with dynamic load gauges, state coloring, and billboard labels.
 * - Coverage discs with monotonic power mapping and electrical down-tilt.
 * - Single InstancedMesh for 350+ users colored by SINR with dropped pulse.
 * - Single LineSegments buffer for user-to-tower RF links.
 * - Buttery-smooth 100 ms tick lerp interpolation at 60 FPS.
 * - Top-down tilted camera with OrbitControls and demo reset.
 * - Frame performance profiling (?stats=1).
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ArbitrationSceneLayer } from './arbitration-scene.js';
import { GhostSceneLayer } from './ghost-scene.js';

// Visual scale mapping constants
const COVERAGE_TX_MIN = 30.0;
const COVERAGE_TX_MAX = 46.0;
const COVERAGE_R_MIN = 150.0;
const COVERAGE_R_MAX = 330.0;

/**
 * Coverage disc radius mapping formula:
 * R(tx) = R_min + (R_max - R_min) * ((tx - tx_min) / (tx_max - tx_min))
 * Visual representation mapping [30, 46] dBm -> [150, 330] metres.
 * If sleeping, radius collapses to 0.
 */
export function computeCoverageRadius(txPowerDbm, isSleeping) {
  if (isSleeping) return 0.0;
  const clamped = Math.max(COVERAGE_TX_MIN, Math.min(COVERAGE_TX_MAX, txPowerDbm || 43.0));
  return COVERAGE_R_MIN + (COVERAGE_R_MAX - COVERAGE_R_MIN) * (
    (clamped - COVERAGE_TX_MIN) / (COVERAGE_TX_MAX - COVERAGE_TX_MIN)
  );
}

/**
 * Color ramp based on SINR:
 * SINR <= -3.0 dB: Red (#ef4444)
 * SINR = 0.0 dB: Amber (#f59e0b)
 * SINR >= 12.0 dB: Emerald Green (#10b981)
 */
export function sinrToColor(sinrDb, isDropped = false) {
  if (isDropped || sinrDb <= -3.0) {
    return new THREE.Color(0xef4444); // Red
  }
  if (sinrDb < 4.0) {
    // Interpolate Red -> Amber
    const t = Math.max(0, (sinrDb + 3.0) / 7.0);
    return new THREE.Color(0xef4444).lerp(new THREE.Color(0xf59e0b), t);
  }
  // Interpolate Amber -> Emerald Green
  const t = Math.min(1.0, (sinrDb - 4.0) / 10.0);
  return new THREE.Color(0xf59e0b).lerp(new THREE.Color(0x10b981), t);
}

export class Arbiter3DScene {
  constructor(containerElement, options = {}) {
    this.container = containerElement;
    this.options = options;
    this.showStats = options.showStats || false;

    this.width = this.container.clientWidth || window.innerWidth;
    this.height = this.container.clientHeight || window.innerHeight;

    // Camera preset
    this.cameraPreset = {
      position: new THREE.Vector3(0, 950, 1100),
      target: new THREE.Vector3(0, 0, 0),
    };

    // State cache
    this.cellEntities = new Map(); // id -> { group, mast, gauge, disc, sprite, hex, config }
    this.userCount = 0;
    this.maxUsers = 2000;

    // Interpolation state
    this.lastTickTime = performance.now();
    this.tickDurationMs = 100;
    this.currentUsers = [];
    this.targetUsers = [];
    this.userPositionsPrev = new Map(); // id -> { x, y, z }
    this.userPositionsTarget = new Map();

    // Profiling
    this.frameCount = 0;
    this.lastFpsUpdate = performance.now();
    this.fps = 60;
    this.frameTimeMs = 16.6;

    this.init();
  }

  init() {
    // 1. Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x08090c);
    this.scene.fog = new THREE.FogExp2(0x08090c, 0.00035);

    // 2. Camera
    this.camera = new THREE.PerspectiveCamera(
      45,
      this.width / this.height,
      10,
      8000
    );
    this.resetCamera();

    // 3. Renderer
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      powerPreference: 'high-performance',
    });
    this.renderer.setSize(this.width, this.height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.appendChild(this.renderer.domElement);

    // 4. OrbitControls
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI / 2.05; // Prevent dipping below ground
    this.controls.minDistance = 80;
    this.controls.maxDistance = 3500;
    this.controls.target.copy(this.cameraPreset.target);

    // 5. Lighting
    this.setupLighting();

    // 6. Ground & Hex Grid
    this.setupGround();

    // 7. Users InstancedMesh
    this.setupUserInstances();

    // 8. RF Connection Lines (Single buffer draw call)
    this.setupConnectionLines();

    // 8b. Arbitration Layer (Controllers, Core, Packets, Conflict Arcs, Verdicts)
    this.arbitrationLayer = new ArbitrationSceneLayer(this.scene, {
      cellEntities: this.cellEntities,
    });

    // 8c. Ghost Scene Layer (Counterfactual Ghost World, Uncertainty Shells, Plans)
    this.ghostLayer = new GhostSceneLayer(this.scene, {
      cellEntities: this.cellEntities,
    });

    // 9. Resize & Key Listeners
    this.setupEventListeners();

    // 10. Start Animation Loop
    this.animate = this.animate.bind(this);
    this.animationId = requestAnimationFrame(this.animate);
  }

  setupLighting() {
    const ambient = new THREE.AmbientLight(0x272e3d, 1.4);
    this.scene.add(ambient);

    // Main key light
    const keyLight = new THREE.DirectionalLight(0xe2e8f0, 2.2);
    keyLight.position.set(600, 1400, 700);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.width = 2048;
    keyLight.shadow.mapSize.height = 2048;
    keyLight.shadow.camera.near = 100;
    keyLight.shadow.camera.far = 3000;
    const d = 1200;
    keyLight.shadow.camera.left = -d;
    keyLight.shadow.camera.right = d;
    keyLight.shadow.camera.top = d;
    keyLight.shadow.camera.bottom = -d;
    keyLight.shadow.bias = -0.0005;
    this.scene.add(keyLight);

    // Subtle blue rim fill
    const fillLight = new THREE.DirectionalLight(0x0284c7, 0.85);
    fillLight.position.set(-800, 500, -700);
    this.scene.add(fillLight);
  }

  setupGround() {
    // Infinite-feel dark NOC floor
    const groundGeo = new THREE.PlaneGeometry(6000, 6000);
    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x0a0c12,
      roughness: 0.9,
      metalness: 0.1,
    });
    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -1;
    ground.receiveShadow = true;
    this.scene.add(ground);

    // Subtle technological grid
    const grid = new THREE.GridHelper(3500, 70, 0x1e293b, 0x0f172a);
    grid.position.y = 0;
    this.scene.add(grid);
  }

  setupUserInstances() {
    const userGeo = new THREE.SphereGeometry(3.6, 8, 8);
    this.userMaterial = new THREE.MeshStandardMaterial({
      roughness: 0.3,
      metalness: 0.2,
      emissive: 0x000000,
    });

    this.userInstancedMesh = new THREE.InstancedMesh(
      userGeo,
      this.userMaterial,
      this.maxUsers
    );
    this.userInstancedMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.userInstancedMesh.receiveShadow = true;
    this.userInstancedMesh.castShadow = true;
    this.scene.add(this.userInstancedMesh);

    // Initial dummy transform off-screen
    const dummy = new THREE.Matrix4();
    dummy.setPosition(0, -9999, 0);
    for (let i = 0; i < this.maxUsers; i++) {
      this.userInstancedMesh.setMatrixAt(i, dummy);
    }
    this.userInstancedMesh.instanceMatrix.needsUpdate = true;
  }

  setupConnectionLines() {
    // 2 vertices per line * 3 coordinates * maxUsers
    this.linePositions = new Float32Array(this.maxUsers * 2 * 3);
    this.lineColors = new Float32Array(this.maxUsers * 2 * 3);

    this.lineGeometry = new THREE.BufferGeometry();
    this.lineGeometry.setAttribute(
      'position',
      new THREE.BufferAttribute(this.linePositions, 3).setUsage(THREE.DynamicDrawUsage)
    );
    this.lineGeometry.setAttribute(
      'color',
      new THREE.BufferAttribute(this.lineColors, 3).setUsage(THREE.DynamicDrawUsage)
    );

    this.lineMaterial = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
    });

    this.connectionLines = new THREE.LineSegments(
      this.lineGeometry,
      this.lineMaterial
    );
    this.scene.add(this.connectionLines);
  }

  setupEventListeners() {
    this.onResize = () => {
      this.width = this.container.clientWidth || window.innerWidth;
      this.height = this.container.clientHeight || window.innerHeight;
      this.camera.aspect = this.width / this.height;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(this.width, this.height);
    };
    window.addEventListener('resize', this.onResize);

    // Reset view on 'R' or 'r' key
    this.onKeyDown = (e) => {
      if (e.key === 'r' || e.key === 'R') {
        this.resetCamera();
      }
    };
    window.addEventListener('keydown', this.onKeyDown);
  }

  setCameraPreset(presetNumber) {
    if (presetNumber === 1) {
      // Preset 1: Full cluster overview
      this.cameraPreset.position.set(0, 680, 850);
      this.cameraPreset.target.set(0, 0, 0);
    } else if (presetNumber === 2) {
      // Preset 2: Demo Collision Close-up (aimed at cell_A & cell_B boundary)
      this.cameraPreset.position.set(115, 140, 320);
      this.cameraPreset.target.set(115, 20, 55);
    }
    this.resetCamera();
  }

  resetCamera() {
    this.camera.position.copy(this.cameraPreset.position);
    if (this.controls) {
      this.controls.target.copy(this.cameraPreset.target);
      this.controls.update();
    } else {
      this.camera.lookAt(this.cameraPreset.target);
    }
  }

  /**
   * Create or update a cell tower entity in the 3D scene.
   */
  getOrCreateCellEntity(cellData) {
    const id = cellData.id;
    if (this.cellEntities.has(id)) {
      return this.cellEntities.get(id);
    }

    const cellGroup = new THREE.Group();
    const cx = Number(cellData.x) || 0;
    const cz = Number(cellData.y) || 0; // Note: RAN y is 3D z
    cellGroup.position.set(cx, 0, cz);

    // 1. Hexagonal ground tile under the cell
    const hexRadius = 500.0 / Math.sqrt(3.0); // 288.68m
    const hexGeo = new THREE.CylinderGeometry(hexRadius, hexRadius, 1.5, 6);
    const hexMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      roughness: 0.7,
      metalness: 0.3,
      transparent: true,
      opacity: 0.85,
    });
    const hexTile = new THREE.Mesh(hexGeo, hexMat);
    hexTile.rotation.y = Math.PI / 6; // Pointy-top hex orientation
    hexTile.position.y = 0.5;
    hexTile.receiveShadow = true;
    cellGroup.add(hexTile);

    // Hex border wireframe
    const hexEdges = new THREE.EdgesGeometry(hexGeo);
    const hexLine = new THREE.LineSegments(
      hexEdges,
      new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.35 })
    );
    hexLine.rotation.y = Math.PI / 6;
    hexLine.position.y = 0.5;
    cellGroup.add(hexLine);

    // 2. Equipment Cabin at Tower Base
    const slabGeo = new THREE.BoxGeometry(11, 1.0, 11);
    const slabMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.9 });
    const slab = new THREE.Mesh(slabGeo, slabMat);
    slab.position.set(10, 0.5, 10);
    cellGroup.add(slab);

    const cabinGeo = new THREE.BoxGeometry(8, 6.5, 8);
    const cabinMat = new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.6, metalness: 0.4 });
    const cabin = new THREE.Mesh(cabinGeo, cabinMat);
    cabin.position.set(10, 4.25, 10);
    cabin.castShadow = true;
    cellGroup.add(cabin);

    // 3. Tapered Lattice Mast
    const mastGeo = new THREE.CylinderGeometry(1.6, 4.8, 45, 4);
    const mastMat = new THREE.MeshStandardMaterial({
      color: 0x64748b,
      metalness: 0.7,
      roughness: 0.35,
    });
    const mast = new THREE.Mesh(mastGeo, mastMat);
    mast.position.y = 22.5;
    mast.rotation.y = Math.PI / 4;
    mast.castShadow = true;
    cellGroup.add(mast);

    const mastEdges = new THREE.EdgesGeometry(mastGeo);
    const mastWire = new THREE.LineSegments(mastEdges, new THREE.LineBasicMaterial({ color: 0x94a3b8 }));
    mastWire.position.y = 22.5;
    mastWire.rotation.y = Math.PI / 4;
    cellGroup.add(mastWire);

    // Horizontal structural lattice ring braces
    const braceHeights = [11, 22, 33, 44];
    for (const bh of braceHeights) {
      const frac = (45 - bh) / 45;
      const r = 1.6 + (4.8 - 1.6) * frac;
      const braceGeo = new THREE.CylinderGeometry(r * 1.05, r * 1.05, 0.8, 4);
      const braceMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.8 });
      const brace = new THREE.Mesh(braceGeo, braceMat);
      brace.position.y = bh;
      brace.rotation.y = Math.PI / 4;
      cellGroup.add(brace);
    }

    // 4. Antenna Platform Platform Head & Three Tilted Sector Panels
    const headGeo = new THREE.CylinderGeometry(3.6, 3.6, 1.2, 3);
    const headMat = new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.8 });
    const head = new THREE.Mesh(headGeo, headMat);
    head.position.y = 44.5;
    head.castShadow = true;
    cellGroup.add(head);

    const sectorPanels = [];
    const sectorAngles = [0, (2 * Math.PI) / 3, (4 * Math.PI) / 3];
    const initialTiltRad = THREE.MathUtils.degToRad(cellData.tilt_deg || 6.0);
    const panelGeo = new THREE.BoxGeometry(1.2, 6.0, 0.6);
    const panelMat = new THREE.MeshStandardMaterial({ color: 0xf1f5f9, roughness: 0.3, metalness: 0.2 });

    for (let s = 0; s < 3; s++) {
      const azimuth = sectorAngles[s];
      const sectorGroup = new THREE.Group();
      sectorGroup.position.set(0, 44.5, 0);
      sectorGroup.rotation.y = azimuth;

      const armGeo = new THREE.CylinderGeometry(0.3, 0.3, 3.2, 8);
      const armMat = new THREE.MeshStandardMaterial({ color: 0x475569 });
      const arm = new THREE.Mesh(armGeo, armMat);
      arm.rotation.z = Math.PI / 2;
      arm.position.set(1.6, 0, 0);
      sectorGroup.add(arm);

      const panel = new THREE.Mesh(panelGeo, panelMat);
      panel.position.set(3.2, 0, 0);
      panel.rotation.x = initialTiltRad;
      panel.castShadow = true;
      sectorGroup.add(panel);

      sectorPanels.push(panel);
      cellGroup.add(sectorGroup);
    }

    // 5. Red Obstacle Warning Beacon on Pinnacle Tip
    const beaconGeo = new THREE.SphereGeometry(1.2, 8, 8);
    const beaconMat = new THREE.MeshStandardMaterial({
      color: 0xef4444,
      emissive: 0xef4444,
      emissiveIntensity: 1.6,
    });
    const beacon = new THREE.Mesh(beaconGeo, beaconMat);
    beacon.position.y = 48.0;
    cellGroup.add(beacon);

    // 6. Load Gauge Cylinder
    const gaugeGeo = new THREE.CylinderGeometry(2.2, 2.2, 20, 16);
    const gaugeMat = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      emissive: 0x0284c7,
      emissiveIntensity: 0.5,
      transparent: true,
      opacity: 0.9,
    });
    const gauge = new THREE.Mesh(gaugeGeo, gaugeMat);
    gauge.position.y = 25;
    cellGroup.add(gauge);

    // 7. Coverage Disc
    const discRadius = computeCoverageRadius(cellData.tx_power_dbm, cellData.is_sleeping);
    const discGeo = new THREE.RingGeometry(0, discRadius, 32);
    const discMat = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0.12,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const coverageDisc = new THREE.Mesh(discGeo, discMat);
    coverageDisc.rotation.x = -Math.PI / 2;
    coverageDisc.position.y = 2.0;
    cellGroup.add(coverageDisc);

    // 8. Billboard Label Sprite
    const labelSprite = this.createCellLabelSprite(id, cellData);
    labelSprite.position.set(0, 60, 0);
    cellGroup.add(labelSprite);

    this.scene.add(cellGroup);

    const entity = {
      id,
      group: cellGroup,
      mast,
      cabin,
      beacon,
      sectorPanels,
      gauge,
      gaugeMat,
      coverageDisc,
      discMat,
      labelSprite,
      hexTile,
      config: cellData,
    };
      hexTile,
      config: cellData,
    };
    this.cellEntities.set(id, entity);
    return entity;
  }

  createCellLabelSprite(id, data) {
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');

    this.drawLabelCanvas(ctx, id, data);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(70, 35, 1);
    sprite.userData = { canvas, ctx, texture };
    return sprite;
  }

  drawLabelCanvas(ctx, id, data) {
    ctx.clearRect(0, 0, 256, 128);

    // Rounded background pill
    ctx.fillStyle = 'rgba(15, 23, 42, 0.88)';
    ctx.strokeStyle = data.is_sleeping ? 'rgba(100, 116, 139, 0.6)' : 'rgba(56, 189, 248, 0.8)';
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.roundRect(8, 8, 240, 112, 16);
    ctx.fill();
    ctx.stroke();

    // Cell ID
    ctx.fillStyle = '#f8fafc';
    ctx.font = 'bold 28px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(id, 128, 44);

    // Users & Load status
    const ues = data.active_users !== undefined ? data.active_users : 0;
    const loadPct = Math.round((data.load_ratio || 0) * 100);
    ctx.font = '20px sans-serif';

    if (data.is_sleeping) {
      ctx.fillStyle = '#94a3b8';
      ctx.fillText('SLEEPING (0W)', 128, 80);
    } else {
      ctx.fillStyle = data.load_ratio > 0.75 ? '#ef4444' : '#38bdf8';
      ctx.fillText(`${ues} UEs • ${loadPct}% Load`, 128, 80);
    }
  }

  updateCellEntities(cells) {
    if (!cells || cells.length === 0) {
      // When there are no cells in the frame, render nothing (no placeholder towers)
      for (const entity of this.cellEntities.values()) {
        entity.group.visible = false;
      }
      return;
    }

    const activeIds = new Set(cells.map((c) => c.id));
    for (const [id, entity] of this.cellEntities) {
      if (!activeIds.has(id)) {
        entity.group.visible = false;
      }
    }

    for (const cellData of cells) {
      const entity = this.getOrCreateCellEntity(cellData);
      entity.group.visible = true;
      entity.config = cellData;

      // Update Tower Load Gauge
      const load = Math.max(0.0, Math.min(1.0, cellData.load_ratio || 0.0));
      entity.gauge.scale.set(1.0, Math.max(0.05, load), 1.0);
      entity.gauge.position.y = 15 + (load * 10);

      // Color by state
      if (cellData.is_sleeping) {
        // Dim grey sleeping
        entity.gaugeMat.color.setHex(0x475569);
        entity.gaugeMat.emissive.setHex(0x1e293b);
        entity.gaugeMat.opacity = 0.35;
      } else if (load > 0.75) {
        // Overloaded Red/Amber
        entity.gaugeMat.color.setHex(0xef4444);
        entity.gaugeMat.emissive.setHex(0xb91c1c);
        entity.gaugeMat.opacity = 0.95;
      } else {
        // Active Cyan
        entity.gaugeMat.color.setHex(0x38bdf8);
        entity.gaugeMat.emissive.setHex(0x0284c7);
        entity.gaugeMat.opacity = 0.9;
      }

      // Update Coverage Disc Radius & Tilt
      const targetRadius = computeCoverageRadius(cellData.tx_power_dbm, cellData.is_sleeping);
      if (targetRadius > 0) {
        entity.coverageDisc.visible = true;
        entity.coverageDisc.scale.set(targetRadius, targetRadius, 1.0);
        // Tilt beam representation
        const tiltRad = THREE.MathUtils.degToRad(cellData.tilt_deg || 6.0);
        entity.coverageDisc.rotation.z = tiltRad * 0.5;
        entity.discMat.opacity = cellData.is_sleeping ? 0.0 : 0.14;
      } else {
        entity.coverageDisc.visible = false;
      }

      // Update Sector Panels Tilt
      if (entity.sectorPanels) {
        const tiltRad = THREE.MathUtils.degToRad(cellData.tilt_deg || 6.0);
        for (const panel of entity.sectorPanels) {
          panel.rotation.x = tiltRad;
        }
      }

      // Redraw Canvas Label
      const { ctx, texture } = entity.labelSprite.userData;
      this.drawLabelCanvas(ctx, entity.id, cellData);
      texture.needsUpdate = true;
    }
  }

  /**
   * Ingest new frame data for tick lerp interpolation.
   */
  onNewFrame(frame) {
    if (!frame || typeof frame !== 'object') return;

    this.lastTickTime = performance.now();
    const cells = Array.isArray(frame.cells) ? frame.cells : [];
    this.updateCellEntities(cells);

    // Update Arbitration Layer with proposals, conflicts, decisions
    if (this.arbitrationLayer) {
      this.arbitrationLayer.onFrame(frame);
    }

    // Update Ghost Layer with Twin Uncertainty Shells & Plan Steps
    if (this.ghostLayer) {
      if (frame.twin) {
        this.ghostLayer.updateUncertaintyShells(frame.twin, cells);
      }
      if (frame.plans) {
        this.ghostLayer.updatePlanGhosts(frame.plans);
      }
    }

    const users = Array.isArray(frame.users) ? frame.users : [];
    if (cells.length === 0 || users.length === 0) {
      // Empty topology or degraded mode: clear users and lines
      this.targetUsers = [];
      this.userPositionsPrev.clear();
      this.userPositionsTarget.clear();
      this.userInstancedMesh.count = 0;
      this.userInstancedMesh.instanceMatrix.needsUpdate = true;
      this.lineGeometry.setDrawRange(0, 0);
      return;
    }

    // Capture previous and target user positions for lerping
    const users = Array.isArray(frame.users) ? frame.users : [];
    this.userPositionsPrev.clear();
    for (const [id, pos] of this.userPositionsTarget.entries()) {
      this.userPositionsPrev.set(id, { ...pos });
    }

    this.userPositionsTarget.clear();
    for (const u of users) {
      const targetPos = {
        x: Number(u.x) || 0,
        y: 3.5,
        z: Number(u.y) || 0, // Note: RAN y is 3D z
        cell: u.cell,
        sinr: Number(u.sinr_db) || 0,
        dropped: Boolean(u.dropped),
      };
      this.userPositionsTarget.set(u.id, targetPos);

      // If user was not present previously, initialize at target
      if (!this.userPositionsPrev.has(u.id)) {
        this.userPositionsPrev.set(u.id, { ...targetPos });
      }
    }

    this.targetUsers = users;
  }

  /**
   * 60 FPS Render and Interpolation Loop.
   */
  animate(timestamp) {
    this.animationId = requestAnimationFrame(this.animate);

    const now = performance.now();
    const frameDeltaSec = (this.lastFrameTime ? (now - this.lastFrameTime) : 16.6) / 1000.0;
    this.lastFrameTime = now;

    const dt = now - this.lastTickTime;
    const alpha = Math.min(1.0, Math.max(0.0, dt / this.tickDurationMs));

    // Update Controls
    this.controls.update();

    // Lerp User Positions & Lines
    this.renderInterpolatedUsers(alpha, now);

    // Update Arbitration Layer animations
    if (this.arbitrationLayer) {
      this.arbitrationLayer.update(frameDeltaSec);
    }

    // Update Ghost Layer animations (Uncertainty shell lerp, falling drop particles)
    if (this.ghostLayer) {
      this.ghostLayer.update(frameDeltaSec);
    }

    // Render Scene
    this.renderer.render(this.scene, this.camera);

    // Profile FPS
    this.frameCount++;
    if (now - this.lastFpsUpdate >= 1000) {
      this.fps = Math.round((this.frameCount * 1000) / (now - this.lastFpsUpdate));
      this.frameTimeMs = (1000 / Math.max(1, this.fps)).toFixed(2);
      this.frameCount = 0;
      this.lastFpsUpdate = now;

      if (this.showStats) {
        console.log(`[3D Stats] FPS: ${this.fps} | FrameTime: ${this.frameTimeMs}ms | DrawCalls: ${this.renderer.info.render.calls}`);
      }
    }
  }

  renderInterpolatedUsers(alpha, timestamp) {
    const users = this.targetUsers;
    const count = Math.min(users.length, this.maxUsers);
    if (count === 0) {
      this.userInstancedMesh.count = 0;
      this.userInstancedMesh.instanceMatrix.needsUpdate = true;
      this.lineGeometry.setDrawRange(0, 0);
      return;
    }
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const scale = new THREE.Vector3();
    const rot = new THREE.Quaternion();

    let lineVertexIdx = 0;

    for (let i = 0; i < count; i++) {
      const u = users[i];
      const prev = this.userPositionsPrev.get(u.id);
      const target = this.userPositionsTarget.get(u.id);

      if (!prev || !target) continue;

      // Linear interpolation between ticks
      position.x = prev.x + (target.x - prev.x) * alpha;
      position.y = 3.5;
      position.z = prev.z + (target.z - prev.z) * alpha;

      // Pulse dropped users
      let userScale = 1.0;
      if (target.dropped) {
        userScale = 1.3 + 0.5 * Math.sin(timestamp * 0.02);
      }
      scale.set(userScale, userScale, userScale);

      matrix.compose(position, rot, scale);
      this.userInstancedMesh.setMatrixAt(i, matrix);

      // Color by SINR / Dropped state
      const color = sinrToColor(target.sinr, target.dropped);
      this.userInstancedMesh.setColorAt(i, color);

      // Update RF Connection Line to Serving Cell Tower
      const servingEntity = this.cellEntities.get(target.cell);
      if (servingEntity && !target.dropped) {
        const cellPos = servingEntity.group.position;

        // Vertex 1: User position
        this.linePositions[lineVertexIdx] = position.x;
        this.linePositions[lineVertexIdx + 1] = position.y;
        this.linePositions[lineVertexIdx + 2] = position.z;

        this.lineColors[lineVertexIdx] = color.r;
        this.lineColors[lineVertexIdx + 1] = color.g;
        this.lineColors[lineVertexIdx + 2] = color.b;
        lineVertexIdx += 3;

        // Vertex 2: Tower mast head
        this.linePositions[lineVertexIdx] = cellPos.x;
        this.linePositions[lineVertexIdx + 1] = 22.0;
        this.linePositions[lineVertexIdx + 2] = cellPos.z;

        this.lineColors[lineVertexIdx] = color.r * 0.6;
        this.lineColors[lineVertexIdx + 1] = color.g * 0.6;
        this.lineColors[lineVertexIdx + 2] = color.b * 0.6;
        lineVertexIdx += 3;
      }
    }

    // Hide remaining unused instances
    const dummy = new THREE.Matrix4().setPosition(0, -9999, 0);
    for (let i = count; i < this.maxUsers; i++) {
      this.userInstancedMesh.setMatrixAt(i, dummy);
    }

    this.userInstancedMesh.instanceMatrix.needsUpdate = true;
    if (this.userInstancedMesh.instanceColor) {
      this.userInstancedMesh.instanceColor.needsUpdate = true;
    }

    // Zero out unused connection lines
    for (let idx = lineVertexIdx; idx < this.linePositions.length; idx++) {
      this.linePositions[idx] = 0;
      this.lineColors[idx] = 0;
    }
    this.lineGeometry.attributes.position.needsUpdate = true;
    this.lineGeometry.attributes.color.needsUpdate = true;
  }

  destroy() {
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
    }
    window.removeEventListener('resize', this.onResize);
    window.removeEventListener('keydown', this.onKeyDown);
    if (this.renderer && this.renderer.domElement) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
  }
}
