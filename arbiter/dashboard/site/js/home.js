/**
 * PROJECT ARBITER — Home Page 3D Visualization & Interactions
 * Module: arbiter/dashboard/site/js/home.js
 *
 * Implements:
 * - Three.js dark glowing hex-tiled sphere with gold rim light
 * - 3 orbiting controller nodes: Energy Saving (green), Load Balancing (blue), Coverage & Capacity (amber)
 * - Animated gold light arcs/pulses travelling into the bright referee core
 * - Mouse parallax & slow ambient rotation
 * - Performance: DPR capped at 2, paused on tab hidden (visibilitychange)
 * - Accessibility: pure CSS/SVG static fallback if WebGL fails or prefers-reduced-motion
 * - Replay modal: opens /dashboard/3d/?replay=1&demo=1, closes on Esc or click outside
 */

import * as THREE from '../vendor/three.module.js';

/**
 * Display the pure CSS/SVG fallback and hide the canvas.
 */
function activateFallback() {
  const canvas = document.getElementById('hero-webgl-canvas');
  const fallback = document.getElementById('hero-canvas-fallback');
  if (canvas) canvas.style.display = 'none';
  if (fallback) fallback.style.display = 'flex';
}

/**
 * Initialize the 3D WebGL Hero Canvas.
 */
