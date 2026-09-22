/* Shared authenticated-app shell: rail nav, tab bar, workspace switcher, theme
   toggle. Each page sets <body data-view="X"> to tell this module which nav
   entry is active; nav items are real cross-page links (each page is its own
   URL), not client-side view-switches. Icon paths for shared views are
   copied verbatim from index.html's IC dictionary; new views (Competitors,
   Sentiment, Search Console, Knowledge, Settings) get new icons drawn in the
   same minimal line-icon style. Requires api.js to be loaded first. */
(function () {
  'use strict';

  window.TS = window.TS || {};

  var ICONS = {
    overview: '<path d="M2 12.5h12M3.5 12.5V7M7 12.5V3.5M10.5 12.5V9M14 12.5V5.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    prompts: '<path d="M2.5 4h11M2.5 8h11M2.5 12h7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    answers: '<path d="M13.5 3.5v6a1 1 0 0 1-1 1H6l-3 2.5v-9.5a1 1 0 0 1 1-1h8.5a1 1 0 0 1 1 1Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>',
    sources: '<path d="M6.5 9.5 9.5 6.5M5.5 11.5 4 13a2.5 2.5 0 0 1-3.5-3.5l3-3M10.5 4.5 12 3a2.5 2.5 0 0 1 3.5 3.5l-3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
    traffic: '<path d="M2 13.5 6 8l3 3 5-7.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    actions: '<path d="M3 8.5l3 3 7-7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    agent: '<circle cx="8" cy="8" r="5.6" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="8" r="1.9" fill="currentColor"/>',
    content: '<path d="M4 2.5h5.5L12.5 5.5V13a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5ZM5.5 8h5M5.5 10.5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    sitehealth: '<path d="M8 2 3 4v4.2C3 11 5.1 13.3 8 14c2.9-.7 5-3 5-5.8V4L8 2Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M6 8l1.6 1.6L10.4 6.8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    reports: '<path d="M4 2.5h5.5L12.5 5.5V13a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M6 11V8.5M8 11V7M10 11V9.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    clients: '<rect x="2" y="5.5" width="5" height="8" rx="1" stroke="currentColor" stroke-width="1.3"/><rect x="9" y="2.5" width="5" height="11" rx="1" stroke="currentColor" stroke-width="1.3"/>',
    /* new, matching the same minimal geometric style */
    competitors: '<circle cx="6" cy="8" r="4" stroke="currentColor" stroke-width="1.3"/><circle cx="10" cy="8" r="4" stroke="currentColor" stroke-width="1.3"/>',
    sentiment: '<path d="M2.5 11a5.5 5.5 0 0 1 11 0" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M8 11l2.6-3.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    'search-console': '<circle cx="7" cy="7" r="4" stroke="currentColor" stroke-width="1.3"/><path d="M10.2 10.2 13.5 13.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    knowledge: '<path d="M2.5 4c1.8-.8 3.7-.8 5.5 0v8c-1.8-.8-3.7-.8-5.5 0V4ZM13.5 4c-1.8-.8-3.7-.8-5.5 0v8c1.8-.8 3.7-.8 5.5 0V4Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
    settings: '<circle cx="8" cy="8" r="2.2" stroke="currentColor" stroke-width="1.3"/><path d="M8 2.8v1.6M8 11.6v1.6M13.2 8h-1.6M4.4 8H2.8M11.4 4.6l-1.1 1.1M5.7 10.3l-1.1 1.1M11.4 11.4l-1.1-1.1M5.7 5.7 4.6 4.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>'
  };

  var NAV = [
    { v: 'workspace', t: 'Projects', g: 'Workspace', href: '/workspace', icon: 'clients' },
    { v: 'settings', t: 'Settings', href: '/profile', icon: 'settings' },
    { v: 'overview', t: 'Dashboard', g: 'Measure', href: '/analytics', icon: 'overview' },
    { v: 'visibility', t: 'AI Visibility', href: '/visibility-tracking', icon: 'traffic' },
    { v: 'mentions', t: 'Mentions', href: '/mentions', icon: 'answers' },
    { v: 'citations', t: 'Citations & Sources', href: '/citations', icon: 'sources' },
    { v: 'competitors', t: 'Competitors', href: '/competitors', icon: 'competitors' },
    { v: 'sentiment', t: 'Sentiment', href: '/sentiment', icon: 'sentiment' },
    { v: 'prompts', t: 'Prompts', g: 'Track', href: '/prompt-intelligence', icon: 'prompts' },
    { v: 'site-audit', t: 'Site Audit', href: '/site-audit', icon: 'sitehealth' },
    { v: 'search-console', t: 'Analytics', href: '/search-console', icon: 'search-console' },
    { v: 'recommendations', t: 'Recommendations', g: 'Act', href: '/recommendations', icon: 'actions' },
    { v: 'content-studio', t: 'Content Studio', href: '/content-studio', icon: 'content' },
    { v: 'reports', t: 'Reports', href: '/reports', icon: 'reports' },
    { v: 'knowledge', t: 'Knowledge', g: 'Knowledge', href: '/knowledge', icon: 'knowledge' },
    { v: 'agent', t: 'Agent', href: '/agent', icon: 'agent' }
  ];

  var TABS = ['overview', 'prompts', 'visibility', 'mentions'];

  TS.NAV = NAV;

  function esc(s) {
    var div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
  }

  function svg(key) {
    return '<svg class="ic" viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
      (ICONS[key] || ICONS.overview) + '</svg>';
  }

  function renderRail(activeView) {
    var anav = document.getElementById('anav');
    if (!anav) return;
    var html = NAV.map(function (item) {
      var group = item.g ? '<div class="nl lbl">' + esc(item.g) + '</div>' : '';
      var current = item.v === activeView ? ' aria-current="true"' : '';
      return group + '<a href="' + item.href + '"' + current + '>' +
        svg(item.icon) + '<span>' + esc(item.t) + '</span></a>';
    }).join('');
    anav.innerHTML = html;
  }

  function renderTabbar(activeView) {
    var tabbar = document.getElementById('tabbar');
    if (!tabbar) return;
    var html = TABS.map(function (v) {
      var item = NAV.filter(function (n) { return n.v === v; })[0];
      if (!item) return '';
      var current = v === activeView ? ' aria-current="true"' : '';
      return '<a href="' + item.href + '"' + current + '>' + svg(item.icon) +
        esc(item.t) + '</a>';
    }).join('');
    tabbar.innerHTML = html;
  }

  function initThemeToggle() {
    var btn = document.querySelector('[data-theme-toggle]');
    var stored = null;
    try { stored = localStorage.getItem('ts_theme'); } catch (e) { /* private mode etc */ }
    if (stored === 'light' || stored === 'dark') {
      document.documentElement.setAttribute('data-theme', stored);
    }
    if (!btn) return;
    btn.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('ts_theme', next); } catch (e) { /* ignore */ }
    });
  }

  function initSwitcher() {
    var btn = document.getElementById('switcher');
    var menu = document.getElementById('switcher-menu');
    if (!btn || !menu) return;

    function activeId() {
      try { return localStorage.getItem('ts_active_project_id'); } catch (e) { return null; }
    }
    function setActiveId(id) {
      try { localStorage.setItem('ts_active_project_id', String(id)); } catch (e) { /* ignore */ }
    }

    TS.api.listProjects().then(function (res) {
      if (!res.ok) return;
      var projects = (res.body && res.body.projects) || [];
      if (!projects.length) {
        btn.querySelector('b').textContent = 'No projects yet';
        btn.querySelector('small').textContent = 'Add a website to get started';
        btn.addEventListener('click', function () { window.location.href = '/workspace'; });
        return;
      }
      var current = projects.filter(function (p) { return String(p.id) === activeId(); })[0] || projects[0];
      setActiveId(current.id);
      btn.querySelector('b').textContent = current.brand_name || current.domain || ('Project ' + current.id);
      btn.querySelector('small').textContent = current.domain || '';

      if (projects.length > 1) {
        menu.innerHTML = projects.map(function (p) {
          var label = p.brand_name || p.domain || ('Project ' + p.id);
          var cur = String(p.id) === String(current.id) ? ' aria-current="true"' : '';
          return '<button type="button" data-project-id="' + p.id + '"' + cur + '>' +
            esc(label) + '</button>';
        }).join('');
        btn.addEventListener('click', function (evt) {
          evt.stopPropagation();
          menu.classList.toggle('open');
        });
        menu.addEventListener('click', function (evt) {
          var item = evt.target.closest('[data-project-id]');
          if (!item) return;
          setActiveId(item.getAttribute('data-project-id'));
          window.location.reload();
        });
        document.addEventListener('click', function () { menu.classList.remove('open'); });
      }
    });
  }

  function initWho() {
    var nameEl = document.getElementById('who-name');
    var avatarEl = document.getElementById('avatar');
    if (!nameEl && !avatarEl) return;
    TS.api.getMe().then(function (res) {
      if (!res.ok || !res.body || !res.body.logged_in) return;
      var user = res.body.user;
      if (nameEl) nameEl.textContent = user.username;
      if (avatarEl) avatarEl.textContent = (user.username || '?').slice(0, 1).toUpperCase();
    });
    var logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', function () {
        TS.api.logout().then(function () { window.location.href = '/'; });
      });
    }
  }

  TS.shell = {
    renderRail: renderRail,
    renderTabbar: renderTabbar,
    initThemeToggle: initThemeToggle,
    initSwitcher: initSwitcher,
    activeProjectId: function () {
      try { return localStorage.getItem('ts_active_project_id'); } catch (e) { return null; }
    },
    boot: function () {
      var view = document.body.getAttribute('data-view');
      renderRail(view);
      renderTabbar(view);
      initThemeToggle();
      initSwitcher();
      initWho();
    }
  };
})();
