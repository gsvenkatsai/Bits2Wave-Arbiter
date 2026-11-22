/**
 * PROJECT ARBITER — Team Page Logic & Member Roster
 * Module: arbiter/dashboard/site/js/team.js
 */

import { initNavbar, injectIcons } from './components.js';
import { loadTeam } from './evidence.js';

document.addEventListener('DOMContentLoaded', async () => {
  initNavbar();
  injectIcons();

  const teamData = await loadTeam('./data/team.json');
  renderTeamMembers(teamData);
});

/**
 * Generate 2-letter uppercase initials from full name.
 * @param {string} name
 * @returns {string}
 */
export function getInitials(name) {
  if (!name) return 'AR';
  const parts = name.trim().split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * Render dynamic team member cards.
 * @param {Object} teamData
 */
export function renderTeamMembers(teamData) {
  const container = document.getElementById('team-members-container');
  if (!container || !teamData || !teamData.members) return;

  container.innerHTML = '';

  teamData.members.forEach((m) => {
    const card = document.createElement('article');
    card.className = 'team-member-card';
    card.setAttribute('data-member-name', m.name);

    const initials = getInitials(m.name);

    card.innerHTML = `
      <div class="member-header">
        <div class="member-avatar" aria-hidden="true">${initials}</div>
        <div class="member-meta">
          <h3 class="member-name">${m.name}</h3>
          <span class="member-role">${m.role}</span>
        </div>
      </div>
      <div class="member-module-badge">
        <span data-icon="shield"></span>
        <span>${m.module}</span>
      </div>
      <p class="member-focus">${m.focus}</p>
    `;

    container.appendChild(card);
  });

  injectIcons();
}