export function initHeroVisualization() {
  const canvas = document.getElementById('hero-webgl-canvas');
  const fallback = document.getElementById('hero-canvas-fallback');
  if (!canvas) return;

  // 1. Accessibility: Check for prefers-reduced-motion
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReducedMotion) {
    activateFallback();
    return;
  }

  // 2. Initialize Three.js WebGLRenderer with fallback on failure
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
  } catch (err) {
    console.warn('[Home 3D] WebGL context creation failed; showing fallback:', err);
    activateFallback();
    return;
  }

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  renderer.setPixelRatio(dpr);

  const rect = canvas.getBoundingClientRect();
  const initialWidth = rect.width || 480;
  const initialHeight = rect.height || 380;
  renderer.setSize(initialWidth, initialHeight, false);

  const scene = new THREE.Scene();

  const camera = new THREE.PerspectiveCamera(45, initialWidth / initialHeight, 0.1, 100);
  camera.position.set(0, 0, 7.2);
  camera.lookAt(0, 0, 0);

  // 3. Lighting: Gold Rim Light + Ambient + Central Core Point Light
  const ambientLight = new THREE.AmbientLight(0x1a150c, 1.4);
  scene.add(ambientLight);

  // Dramatic gold rim light from behind-left
  const rimLight = new THREE.DirectionalLight(0xffc800, 3.2);
  rimLight.position.set(-4.5, 3.5, -3.0);
  scene.add(rimLight);

  // Soft key light from front-right
  const frontKeyLight = new THREE.DirectionalLight(0xfff5dd, 0.9);
  frontKeyLight.position.set(3.5, 3.0, 4.0);
  scene.add(frontKeyLight);

  // Central gold core light illuminating the sphere interior & nodes
  const coreLight = new THREE.PointLight(0xffd84a, 2.8, 12);
  coreLight.position.set(0, 0, 0);
  scene.add(coreLight);

  // 4. Group for the Hex-Tiled Central Sphere
  const sphereGroup = new THREE.Group();
  scene.add(sphereGroup);

  // 4a. Dark inner sphere base
  const innerGeo = new THREE.SphereGeometry(2.1, 36, 24);
  const innerMat = new THREE.MeshStandardMaterial({
    color: 0x08080c,
    roughness: 0.85,
    metalness: 0.15,
  });
  const innerSphere = new THREE.Mesh(innerGeo, innerMat);
  sphereGroup.add(innerSphere);

  // 4b. Geodesic hex-lattice wireframe
  const icoGeo = new THREE.IcosahedronGeometry(2.14, 2);
  const wireframeGeo = new THREE.WireframeGeometry(icoGeo);
  const wireMat = new THREE.LineBasicMaterial({
    color: 0xffc800,
    transparent: true,
    opacity: 0.32,
  });
  const wireframe = new THREE.LineSegments(wireframeGeo, wireMat);
  sphereGroup.add(wireframe);

  // 4c. Hex-tile facet discs on vertices
  const posAttr = icoGeo.attributes.position;
  const vertexCount = posAttr.count;
  const hexTileGeo = new THREE.CircleGeometry(0.24, 6);
  const hexTileMat = new THREE.MeshStandardMaterial({
    color: 0x14120e,
    emissive: 0xffc800,
    emissiveIntensity: 0.22,
    roughness: 0.45,
    metalness: 0.5,
    side: THREE.DoubleSide,
  });

  const instancedHexTiles = new THREE.InstancedMesh(hexTileGeo, hexTileMat, vertexCount);
  const dummy = new THREE.Object3D();
  const vPos = new THREE.Vector3();

  for (let i = 0; i < vertexCount; i++) {
    vPos.fromBufferAttribute(posAttr, i).normalize().multiplyScalar(2.15);
    dummy.position.copy(vPos);
    dummy.lookAt(vPos.clone().multiplyScalar(2));
    dummy.updateMatrix();
    instancedHexTiles.setMatrixAt(i, dummy.matrix);
  }
  instancedHexTiles.instanceMatrix.needsUpdate = true;
  sphereGroup.add(instancedHexTiles);

  // 5. Central Referee Core (Bright Glowing Engine)
  const coreGroup = new THREE.Group();
  scene.add(coreGroup);

  const coreGeo = new THREE.SphereGeometry(0.48, 24, 20);
  const coreMat = new THREE.MeshStandardMaterial({
    color: 0xfff0aa,
    emissive: 0xffc800,
    emissiveIntensity: 2.2,
    roughness: 0.2,
  });
  const coreMesh = new THREE.Mesh(coreGeo, coreMat);
  coreGroup.add(coreMesh);

  // Pulsing gold halo around referee core
  const haloGeo = new THREE.SphereGeometry(0.68, 16, 16);
  const haloMat = new THREE.MeshBasicMaterial({
    color: 0xffc800,
    wireframe: true,
    transparent: true,
    opacity: 0.35,
  });
  const haloMesh = new THREE.Mesh(haloGeo, haloMat);
  coreGroup.add(haloMesh);

  // 6. Three Controller Nodes (ES: Green, LB: Blue, CC: Amber)
  const nodeDefs = [
    {
      name: 'ES',
      color: 0x2ee6a6, // Green
      radius: 3.1,
      speed: 0.55,
      tilt: new THREE.Euler(0.4, 0.2, 0.3),
      angleOffset: 0,
    },
    {
      name: 'LB',
      color: 0x4fc3f7, // Blue
      radius: 3.4,
      speed: 0.42,
      tilt: new THREE.Euler(-0.5, 0.4, -0.2),
      angleOffset: 2.1,
    },
    {
      name: 'CC',
      color: 0xffb020, // Amber
      radius: 2.9,
      speed: 0.68,
      tilt: new THREE.Euler(0.6, -0.5, 0.4),
      angleOffset: 4.2,
    },
  ];

  const nodeGeo = new THREE.SphereGeometry(0.13, 16, 16);
  const nodes = nodeDefs.map((def) => {
    const mat = new THREE.MeshStandardMaterial({
      color: def.color,
      emissive: def.color,
      emissiveIntensity: 1.6,
      roughness: 0.2,
    });
    const mesh = new THREE.Mesh(nodeGeo, mat);
    scene.add(mesh);

    // Glowing aura ring
    const ringGeo = new THREE.RingGeometry(0.18, 0.24, 16);
    const ringMat = new THREE.MeshBasicMaterial({
      color: def.color,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.6,
    });
    const ringMesh = new THREE.Mesh(ringGeo, ringMat);
    mesh.add(ringMesh);

    // Dynamic light arc geometry & line
    const arcPoints = new Float32Array(30 * 3);
    const arcGeo = new THREE.BufferGeometry();
    arcGeo.setAttribute('position', new THREE.BufferAttribute(arcPoints, 3));
    const arcMat = new THREE.LineBasicMaterial({
      color: 0xffc800,
      transparent: true,
      opacity: 0.45,
    });
    const arcLine = new THREE.Line(arcGeo, arcMat);
    scene.add(arcLine);

    // Pulsing traveling light packet on the arc
    const packetGeo = new THREE.SphereGeometry(0.06, 10, 10);
    const packetMat = new THREE.MeshBasicMaterial({
      color: 0xffd84a,
    });
    const packetMesh = new THREE.Mesh(packetGeo, packetMat);
    scene.add(packetMesh);

    return {
      def,
      mesh,
      ringMesh,
      arcLine,
      arcGeo,
      packetMesh,
      currentPos: new THREE.Vector3(),
    };
  });

  // 7. Mouse Parallax Tracking
  let targetParallaxX = 0;
  let targetParallaxY = 0;
  let currentParallaxX = 0;
  let currentParallaxY = 0;

  const onMouseMove = (e) => {
    const cardRect = canvas.parentElement ? canvas.parentElement.getBoundingClientRect() : rect;
    const x = (e.clientX - (cardRect.left + cardRect.width / 2)) / (cardRect.width / 2);
    const y = (e.clientY - (cardRect.top + cardRect.height / 2)) / (cardRect.height / 2);
    targetParallaxX = Math.max(-0.6, Math.min(0.6, x * 0.45));
    targetParallaxY = Math.max(-0.6, Math.min(0.6, -y * 0.45));
  };
  window.addEventListener('mousemove', onMouseMove, { passive: true });

  // 8. Visibility Change: Pause rendering when tab is hidden to save battery & GPU
  let isPaused = false;
  document.addEventListener('visibilitychange', () => {
    isPaused = document.hidden;
  });

  // 9. Resize Handling
  const handleResize = () => {
    if (!canvas || !canvas.parentElement) return;
    const width = canvas.parentElement.clientWidth || 480;
    const height = canvas.parentElement.clientHeight || 380;
    if (width > 0 && height > 0) {
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    }
  };
  window.addEventListener('resize', handleResize, { passive: true });

  // 10. Animation Loop
  let clock = new THREE.Clock();
  let reqId;

  function animate() {
    reqId = requestAnimationFrame(animate);
    if (isPaused) return;

    const delta = clock.getDelta();
    const elapsedTime = clock.getElapsedTime();

    // Ambient slow rotation of hex sphere
    sphereGroup.rotation.y += delta * 0.12;
    sphereGroup.rotation.x += delta * 0.04;

    // Pulse referee core
    const corePulse = 1.0 + 0.08 * Math.sin(elapsedTime * 4.0);
    coreGroup.scale.set(corePulse, corePulse, corePulse);
    haloMesh.rotation.y += delta * 0.3;
    haloMesh.rotation.z -= delta * 0.2;

    // Update controller nodes and their gold arcs
    nodes.forEach(({ def, mesh, ringMesh, arcLine, arcGeo, packetMesh, currentPos }) => {
      const angle = elapsedTime * def.speed + def.angleOffset;
      const x = Math.cos(angle) * def.radius;
      const z = Math.sin(angle) * def.radius;
      const pos = new THREE.Vector3(x, 0, z);
      pos.applyEuler(def.tilt);

      mesh.position.copy(pos);
      currentPos.copy(pos);
      ringMesh.lookAt(camera.position);

      // Construct quadratic bezier arc from node position to core (0,0,0)
      // Midpoint lifted slightly above the direct chord for a curved arc
      const midPoint = pos.clone().multiplyScalar(0.55);
      midPoint.y += Math.sin(angle) * 0.6;
      midPoint.x += Math.cos(angle) * 0.4;

      const curve = new THREE.QuadraticBezierCurve3(pos, midPoint, new THREE.Vector3(0, 0, 0));
      const points = curve.getPoints(29);
      const posArray = arcGeo.attributes.position.array;

      for (let i = 0; i < points.length; i++) {
        posArray[i * 3] = points[i].x;
        posArray[i * 3 + 1] = points[i].y;
        posArray[i * 3 + 2] = points[i].z;
      }
      arcGeo.attributes.position.needsUpdate = true;

      // Animate packet travelling down the arc into referee core
      const t = (elapsedTime * 1.2 + def.angleOffset * 0.4) % 1.0;
      const packetPos = curve.getPoint(t);
      packetMesh.position.copy(packetPos);
    });

    // Smooth mouse parallax
    currentParallaxX += (targetParallaxX - currentParallaxX) * 0.06;
    currentParallaxY += (targetParallaxY - currentParallaxY) * 0.06;
    camera.position.x = currentParallaxX * 1.5;
    camera.position.y = currentParallaxY * 1.5;
    camera.lookAt(0, 0, 0);

    renderer.render(scene, camera);
  }

  animate();

  return () => {
    if (reqId) cancelAnimationFrame(reqId);
    window.removeEventListener('mousemove', onMouseMove);
    window.removeEventListener('resize', handleResize);
  };
}

