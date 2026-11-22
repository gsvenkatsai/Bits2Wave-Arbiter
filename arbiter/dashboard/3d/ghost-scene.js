/**
 * PROJECT ARBITER — Counterfactual Ghost World & Twin Uncertainty Scene Layer
 * Module: arbiter/dashboard/3d/ghost-scene.js
 *
 * Implements:
 * 1. Counterfactual Ghost World: side-by-side (+850m X offset) or overlay cluster,
 *    ethereal towers, dark Cell B, falling red dropped-user particles, interference tint.
 * 2. Twin Uncertainty Shells: translucent pulsating shells around towers with
 *    calibrated / uncalibrated badges and drift expansion.
 * 3. Plan Step Ghosts: holographic ghost step icons (sleep, offload, power) with
 *    circular countdown progress rings and precondition labels.
 */

import * as THREE from 'three';
import {
  mapTwinIntervalsToShells,
  mapPlanStepsToGhosts,
} from './ghost-world-mapper.js';
import { sinrToColor } from './scene.js';

export const GHOST_OFFSET_X = 850.0;

export class GhostSceneLayer {
  constructor(scene, options = {}) {
    this.scene = scene;
    this.cellEntities = options.cellEntities || new Map();

    // Mode: 'side-by-side' (offset +850m) or 'overlay' (offset 0m)
    this.viewMode = options.viewMode || 'side-by-side';
    this.isGhostWorldVisible = false;

    // Groups
    this.ghostWorldGroup = new THREE.Group();
    this.ghostWorldGroup.position.set(GHOST_OFFSET_X, 0, 0);
    this.ghostWorldGroup.visible = false;
    this.scene.add(this.ghostWorldGroup);

    this.shellsGroup = new THREE.Group();
    this.scene.add(this.shellsGroup);

    this.planGhostsGroup = new THREE.Group();
    this.scene.add(this.planGhostsGroup);

    // Entity maps
    this.ghostCells = new Map(); // id -> { group, mast, disc, label }
    this.uncertaintyShells = new Map(); // cellId -> { mesh, badgeSprite, radiusTarget }
    this.planStepMeshes = new Map(); // stepKey -> { group, ring, sprite }
    this.fallingDropParticles = [];

    this.initGhostCluster();
  }

