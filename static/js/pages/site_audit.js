/* Site Audit page controller (/site-audit). One data source: GET
   .../audit, which is app/jobs.py::latest_site_audit() as-is - run, pages,
   findings, sitemaps and history are already fully computed by the crawler
   and scoring modules. Nothing here recomputes a score or re-derives a
   finding; this page only searches, filters, sorts and displays what the
   backend already stored. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    audit: null,        // latest_site_audit() payload, or null if never run
    activeJob: null,
    pollTimer: null,
    findingSearch: '',
    areaFilter: null,
    severityFilter: null,
    pageSearch: '',
    failedOnly: false
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
  function fmtScore(v) { return v == null ? '—' : String(v); }
  function sevClass(sev) {
    if (sev === 'critical' || sev === 'high') return 'high';
    if (sev === 'medium') return 'med';
    return 'low';
  }

  var VIEWS_HTML =
    '<div class="phead">' +
    '  <div><h1>Site Audit</h1><p>Crawlability, metadata and structured-data health for <span id="project-name"></span>.</p></div>' +
    '  <button class="btn" id="run-audit-btn">Run new audit</button>' +
    '</div>' +
    '<p class="hint" id="audit-status"></p>' +
    '<div class="empty" id="state-banner" hidden></div>' +

    '<div id="audit-content" hidden>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Crawl status</h4></div>' +
    '    <div class="pb">' +
    '      <dl class="kv">' +
    '        <dt>Status</dt><dd id="crawl-status-badge"></dd>' +
    '        <dt>Pages discovered</dt><dd id="pages-discovered"></dd>' +
    '        <dt>Pages audited</dt><dd id="pages-audited"></dd>' +
    '        <dt>Pages failed</dt><dd id="pages-failed"></dd>' +
    '        <dt>Start URL</dt><dd id="start-url"></dd>' +
    '        <dt>Completed</dt><dd id="completed-at"></dd>' +
    '      </dl>' +
    '    </div>' +
    '  </div>' +

    '  <div class="cols c4">' +
    '    <div class="panel"><div class="pb"><span class="hint">Readiness</span><h2 id="score-readiness"></h2></div></div>' +
    '    <div class="panel"><div class="pb"><span class="hint">Metadata</span><h2 id="score-metadata"></h2></div></div>' +
    '    <div class="panel"><div class="pb"><span class="hint">Crawlability</span><h2 id="score-crawlability"></h2></div></div>' +
    '    <div class="panel"><div class="pb"><span class="hint">Structured data</span><h2 id="score-structured"></h2></div></div>' +
    '  </div>' +

    '  <div class="panel">' +
    '    <div class="ph"><h4>Readiness trend</h4></div>' +
    '    <div class="pb" id="trend-container"></div>' +
    '  </div>' +

    '  <div class="panel">' +
    '    <div class="ph"><h4>Findings</h4><span class="badge thin" id="findings-count-badge"></span></div>' +
    '    <div class="pb">' +
    '      <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '        <input type="text" id="finding-search-input" placeholder="Search findings or URLs&hellip;" style="max-width:260px">' +
    '        <div id="severity-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '      </div>' +
    '      <div id="area-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px"></div>' +
    '    </div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>Severity</th><th>Area</th><th>Issue</th><th>Affected URL</th><th></th>' +
    '    </tr></thead><tbody id="findings-table-body"></tbody></table></div>' +
    '    <div class="empty" id="findings-empty" hidden><p>No findings match.</p></div>' +
    '  </div>' +

    '  <div class="panel">' +
    '    <div class="ph"><h4>Pages crawled</h4><span class="badge thin" id="pages-count-badge"></span></div>' +
    '    <div class="pb">' +
    '      <div class="field">' +
    '        <input type="text" id="page-search-input" placeholder="Search URLs&hellip;" style="max-width:260px">' +
    '        <button type="button" class="chip" id="failed-only-chip">Failed only</button>' +
    '      </div>' +
    '    </div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>URL</th><th>HTTP status</th><th>Readiness</th><th>Issues</th>' +
    '    </tr></thead><tbody id="pages-table-body"></tbody></table></div>' +
    '    <div class="empty" id="pages-empty" hidden><p>No pages match.</p></div>' +
    '  </div>' +
    '</div>';

  /* ------------------------------------------------------------- boot -- */

  function boot() {
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
      wireControls();
      loadWorkspace(initial.id);
    });
  }

  function loadWorkspace(id) {
    state.workspaceId = id;
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    refresh();
  }

  function refresh() {
    TS.api.getAudit(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      state.project = res.body.project;
      state.audit = res.body.audit;
      state.activeJob = res.body.active_job;
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      handleActiveJob(state.activeJob);
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderStateBanner();
    var content = $('#audit-content');
    if (!state.audit) {
      content.hidden = true;
      return;
    }
    content.hidden = false;
    renderCrawlStatus();
    renderScoreTiles();
    renderTrend();
    renderSeverityChips();
    renderAreaChips();
    renderFindingsTable();
    renderPagesTable();
  }

  function renderStateBanner() {
    var banner = $('#state-banner');
    if (state.activeJob) {
      banner.hidden = false;
      banner.innerHTML = '<b>Audit running</b><p>Crawling the site now. Results below are from the last completed run and will update automatically.</p>';
    } else if (!state.audit) {
      banner.hidden = false;
      banner.innerHTML = '<b>No audit yet</b><p>Run your first site audit to see crawlability, metadata and structured-data health.</p>';
    } else {
      banner.hidden = true;
    }
  }

  function renderCrawlStatus() {
    var run = state.audit.run;
    var badgeClass = run.status === 'succeeded' ? 'yes' : (run.status === 'failed' ? 'no' : 'thin');
    $('#crawl-status-badge').innerHTML = '<span class="badge ' + badgeClass + '">' + esc(run.status) + '</span>';
    text($('#pages-discovered'), String(run.pages_discovered || 0));
    text($('#pages-audited'), String(run.pages_audited || 0));
    text($('#pages-failed'), String(run.pages_failed || 0));
    text($('#start-url'), run.final_url || run.start_url || '—');
    text($('#completed-at'), fmtDateTime(run.completed_at));
  }

  function renderScoreTiles() {
    var run = state.audit.run;
    text($('#score-readiness'), fmtScore(run.readiness_score));
    text($('#score-metadata'), fmtScore(run.metadata_score));
    text($('#score-crawlability'), fmtScore(run.crawlability_score));
    text($('#score-structured'), fmtScore(run.structured_data_score));
  }

  function renderTrend() {
    var series = (state.audit.history || []).map(function (row) {
      return { date: row.created_at, visibility_score: row.readiness_score };
    });
    TS.renderTrendChart($('#trend-container'), series);
  }

  function renderSeverityChips() {
    var container = $('#severity-filter-chips');
    var chips = [
      { id: null, label: 'All' }, { id: 'critical', label: 'Critical' },
      { id: 'high', label: 'High' }, { id: 'medium', label: 'Medium' }, { id: 'low', label: 'Low' }
    ];
    container.innerHTML = chips.map(function (c) {
      var on = state.severityFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-severity-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function allAreas() {
    var seen = {};
    (state.audit.findings || []).forEach(function (f) { seen[f.area] = true; });
    return Object.keys(seen).sort();
  }

  function renderAreaChips() {
    var container = $('#area-filter-chips');
    var areas = allAreas();
    var chips = [{ id: null, label: 'All areas' }].concat(areas.map(function (a) { return { id: a, label: a }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.areaFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-area-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function pageUrlFor(pageId) {
    var page = (state.audit.pages || []).filter(function (p) { return p.id === pageId; })[0];
    return page ? (page.final_url || page.url) : null;
  }

  function filteredFindings() {
    var q = state.findingSearch.trim().toLowerCase();
    return (state.audit.findings || []).filter(function (f) {
      if (state.severityFilter != null && f.severity !== state.severityFilter) return false;
      if (state.areaFilter != null && f.area !== state.areaFilter) return false;
      if (q) {
        var url = pageUrlFor(f.page_id) || '';
        var hay = (f.code || '') + ' ' + (f.evidence || '') + ' ' + url;
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function renderFindingsTable() {
    var body = $('#findings-table-body'), empty = $('#findings-empty');
    var list = filteredFindings();
    text($('#findings-count-badge'), (state.audit.findings || []).length + ' findings');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (f) {
      var url = pageUrlFor(f.page_id);
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Severity"><span class="sev ' + sevClass(f.severity) + '">' + esc(f.severity) + '</span></td>' +
        '<td data-l="Area">' + esc(f.area) + '</td>' +
        '<td data-l="Issue">' + esc(f.code) + '</td>' +
        '<td data-l="Affected URL">' + (url ? '<span title="' + esc(url) + '">' + esc(url.length > 50 ? url.slice(0, 47) + '…' : url) + '</span>' : '<span class="hint">Site-wide</span>') + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-finding="' + f.id + '">View</button></td>';
      body.appendChild(tr);
    });
  }

  function filteredPages() {
    var q = state.pageSearch.trim().toLowerCase();
    return (state.audit.pages || []).filter(function (p) {
      if (state.failedOnly && p.fetched) return false;
      if (q && (p.url || '').toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
  }

  function renderPagesTable() {
    var body = $('#pages-table-body'), empty = $('#pages-empty');
    var list = filteredPages();
    text($('#pages-count-badge'), (state.audit.pages || []).length + ' pages');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (p) {
      var tr = document.createElement('tr');
      var statusBadge = p.fetched
        ? '<span class="badge yes">' + (p.http_status || '—') + '</span>'
        : '<span class="badge no">' + (p.http_status || 'Failed') + '</span>';
      tr.innerHTML =
        '<td data-l="URL"><span title="' + esc(p.url) + '">' + esc(p.url.length > 60 ? p.url.slice(0, 57) + '…' : p.url) + '</span></td>' +
        '<td data-l="HTTP status">' + statusBadge + '</td>' +
        '<td data-l="Readiness">' + fmtScore(p.readiness_score) + '</td>' +
        '<td data-l="Issues">' + (p.issues_count || 0) + '</td>';
      body.appendChild(tr);
    });
  }

  /* ------------------------------------------------------------ drawer -- */

  function openFindingDrawer(findingId) {
    var finding = (state.audit.findings || []).filter(function (f) { return f.id === findingId; })[0];
    if (!finding) return;
    text($('#drawer-title'), finding.code);
    var url = pageUrlFor(finding.page_id);
    $('#drawer-body').innerHTML =
      '<div class="panel"><div class="ph"><h4>' + esc(finding.area) + '</h4>' +
      '<span class="sev ' + sevClass(finding.severity) + '">' + esc(finding.severity) + '</span></div>' +
      '<div class="pb">' +
      '<p><b>Evidence</b><br>' + esc(finding.evidence) + '</p>' +
      '<p><b>Recommendation</b><br>' + esc(finding.recommendation) + '</p>' +
      (url ? '<p><b>Affected URL</b><br><a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(url) + '</a></p>'
           : '<p class="hint">This finding applies to the site as a whole, not one page.</p>') +
      '</div></div>';
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
  }

  function closeDrawer() {
    $('#drawer-scrim').classList.remove('open');
    $('#answer-drawer').classList.remove('open');
  }

  /* --------------------------------------------------------------- audit */

  function handleActiveJob(job) {
    if (job) {
      $('#run-audit-btn').disabled = true;
      text($('#audit-status'), 'An audit is running…');
      state.pollTimer = setTimeout(refresh, 5000);
    } else {
      $('#run-audit-btn').disabled = false;
      text($('#audit-status'), '');
      if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    }
  }

  function handleAuditResponse(res) {
    var statusEl = $('#audit-status');
    if (res.status === 202) {
      text(statusEl, 'Audit queued…');
      refresh();
      return;
    }
    text(statusEl, (res.body && res.body.error) || 'Could not start an audit.');
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#run-audit-btn').addEventListener('click', function () {
      TS.api.startAudit(state.workspaceId).then(handleAuditResponse);
    });

    $('#finding-search-input').addEventListener('input', function (evt) {
      state.findingSearch = evt.target.value;
      renderFindingsTable();
    });

    $('#severity-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-severity-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-severity-filter');
      state.severityFilter = raw === '' ? null : raw;
      renderSeverityChips();
      renderFindingsTable();
    });

    $('#area-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-area-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-area-filter');
      state.areaFilter = raw === '' ? null : raw;
      renderAreaChips();
      renderFindingsTable();
    });

    $('#findings-table-body').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-view-finding]');
      if (!btn) return;
      openFindingDrawer(Number(btn.getAttribute('data-view-finding')));
    });

    $('#page-search-input').addEventListener('input', function (evt) {
      state.pageSearch = evt.target.value;
      renderPagesTable();
    });

    $('#failed-only-chip').addEventListener('click', function () {
      state.failedOnly = !state.failedOnly;
      $('#failed-only-chip').classList.toggle('on', state.failedOnly);
      renderPagesTable();
    });

    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-scrim').addEventListener('click', closeDrawer);
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
