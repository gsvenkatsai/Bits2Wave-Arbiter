/**
 * PROJECT ARBITER — Site Main Application Entry Point
 * Module: arbiter/dashboard/site/app.js
 *
 * ES Module coordinating offline evidence loading, UI interactions,
 * accessible modal dialogues, and responsive navigation.
 */

import { initNavbar, injectIcons, Modal } from './js/components.js';
import { loadEvidence, loadTeam, populateEvidenceDOM, formatMetricValue, getMetricStatus } from './js/evidence.js';
import { initHeroVisualization, initReplayModal, initSmoothScroll } from './js/home.js';

document.addEventListener('DOMContentLoaded', async () => {
  // 1. Initialize Navigation, Icons & Smooth Scroll
  initNavbar();
  injectIcons();
  initSmoothScroll();

  // 2. Setup Accessible Architecture Modal & Replay Modal
  const modalEl = document.getElementById('architecture-modal');
  let archModal = null;
  if (modalEl) {
    archModal = new Modal(modalEl);
    const triggerBtns = document.querySelectorAll('[data-open-modal="architecture-modal"]');
    triggerBtns.forEach((btn) => {
      btn.addEventListener('click', () => archModal.open());
    });
  }
  initReplayModal();

  // 3. Initialize 3D Hero WebGL Canvas
  initHeroVisualization();

  // 4. Load Real Evidence from Pipeline
  const evidence = await loadEvidence('./data/evidence.json');
  populateEvidenceDOM(document, evidence);

  // 5. Render Evidence Table Rows
  renderEvidenceTable(evidence);

  // 6. Load Real Team Roster
  const teamData = await loadTeam('./data/team.json');
  renderTeamGrid(teamData);
});

/**
 * Render dynamic rows into the Evidence & Quantitative Proofs table.
 * @param {Object} evidence
 */
function renderEvidenceTable(evidence) {
  const tbody = document.getElementById('evidence-table-body');
  if (!tbody || !evidence || !evidence.metrics) return;

  tbody.innerHTML = '';
  const metrics = evidence.metrics;

  const displayOrder = [
    { key: 'optimality_gap', label: 'Optimality Gap vs Combinatorial Search' },
    { key: 'arbiter_decision_latency', label: 'ARBITER Near-RT RIC Decision Latency' },
    { key: 'target_ric_latency', label: 'Target Near-RT RIC Control Loop Latency' },
    { key: 'exhaustive_search_latency', label: 'Exhaustive Brute-Force Solver Latency' },
    { key: 'utility_gain_over_baseline', label: 'Utility Improvement over Uncoordinated Baseline' },
    { key: 'conformal_confidence_level', label: 'Conformal Safety Marginal Coverage Level' },
    { key: 'conformal_calibration_window', label: 'Conformal Rolling Residual Buffer Window' },
    { key: 'collision_uncoordinated_drops', label: 'Call Drops in Uncoordinated 2 AM Baseline' },
    { key: 'collision_arbiter_drops', label: 'Call Drops with ARBITER Coordinated Actions' },
    { key: 'energy_saved_per_tick', label: 'Cluster Energy Saved per Coordinated Tick' },
    { key: 'avoided_co2_per_tick', label: 'Avoided Carbon Emissions per Tick' },
    { key: 'suite_test_count', label: 'Automated Regression & Verification Tests' },
    { key: 'cluster_cells', label: 'Dense Urban Hexagonal Cell Cluster Size' },
    { key: 'connected_active_users', label: 'Simulated Concurrent Active User Pool' },
  ];

  displayOrder.forEach(({ key, label }) => {
    const m = metrics[key];
    const tr = document.createElement('tr');

    const tdName = document.createElement('td');
    tdName.innerHTML = `<strong>${label}</strong><br><span style="font-size:12px;color:var(--muted);">${m?.description || ''}</span>`;

    const tdValue = document.createElement('td');
    tdValue.className = 'num';
    const valStr = formatMetricValue(m, { showUnit: true });
    tdValue.innerHTML = `<span style="font-weight:700;color:var(--text);font-size:15px;">${valStr}</span>`;

    const tdStatus = document.createElement('td');
    const status = getMetricStatus(m);
    tdStatus.innerHTML = `<span class="stat-chip chip-${status}">${status.toUpperCase()}</span>`;

    const tdSource = document.createElement('td');
    tdSource.innerHTML = `<code style="font-size:11px;color:var(--text-dim);">${m?.source_file || '--'}</code>`;

    tr.appendChild(tdName);
    tr.appendChild(tdValue);
    tr.appendChild(tdStatus);
    tr.appendChild(tdSource);
    tbody.appendChild(tr);
  });
}

/**
 * Render team member cards from team.json.
 * @param {Object} teamData
 */
function renderTeamGrid(teamData) {
  const container = document.getElementById('team-grid');
  if (!container || !teamData || !teamData.members) return;

  container.innerHTML = '';
  teamData.members.forEach((member) => {
    const card = document.createElement('div');
    card.className = 'team-card';

    const initials = member.name.split(' ').map((n) => n[0]).join('');

    card.innerHTML = `
      <div class="team-avatar-placeholder">${initials}</div>
      <div class="team-name">${member.name}</div>
      <div class="team-module">${member.module}</div>
      <div class="team-role">${member.role}</div>
      <p style="font-size:12px;color:var(--muted);margin:0;line-height:1.4;">${member.focus}</p>
    `;
    container.appendChild(card);
  });
}
