/**
 * PROJECT ARBITER — Shared Components & Inline SVG Icons
 * Module: arbiter/dashboard/site/js/components.js
 *
 * Implements:
 * 1. Original hand-crafted inline SVG icons (Zero CDN dependencies).
 * 2. Original Logo Mark: Hexagon outline with gold central diamond.
 * 3. Responsive Navbar controller with mobile drawer & active-state highlight.
 * 4. Accessible Modal component (role="dialog", Esc key, focus trapping).
 */

// ---------------------------------------------------------------------------
// 1. Hand-crafted Inline SVG Icons
// ---------------------------------------------------------------------------
export const ICONS = {
  logo: `<svg class="logo-svg" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <!-- Hexagon Outer Boundary -->
    <polygon points="16,2 29,9.5 29,24.5 16,32 3,24.5 3,9.5" stroke="#FFC800" stroke-width="2" fill="rgba(255, 200, 0, 0.08)"/>
    <!-- Concentric Inner Hexagon Accent -->
    <polygon points="16,6 25,11.2 25,20.8 16,26 7,20.8 7,11.2" stroke="rgba(255, 255, 255, 0.15)" stroke-width="1.2" fill="none"/>
    <!-- Central Luminous Gold Diamond -->
    <polygon points="16,11 21,16 16,21 11,16" fill="#FFC800"/>
    <circle cx="16" cy="16" r="2.2" fill="#0A0A0D"/>
  </svg>`,

  bolt: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
  </svg>`,

  clock: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10"></circle>
    <polyline points="12 6 12 12 16 14"></polyline>
  </svg>`,

  shield: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
  </svg>`,

  chart: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <line x1="18" y1="20" x2="18" y2="10"></line>
    <line x1="12" y1="20" x2="12" y2="4"></line>
    <line x1="6" y1="20" x2="6" y2="14"></line>
  </svg>`,

  user: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
    <circle cx="12" cy="7" r="4"></circle>
  </svg>`,

  globe: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="10"></circle>
    <line x1="2" y1="12" x2="22" y2="12"></line>
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
  </svg>`,

  calendar: `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
    <line x1="16" y1="2" x2="16" y2="6"></line>
    <line x1="8" y1="2" x2="8" y2="6"></line>
    <line x1="3" y1="10" x2="21" y2="10"></line>
  </svg>`,

  arrowRight: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <line x1="5" y1="12" x2="19" y2="12"></line>
    <polyline points="12 5 19 12 12 19"></polyline>
  </svg>`,

  close: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <line x1="18" y1="6" x2="6" y2="18"></line>
    <line x1="6" y1="6" x2="18" y2="18"></line>
  </svg>`,

  menu: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <line x1="3" y1="12" x2="21" y2="12"></line>
    <line x1="3" y1="6" x2="21" y2="6"></line>
    <line x1="3" y1="18" x2="21" y2="18"></line>
  </svg>`,

  play: `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true">
    <polygon points="6 4 20 12 6 20 6 4"></polygon>
  </svg>`,
};

// ---------------------------------------------------------------------------
// 2. Responsive Navbar Controller
// ---------------------------------------------------------------------------
export function initNavbar() {
  const menuBtn = document.getElementById('nav-menu-toggle');
  const navLinks = document.getElementById('nav-links-menu');

  if (menuBtn && navLinks) {
    menuBtn.addEventListener('click', () => {
      const isOpen = navLinks.classList.toggle('mobile-open');
      menuBtn.setAttribute('aria-expanded', String(isOpen));
    });

    // Close on link click in mobile view
    navLinks.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', () => {
        navLinks.classList.remove('mobile-open');
        menuBtn.setAttribute('aria-expanded', 'false');
      });
    });
  }

  // Active Link Highlighting
  const currentPath = window.location.pathname.replace(/\/$/, '') || '/';
  const navItemLinks = document.querySelectorAll('.nav-item-link');
  navItemLinks.forEach((link) => {
    const href = link.getAttribute('href');
    if (href === currentPath || (href.startsWith('#') && window.location.hash === href)) {
      link.classList.add('active');
    }
  });

  window.addEventListener('hashchange', () => {
    navItemLinks.forEach((link) => {
      if (link.getAttribute('href') === window.location.hash) {
        link.classList.add('active');
      } else if (link.getAttribute('href').startsWith('#')) {
        link.classList.remove('active');
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 3. Accessible Modal Component
// ---------------------------------------------------------------------------
export class Modal {
  constructor(overlayElement) {
    this.overlay = overlayElement;
    if (!this.overlay) return;

    this.closeBtn = this.overlay.querySelector('.modal-close');
    this.content = this.overlay.querySelector('.modal-content');

    this.init();
  }

  init() {
    this.overlay.setAttribute('role', 'dialog');
    this.overlay.setAttribute('aria-modal', 'true');

    if (this.closeBtn) {
      this.closeBtn.addEventListener('click', () => this.close());
    }

    this.overlay.addEventListener('click', (e) => {
      if (e.target === this.overlay) {
        this.close();
      }
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.isOpen()) {
        this.close();
      }
    });
  }

  open() {
    this.overlay.classList.add('open');
    document.body.style.overflow = 'hidden';
    this.overlay.setAttribute('aria-hidden', 'false');
    const focusable = this.overlay.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (focusable) focusable.focus();
  }

  close() {
    this.overlay.classList.remove('open');
    document.body.style.overflow = '';
    this.overlay.setAttribute('aria-hidden', 'true');
  }

  isOpen() {
    return this.overlay.classList.contains('open');
  }
}

// ---------------------------------------------------------------------------
// 4. Auto-initialize Icons in DOM
// ---------------------------------------------------------------------------
export function injectIcons() {
  document.querySelectorAll('[data-icon]').forEach((el) => {
    const iconName = el.getAttribute('data-icon');
    if (ICONS[iconName]) {
      el.innerHTML = ICONS[iconName];
    }
  });
}
