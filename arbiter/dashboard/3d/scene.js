/**
 * PROJECT ARBITER — 3D RAN Digital Twin Visualizer
 * Module: arbiter/dashboard/3d/scene.js
 *
 * Responsibilities:
 * - Three.js WebGL scene, lighting, dark NOC ground plane & warm gold radial vignette.
 * - Dynamic Voronoi cell footprint generation from real cell coordinates (non-overlapping).
 * - Tower geometry visible from Overview preset (mast ~76m, tapered lattice, cabin, 3 sector panels).
 * - Amber/white active pinnacle beacon (dark when sleeping) and base load gauge ring.
 * - HTML/CSS2D glass pill labels (Outfit bold title, JetBrains Mono users/load, calibration tooltip).
 * - Gold/white gradient coverage discs with additive blending.
 * - InstancedMesh for 350+ users colored by SINR (radius 5.8m, y=4.0) with dropped pulse.
 * - Subtle slow dust particles (CSS/reduced-motion safe).
 * - Single LineSegments buffer for user-to-tower RF links.
 * - 60 FPS tick lerp interpolation with clean resource disposal on reset.
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
 * Color ramp based on SINR mapped to Site Semantic Tokens:
 * SINR <= -3.0 dB: Bad Red (#FF4D5E / 0xFF4D5E)
 * SINR = 0.0 dB: Amber (#FFB020 / 0xFFB020)
 * SINR >= 12.0 dB: Emerald Green (#2EE6A6 / 0x2EE6A6)
 */
export function sinrToColor(sinrDb, isDropped = false) {
  if (isDropped || sinrDb <= -3.0) {
    return new THREE.Color(0xFF4D5E); // Red
  }
  if (sinrDb < 4.0) {
    // Interpolate Red -> Amber
    const t = Math.max(0, (sinrDb + 3.0) / 7.0);
    return new THREE.Color(0xFF4D5E).lerp(new THREE.Color(0xFFB020), t);
  }
  // Interpolate Amber -> Emerald Green
  const t = Math.min(1.0, (sinrDb - 4.0) / 10.0);
  return new THREE.Color(0xFFB020).lerp(new THREE.Color(0x2EE6A6), t);
}

/**
 * Sutherland-Hodgman polygon clipping against a 2D half-plane.
 */
function clipPolygonWithHalfPlane(poly, pLine, normal) {
  const inside = (cp) => (cp.x - pLine.x) * normal.x + (cp.z - pLine.z) * normal.z <= 0;
  const intersection = (cp1, cp2) => {
    const dc = { x: cp1.x - cp2.x, z: cp1.z - cp2.z };
    const dp = { x: cp1.x - pLine.x, z: cp1.z - pLine.z };
    const denom = dc.x * normal.x + dc.z * normal.z;
    if (Math.abs(denom) < 1e-9) return cp1;
    const t = (dp.x * normal.x + dp.z * normal.z) / denom;
    return { x: cp1.x - t * dc.x, z: cp1.z - t * dc.z };
  };

  const out = [];
  if (!poly || poly.length === 0) return out;
  let s = poly[poly.length - 1];
  for (const e of poly) {
    if (inside(e)) {
      if (inside(s)) {
        out.push(e);
      } else {
        out.push(intersection(s, e));
        out.push(e);
      }
    } else if (inside(s)) {
      out.push(intersection(s, e));
    }
    s = e;
  }
  return out;
}

/**
 * Computes non-overlapping Voronoi footprint polygons for all cells
 * clipped to a bounding circle around the cluster.
 */
