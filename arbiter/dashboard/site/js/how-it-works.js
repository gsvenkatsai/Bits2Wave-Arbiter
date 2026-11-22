/**
 * PROJECT ARBITER — How It Works Page Interactive Architecture & Ladder
 * Module: arbiter/dashboard/site/js/how-it-works.js
 */

import { initNavbar, injectIcons } from './components.js';

// Architecture node metadata for interactive hover/focus cards
const ARCH_NODE_METADATA = {
  controllers: {
    title: 'Autonomous Controllers (xApps / rApps)',
    module: 'arbiter.controllers',
    desc: 'Independent O-RAN xApps and rApps (Energy Saving, Load Balancing, Coverage & Capacity Optimization) generating concurrent parameter modification requests.',
  },
  queue: {
    title: 'Proposal Queue (100 ms Horizon)',
    module: 'arbiter.engine.queue',
    desc: 'Ingests, timestamps, and deduplicates candidate control actions from external controllers within the Near-RT RIC 100 ms actuation window.',
  },
  detector: {
    title: 'Graph-Theoretic Conflict Detector',
    module: 'arbiter.engine.detector',
    desc: 'Constructs the dynamic bipartite conflict graph, classifying pairwise direct, indirect, and cyclic ping-pong interactions across cells.',
  },
  engine: {
    title: 'Decision Engine & Plan Synthesizer',
    module: 'arbiter.engine.referee',
    desc: 'Evaluates multi-objective utility, synthesizes joint candidate vectors, executes parameter bisection, and orchestrates timed hold queues.',
  },
  twin: {
    title: 'Conformal Digital Twin',
    module: 'arbiter.twin.digital_twin',
    desc: 'Simulates radio KPI impacts counterfactually, computing distribution-free conformal uncertainty intervals (≥ 90% coverage) with rolling calibration.',
  },
  actuator: {
    title: 'Near-RT RIC Actuator',
    module: 'arbiter.engine.actuator',
    desc: 'Dispatches approved and modified joint setpoints over E2 service models down to the cellular radio unit, logging auditable verdicts.',
  },
  simulator: {
    title: 'Cellular RAN Simulator & Network',
    module: 'arbiter.sim.ran_sim',
    desc: 'Multi-cell dense urban RF environment modeling propagation, antenna down-tilts, user mobility, and physical layer interference.',
  },
  telemetry: {
    title: 'Telemetry Stream & Feedback Bus',
    module: 'arbiter.sim.telemetry',
    desc: 'Streams real-time cell and UE Key Performance Indicators back to controllers and feeds ground truth into the twin rolling calibration buffer.',
  },
};

document.addEventListener('DOMContentLoaded', () => {
  // 1. Initialize Shared Navbar & Icons
  initNavbar();
  injectIcons();

  // 2. Setup Interactive Architecture Diagram Nodes
  setupArchitectureDiagram();
});

function setupArchitectureDiagram() {
  const detailTitle = document.getElementById('arch-detail-title');
  const detailModule = document.getElementById('arch-detail-module');
  const detailDesc = document.getElementById('arch-detail-desc');
  const nodes = document.querySelectorAll('.arch-node');

  if (!detailTitle || !detailModule || !detailDesc || !nodes.length) return;

  function updateDetail(nodeId) {
    const meta = ARCH_NODE_METADATA[nodeId];
    if (!meta) return;

    detailTitle.textContent = meta.title;
    detailModule.textContent = meta.module;
    detailDesc.textContent = meta.desc;

    nodes.forEach((n) => {
      if (n.getAttribute('data-node-id') === nodeId) {
        n.classList.add('active');
      } else {
        n.classList.remove('active');
      }
    });
  }

  nodes.forEach((node) => {
    const nodeId = node.getAttribute('data-node-id');

    node.addEventListener('mouseenter', () => updateDetail(nodeId));
    node.addEventListener('focus', () => updateDetail(nodeId));
    node.addEventListener('click', () => updateDetail(nodeId));

    // Keyboard navigation: Enter or Space activates
    node.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        updateDetail(nodeId);
      }
    });
  });
}