  // ---------------------------------------------------------------------------
  // 1. Ghost Cluster Setup
  // ---------------------------------------------------------------------------
  initGhostCluster() {
    // Ghost Ground Tile & Grid
    const groundGeo = new THREE.PlaneGeometry(1800, 1800);
    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x11131f,
      roughness: 0.9,
      transparent: true,
      opacity: 0.5,
    });
    const ghostGround = new THREE.Mesh(groundGeo, groundMat);
    ghostGround.rotation.x = -Math.PI / 2;
    ghostGround.position.y = -0.5;
    this.ghostWorldGroup.add(ghostGround);

    // Subtle violet/reddish interference haze grid
    const grid = new THREE.GridHelper(1600, 32, 0x6366f1, 0x312e81);
    grid.position.y = 0.2;
    this.ghostWorldGroup.add(grid);

    // Ghost Users InstancedMesh
    const userGeo = new THREE.SphereGeometry(3.5, 8, 8);
    this.ghostUserMat = new THREE.MeshStandardMaterial({
      color: 0x818cf8,
      transparent: true,
      opacity: 0.65,
      roughness: 0.3,
    });
    this.ghostUserInstancedMesh = new THREE.InstancedMesh(userGeo, this.ghostUserMat, 2000);
    this.ghostUserInstancedMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.ghostWorldGroup.add(this.ghostUserInstancedMesh);

    // Billboard title over ghost cluster
    this.ghostTitleSprite = this.createClusterTitleSprite("WITHOUT ARBITER (UNCOORDINATED BASELINE)");
    this.ghostTitleSprite.position.set(0, 160, 0);
    this.ghostWorldGroup.add(this.ghostTitleSprite);
  }

  createClusterTitleSprite(text) {
    const canvas = document.createElement('canvas');
    canvas.width = 440;
    canvas.height = 70;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(239, 68, 68, 0.85)';
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(6, 6, 428, 58, 14);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 22px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(text, 220, 42);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(130, 21, 1);
    return sprite;
  }

  setViewMode(mode) {
    this.viewMode = mode === 'overlay' ? 'overlay' : 'side-by-side';
    const offsetX = this.viewMode === 'side-by-side' ? GHOST_OFFSET_X : 0.0;
    this.ghostWorldGroup.position.set(offsetX, 0, 0);
  }

  setGhostWorldVisible(visible) {
    this.isGhostWorldVisible = Boolean(visible);
    this.ghostWorldGroup.visible = this.isGhostWorldVisible;
  }

  // ---------------------------------------------------------------------------
  // 2. Twin Uncertainty Shells
  // ---------------------------------------------------------------------------
  updateUncertaintyShells(twinData, cells) {
    const shellDescriptors = mapTwinIntervalsToShells(twinData, cells);

    for (const shell of shellDescriptors) {
      let entry = this.uncertaintyShells.get(shell.cellId);
      if (!entry) {
        // Create 3D translucent bubble shell
        const geo = new THREE.SphereGeometry(1, 24, 16);
        const mat = new THREE.MeshStandardMaterial({
          color: shell.colorHex,
          transparent: true,
          opacity: 0.16,
          roughness: 0.2,
          metalness: 0.1,
          side: THREE.DoubleSide,
          depthWrite: false,
        });
        const mesh = new THREE.Mesh(geo, mat);
        const cellTower = this.cellEntities.get(shell.cellId);
        const cx = cellTower ? cellTower.group.position.x : shell.x;
        const cz = cellTower ? cellTower.group.position.z : shell.y;
        mesh.position.set(cx, 25, cz);
        this.shellsGroup.add(mesh);

        // Badge sprite
        const badgeSprite = this.createShellBadge(shell.badgeText, shell.isCalibrated);
        badgeSprite.position.set(cx, 96, cz);
        this.shellsGroup.add(badgeSprite);

        entry = { mesh, mat, badgeSprite, radiusTarget: shell.radius };
        this.uncertaintyShells.set(shell.cellId, entry);
      }

      entry.radiusTarget = shell.radius;
      entry.mat.color.setHex(shell.colorHex);

      // Redraw badge if calibration changed
      const { ctx, texture } = entry.badgeSprite.userData;
      this.drawShellBadgeCanvas(ctx, shell.badgeText, shell.isCalibrated);
      texture.needsUpdate = true;
    }
  }

  createShellBadge(text, isCalibrated) {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 70;
    const ctx = canvas.getContext('2d');

    this.drawShellBadgeCanvas(ctx, text, isCalibrated);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(80, 17.5, 1);
    sprite.userData = { canvas, ctx, texture };
    return sprite;
  }

  drawShellBadgeCanvas(ctx, text, isCalibrated) {
    ctx.clearRect(0, 0, 320, 70);
    // Dark gold glass pill style
    ctx.fillStyle = isCalibrated ? 'rgba(10, 10, 13, 0.90)' : 'rgba(30, 12, 14, 0.92)';
    ctx.strokeStyle = isCalibrated ? '#FFC800' : '#FF4D5E';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(6, 6, 308, 58, 29);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = isCalibrated ? '#FFD84A' : '#FF4D5E';
    ctx.font = 'bold 20px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(text, 160, 42);
  }

  // ---------------------------------------------------------------------------
  // 3. Plan Step Ghosts
  // ---------------------------------------------------------------------------
  updatePlanGhosts(plans) {
    const ghosts = mapPlanStepsToGhosts(plans);
    const activeKeys = new Set();

    for (const ghost of ghosts) {
      const key = `${ghost.planId}-${ghost.stepIndex}`;
      activeKeys.add(key);

      let entry = this.planStepMeshes.get(key);
      const targetTower = this.cellEntities.get(ghost.targetCellId);
      const tx = targetTower ? targetTower.group.position.x : 0;
      const tz = targetTower ? targetTower.group.position.z : 0;

      if (!entry) {
        const group = new THREE.Group();
        group.position.set(tx, 78 + ghost.stepIndex * 18, tz);

        // Holographic Progress Ring
        const ringGeo = new THREE.RingGeometry(10, 12, 24);
        const ringMat = new THREE.MeshBasicMaterial({
          color: ghost.colorHex,
          transparent: true,
          opacity: ghost.opacity,
          side: THREE.DoubleSide,
        });
        const ring = new THREE.Mesh(ringGeo, ringMat);
        ring.rotation.x = -Math.PI / 2;
        group.add(ring);

        // Precondition Label Sprite
        const sprite = this.createPlanSprite(ghost);
        sprite.position.set(0, 12, 0);
        group.add(sprite);

        this.planGhostsGroup.add(group);
        entry = { group, ring, ringMat, sprite };
        this.planStepMeshes.set(key, entry);
      }

      entry.ringMat.color.setHex(ghost.colorHex);
      entry.ringMat.opacity = ghost.opacity;
    }

    // Cleanup stale plan steps
    for (const [key, entry] of this.planStepMeshes.entries()) {
      if (!activeKeys.has(key)) {
        this.planGhostsGroup.remove(entry.group);
        this.planStepMeshes.delete(key);
      }
    }
  }

  createPlanSprite(ghost) {
    const canvas = document.createElement('canvas');
    canvas.width = 320;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.strokeStyle = ghost.status === 'EXECUTED' ? '#10b981' : '#38bdf8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(6, 6, 308, 52, 10);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 18px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(`PLAN [${ghost.status}]: ${ghost.precondition}`, 160, 38);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(65, 13, 1);
    return sprite;
  }

  // ---------------------------------------------------------------------------
  // 4. Update Counterfactual Ghost Cluster on Frame
  // ---------------------------------------------------------------------------
  updateGhostWorldFrame(cfFrame) {
    if (!cfFrame || !this.isGhostWorldVisible) return;

    // 1. Update or create ghost cell towers
    const cells = cfFrame.cells || [];
    for (const cellData of cells) {
      let gCell = this.ghostCells.get(cellData.id);
      if (!gCell) {
        const group = new THREE.Group();
        group.position.set(cellData.x, 0, cellData.y);

        // Mast mesh with ethereal ghost material
        const mastGeo = new THREE.CylinderGeometry(1.6, 4.0, 45, 8);
        const mastMat = new THREE.MeshStandardMaterial({
          color: 0x64748b,
          transparent: true,
          opacity: 0.45,
          roughness: 0.5,
        });
        const mast = new THREE.Mesh(mastGeo, mastMat);
        mast.position.y = 22.5;
        group.add(mast);

        // Status badge
        const badge = this.createGhostCellBadge(cellData.id, cellData.is_sleeping);
        badge.position.set(0, 55, 0);
        group.add(badge);

        this.ghostWorldGroup.add(group);
        gCell = { group, mast, mastMat, badge };
        this.ghostCells.set(cellData.id, gCell);
      }

      // If Cell B is sleeping in ghost world, dim completely
      if (cellData.is_sleeping) {
        gCell.mastMat.color.setHex(0x1e293b);
        gCell.mastMat.opacity = 0.25;
      } else {
        gCell.mastMat.color.setHex(0x64748b);
        gCell.mastMat.opacity = 0.5;
      }
    }

    // 2. Render Counterfactual Ghost Users & Detect Call Drops
    const users = cfFrame.users || [];
    const count = Math.min(users.length, 2000);
    const matrix = new THREE.Matrix4();
    const pos = new THREE.Vector3();
    const rot = new THREE.Quaternion();
    const scale = new THREE.Vector3();

    for (let i = 0; i < count; i++) {
      const u = users[i];
      pos.set(Number(u.x) || 0, 3.5, Number(u.y) || 0);

      if (u.dropped) {
        // Spawn falling red dropped-call particle
        this.spawnFallingDropParticle(pos.clone().add(this.ghostWorldGroup.position));
        scale.set(0.1, 0.1, 0.1); // Hide dropped ghost user instance
      } else {
        scale.set(1.0, 1.0, 1.0);
      }

      matrix.compose(pos, rot, scale);
      this.ghostUserInstancedMesh.setMatrixAt(i, matrix);
      const color = sinrToColor(u.sinr_db, u.dropped);
      this.ghostUserInstancedMesh.setColorAt(i, color);
    }

    // Hide remainder
    const dummy = new THREE.Matrix4().setPosition(0, -9999, 0);
    for (let i = count; i < 2000; i++) {
      this.ghostUserInstancedMesh.setMatrixAt(i, dummy);
    }
    this.ghostUserInstancedMesh.instanceMatrix.needsUpdate = true;
    if (this.ghostUserInstancedMesh.instanceColor) {
      this.ghostUserInstancedMesh.instanceColor.needsUpdate = true;
    }
  }

  createGhostCellBadge(id, isSleeping) {
    const canvas = document.createElement('canvas');
    canvas.width = 240;
    canvas.height = 70;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = isSleeping ? 'rgba(30, 41, 59, 0.9)' : 'rgba(15, 23, 42, 0.85)';
    ctx.strokeStyle = isSleeping ? '#ef4444' : '#818cf8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(6, 6, 228, 58, 12);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = isSleeping ? '#ef4444' : '#cbd5e1';
    ctx.font = 'bold 22px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText(isSleeping ? `${id} (SLEEPING)` : id, 120, 42);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(65, 19, 1);
    return sprite;
  }

  spawnFallingDropParticle(worldPos) {
    if (this.fallingDropParticles.length >= 30) return;

    const geo = new THREE.SphereGeometry(2.5, 6, 6);
    const mat = new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.9 });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.copy(worldPos);
    this.scene.add(mesh);

    this.fallingDropParticles.push({
      mesh,
      life: 1.0,
      vy: -60.0,
    });
  }

  // ---------------------------------------------------------------------------
  // 5. Render Loop Update
  // ---------------------------------------------------------------------------
  update(deltaSec) {
    const dt = Math.min(0.1, deltaSec);

    // 1. Animate Uncertainty Shells
    for (const shell of this.uncertaintyShells.values()) {
      const currentScale = shell.mesh.scale.x;
      const targetScale = shell.radiusTarget || BASE_SHELL_RADIUS;
      const lerpedScale = THREE.MathUtils.lerp(currentScale, targetScale, dt * 3.5);
      shell.mesh.scale.set(lerpedScale, lerpedScale, lerpedScale);
    }

    // 2. Animate Falling Dropped Call Particles
    for (let i = this.fallingDropParticles.length - 1; i >= 0; i--) {
      const p = this.fallingDropParticles[i];
      p.life -= dt * 1.5;
      p.mesh.position.y += p.vy * dt;
      p.mesh.material.opacity = p.life;

      if (p.life <= 0 || p.mesh.position.y <= 0) {
        this.scene.remove(p.mesh);
        p.mesh.geometry.dispose();
        this.fallingDropParticles.splice(i, 1);
      }
    }

    // 3. Animate Plan Step Progress Rings
    for (const entry of this.planStepMeshes.values()) {
      if (entry.ring) {
        entry.ring.rotation.z += 0.03;
      }
    }
  }
}