export function computeVoronoiFootprints(cells, boundingRadius = 750) {
  if (!cells || cells.length === 0) return new Map();

  // Find cluster center
  let sumX = 0;
  let sumZ = 0;
  for (const c of cells) {
    sumX += Number(c.x) || 0;
    sumZ += Number(c.y) || 0; // RAN y is 3D z
  }
  const centerX = sumX / cells.length;
  const centerZ = sumZ / cells.length;

  // Base bounding circle polygon (32 segments)
  const segments = 32;
  const basePolygon = [];
  for (let i = 0; i < segments; i++) {
    const angle = (i * 2 * Math.PI) / segments;
    basePolygon.push({
      x: centerX + boundingRadius * Math.cos(angle),
      z: centerZ + boundingRadius * Math.sin(angle),
    });
  }

  const voronoiMap = new Map();

  for (let i = 0; i < cells.length; i++) {
    const ci = cells[i];
    const cix = Number(ci.x) || 0;
    const ciz = Number(ci.y) || 0;

    let poly = basePolygon.map((p) => ({ ...p }));

    for (let j = 0; j < cells.length; j++) {
      if (i === j) continue;
      const cj = cells[j];
      const cjx = Number(cj.x) || 0;
      const cjz = Number(cj.y) || 0;

      const mid = { x: (cix + cjx) / 2, z: (ciz + cjz) / 2 };
      const norm = { x: cjx - cix, z: cjz - ciz };
      poly = clipPolygonWithHalfPlane(poly, mid, norm);
    }

    voronoiMap.set(ci.id, poly);
  }

  return voronoiMap;
}

/**
 * Generate a soft gold/white radial gradient canvas texture for coverage discs.
 */
function createCoverageDiscTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');
  const grad = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0, 'rgba(255, 255, 255, 0.28)');
  grad.addColorStop(0.35, 'rgba(255, 216, 74, 0.16)');
  grad.addColorStop(0.75, 'rgba(255, 200, 0, 0.06)');
  grad.addColorStop(1, 'rgba(255, 200, 0, 0.0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 256, 256);
  const texture = new THREE.CanvasTexture(canvas);
  return texture;
}

export class Arbiter3DScene {
  constructor(containerElement, options = {}) {
    this.container = containerElement;
    this.options = options;
    this.showStats = options.showStats || false;

    this.width = this.container.clientWidth || window.innerWidth;
    this.height = this.container.clientHeight || window.innerHeight;

    // Check prefers-reduced-motion
    this.prefersReducedMotion = false;
    if (typeof window !== 'undefined' && window.matchMedia) {
      this.prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    // Camera preset
    this.cameraPreset = {
      position: new THREE.Vector3(0, 950, 1100),
      target: new THREE.Vector3(0, 0, 0),
    };

    // State cache
    this.cellEntities = new Map(); // id -> { group, mast, gaugeRing, disc, domLabel, footprintMesh, footprintOutline, config }
    this.cachedVoronoiTopologyKey = '';
    this.userCount = 0;
    this.maxUsers = 2000;

    // Coverage texture singleton
    this.coverageDiscTexture = createCoverageDiscTexture();

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

    // HTML labels overlay container
    this.labelsOverlay = document.getElementById('tower-labels-layer');
    if (!this.labelsOverlay) {
      this.labelsOverlay = document.createElement('div');
      this.labelsOverlay.id = 'tower-labels-layer';
      this.labelsOverlay.className = 'tower-labels-layer';
      this.container.appendChild(this.labelsOverlay);
    }

    this.init();
  }

  init() {
    // 1. Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0A0A0D); // --bg
    this.scene.fog = new THREE.FogExp2(0x0A0A0D, 0.00030); // fog uses --bg

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

    // 6. Ground, Warm Gold Radial Vignette & Grid
    this.setupGround();

    // 6b. Slow Dust Particles
    this.setupDustParticles();

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
    // Warm ambient light matching dark gold theme
    const ambient = new THREE.AmbientLight(0x1F1A12, 1.6);
    this.scene.add(ambient);

    // Main key light (warm white)
    const keyLight = new THREE.DirectionalLight(0xFFF5EA, 2.2);
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

    // Subtle warm rim fill (replacing blue rim fill)
    const fillLight = new THREE.DirectionalLight(0xFFC800, 0.40);
    fillLight.position.set(-800, 500, -700);
    this.scene.add(fillLight);
  }

  setupGround() {
    // 1. Near-black ground plane (--bg: #0A0A0D)
    const groundGeo = new THREE.PlaneGeometry(6000, 6000);
    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x0A0A0D,
      roughness: 0.95,
      metalness: 0.05,
    });
    const ground = new THREE.Mesh(groundGeo, groundMat);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -1;
    ground.receiveShadow = true;
    this.scene.add(ground);

