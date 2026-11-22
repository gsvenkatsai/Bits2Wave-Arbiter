/**
 * PROJECT ARBITER — 3D Arbitration Scene Layer
 * Module: arbiter/dashboard/3d/arbitration-scene.js
 *
 * Implements:
 * 1. Controller Nodes: 4 floating emissive nodes (ES, LB, CC, PS) with labels & emission pulse.
 * 2. Proposal Packets: curved Bezier flight to core with text labels & target tower beacons.
 * 3. ARBITER Core: rotating concentric rings/prism, packet gathering, conflict arcs & twin shimmer.
 * 4. Verdict Animations: APPROVE (fly to tower), MODIFY (value morph), DELAY (orbit ring), REJECT (shatter).
 * 5. Real Offload Handover Stream: user handover arcs between source and target cells.
 * 6. Animation Queue: graceful throttling so 100 ms tick stream is never blocked.
 */

import * as THREE from 'three';
import {
  CONTROLLER_SPECS,
  mapProposalsToPackets,
  mapConflictsToEdges,
  mapDecisionsToVerdicts,
  detectUserHandovers,
  mapOffloadArrow,
} from './arbitration-mapper.js';

export class ArbitrationSceneLayer {
  constructor(scene, options = {}) {
    this.scene = scene;
    this.cellEntities = options.cellEntities || new Map();
    this.corePosition = new THREE.Vector3(0, 160, 0);

    // Sub-containers
    this.layerGroup = new THREE.Group();
    this.scene.add(this.layerGroup);

    // Controller Nodes
    this.controllerNodes = new Map(); // 'ENERGY_SAVING' -> { mesh, halo, sprite, spec, pos }
    this.controllerPulses = new Map();
    this.controllerLines = new Map();

    // In-flight proposal and verdict animations
    this.activePackets = [];
    this.activeConflictArcs = [];
    this.activeVerdicts = [];
    this.activeHandovers = [];
    this.activeOffloadArrows = [];
    this.heldPackets = new Map(); // commandId -> { mesh, angle, badge }

    // Twin shimmer state
    this.isTwinEvaluating = false;
    this.twinShimmerTimer = 0;

    // Previous users state for real handover detection
    this.prevUsers = [];

    // Max active animations for queue throttle protection
    this.maxActiveAnimations = 24;

    this.init();
  }

  init() {
    this.setupControllerNodes();
    this.setupArbiterCore();
    this.setupTargetBeacons();
  }

  // ---------------------------------------------------------------------------
  // 1. Controller Nodes
  // ---------------------------------------------------------------------------
  getTowerHeadPosition(cellId) {
    if (this.cellEntities && this.cellEntities.has(cellId)) {
      const ent = this.cellEntities.get(cellId);
      if (ent && ent.group) {
        return new THREE.Vector3(ent.group.position.x, 78.5, ent.group.position.z);
      }
    }
    const defaults = {
      cell_A: new THREE.Vector3(0, 78.5, 0),
      cell_B: new THREE.Vector3(500, 78.5, 0),
      cell_C: new THREE.Vector3(250, 78.5, 433.01),
      cell_D: new THREE.Vector3(-250, 78.5, 433.01),
      cell_E: new THREE.Vector3(0, 78.5, -500.0),
    };
    return defaults[cellId] || new THREE.Vector3(0, 78.5, 0);
  }

  updateNetworkLines() {
    if (!this.controllerLines) return;
    const centerTowerPos = this.getTowerHeadPosition('cell_A');
    for (const [, item] of this.controllerLines.entries()) {
      const towerHeadPos = this.getTowerHeadPosition(item.spec.associatedCell || 'cell_A');
      const linePoints = [
        towerHeadPos,
        new THREE.Vector3(item.pos.x, item.pos.y, item.pos.z),
        new THREE.Vector3(this.corePosition.x, this.corePosition.y, this.corePosition.z),
        centerTowerPos,
      ];
      item.line.geometry.setFromPoints(linePoints);
      item.line.computeLineDistances();
    }

    // Connect ARBITER Core to all towers across the city
    if (this.cellEntities && this.coreTowerLines) {
      const pts = [];
      for (const [cellId, entity] of this.cellEntities.entries()) {
        if (cellId === 'cell_A') continue;
        if (entity && entity.group && entity.group.visible) {
          pts.push(this.corePosition.clone());
          pts.push(new THREE.Vector3(entity.group.position.x, 78.5, entity.group.position.z));
        }
      }
      if (pts.length > 0) {
        this.coreTowerLines.geometry.setFromPoints(pts);
        this.coreTowerLines.computeLineDistances();
        this.coreTowerLines.visible = true;
      }
    }
  }