/**
 * Initialize the 2 AM Replay Modal dialog.
 */
export function initReplayModal() {
  const replayModal = document.getElementById('replay-modal');
  const modalIframe = document.getElementById('replay-modal-iframe');
  const triggerBtns = document.querySelectorAll('[data-open-replay]');
  const closeBtns = document.querySelectorAll('[data-close-replay]');

  if (!replayModal) return;

  const openModal = () => {
    replayModal.setAttribute('aria-hidden', 'false');
    replayModal.classList.add('open', 'active');
    document.body.style.overflow = 'hidden';

    // Lazy load the replay iframe to avoid CPU usage until opened
    if (modalIframe && !modalIframe.getAttribute('src')) {
      modalIframe.setAttribute('src', '/dashboard/3d/?replay=1&demo=1');
    }

    // Set focus to the modal close button for accessibility
    const closeBtn = replayModal.querySelector('.modal-close') || replayModal.querySelector('button');
    if (closeBtn) closeBtn.focus();
  };

  const closeModal = () => {
    replayModal.setAttribute('aria-hidden', 'true');
    replayModal.classList.remove('open', 'active');
    document.body.style.overflow = '';

    // Clear iframe source to halt 3D render loop inside the iframe
    if (modalIframe) {
      modalIframe.setAttribute('src', '');
    }
  };

  triggerBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      openModal();
    });
  });

  closeBtns.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      closeModal();
    });
  });

  // Close on backdrop click
  replayModal.addEventListener('click', (e) => {
    if (e.target === replayModal) {
      closeModal();
    }
  });

  // Close on Escape key press
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && replayModal.classList.contains('active')) {
      closeModal();
    }
  });
}

/**
 * Initialize smooth scroll for anchor links.
 */
export function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener('click', function (e) {
      const targetId = this.getAttribute('href');
      if (targetId && targetId !== '#') {
        const targetEl = document.querySelector(targetId);
        if (targetEl) {
          e.preventDefault();
          targetEl.scrollIntoView({ behavior: 'smooth' });
        }
      }
    });
  });
}
