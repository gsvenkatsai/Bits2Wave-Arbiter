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

    // In-flight proposal and verdict animations
    this.activePackets = [];
    this.activeConflictArcs = [];
    this.activeVerdicts = [];
    this.activeHandovers = [];
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

      // Label sprite
      const labelSprite = this.createControllerLabel(spec.abbrev, spec.name, spec.color);
      labelSprite.position.set(0, 24, 0);
      nodeGroup.add(labelSprite);

      this.layerGroup.add(nodeGroup);
      this.controllerNodes.set(key, { group: nodeGroup, mesh, mat, halo, pos, spec });
      this.controllerPulses.set(key, 0.0);
    }
  }

  createControllerLabel(abbrev, name, color) {
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 80;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(6, 6, 244, 68, 12);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#f8fafc';
    ctx.font = 'bold 26px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(`${abbrev} • ${name}`, 128, 48);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(70, 22, 1);
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

    // 1. Concentric rotating outer ring
    const outerRingGeo = new THREE.TorusGeometry(60, 2.5, 12, 48);
    this.outerRingMat = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      emissive: 0x0284c7,
      emissiveIntensity: 0.9,
      roughness: 0.2,
      metalness: 0.9,
    });
    this.coreOuterRing = new THREE.Mesh(outerRingGeo, this.outerRingMat);
    this.coreOuterRing.rotation.x = Math.PI / 2.3;
    this.coreGroup.add(this.coreOuterRing);

    // 2. Counter-rotating inner ring
    const innerRingGeo = new THREE.TorusGeometry(42, 2.0, 12, 36);
    this.innerRingMat = new THREE.MeshStandardMaterial({
      color: 0x10b981,
      emissive: 0x059669,
      emissiveIntensity: 0.8,
      roughness: 0.3,
      metalness: 0.8,
    });
    this.coreInnerRing = new THREE.Mesh(innerRingGeo, this.innerRingMat);
    this.coreInnerRing.rotation.x = -Math.PI / 3;
    this.coreGroup.add(this.coreInnerRing);

    // 3. Central rotating crystalline prism (Octahedron)
    const prismGeo = new THREE.OctahedronGeometry(20, 0);
    this.prismMat = new THREE.MeshStandardMaterial({
      color: 0x60a5fa,
      emissive: 0x1d4ed8,
      emissiveIntensity: 0.8,
      roughness: 0.1,
      metalness: 0.9,
      wireframe: false,
    });
    this.corePrism = new THREE.Mesh(prismGeo, this.prismMat);
    this.coreGroup.add(this.corePrism);

    // 4. Central luminous core light
    this.coreLight = new THREE.PointLight(0x38bdf8, 3.5, 500);
    this.coreGroup.add(this.coreLight);

    // 5. Digital Twin Shimmer Badge (billboard)
    this.twinShimmerSprite = this.createTwinShimmerSprite();
    this.twinShimmerSprite.position.set(0, 48, 0);
    this.twinShimmerSprite.visible = false;
    this.coreGroup.add(this.twinShimmerSprite);

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
      // Exits core and flies to target tower, turning green
      const startPos = this.corePosition.clone();
      const midPos = startPos.clone().lerp(targetPos, 0.5);
      midPos.y += 60;
      const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, targetPos);

      const geo = new THREE.SphereGeometry(7, 12, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: 0x10b981,
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
      // Morphs requested value -> approved value, then flies out amber
      const startPos = this.corePosition.clone();
      const midPos = startPos.clone().lerp(targetPos, 0.5);
      midPos.y += 60;
      const curve = new THREE.QuadraticBezierCurve3(startPos, midPos, targetPos);

      const geo = new THREE.SphereGeometry(7, 12, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: 0xf59e0b,
        emissive: 0xd97706,
        emissiveIntensity: 1.5,
      });
      const mesh = new THREE.Mesh(geo, mat);
      this.layerGroup.add(mesh);

      // Morphing countdown label
      const morphSprite = this.createPacketLabelSprite(
        `BISECT: ${verdict.morphLabel || 'MODIFIED'}`,
        '#f59e0b'
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
      // Orbits the ARBITER core ring with a HOLD n/3 badge
      const geo = new THREE.SphereGeometry(6, 12, 12);
      const mat = new THREE.MeshStandardMaterial({
        color: 0xeab308,
        emissive: 0xca8a04,
        emissiveIntensity: 1.2,
      });
      const mesh = new THREE.Mesh(geo, mat);
      this.coreGroup.add(mesh);

      const holdSprite = this.createPacketLabelSprite(
        `HOLD ${verdict.holdCount}/3 • ${verdict.reasonCode}`,
        '#eab308'
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
      // Red shatter explosion particle burst
      this.spawnShatterExplosion(this.corePosition, verdict.reasonCode);
    }
  }

  spawnShatterExplosion(pos, reasonCode) {
    const particleCount = 18;
    const particles = [];
    const geo = new THREE.BoxGeometry(2.5, 2.5, 2.5);
    const mat = new THREE.MeshBasicMaterial({ color: 0xef4444 });

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

    const reasonSprite = this.createPacketLabelSprite(`REJECTED: ${reasonCode}`, '#ef4444');
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

    // 1. Rotate ARBITER Core
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
  }
}
