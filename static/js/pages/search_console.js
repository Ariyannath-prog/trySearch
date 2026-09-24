/* Analytics / Traffic Intelligence page (/search-console). One data source:
   GET .../search-console, which is app/integrations/gsc.py::gsc_report() -
   OAuth connection status, the latest sync's aggregated clicks/impressions/
   CTR/position, top queries and top pages (grouped in Python from the same
   stored (query,page) rows - no second fetch), and sync history. Device and
   country are not collected by the existing sync (it requests only the
   query+page dimensions from the Search Console API), so this page says so
   plainly rather than showing an empty chart that looks like a bug. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    sc: null,
    banner: null,       // {kind, message} from the OAuth-callback redirect, if any
    querySearch: '',
    pageSearch: ''
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function text(el, value) { el.textContent = value == null ? '' : value; }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function fmtDateTime(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }
  function fmtNum(v) { return v == null ? '—' : Number(v).toLocaleString(); }
  function fmtPct(v) { return v == null ? '—' : v + '%'; }
  function fmtPos(v) { return v == null ? '—' : v.toFixed(1); }
  function isoDate(d) { return d.toISOString().slice(0, 10); }
  function defaultDateRange() {
    var end = new Date(); end.setUTCDate(end.getUTCDate() - 3);
    var start = new Date(end); start.setUTCDate(start.getUTCDate() - 27);
    return { start: isoDate(start), end: isoDate(end) };
  }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Analytics</h1><p>Organic search performance for <span id="project-name"></span>, from Google Search Console.</p></div></div>' +
    '<div class="empty" id="oauth-banner" hidden></div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Google Search Console</h4></div>' +
    '  <div class="pb" id="connection-body"></div>' +
    '</div>' +

    '<div id="sc-content" hidden>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Sync</h4></div>' +
    '    <div class="pb">' +
    '      <div class="field">' +
    '        <input type="date" id="sync-start-date">' +
    '        <input type="date" id="sync-end-date">' +
    '        <button class="btn sm" id="sync-now-btn">Sync now</button>' +
    '      </div>' +
    '      <p class="hint" id="sync-status" style="margin-top:8px"></p>' +
    '      <p class="form-error" id="sync-error" hidden></p>' +
    '    </div>' +
    '  </div>' +

    '  <div class="cols c4">' +
    '    <div class="panel"><div class="pb"><span class="hint">Clicks</span><h2 id="stat-clicks">—</h2></div></div>' +
    '    <div class="panel"><div class="pb"><span class="hint">Impressions</span><h2 id="stat-impressions">—</h2></div></div>' +
    '    <div class="panel"><div class="pb"><span class="hint">CTR</span><h2 id="stat-ctr">—</h2></div></div>' +
    '    <div class="panel"><div class="pb"><span class="hint">Avg. position</span><h2 id="stat-position">—</h2></div></div>' +
    '  </div>' +
    '  <p class="hint" id="metrics-empty-hint" hidden>No synced data yet. Choose a date range above and sync.</p>' +

    '  <div class="cols c2">' +
    '    <div class="panel">' +
    '      <div class="ph"><h4>Top queries</h4></div>' +
    '      <div class="pb"><input type="text" id="query-search-input" placeholder="Search queries&hellip;" style="max-width:220px"></div>' +
    '      <div class="tw"><table class="data cards"><thead><tr>' +
    '        <th>Query</th><th>Clicks</th><th>Impressions</th><th>CTR</th><th>Position</th>' +
    '      </tr></thead><tbody id="top-queries-body"></tbody></table></div>' +
    '      <div class="empty" id="top-queries-empty" hidden><p>No queries recorded yet.</p></div>' +
    '    </div>' +
    '    <div class="panel">' +
    '      <div class="ph"><h4>Top pages</h4></div>' +
    '      <div class="pb"><input type="text" id="page-search-input" placeholder="Search pages&hellip;" style="max-width:220px"></div>' +
    '      <div class="tw"><table class="data cards"><thead><tr>' +
    '        <th>Page</th><th>Clicks</th><th>Impressions</th><th>CTR</th><th>Position</th>' +
    '      </tr></thead><tbody id="top-pages-body"></tbody></table></div>' +
    '      <div class="empty" id="top-pages-empty" hidden><p>No pages recorded yet.</p></div>' +
    '    </div>' +
    '  </div>' +

    '  <div class="panel">' +
    '    <div class="ph"><h4>Sync history</h4></div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>Date range</th><th>Status</th><th>Clicks</th><th>Impressions</th><th>CTR</th><th>Position</th>' +
    '    </tr></thead><tbody id="history-body"></tbody></table></div>' +
    '    <div class="empty" id="history-empty" hidden><p>No syncs yet.</p></div>' +
    '  </div>' +

    '  <div class="panel">' +
    '    <div class="ph"><h4>Device &amp; country breakdown</h4></div>' +
    '    <div class="pb"><div class="empty"><b>Not collected yet</b>' +
    '      <p>This workspace’s Search Console sync currently requests the query and page dimensions only, ' +
    '      so device and country are not available to break down. Nothing is fabricated here.</p></div></div>' +
    '  </div>' +
    '</div>';

  /* ------------------------------------------------------------- boot -- */

  function adoptProjectFromQuery() {
    var params = new URLSearchParams(window.location.search);
    var projectId = params.get('project');
    if (projectId) {
      try { localStorage.setItem('ts_active_project_id', projectId); } catch (e) { /* ignore */ }
    }
    var gsc = params.get('gsc');
    if (gsc === 'connected') {
      state.banner = { kind: 'good', message: 'Google Search Console connected.' };
    } else if (gsc === 'denied') {
      state.banner = { kind: 'warn', message: 'Google Search Console connection was cancelled.' };
    } else if (gsc === 'error') {
      state.banner = { kind: 'bad', message: 'Could not connect Google Search Console' +
        (params.get('message') ? ': ' + params.get('message') : '.') };
    }
    if (gsc) {
      var url = new URL(window.location.href);
      url.search = '';
      window.history.replaceState({}, '', url.toString());
    }
  }

  function boot() {
    adoptProjectFromQuery();
    var views = $('#views');
    views.classList.add('skel');
    TS.api.listProjects().then(function (res) {
      views.classList.remove('skel');
      if (!res.ok) {
        views.innerHTML = '<div class="empty"><b>Something went wrong</b><p>Could not load your projects. Try reloading the page.</p></div>';
        return;
      }
      var projects = (res.body && res.body.projects) || [];
      if (!projects.length) {
        views.innerHTML = '<div class="empty" style="max-width:480px;margin:8vh auto 0">' +
          '<b>No project yet</b><p>Add a website first.</p>' +
          '<a class="btn" href="/onboarding" style="margin-top:10px;display:inline-block">Get started</a></div>';
        return;
      }
      var activeId = TS.shell.activeProjectId();
      var initial = projects.filter(function (p) { return String(p.id) === String(activeId); })[0] || projects[0];
      views.innerHTML = VIEWS_HTML;
      var range = defaultDateRange();
      $('#sync-start-date').value = range.start;
      $('#sync-end-date').value = range.end;
      wireControls();
      renderBanner();
      loadWorkspace(initial.id);
    });
  }

  function loadWorkspace(id) {
    state.workspaceId = id;
    TS.api.getSearchConsole(id).then(function (res) {
      if (!res.ok) return;
      state.sc = res.body.search_console;
      state.project = res.body.project;
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderBanner() {
    var el = $('#oauth-banner');
    if (!state.banner) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = '<b>' + esc(state.banner.message) + '</b>';
  }

  function renderAll() {
    renderConnection();
    var content = $('#sc-content');
    var connected = state.sc && state.sc.status === 'connected' && state.sc.property;
    content.hidden = !connected;
    if (!connected) return;
    renderMetrics();
    renderTopQueries();
    renderTopPages();
    renderHistory();
  }

  function renderConnection() {
    var body = $('#connection-body');
    var sc = state.sc;
    if (!sc.configured) {
      body.innerHTML = '<div class="empty"><b>Not available</b><p>Google Search Console is not configured on this server. Ask an admin to set it up.</p></div>';
      return;
    }
    if (sc.status === 'disconnected' || !sc.property && !(sc.properties || []).length) {
      body.innerHTML = '<p class="hint" style="margin:0 0 10px">Connect Google Search Console to see organic search traffic for this project.</p>' +
        '<a class="btn sm" id="connect-gsc-btn" href="/api/analytics/integrations/google/start?workspace_id=' + state.workspaceId + '">Connect Google Search Console</a>';
      return;
    }
    var propertyOptions = (sc.properties || []).map(function (p) {
      return '<option value="' + esc(p.site_url) + '"' + (p.site_url === sc.property ? ' selected' : '') + '>' + esc(p.site_url) + '</option>';
    }).join('');
    var statusBadge = sc.status === 'connected'
      ? '<span class="badge yes">Connected</span>'
      : '<span class="badge no">' + esc(sc.status) + '</span>';
    body.innerHTML =
      '<div class="field" style="margin-bottom:8px">' + statusBadge +
      (sc.properties && sc.properties.length > 1
        ? '<select id="property-select">' + propertyOptions + '</select>'
        : (sc.property ? '<span class="hint">' + esc(sc.property) + '</span>' : '')) +
      '<button type="button" class="btn ghost sm" id="disconnect-gsc-btn" style="margin-left:auto">Disconnect</button>' +
      '</div>' +
      (sc.last_error ? '<p class="form-error">' + esc(sc.last_error) + '</p>' : '');
  }

  function renderMetrics() {
    var m = state.sc.metrics;
    var emptyHint = $('#metrics-empty-hint');
    if (!m) {
      text($('#stat-clicks'), '—'); text($('#stat-impressions'), '—');
      text($('#stat-ctr'), '—'); text($('#stat-position'), '—');
      emptyHint.hidden = false;
      return;
    }
    emptyHint.hidden = true;
    text($('#stat-clicks'), fmtNum(m.clicks));
    text($('#stat-impressions'), fmtNum(m.impressions));
    text($('#stat-ctr'), fmtPct(m.ctr));
    text($('#stat-position'), fmtPos(m.position));
  }

  function renderRankedTable(bodyId, emptyId, rows, dimension, search) {
    var body = $(bodyId), empty = $(emptyId);
    var q = search.trim().toLowerCase();
    var list = (rows || []).filter(function (r) {
      return !q || String(r[dimension] || '').toLowerCase().indexOf(q) !== -1;
    });
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (r) {
      var tr = document.createElement('tr');
      var label = r[dimension] || '—';
      tr.innerHTML =
        '<td data-l="' + (dimension === 'query' ? 'Query' : 'Page') + '"><span title="' + esc(label) + '">' +
        esc(label.length > 60 ? label.slice(0, 57) + '…' : label) + '</span></td>' +
        '<td data-l="Clicks">' + fmtNum(r.clicks) + '</td>' +
        '<td data-l="Impressions">' + fmtNum(r.impressions) + '</td>' +
        '<td data-l="CTR">' + fmtPct(r.ctr) + '</td>' +
        '<td data-l="Position">' + fmtPos(r.position) + '</td>';
      body.appendChild(tr);
    });
  }

  function renderTopQueries() {
    renderRankedTable('#top-queries-body', '#top-queries-empty', state.sc.top_queries, 'query', state.querySearch);
  }

  function renderTopPages() {
    renderRankedTable('#top-pages-body', '#top-pages-empty', state.sc.top_pages, 'page', state.pageSearch);
  }

  function renderHistory() {
    var body = $('#history-body'), empty = $('#history-empty');
    var history = state.sc.history || [];
    clearChildren(body);
    if (!history.length) { empty.hidden = false; return; }
    empty.hidden = true;
    history.forEach(function (item) {
      var tr = document.createElement('tr');
      var statusBadge = item.status === 'succeeded' ? 'yes' : (item.status === 'failed' ? 'no' : 'thin');
      var m = item.metrics;
      tr.innerHTML =
        '<td data-l="Date range">' + esc(item.start_date) + ' – ' + esc(item.end_date) + '</td>' +
        '<td data-l="Status"><span class="badge ' + statusBadge + '">' + esc(item.status) + '</span></td>' +
        '<td data-l="Clicks">' + (m ? fmtNum(m.clicks) : '—') + '</td>' +
        '<td data-l="Impressions">' + (m ? fmtNum(m.impressions) : '—') + '</td>' +
        '<td data-l="CTR">' + (m ? fmtPct(m.ctr) : '—') + '</td>' +
        '<td data-l="Position">' + (m ? fmtPos(m.position) : '—') + '</td>';
      body.appendChild(tr);
    });
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#connection-body').addEventListener('click', function (evt) {
      if (evt.target.closest('#disconnect-gsc-btn')) {
        if (!window.confirm('Disconnect Google Search Console from this project?')) return;
        TS.api.disconnectSearchConsole(state.workspaceId).then(function () { loadWorkspace(state.workspaceId); });
      }
    });

    $('#connection-body').addEventListener('change', function (evt) {
      if (evt.target.id === 'property-select') {
        TS.api.selectSearchConsoleProperty(state.workspaceId, evt.target.value)
          .then(function (res) { if (res.ok) { state.sc = res.body.search_console; renderAll(); } });
      }
    });

    $('#sync-now-btn').addEventListener('click', function () {
      var errorEl = $('#sync-error'), statusEl = $('#sync-status');
      errorEl.hidden = true;
      text(statusEl, 'Syncing…');
      $('#sync-now-btn').disabled = true;
      TS.api.syncSearchConsole(state.workspaceId, {
        start_date: $('#sync-start-date').value, end_date: $('#sync-end-date').value,
      }).then(function (res) {
        $('#sync-now-btn').disabled = false;
        if (res.ok) {
          text(statusEl, 'Synced.');
          state.sc = res.body.search_console;
          renderAll();
        } else {
          text(statusEl, '');
          text(errorEl, (res.body && res.body.error) || 'Sync failed.');
          errorEl.hidden = false;
        }
      });
    });

    $('#query-search-input').addEventListener('input', function (evt) {
      state.querySearch = evt.target.value;
      renderTopQueries();
    });

    $('#page-search-input').addEventListener('input', function (evt) {
      state.pageSearch = evt.target.value;
      renderTopPages();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