    // 2. Warm radial vignette (gold ~6% opacity fading to near-black)
    const vignetteCanvas = document.createElement('canvas');
    vignetteCanvas.width = 512;
    vignetteCanvas.height = 512;
    const vCtx = vignetteCanvas.getContext('2d');
    const vGrad = vCtx.createRadialGradient(256, 256, 0, 256, 256, 256);
    vGrad.addColorStop(0, 'rgba(255, 200, 0, 0.06)');
    vGrad.addColorStop(0.4, 'rgba(255, 200, 0, 0.02)');
    vGrad.addColorStop(0.85, 'rgba(10, 10, 13, 0.0)');
    vGrad.addColorStop(1, 'rgba(10, 10, 13, 0.0)');
    vCtx.fillStyle = vGrad;
    vCtx.fillRect(0, 0, 512, 512);

    const vignetteTex = new THREE.CanvasTexture(vignetteCanvas);
    const vignetteGeo = new THREE.PlaneGeometry(4500, 4500);
    const vignetteMat = new THREE.MeshBasicMaterial({
      map: vignetteTex,
      transparent: true,
      depthWrite: false,
      blending: THREE.NormalBlending,
    });
    const vignetteMesh = new THREE.Mesh(vignetteGeo, vignetteMat);
    vignetteMesh.rotation.x = -Math.PI / 2;
    vignetteMesh.position.y = -0.6;
    this.scene.add(vignetteMesh);

