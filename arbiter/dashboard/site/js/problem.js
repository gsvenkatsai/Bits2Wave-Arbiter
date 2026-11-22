/**
 * PROJECT ARBITER — Problem Page Logic & Evidence Binding
 * Module: arbiter/dashboard/site/js/problem.js
 */

import { initNavbar, injectIcons } from './components.js';
import { loadEvidence, populateEvidenceDOM } from './evidence.js';

document.addEventListener('DOMContentLoaded', async () => {
  // 1. Initialize Shared Navbar & Icons
  initNavbar();
  injectIcons();

  // 2. Load Empirical Evidence & Populate Metric Data-Attributes
  const evidence = await loadEvidence('./data/evidence.json');
  populateEvidenceDOM(document, evidence);
});