  setupControllerNodes() {
    for (const [key, spec] of Object.entries(CONTROLLER_SPECS)) {
      const nodeGroup = new THREE.Group();
      const pos = new THREE.Vector3(spec.defaultPos.x, spec.defaultPos.y, spec.defaultPos.z);
      nodeGroup.position.copy(pos);

      // Node mesh: floating diamond / octahedron node
      const geo = new THREE.OctahedronGeometry(12, 0);
      const mat = new THREE.MeshStandardMaterial({
        color: spec.colorHex,
        emissive: spec.colorHex,
        emissiveIntensity: 0.8,
        roughness: 0.2,
        metalness: 0.8,
      });
      const mesh = new THREE.Mesh(geo, mat);
      nodeGroup.add(mesh);

      // Orbiting halo ring
      const ringGeo = new THREE.TorusGeometry(18, 1.2, 8, 24);
      const ringMat = new THREE.MeshBasicMaterial({
        color: spec.colorHex,
        transparent: true,
        opacity: 0.6,
      });
      const halo = new THREE.Mesh(ringGeo, ringMat);
      halo.rotation.x = Math.PI / 2;
      nodeGroup.add(halo);

      // Label sprite (untruncated full pill style)
      const labelSprite = this.createControllerLabel(spec.abbrev, spec.name, spec.color);
      labelSprite.position.set(0, 24, 0);
      nodeGroup.add(labelSprite);

      // Dotted network line touching the tower antenna, passing through the controller,
      // connecting into ARBITER Core, and touching down into central Cell A antenna
      const towerHeadPos = this.getTowerHeadPosition(spec.associatedCell || 'cell_A');
      const centerTowerPos = this.getTowerHeadPosition('cell_A');
      const linePoints = [
        towerHeadPos,
        new THREE.Vector3(pos.x, pos.y, pos.z),
        new THREE.Vector3(this.corePosition.x, this.corePosition.y, this.corePosition.z),
        centerTowerPos,
      ];
      const lineGeo = new THREE.BufferGeometry().setFromPoints(linePoints);
      const lineMat = new THREE.LineDashedMaterial({
        color: 0xFFC800,
        dashSize: 22,
        gapSize: 9,
        transparent: true,
        opacity: 0.90,
      });
      const netLine = new THREE.Line(lineGeo, lineMat);
      netLine.computeLineDistances();
      this.layerGroup.add(netLine);
      this.controllerLines.set(key, { line: netLine, pos, spec });

      this.layerGroup.add(nodeGroup);
      this.controllerNodes.set(key, { group: nodeGroup, mesh, mat, halo, pos, spec });
      this.controllerPulses.set(key, 0.0);
    }
  }

  createControllerLabel(abbrev, name, color) {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 110;
    const ctx = canvas.getContext('2d');

    // Telecom NOC glass pill style
    ctx.fillStyle = 'rgba(11, 18, 32, 0.95)';
    ctx.strokeStyle = color;
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.roundRect(8, 8, 496, 94, 47);
    ctx.fill();
    ctx.stroke();

    const icons = {
      ES: '⚡',
      LB: '⚖️',
      CC: '📶',
      PS: '🚨',
    };
    const icon = icons[abbrev] || '⚙️';

    ctx.fillStyle = color;
    ctx.font = 'bold 36px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`${icon} [${abbrev}]`, 32, 64);

    ctx.fillStyle = '#E6EDF7';
    ctx.font = 'bold 32px sans-serif';
    ctx.fillText(name, 190, 62);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(160, 36, 1);
    return sprite;
  }