    // 3. Faint gold-tinted ground grid at ~4% opacity (removing teal/blue grid)
    const grid = new THREE.GridHelper(3500, 70, 0x554415, 0x221B0A);
    grid.position.y = 0;
    if (grid.material) {
      grid.material.transparent = true;
      grid.material.opacity = 0.04;
    }
    this.scene.add(grid);
  }

  setupDustParticles() {
    const particleCount = 180;
    const posArray = new Float32Array(particleCount * 3);

    for (let i = 0; i < particleCount; i++) {
      posArray[i * 3 + 0] = (Math.random() - 0.5) * 2600; // X
      posArray[i * 3 + 1] = 10 + Math.random() * 260;     // Y (above ground)
      posArray[i * 3 + 2] = (Math.random() - 0.5) * 2600; // Z
    }

    this.dustGeometry = new THREE.BufferGeometry();
    this.dustGeometry.setAttribute('position', new THREE.BufferAttribute(posArray, 3));

    // Soft round dot texture for dust particles
    const dCanvas = document.createElement('canvas');
    dCanvas.width = 32;
    dCanvas.height = 32;
    const dCtx = dCanvas.getContext('2d');
    const dGrad = dCtx.createRadialGradient(16, 16, 0, 16, 16, 16);
    dGrad.addColorStop(0, 'rgba(255, 216, 74, 0.85)');
    dGrad.addColorStop(0.5, 'rgba(255, 200, 0, 0.35)');
    dGrad.addColorStop(1, 'rgba(255, 200, 0, 0.0)');
    dCtx.fillStyle = dGrad;
    dCtx.fillRect(0, 0, 32, 32);
    const dTex = new THREE.CanvasTexture(dCanvas);

    this.dustMaterial = new THREE.PointsMaterial({
      size: 4.5,
      map: dTex,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });

    this.dustPoints = new THREE.Points(this.dustGeometry, this.dustMaterial);
    this.scene.add(this.dustPoints);
  }

  setupUserInstances() {
    // Increased user radius to 5.8m (up from 3.6m per T0 visibility findings)
    const userGeo = new THREE.SphereGeometry(5.8, 10, 10);
    this.userMaterial = new THREE.MeshStandardMaterial({
      roughness: 0.25,
      metalness: 0.35,
      emissive: 0x222222,
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

    // 1. Dynamic Voronoi Footprint placeholder group (populated via updateVoronoiFootprints)
    const footprintGroup = new THREE.Group();
    cellGroup.add(footprintGroup);

    // 2. Equipment Cabin at Tower Base
    const cabinGeo = new THREE.BoxGeometry(12, 7.5, 12);
    const cabinMat = new THREE.MeshStandardMaterial({
      color: 0x1E222B,
      roughness: 0.6,
      metalness: 0.5,
    });
    const cabin = new THREE.Mesh(cabinGeo, cabinMat);
    cabin.position.set(8, 3.75, 8);
    cabin.castShadow = true;
    cellGroup.add(cabin);

    // 3. Tapered Lattice Mast (Height scaled to ~76m: 6-8% of cluster width)
    const mastHeight = 76.0;
    const mastGeo = new THREE.CylinderGeometry(1.8, 5.6, mastHeight, 4);
    const mastMat = new THREE.MeshStandardMaterial({
      color: 0xE2E8F0, // Light grey-white metal
      metalness: 0.8,
      roughness: 0.25,
    });
    const mast = new THREE.Mesh(mastGeo, mastMat);
    mast.position.y = mastHeight / 2;
    mast.rotation.y = Math.PI / 4;
    mast.castShadow = true;
    cellGroup.add(mast);

    const mastEdges = new THREE.EdgesGeometry(mastGeo);
    const mastWire = new THREE.LineSegments(
      mastEdges,
      new THREE.LineBasicMaterial({ color: 0xCBD5E1 })
    );
    mastWire.position.y = mastHeight / 2;
    mastWire.rotation.y = Math.PI / 4;
    cellGroup.add(mastWire);

    // Horizontal structural lattice ring braces
    const braceHeights = [18, 36, 54, 72];
    for (const bh of braceHeights) {
      const frac = (mastHeight - bh) / mastHeight;
      const r = 1.8 + (5.6 - 1.8) * frac;
      const braceGeo = new THREE.CylinderGeometry(r * 1.05, r * 1.05, 0.9, 4);
      const braceMat = new THREE.MeshStandardMaterial({ color: 0xCBD5E1, metalness: 0.8 });
      const brace = new THREE.Mesh(braceGeo, braceMat);
      brace.position.y = bh;
      brace.rotation.y = Math.PI / 4;
      cellGroup.add(brace);
    }

    // 4. Antenna Platform Platform Head & Three Tilted Sector Panels
    const headY = 75.0;
    const headGeo = new THREE.CylinderGeometry(4.2, 4.2, 1.6, 3);
    const headMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.8 });
    const head = new THREE.Mesh(headGeo, headMat);
    head.position.y = headY;
    head.castShadow = true;
    cellGroup.add(head);

    const sectorPanels = [];
    const sectorAngles = [0, (2 * Math.PI) / 3, (4 * Math.PI) / 3];
    const initialTiltRad = THREE.MathUtils.degToRad(cellData.tilt_deg || 6.0);
    const panelGeo = new THREE.BoxGeometry(2.0, 9.0, 0.8);
    const panelMat = new THREE.MeshStandardMaterial({
      color: 0xF8FAFC,
      roughness: 0.3,
      metalness: 0.2,
    });

    for (let s = 0; s < 3; s++) {
      const azimuth = sectorAngles[s];
      const sectorGroup = new THREE.Group();
      sectorGroup.position.set(0, headY, 0);
      sectorGroup.rotation.y = azimuth;

      const armGeo = new THREE.CylinderGeometry(0.35, 0.35, 4.2, 8);
      const armMat = new THREE.MeshStandardMaterial({ color: 0x64748B });
      const arm = new THREE.Mesh(armGeo, armMat);
      arm.rotation.z = Math.PI / 2;
      arm.position.set(2.1, 0, 0);
      sectorGroup.add(arm);

      const panel = new THREE.Mesh(panelGeo, panelMat);
      panel.position.set(4.2, 0, 0);
      panel.rotation.x = initialTiltRad;
      panel.castShadow = true;
      sectorGroup.add(panel);

      sectorPanels.push(panel);
      cellGroup.add(sectorGroup);
    }

    // 5. Amber/White Active Pinnacle Beacon (Dark when sleeping)
    const beaconGeo = new THREE.SphereGeometry(1.6, 12, 12);
    const beaconMat = new THREE.MeshStandardMaterial({
      color: 0xFFC800,
      emissive: 0xFFD84A,
      emissiveIntensity: 1.8,
      roughness: 0.2,
    });
    const beacon = new THREE.Mesh(beaconGeo, beaconMat);
    beacon.position.y = 80.5;
    cellGroup.add(beacon);

    // 6. Base Load Gauge Ring encircling tower base
    const gaugeRingGeo = new THREE.TorusGeometry(14, 1.4, 8, 32);
    const gaugeRingMat = new THREE.MeshStandardMaterial({
      color: 0xFFC800,
      emissive: 0xFFC800,
      emissiveIntensity: 0.6,
      transparent: true,
      opacity: 0.9,
    });
    const gaugeRing = new THREE.Mesh(gaugeRingGeo, gaugeRingMat);
    gaugeRing.rotation.x = Math.PI / 2;
    gaugeRing.position.y = 1.8;
    cellGroup.add(gaugeRing);

    // 7. Gold/White Coverage Disc with Additive Blending
    const discRadius = computeCoverageRadius(cellData.tx_power_dbm, cellData.is_sleeping);
    const discGeo = new THREE.PlaneGeometry(1, 1);
    const discMat = new THREE.MeshBasicMaterial({
      map: this.coverageDiscTexture,
      transparent: true,
      opacity: 0.85,
      side: THREE.DoubleSide,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const coverageDisc = new THREE.Mesh(discGeo, discMat);
    coverageDisc.rotation.x = -Math.PI / 2;
    coverageDisc.position.y = 1.5;
    coverageDisc.scale.set(discRadius * 2, discRadius * 2, 1.0);
    cellGroup.add(coverageDisc);

    // 8. HTML/CSS2D High-Resolution Glass Pill Label
    const domLabel = this.createCellDomLabel(id, cellData);

    this.scene.add(cellGroup);

    const entity = {
      id,
      group: cellGroup,
      mast,
      mastMat,
      cabin,
      cabinMat,
      beacon,
      beaconMat,
      sectorPanels,
      gaugeRing,
      gaugeRingMat,
      coverageDisc,
      discMat,
      footprintGroup,
      footprintMesh: null,
      footprintOutline: null,
      footprintMat: null,
      footprintLineMat: null,
      domLabel,
      config: cellData,
    };
    this.cellEntities.set(id, entity);
    return entity;
  }

  createCellDomLabel(id, data) {
    const pill = document.createElement('div');
    pill.className = 'cell-label-pill';
    pill.id = `cell-label-${id}`;

    const title = document.createElement('div');
    title.className = 'cell-label-title';
    title.textContent = id;
    pill.appendChild(title);

    const subtitle = document.createElement('div');
    subtitle.className = 'cell-label-subtitle';
    const ues = data.active_users !== undefined ? data.active_users : 0;
    const loadPct = Math.round((data.load_ratio || 0) * 100);
    subtitle.textContent = data.is_sleeping ? 'SLEEPING (0W)' : `${ues} users • ${loadPct}% load`;
    pill.appendChild(subtitle);

    const tooltip = document.createElement('div');
    tooltip.className = 'cell-label-tooltip';
    tooltip.textContent = 'CALIBRATED ±0.30 dB';
    pill.appendChild(tooltip);

    if (this.labelsOverlay) {
      this.labelsOverlay.appendChild(pill);
    }
    return { pill, title, subtitle, tooltip };
  }

  updateVoronoiFootprints(cells) {
    if (!cells || cells.length === 0) return;

    // Check if topology changed
    const topologyKey = cells.map((c) => `${c.id}:${c.x}:${c.y}`).join('|');
    if (topologyKey === this.cachedVoronoiTopologyKey) return;
    this.cachedVoronoiTopologyKey = topologyKey;

    const voronoiMap = computeVoronoiFootprints(cells, 750);

    for (const cellData of cells) {
      const entity = this.cellEntities.get(cellData.id);
      if (!entity) continue;

      const poly = voronoiMap.get(cellData.id);
      if (!poly || poly.length < 3) continue;

      // Clear previous footprint meshes
      while (entity.footprintGroup.children.length > 0) {
        const obj = entity.footprintGroup.children.pop();
        if (obj.geometry) obj.geometry.dispose();
      }

      // Convert cluster-relative world coords to cellGroup-local coords
      const cx = Number(cellData.x) || 0;
      const cz = Number(cellData.y) || 0;

      const shape = new THREE.Shape();
      const firstPt = poly[0];
      shape.moveTo(firstPt.x - cx, firstPt.z - cz);
      for (let i = 1; i < poly.length; i++) {
        shape.lineTo(poly[i].x - cx, poly[i].z - cz);
      }
      shape.closePath();

      const footprintGeo = new THREE.ShapeGeometry(shape);
      const footprintMat = new THREE.MeshBasicMaterial({
        color: 0xFFC800,
        transparent: true,
        opacity: 0.04,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const footprintMesh = new THREE.Mesh(footprintGeo, footprintMat);
      footprintMesh.rotation.x = -Math.PI / 2;
      footprintMesh.position.y = 0.4;
      entity.footprintGroup.add(footprintMesh);

      // Thin outline wireframe
      const linePts = poly.map((p) => new THREE.Vector3(p.x - cx, 0.45, p.z - cz));
      const lineGeo = new THREE.BufferGeometry().setFromPoints(linePts);
      const lineMat = new THREE.LineBasicMaterial({
        color: 0xFFC800,
        transparent: true,
        opacity: 0.25,
      });
      const outlineLine = new THREE.LineLoop(lineGeo, lineMat);
      entity.footprintGroup.add(outlineLine);

      entity.footprintMesh = footprintMesh;
      entity.footprintOutline = outlineLine;
      entity.footprintMat = footprintMat;
      entity.footprintLineMat = lineMat;
    }
  }

  updateCellEntities(cells) {
    if (!cells || cells.length === 0) {
      // When there are no cells in the frame, render nothing (no placeholder towers)
      for (const entity of this.cellEntities.values()) {
        entity.group.visible = false;
        if (entity.domLabel && entity.domLabel.pill) {
          entity.domLabel.pill.style.display = 'none';
        }
      }
      return;
    }

    const activeIds = new Set(cells.map((c) => c.id));
    for (const [id, entity] of this.cellEntities) {
      if (!activeIds.has(id)) {
        entity.group.visible = false;
        if (entity.domLabel && entity.domLabel.pill) {
          entity.domLabel.pill.style.display = 'none';
        }
      }
    }

    // Ensure non-overlapping Voronoi footprints are computed & cached
    this.updateVoronoiFootprints(cells);

    for (const cellData of cells) {
      const entity = this.getOrCreateCellEntity(cellData);
      entity.group.visible = true;
      entity.config = cellData;

      const isSleeping = Boolean(cellData.is_sleeping);
      const load = Math.max(0.0, Math.min(1.0, cellData.load_ratio || 0.0));
      const isOverloaded = load > 0.75;

      // Update Tower Materials & Load Ring
      if (isSleeping) {
        // Dimmed grey sleeping tower
        entity.mastMat.color.setHex(0x475569);
        entity.cabinMat.color.setHex(0x1E222B);
        entity.beaconMat.color.setHex(0x334155);
        entity.beaconMat.emissive.setHex(0x000000);
        entity.beaconMat.emissiveIntensity = 0.0; // Completely dark
        entity.gaugeRingMat.color.setHex(0x475569);
        entity.gaugeRingMat.emissive.setHex(0x000000);
        entity.gaugeRingMat.opacity = 0.3;

        // Footprint tint: grey
        if (entity.footprintMat) {
          entity.footprintMat.color.setHex(0x475569);
          entity.footprintMat.opacity = 0.04;
          entity.footprintLineMat.color.setHex(0x64748B);
          entity.footprintLineMat.opacity = 0.25;
        }
      } else if (isOverloaded) {
        // Overloaded tower: amber load ring & footprint
        entity.mastMat.color.setHex(0xE2E8F0);
        entity.cabinMat.color.setHex(0x1E222B);
        entity.beaconMat.color.setHex(0xFFC800);
        entity.beaconMat.emissive.setHex(0xFFB020);
        entity.beaconMat.emissiveIntensity = 1.8;
        entity.gaugeRingMat.color.setHex(0xFFB020);
        entity.gaugeRingMat.emissive.setHex(0xFFB020);
        entity.gaugeRingMat.opacity = 0.95;

        // Footprint tint: amber
        if (entity.footprintMat) {
          entity.footprintMat.color.setHex(0xFFB020);
          entity.footprintMat.opacity = 0.08;
          entity.footprintLineMat.color.setHex(0xFFB020);
          entity.footprintLineMat.opacity = 0.50;
        }
      } else {
        // Active balanced tower: white-metal mast, gold/cyan load ring, gold footprint
        entity.mastMat.color.setHex(0xE2E8F0);
        entity.cabinMat.color.setHex(0x1E222B);
        entity.beaconMat.color.setHex(0xFFC800);
        entity.beaconMat.emissive.setHex(0xFFD84A);
        entity.beaconMat.emissiveIntensity = 1.6;
        entity.gaugeRingMat.color.setHex(0xFFFFFF); // Balanced white/cyan
        entity.gaugeRingMat.emissive.setHex(0xFFC800);
        entity.gaugeRingMat.opacity = 0.85;

        // Footprint tint: gold
        if (entity.footprintMat) {
          entity.footprintMat.color.setHex(0xFFC800);
          entity.footprintMat.opacity = 0.04;
          entity.footprintLineMat.color.setHex(0xFFC800);
          entity.footprintLineMat.opacity = 0.25;
        }
      }

      // Update Coverage Disc Radius & Tilt
      const targetRadius = computeCoverageRadius(cellData.tx_power_dbm, isSleeping);
      if (targetRadius > 0 && !isSleeping) {
        entity.coverageDisc.visible = true;
        entity.coverageDisc.scale.set(targetRadius * 2, targetRadius * 2, 1.0);
        const tiltRad = THREE.MathUtils.degToRad(cellData.tilt_deg || 6.0);
        entity.coverageDisc.rotation.z = tiltRad * 0.5;
        entity.discMat.opacity = 0.85;
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

      // Update HTML DOM Label Content
      if (entity.domLabel) {
        const { pill, title, subtitle } = entity.domLabel;
        pill.style.display = 'flex';
        title.textContent = entity.id;

        const ues = cellData.active_users !== undefined ? cellData.active_users : 0;
        const loadPct = Math.round(load * 100);

        subtitle.className = 'cell-label-subtitle';
        if (isSleeping) {
          subtitle.textContent = 'SLEEPING (0W)';
          subtitle.classList.add('sleeping');
          pill.classList.add('sleeping');
        } else {
          subtitle.textContent = `${ues} users • ${loadPct}% load`;
          pill.classList.remove('sleeping');
          if (isOverloaded) {
            subtitle.classList.add('overloaded');
          }
        }
      }
    }
  }

  /**
   * Update HTML label screen projections and perform collision avoidance.
   */
  updateLabels() {
    if (!this.labelsOverlay || this.cellEntities.size === 0) return;

    const visibleItems = [];

    for (const [id, entity] of this.cellEntities.entries()) {
      if (!entity.group.visible || !entity.domLabel) continue;

      const worldPos = new THREE.Vector3();
      entity.group.getWorldPosition(worldPos);
      worldPos.y = 82.0; // Anchor above tower pinnacle beacon

      const screenPos = worldPos.clone().project(this.camera);

      // Frustum check
      if (screenPos.z < -1 || screenPos.z > 1) {
        entity.domLabel.pill.style.display = 'none';
        continue;
      }

      const sx = (screenPos.x + 1) / 2 * this.width;
      const sy = (1 - screenPos.y) / 2 * this.height;

      // Off-screen check
      if (sx < -80 || sx > this.width + 80 || sy < -50 || sy > this.height + 50) {
        entity.domLabel.pill.style.display = 'none';
        continue;
      }

      const camDist = this.camera.position.distanceTo(worldPos);
      const isFar = camDist > 2000;

      visibleItems.push({
        id,
        entity,
        sx,
        sy,
        camDist,
        isFar,
      });
    }

    // Sort horizontally to detect overlaps
    visibleItems.sort((a, b) => a.sx - b.sx);

    // Screen-space collision avoidance: offset Y if two labels collide
    for (let i = 0; i < visibleItems.length; i++) {
      const itemA = visibleItems[i];
      let offsetY = 0;

      for (let j = 0; j < i; j++) {
        const itemB = visibleItems[j];
        const dx = Math.abs(itemA.sx - itemB.sx);
        const dy = Math.abs((itemA.sy + offsetY) - itemB.sy);

        if (dx < 110 && dy < 38) {
          // Collision: nudge down
          offsetY += 32;
        }
      }

      const { entity, sx, sy, isFar } = itemA;
      const pill = entity.domLabel.pill;
      pill.style.display = 'flex';
      pill.style.transform = `translate(${sx.toFixed(1)}px, ${(sy + offsetY).toFixed(1)}px) translate(-50%, -100%)`;

      if (isFar) {
        pill.classList.add('far');
      } else {
        pill.classList.remove('far');
      }
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
    this.userPositionsPrev.clear();
    for (const [id, pos] of this.userPositionsTarget.entries()) {
      this.userPositionsPrev.set(id, { ...pos });
    }

    this.userPositionsTarget.clear();
    for (const u of users) {
      const targetPos = {
        x: Number(u.x) || 0,
        y: 4.0, // Placed distinctly above ground at y=4.0
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

    // Pulse Active Pinnacle Beacons (steady if reduced motion)
    const beaconPulse = this.prefersReducedMotion ? 1.4 : 1.2 + 0.8 * Math.sin(now * 0.004);
    for (const entity of this.cellEntities.values()) {
      if (entity.config && !entity.config.is_sleeping && entity.beaconMat) {
        entity.beaconMat.emissiveIntensity = beaconPulse;
      }
    }

    // Update Dust Particles Drift (pause if reduced motion)
    if (!this.prefersReducedMotion && this.dustPoints && this.dustGeometry) {
      const positions = this.dustGeometry.attributes.position.array;
      for (let i = 0; i < positions.length / 3; i++) {
        positions[i * 3 + 1] += frameDeltaSec * 2.5; // Slow upward drift
        if (positions[i * 3 + 1] > 260) {
          positions[i * 3 + 1] = 10;
        }
      }
      this.dustGeometry.attributes.position.needsUpdate = true;
    }

    // Update Screen-space HTML Labels
    this.updateLabels();

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
      position.y = 4.0;
      position.z = prev.z + (target.z - prev.z) * alpha;

      // Pulse dropped users (2.2x scale pulse)
      let userScale = 1.0;
      if (target.dropped) {
        userScale = this.prefersReducedMotion ? 2.0 : 1.6 + 0.6 * Math.sin(timestamp * 0.015);
      }
      scale.set(userScale, userScale, userScale);

      matrix.compose(position, rot, scale);
      this.userInstancedMesh.setMatrixAt(i, matrix);

      // Color by SINR / Dropped state
      const color = sinrToColor(target.sinr, target.dropped);
      this.userInstancedMesh.setColorAt(i, color);

      // Update RF Connection Line to Serving Cell Tower
      const servingEntity = this.cellEntities.get(target.cell);
      if (servingEntity && !target.dropped && servingEntity.group.visible) {
        const cellPos = servingEntity.group.position;

        // Vertex 1: User position
        this.linePositions[lineVertexIdx] = position.x;
        this.linePositions[lineVertexIdx + 1] = position.y;
        this.linePositions[lineVertexIdx + 2] = position.z;

        this.lineColors[lineVertexIdx] = color.r;
        this.lineColors[lineVertexIdx + 1] = color.g;
        this.lineColors[lineVertexIdx + 2] = color.b;
        lineVertexIdx += 3;

        // Vertex 2: Tower mast head (75m platform)
        this.linePositions[lineVertexIdx] = cellPos.x;
        this.linePositions[lineVertexIdx + 1] = 75.0;
        this.linePositions[lineVertexIdx + 2] = cellPos.z;

        this.lineColors[lineVertexIdx] = color.r * 0.7;
        this.lineColors[lineVertexIdx + 1] = color.g * 0.7;
        this.lineColors[lineVertexIdx + 2] = color.b * 0.7;
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

    // Clean up DOM labels
    if (this.labelsOverlay) {
      this.labelsOverlay.innerHTML = '';
    }

    // Dispose cell geometries & materials
    for (const entity of this.cellEntities.values()) {
      if (entity.footprintMesh && entity.footprintMesh.geometry) {
        entity.footprintMesh.geometry.dispose();
      }
      if (entity.footprintOutline && entity.footprintOutline.geometry) {
        entity.footprintOutline.geometry.dispose();
      }
    }
    this.cellEntities.clear();

    if (this.coverageDiscTexture) {
      this.coverageDiscTexture.dispose();
    }
    if (this.dustGeometry) {
      this.dustGeometry.dispose();
    }
    if (this.dustMaterial) {
      this.dustMaterial.dispose();
    }

    if (this.renderer && this.renderer.domElement) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
  }
}