  createRefereeLabelSprite() {
    const canvas = document.createElement('canvas');
    canvas.width = 460;
    canvas.height = 96;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(11, 18, 32, 0.95)';
    ctx.strokeStyle = '#22D3EE';
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.roundRect(6, 6, 448, 84, 20);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#22D3EE';
    ctx.font = 'bold 34px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('🛡️ ARBITER REFEREE', 230, 56);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(130, 27, 1);
    return sprite;
  }

  pulseControllerNode(controllerType) {
    const key = String(controllerType || '').toUpperCase();
    if (this.controllerPulses.has(key)) {
      this.controllerPulses.set(key, 1.0); // Flare pulse
    }
  }

  // ---------------------------------------------------------------------------
  // 2. ARBITER Core
  // ---------------------------------------------------------------------------
  setupArbiterCore() {
    this.coreGroup = new THREE.Group();
    this.coreGroup.position.copy(this.corePosition);

    // 1. Concentric rotating outer ring (Gold)
    const outerRingGeo = new THREE.TorusGeometry(60, 2.5, 12, 48);
    this.outerRingMat = new THREE.MeshStandardMaterial({
      color: 0xFFC800,
      emissive: 0xB38600,
      emissiveIntensity: 1.0,
      roughness: 0.2,
      metalness: 0.9,
    });
    this.coreOuterRing = new THREE.Mesh(outerRingGeo, this.outerRingMat);
    this.coreOuterRing.rotation.x = Math.PI / 2.3;
    this.coreGroup.add(this.coreOuterRing);

    // 2. Counter-rotating inner ring (Warm Gold)
    const innerRingGeo = new THREE.TorusGeometry(42, 2.0, 12, 36);
    this.innerRingMat = new THREE.MeshStandardMaterial({
      color: 0xFFD84A,
      emissive: 0xFFB020,
      emissiveIntensity: 0.9,
      roughness: 0.25,
      metalness: 0.85,
    });
    this.coreInnerRing = new THREE.Mesh(innerRingGeo, this.innerRingMat);
    this.coreInnerRing.rotation.x = -Math.PI / 3;
    this.coreGroup.add(this.coreInnerRing);

    // 3. Central rotating crystalline prism (Octahedron in Bright Gold)
    const prismGeo = new THREE.OctahedronGeometry(20, 0);
    this.prismMat = new THREE.MeshStandardMaterial({
      color: 0xFFFBEB,
      emissive: 0xFFC800,
      emissiveIntensity: 1.5,
      roughness: 0.1,
      metalness: 0.95,
      wireframe: false,
    });
    this.corePrism = new THREE.Mesh(prismGeo, this.prismMat);
    this.coreGroup.add(this.corePrism);

    // 4. Central luminous core light (Gold)
    this.coreLight = new THREE.PointLight(0xFFC800, 4.5, 700);
    this.coreGroup.add(this.coreLight);

    // 4b. Soft bloom-like glow sprite (Additive blending, no post-processing lib)
    const glowCanvas = document.createElement('canvas');
    glowCanvas.width = 256;
    glowCanvas.height = 256;
    const gCtx = glowCanvas.getContext('2d');
    const gGrad = gCtx.createRadialGradient(128, 128, 0, 128, 128, 128);
    gGrad.addColorStop(0, 'rgba(255, 235, 120, 0.95)');
    gGrad.addColorStop(0.25, 'rgba(255, 200, 0, 0.50)');
    gGrad.addColorStop(0.55, 'rgba(255, 180, 0, 0.16)');
    gGrad.addColorStop(1, 'rgba(255, 160, 0, 0.0)');
    gCtx.fillStyle = gGrad;
    gCtx.fillRect(0, 0, 256, 256);
    const glowTex = new THREE.CanvasTexture(glowCanvas);
    this.coreGlowSprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: glowTex,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      })
    );
    this.coreGlowSprite.scale.set(190, 190, 1);
    this.coreGroup.add(this.coreGlowSprite);

    // 5. Digital Twin Shimmer Badge (billboard)
    this.twinShimmerSprite = this.createTwinShimmerSprite();
    this.twinShimmerSprite.position.set(0, 48, 0);
    this.twinShimmerSprite.visible = false;
    this.coreGroup.add(this.twinShimmerSprite);

    // 6. Central ARBITER Referee Badge (billboard)
    this.refereeBadgeSprite = this.createRefereeLabelSprite();
    this.refereeBadgeSprite.position.set(0, 82, 0);
    this.coreGroup.add(this.refereeBadgeSprite);

    // 7. Referee lines from ARBITER Core directly down to every tower head in the cluster
    const coreLineMat = new THREE.LineDashedMaterial({
      color: 0x22D3EE, // Glowing cyan referee telemetry link
      dashSize: 20,
      gapSize: 10,
      transparent: true,
      opacity: 0.80,
    });
    this.coreTowerLines = new THREE.LineSegments(new THREE.BufferGeometry(), coreLineMat);
    this.layerGroup.add(this.coreTowerLines);

    this.layerGroup.add(this.coreGroup);
  }

  createTwinShimmerSprite() {
    const canvas = document.createElement('canvas');
    canvas.width = 384;
    canvas.height = 70;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(15, 23, 42, 0.92)';
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(6, 6, 372, 58, 12);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#fbbf24';
    ctx.font = 'bold 22px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('TWIN CHECKING (MAPE < 10% CONFORMAL)', 192, 42);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(95, 18, 1);
    return sprite;
  }

  // ---------------------------------------------------------------------------
  // 3. Target Cell Beacons
  // ---------------------------------------------------------------------------
  setupTargetBeacons() {
    this.targetBeacons = new Map(); // cellId -> mesh
    // Beacons created dynamically on demand
  }

  showTargetBeacon(cellId) {
    const tower = this.cellEntities.get(cellId);
    if (!tower) return;

    let beacon = this.targetBeacons.get(cellId);
    if (!beacon) {
      const geo = new THREE.CylinderGeometry(8, 25, 220, 16, 1, true);
      const mat = new THREE.MeshBasicMaterial({
        color: 0x38bdf8,
        transparent: true,
        opacity: 0.35,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      beacon = new THREE.Mesh(geo, mat);
      const pos = tower.group.position;
      beacon.position.set(pos.x, 110, pos.z);
      this.layerGroup.add(beacon);
      this.targetBeacons.set(cellId, beacon);
    }

    beacon.visible = true;
    beacon.userData.life = 1.0;
  }

  // ---------------------------------------------------------------------------
  // 4. Ingest Frame Telemetry & Trigger Animations
  // ---------------------------------------------------------------------------
  onFrame(frame) {
    if (!frame) return;

    this.updateNetworkLines();

    const proposals = frame.proposals || [];
    const decisions = frame.decisions || [];
    const conflicts = frame.conflicts || [];

    // 1. Process Ingress Proposals
    if (proposals.length > 0) {
      const packets = mapProposalsToPackets(proposals);
      for (const p of packets) {
        this.pulseControllerNode(p.controllerType);
        this.showTargetBeacon(p.targetCellId);
        this.spawnProposalPacket(p);
      }

      // Show twin checking shimmer
      this.isTwinEvaluating = true;
      this.twinShimmerTimer = 1.2;
      this.twinShimmerSprite.visible = true;
    }

    // 2. Process Conflicts
    if (conflicts.length > 0) {
      const packets = mapProposalsToPackets(proposals);
      const conflictEdges = mapConflictsToEdges(conflicts, packets);
      for (const edge of conflictEdges) {
        this.spawnConflictArc(edge);
      }
    }

    // 3. Process Arbitration Decisions / Verdicts
    if (decisions.length > 0) {
      const verdicts = mapDecisionsToVerdicts(decisions, proposals);
      for (const v of verdicts) {
        this.spawnVerdictAnimation(v);
      }

      // Check for offload handover arrow
      const offloadDesc = mapOffloadArrow(proposals, decisions);
      if (offloadDesc) {
        this.spawnOffloadArrow(offloadDesc.sourceCell, offloadDesc.targetCell, offloadDesc.offloadRatio);
      }
    }

    // 4. Detect & Animate Real User Handover Transitions
    if (frame.users && this.prevUsers.length > 0) {
      const handovers = detectUserHandovers(this.prevUsers, frame.users);
      for (const ho of handovers) {
        this.spawnUserHandoverParticle(ho);
      }
    }
    this.prevUsers = frame.users || [];
  }

  // ---------------------------------------------------------------------------
  // 5. Proposal Packet Flight Animation
  // ---------------------------------------------------------------------------
  spawnProposalPacket(packetData) {
    if (this.activePackets.length >= this.maxActiveAnimations) {
      return; // Skip animation under queue pressure to protect frame budget
    }

    const startPos = new THREE.Vector3(
      packetData.sourcePos.x,
      packetData.sourcePos.y,
      packetData.sourcePos.z
    );
    const endPos = this.corePosition.clone();

    // Curved Bezier Control Point
    const midPos = startPos.clone().lerp(endPos, 0.5);
    midPos.y += 80; // Arc upwards

    const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, endPos);

    // Glowing packet sphere
    const geo = new THREE.SphereGeometry(6, 12, 12);
    const mat = new THREE.MeshStandardMaterial({
      color: packetData.colorHex,
      emissive: packetData.colorHex,
      emissiveIntensity: 1.2,
      roughness: 0.1,
    });
    const packetMesh = new THREE.Mesh(geo, mat);
    this.layerGroup.add(packetMesh);

    // Text Label Sprite
    const labelSprite = this.createPacketLabelSprite(packetData.label, packetData.color);
    packetMesh.add(labelSprite);
    labelSprite.position.set(0, 14, 0);

    this.activePackets.push({
      mesh: packetMesh,
      curve,
      t: 0.0,
      speed: 0.8, // Completes flight in ~1.2s
      data: packetData,
    });
  }

  createPacketLabelSprite(text, color) {
    const canvas = document.createElement('canvas');
    canvas.width = 280;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(6, 6, 268, 52, 10);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#f8fafc';
    ctx.font = 'bold 22px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(text, 140, 38);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(55, 13, 1);
    return sprite;
  }

  // ---------------------------------------------------------------------------
  // 6. Conflict Arc Animation
  // ---------------------------------------------------------------------------
  spawnConflictArc(conflictEdge) {
    const p1 = this.corePosition.clone().add(new THREE.Vector3(-25, 0, -15));
    const p2 = this.corePosition.clone().add(new THREE.Vector3(25, 0, 15));
    const mid = p1.clone().lerp(p2, 0.5);
    mid.y += 35; // Red arc rises above core

    const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2);
    const points = curve.getPoints(24);
    const geo = new THREE.BufferGeometry().setFromPoints(points);
    const mat = new THREE.LineBasicMaterial({
      color: 0xef4444,
      linewidth: 3,
      transparent: true,
      opacity: 0.9,
    });
    const line = new THREE.Line(geo, mat);
    this.layerGroup.add(line);

    // Conflict Label
    const labelSprite = this.createConflictLabelSprite(conflictEdge.label);
    labelSprite.position.copy(mid).add(new THREE.Vector3(0, 12, 0));
    this.layerGroup.add(labelSprite);

    this.activeConflictArcs.push({
      line,
      labelSprite,
      life: 1.0,
      decay: 0.6,
    });
  }

  createConflictLabelSprite(text) {
    const canvas = document.createElement('canvas');
    canvas.width = 340;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(239, 68, 68, 0.92)';
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(6, 6, 328, 52, 10);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 20px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(`⚡ CONFLICT: ${text}`, 170, 38);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(75, 14, 1);
    return sprite;
  }

  // ---------------------------------------------------------------------------
  // 7. Verdict Animations (APPROVE, MODIFY, DELAY, REJECT)
  // ---------------------------------------------------------------------------
  spawnVerdictAnimation(verdict) {
    const action = verdict.action;
    const targetTower = this.cellEntities.get(verdict.targetCellId);
    const targetPos = targetTower
      ? targetTower.group.position.clone().add(new THREE.Vector3(0, 45, 0))
      : new THREE.Vector3(0, 20, 0);

    if (action === 'APPROVE') {
      // Exits core and flies to target tower, turning green (#34D399)
      const startPos = this.corePosition.clone();
      const midPos = startPos.clone().lerp(targetPos, 0.5);
      midPos.y += 60;
      const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, targetPos);

      const geo = new THREE.SphereGeometry(7, 12, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: 0x34D399,
        emissive: 0x059669,
        emissiveIntensity: 1.5,
      });
      const mesh = new THREE.Mesh(geo, mat);
      this.layerGroup.add(mesh);

      this.activeVerdicts.push({
        type: 'APPROVE',
        mesh,
        curve,
        t: 0.0,
        speed: 1.0,
        verdict,
      });
    } else if (action === 'MODIFY') {
      // Morphs requested value -> approved value, then flies out cyan (#22D3EE)
      const startPos = this.corePosition.clone();
      const midPos = startPos.clone().lerp(targetPos, 0.5);
      midPos.y += 60;
      const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, targetPos);

      const geo = new THREE.SphereGeometry(7, 12, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: 0x22D3EE,
        emissive: 0x0891B2,
        emissiveIntensity: 1.5,
      });
      const mesh = new THREE.Mesh(geo, mat);
      this.layerGroup.add(mesh);

      // Morphing countdown label
      const morphSprite = this.createPacketLabelSprite(
        `BISECT: ${verdict.morphLabel || 'MODIFIED'}`,
        '#22D3EE'
      );
      mesh.add(morphSprite);
      morphSprite.position.set(0, 16, 0);

      this.activeVerdicts.push({
        type: 'MODIFY',
        mesh,
        curve,
        t: 0.0,
        speed: 0.9,
        verdict,
      });
    } else if (action === 'DELAY') {
      // Orbits the ARBITER core ring with a HOLD n/3 amber badge (#FBBF24)
      const geo = new THREE.SphereGeometry(6, 12, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: 0xFBBF24,
        emissive: 0xD97706,
        emissiveIntensity: 1.2,
      });
      const mesh = new THREE.Mesh(geo, mat);
      this.coreGroup.add(mesh);

      const holdSprite = this.createPacketLabelSprite(
        `HOLD ${verdict.holdCount}/3 • ${verdict.reasonCode}`,
        '#FBBF24'
      );
      mesh.add(holdSprite);
      holdSprite.position.set(0, 15, 0);

      this.heldPackets.set(verdict.commandId, {
        mesh,
        angle: Math.random() * Math.PI * 2,
        radius: 68,
        life: 2.0, // Orbits for ~2s
      });
    } else if (action === 'REJECT') {
      // Red shatter explosion particle burst (#FB7185)
      this.spawnShatterExplosion(this.corePosition, verdict.reasonCode);
    }
  }

  spawnShatterExplosion(pos, reasonCode) {
    const particleCount = 18;
    const particles = [];
    const geo = new THREE.BoxGeometry(2.5, 2.5, 2.5);
    const mat = new THREE.MeshBasicMaterial({ color: 0xFB7185 });

    for (let i = 0; i < particleCount; i++) {
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.copy(pos);
      const vel = new THREE.Vector3(
        (Math.random() - 0.5) * 80,
        Math.random() * 60 + 20,
        (Math.random() - 0.5) * 80
      );
      this.layerGroup.add(mesh);
      particles.push({ mesh, vel });
    }

    const reasonSprite = this.createPacketLabelSprite(`REJECTED: ${reasonCode}`, '#FB7185');
    reasonSprite.position.copy(pos).add(new THREE.Vector3(0, 30, 0));
    this.layerGroup.add(reasonSprite);

    this.activeVerdicts.push({
      type: 'REJECT_SHATTER',
      particles,
      reasonSprite,
      life: 1.0,
      decay: 0.9,
    });
  }

  spawnOffloadArrow(sourceCellId, targetCellId, offloadRatio) {
    const srcTower = this.cellEntities.get(sourceCellId);
    const dstTower = this.cellEntities.get(targetCellId);
    if (!srcTower || !dstTower) return;

    for (const oa of this.activeOffloadArrows) {
      if (oa.sourceCellId === sourceCellId && oa.targetCellId === targetCellId) {
        oa.life = 3.5;
        return;
      }
    }

    const startPos = srcTower.group.position.clone().add(new THREE.Vector3(0, 32, 0));
    const endPos = dstTower.group.position.clone().add(new THREE.Vector3(0, 32, 0));
    const midPos = startPos.clone().lerp(endPos, 0.5);
    midPos.y += 40;

    const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, endPos);
    const points = curve.getPoints(36);
    const geo = new THREE.BufferGeometry().setFromPoints(points);

    const mat = new THREE.LineDashedMaterial({
      color: 0x22D3EE,
      linewidth: 3,
      scale: 1,
      dashSize: 6,
      gapSize: 3,
      transparent: true,
      opacity: 0.9,
    });
    const line = new THREE.Line(geo, mat);
    line.computeLineDistances();
    this.layerGroup.add(line);

    const coneGeo = new THREE.ConeGeometry(5, 14, 12);
    const coneMat = new THREE.MeshStandardMaterial({
      color: 0x22D3EE,
      emissive: 0x0891B2,
      emissiveIntensity: 1.2,
    });
    const cone = new THREE.Mesh(coneGeo, coneMat);
    cone.position.copy(endPos);
    const tangent = curve.getTangent(1.0).normalize();
    cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tangent);
    this.layerGroup.add(cone);

    const pct = (offloadRatio * 100).toFixed(1);
    const labelSprite = this.createPacketLabelSprite(`${pct}% OFFLOAD`, '#22D3EE');
    labelSprite.position.copy(midPos).add(new THREE.Vector3(0, 14, 0));
    this.layerGroup.add(labelSprite);

    this.activeOffloadArrows.push({
      sourceCellId,
      targetCellId,
      line,
      cone,
      labelSprite,
      life: 3.5,
      decay: 0.8,
    });
  }

  // ---------------------------------------------------------------------------
  // 8. Real Offload Handover Particle Stream
  // ---------------------------------------------------------------------------
  spawnUserHandoverParticle(handover) {
    const targetTower = this.cellEntities.get(handover.targetCell);
    if (!targetTower) return;

    const startPos = new THREE.Vector3(handover.x, 3.5, handover.y);
    const endPos = targetTower.group.position.clone().add(new THREE.Vector3(0, 3.5, 0));
    const midPos = startPos.clone().lerp(endPos, 0.5);
    midPos.y += 25; // Handover arc

    const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, endPos);

    const geo = new THREE.SphereGeometry(3.2, 8, 8);
    const mat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const mesh = new THREE.Mesh(geo, mat);
    this.layerGroup.add(mesh);

    this.activeHandovers.push({
      mesh,
      curve,
      t: 0.0,
      speed: 1.2,
    });
  }

  // ---------------------------------------------------------------------------
  // 9. Frame Update & Animation Loop
  // ---------------------------------------------------------------------------
  update(deltaSec) {
    const dt = Math.min(0.1, deltaSec);

    // 1. Rotate ARBITER Core & Pulse Bloom Glow
    if (this.coreOuterRing) {
      this.coreOuterRing.rotation.z += 0.015;
    }
    if (this.coreInnerRing) {
      this.coreInnerRing.rotation.z -= 0.02;
    }
    if (this.corePrism) {
      this.corePrism.rotation.y += 0.025;
      this.corePrism.rotation.x += 0.01;
    }
    if (this.coreGlowSprite) {
      const pulse = 185 + 14 * Math.sin(performance.now() * 0.003);
      this.coreGlowSprite.scale.set(pulse, pulse, 1);
    }

    // 2. Twin Shimmer Fade
    if (this.isTwinEvaluating) {
      this.twinShimmerTimer -= dt;
      if (this.twinShimmerTimer <= 0) {
        this.isTwinEvaluating = false;
        this.twinShimmerSprite.visible = false;
      }
    }

    // 3. Controller Pulses
    for (const [key, pulse] of this.controllerPulses.entries()) {
      if (pulse > 0) {
        const next = Math.max(0, pulse - dt * 2.0);
        this.controllerPulses.set(key, next);
        const node = this.controllerNodes.get(key);
        if (node) {
          const s = 1.0 + next * 0.6;
          node.mesh.scale.set(s, s, s);
          node.mat.emissiveIntensity = 0.8 + next * 2.2;
        }
      }
    }

    // 4. Update In-Flight Proposal Packets
    for (let i = this.activePackets.length - 1; i >= 0; i--) {
      const p = this.activePackets[i];
      p.t += dt * p.speed;
      if (p.t >= 1.0) {
        // Reached ARBITER core
        this.layerGroup.remove(p.mesh);
        p.mesh.geometry.dispose();
        this.activePackets.splice(i, 1);
      } else {
        const pos = p.curve.getPoint(p.t);
        p.mesh.position.copy(pos);
      }
    }

    // 5. Update Conflict Arcs
    for (let i = this.activeConflictArcs.length - 1; i >= 0; i--) {
      const arc = this.activeConflictArcs[i];
      arc.life -= dt * arc.decay;
      if (arc.life <= 0) {
        this.layerGroup.remove(arc.line);
        this.layerGroup.remove(arc.labelSprite);
        arc.line.geometry.dispose();
        this.activeConflictArcs.splice(i, 1);
      } else {
        arc.line.material.opacity = arc.life;
        arc.labelSprite.material.opacity = arc.life;
      }
    }

    // 6. Update Held Orbiting Packets
    for (const [cmdId, held] of this.heldPackets.entries()) {
      held.angle += dt * 1.5;
      held.mesh.position.set(
        Math.cos(held.angle) * held.radius,
        0,
        Math.sin(held.angle) * held.radius
      );
      held.life -= dt * 0.4;
      if (held.life <= 0) {
        this.coreGroup.remove(held.mesh);
        this.heldPackets.delete(cmdId);
      }
    }

    // 7. Update Verdict Animations
    for (let i = this.activeVerdicts.length - 1; i >= 0; i--) {
      const v = this.activeVerdicts[i];
      if (v.type === 'APPROVE' || v.type === 'MODIFY') {
        v.t += dt * v.speed;
        if (v.t >= 1.0) {
          this.layerGroup.remove(v.mesh);
          v.mesh.geometry.dispose();
          this.activeVerdicts.splice(i, 1);
        } else {
          v.mesh.position.copy(v.curve.getPoint(v.t));
        }
      } else if (v.type === 'REJECT_SHATTER') {
        v.life -= dt * v.decay;
        if (v.life <= 0) {
          for (const part of v.particles) {
            this.layerGroup.remove(part.mesh);
            part.mesh.geometry.dispose();
          }
          this.layerGroup.remove(v.reasonSprite);
          this.activeVerdicts.splice(i, 1);
        } else {
          for (const part of v.particles) {
            part.mesh.position.addScaledVector(part.vel, dt);
            part.vel.y -= 120 * dt; // Gravity
            part.mesh.material.opacity = v.life;
          }
          v.reasonSprite.material.opacity = v.life;
        }
      }
    }

    // 8. Update User Handovers
    for (let i = this.activeHandovers.length - 1; i >= 0; i--) {
      const ho = this.activeHandovers[i];
      ho.t += dt * ho.speed;
      if (ho.t >= 1.0) {
        this.layerGroup.remove(ho.mesh);
        ho.mesh.geometry.dispose();
        this.activeHandovers.splice(i, 1);
      } else {
        ho.mesh.position.copy(ho.curve.getPoint(ho.t));
      }
    }

    // 9. Update Target Beacons
    for (const [cid, beacon] of this.targetBeacons.entries()) {
      if (beacon.visible) {
        beacon.userData.life -= dt * 0.8;
        if (beacon.userData.life <= 0) {
          beacon.visible = false;
        } else {
          beacon.material.opacity = beacon.userData.life * 0.35;
        }
      }
    }

    // 10. Update Offload Arrows
    for (let i = this.activeOffloadArrows.length - 1; i >= 0; i--) {
      const oa = this.activeOffloadArrows[i];
      oa.life -= dt * oa.decay;
      if (oa.life <= 0) {
        this.layerGroup.remove(oa.line);
        this.layerGroup.remove(oa.cone);
        this.layerGroup.remove(oa.labelSprite);
        oa.line.geometry.dispose();
        oa.cone.geometry.dispose();
        this.activeOffloadArrows.splice(i, 1);
      } else {
        const op = Math.min(1.0, oa.life);
        oa.line.material.opacity = op * 0.9;
        oa.cone.material.opacity = op;
        oa.labelSprite.material.opacity = op;
      }
    }
  }
}
