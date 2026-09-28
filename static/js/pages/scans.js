/* Scan History + Scan Detail (/scans). List: GET .../scans, which is
   app/metrics.py::scan_history() - plain columns already stored on
   analytics_prompt_scan_runs, no aggregation. Detail: the existing
   GET .../evidence?run_id=X (app/metrics.py::latest_prompt_evidence(),
   completely unchanged) - one scan_run row is one provider's run of N
   prompts (provider/region are singular per row in this schema), so a
   scan's detail is that provider's answers, sources, competitor
   comparison and opportunities for that specific run. Both views live on
   one page/URL; ?run_id= in the query string opens the detail view
   directly, so a scan is linkable and back-able without a client-side
   router. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    scans: [],
    searchQuery: '',
    providerFilter: null,
    statusFilter: null,
    view: 'list',       // 'list' | 'detail'
    selectedRunId: null,
    detail: null         // latest_prompt_evidence() payload for the selected run
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
  function fmtPct(v) { return v == null ? '—' : v + '%'; }
  function statusBadgeClass(status) {
    if (status === 'succeeded') return 'yes';
    if (status === 'failed') return 'no';
    return 'thin'; // partial, running, queued
  }

  var VIEWS_HTML =
    '<div id="list-view">' +
    '  <div class="phead"><div><h1>Scan History</h1><p>Every prompt scan run for <span id="project-name"></span>.</p></div></div>' +
    '  <div class="panel">' +
    '    <div class="pb">' +
    '      <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '        <input type="text" id="search-input" placeholder="Search by model or region&hellip;" style="max-width:240px">' +
    '        <div id="status-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '      </div>' +
    '      <div id="provider-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '    </div>' +
    '  </div>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Scans</h4><span class="badge thin" id="scan-count-badge"></span></div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>ID</th><th>Date</th><th>Provider</th><th>Region</th><th>Status</th><th>Prompts</th>' +
    '      <th>Mention rate</th><th>Citation rate</th><th>SOV</th><th></th>' +
    '    </tr></thead><tbody id="scans-table-body"></tbody></table></div>' +
    '    <div class="empty" id="scans-empty" hidden><b>No scans have been completed yet.</b><p>Run your first scan to generate analytics.</p></div>' +
    '  </div>' +
    '</div>' +

    '<div id="detail-view" hidden>' +
    '  <div class="phead"><div><button type="button" class="btn ghost sm" id="back-to-list-btn">&larr; All scans</button>' +
    '    <h1 style="margin-top:8px">Scan #<span id="detail-run-id"></span></h1></div></div>' +
    '  <div id="detail-body"></div>' +
    '</div>' +

    '<div class="scrim" id="drawer-scrim"></div>' +
    '<div class="drawer" id="answer-drawer">' +
    '  <div class="dh"><h4 id="drawer-title">Answer</h4><span class="spacer"></span>' +
    '    <button class="iconbtn" id="drawer-close">&times;</button></div>' +
    '  <div class="dbody" id="drawer-body"></div>' +
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
      state.workspaceId = initial.id;
      var requestedRunId = new URLSearchParams(window.location.search).get('run_id');
      loadList().then(function () {
        if (requestedRunId) openDetail(Number(requestedRunId));
      });
    });
  }

  function loadList() {
    return TS.api.getScanHistory(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      state.project = res.body.project;
      state.scans = res.body.scans || [];
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      renderList();
    });
  }

  /* ------------------------------------------------------- list render -- */

  function renderList() {
    renderStatusChips();
    renderProviderChips();
    renderScansTable();
  }

  function renderStatusChips() {
    var statuses = Array.from(new Set(state.scans.map(function (s) { return s.status; })));
    var container = $('#status-filter-chips');
    var chips = [{ id: null, label: 'All statuses' }].concat(statuses.map(function (s) { return { id: s, label: s }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.statusFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-status-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function renderProviderChips() {
    var providers = Array.from(new Set(state.scans.map(function (s) { return s.provider; })));
    var container = $('#provider-filter-chips');
    var chips = [{ id: null, label: 'All providers' }].concat(providers.map(function (p) { return { id: p, label: p }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.providerFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-provider-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function filteredScans() {
    var q = state.searchQuery.trim().toLowerCase();
    return state.scans.filter(function (s) {
      if (state.statusFilter != null && s.status !== state.statusFilter) return false;
      if (state.providerFilter != null && s.provider !== state.providerFilter) return false;
      if (q) {
        var hay = (s.model || '') + ' ' + (s.region || '');
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function renderScansTable() {
    var body = $('#scans-table-body'), empty = $('#scans-empty');
    var list = filteredScans();
    text($('#scan-count-badge'), state.scans.length + ' total');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (s) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="ID">#' + s.id + '</td>' +
        '<td data-l="Date">' + fmtDateTime(s.created_at) + '</td>' +
        '<td data-l="Provider">' + esc(s.provider) + '<div class="hint">' + esc(s.model || '') + '</div></td>' +
        '<td data-l="Region">' + esc(s.region || 'All') + '</td>' +
        '<td data-l="Status"><span class="badge ' + statusBadgeClass(s.status) + '">' + esc(s.status) + '</span></td>' +
        '<td data-l="Prompts">' + s.completed_count + ' / ' + s.prompt_count + '</td>' +
        '<td data-l="Mention rate">' + fmtPct(s.mention_rate) + '</td>' +
        '<td data-l="Citation rate">' + fmtPct(s.citation_rate) + '</td>' +
        '<td data-l="SOV">' + fmtPct(s.share_of_voice) + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-scan="' + s.id + '">View</button></td>';
      body.appendChild(tr);
    });
  }

  /* ----------------------------------------------------- detail render -- */

  function openDetail(runId) {
    state.selectedRunId = runId;
    state.view = 'detail';
    $('#list-view').hidden = true;
    $('#detail-view').hidden = false;
    text($('#detail-run-id'), String(runId));
    $('#detail-body').innerHTML = '<div class="empty"><b>Loading&hellip;</b></div>';
    var url = new URL(window.location.href);
    url.searchParams.set('run_id', runId);
    window.history.replaceState({}, '', url.toString());

    TS.api.getEvidence(state.workspaceId, runId).then(function (res) {
      if (!res.ok || !res.body.evidence.run) {
        $('#detail-body').innerHTML = '<div class="empty"><b>Scan not found.</b></div>';
        return;
      }
      state.detail = res.body.evidence;
      renderDetail();
    });
  }

  function backToList() {
    state.view = 'list';
    $('#detail-view').hidden = true;
    $('#list-view').hidden = false;
    var url = new URL(window.location.href);
    url.searchParams.delete('run_id');
    window.history.replaceState({}, '', url.toString());
  }

  function renderDetail() {
    var d = state.detail, run = d.run;
    var body = $('#detail-body');

    var issuesPanel = '';
    var failedAnswers = (d.answers || []).filter(function (a) { return a.status !== 'ok' && a.status !== 'succeeded'; });
    if (run.status !== 'succeeded' || run.error || failedAnswers.length) {
      issuesPanel =
        '<div class="panel"><div class="ph"><h4>Provider issues</h4>' +
        '<span class="badge ' + statusBadgeClass(run.status) + '">' + esc(run.status) + '</span></div>' +
        '<div class="pb">' +
        (run.error ? '<p class="form-error">' + esc(run.error) + '</p>' : '') +
        (failedAnswers.length
          ? failedAnswers.map(function (a) {
              return '<div class="cite"><span class="u">' + esc(a.prompt || 'Prompt') + '</span>' +
                '<span class="badge no">' + esc(a.status) + '</span></div>' +
                (a.error ? '<p class="hint" style="margin:2px 0 8px">' + esc(a.error) + '</p>' : '');
            }).join('')
          : '<p class="hint">No individual answer errors recorded.</p>') +
        '</div></div>';
    }

    var summary =
      '<div class="panel"><div class="ph"><h4>Summary</h4></div><div class="pb"><dl class="kv">' +
      '<dt>Status</dt><dd><span class="badge ' + statusBadgeClass(run.status) + '">' + esc(run.status) + '</span></dd>' +
      '<dt>Provider</dt><dd>' + esc(run.provider) + '</dd>' +
      '<dt>Model</dt><dd>' + esc(run.model) + '</dd>' +
      '<dt>Region</dt><dd>' + esc(run.region || 'All') + '</dd>' +
      '<dt>Run type</dt><dd>' + esc(run.run_type) + '</dd>' +
      '<dt>Prompts</dt><dd>' + run.completed_count + ' / ' + run.prompt_count + '</dd>' +
      '<dt>Started</dt><dd>' + fmtDateTime(run.created_at) + '</dd>' +
      '<dt>Completed</dt><dd>' + fmtDateTime(run.completed_at) + '</dd>' +
      '</dl></div></div>';

    var metrics =
      '<div class="cols c4">' +
      '  <div class="panel"><div class="pb"><span class="hint">Mention rate</span><h2>' + fmtPct(run.mention_rate) + '</h2></div></div>' +
      '  <div class="panel"><div class="pb"><span class="hint">Citation rate</span><h2>' + fmtPct(run.citation_rate) + '</h2></div></div>' +
      '  <div class="panel"><div class="pb"><span class="hint">Source presence</span><h2>' + fmtPct(run.source_presence_rate) + '</h2></div></div>' +
      '  <div class="panel"><div class="pb"><span class="hint">Share of voice</span><h2>' + fmtPct(run.share_of_voice) + '</h2></div></div>' +
      '</div>';

    var competitors = (d.brand_rankings || []).length
      ? '<div class="panel"><div class="ph"><h4>Competitors</h4></div>' +
        '<div class="tw"><table class="data cards"><thead><tr><th>Name</th><th>Mentioned</th><th>Visibility</th>' +
        '<th>Share of voice</th><th>Avg. position</th></tr></thead><tbody>' +
        d.brand_rankings.map(function (b) {
          return '<tr><td data-l="Name">' + (b.tracked ? '<b>' + esc(b.name) + '</b> <span class="badge brand">You</span>' : esc(b.name)) + '</td>' +
            '<td data-l="Mentioned">' + b.mention_count + ' / ' + b.answer_count + '</td>' +
            '<td data-l="Visibility">' + (b.visibility == null ? '—' : b.visibility + '%') + '</td>' +
            '<td data-l="Share of voice">' + (b.share_of_voice == null ? '—' : b.share_of_voice + '%') + '</td>' +
            '<td data-l="Avg. position">' + (b.average_source_position == null ? '—' : '#' + b.average_source_position) + '</td></tr>';
        }).join('') + '</tbody></table></div></div>'
      : '';

    var recommendations = (d.opportunities || []).length
      ? '<div class="panel"><div class="ph"><h4>Recommendations</h4></div><div class="pb">' +
        d.opportunities.map(function (o) {
          return '<div class="cite"><span class="u">' + esc(o.title) + '</span>' +
            '<span class="badge ' + (o.priority === 'high' ? 'no' : o.priority === 'low' ? 'yes' : 'thin') + '">' + esc(o.priority) + '</span></div>' +
            '<p class="hint" style="margin:2px 0 8px">' + esc(o.rationale) + '</p>';
        }).join('') + '</div></div>'
      : '';

    var answers =
      '<div class="panel"><div class="ph"><h4>Prompts &amp; answers</h4><span class="badge thin">' + (d.answers || []).length + ' prompts</span></div>' +
      '<div class="tw"><table class="data cards"><thead><tr>' +
      '<th>Prompt</th><th>Topic</th><th>Status</th><th>Mentioned</th><th>Cited</th><th>Latency</th><th></th>' +
      '</tr></thead><tbody>' +
      (d.answers || []).map(function (a) {
        var mentionedBadge = '<span class="badge ' + (a.brand_mentioned ? 'yes' : 'no') + '">' +
          (a.brand_mentioned ? ('Yes' + (a.brand_rank ? ' · #' + a.brand_rank : '')) : 'No') + '</span>';
        var citedBadge = '<span class="badge ' + (a.brand_cited ? 'yes' : 'no') + '">' + (a.brand_cited ? 'Yes' : 'No') + '</span>';
        return '<tr><td data-l="Prompt">' + esc(a.prompt || '—') + '</td>' +
          '<td data-l="Topic">' + esc(a.topic_name || '—') + '</td>' +
          '<td data-l="Status"><span class="badge ' + (a.status === 'ok' ? 'yes' : 'no') + '">' + esc(a.status) + '</span></td>' +
          '<td data-l="Mentioned">' + mentionedBadge + '</td>' +
          '<td data-l="Cited">' + citedBadge + '</td>' +
          '<td data-l="Latency">' + (a.latency_ms != null ? a.latency_ms + ' ms' : '—') + '</td>' +
          '<td data-l=""><button type="button" class="btn ghost sm" data-view-answer="' + a.id + '">View</button></td></tr>';
      }).join('') + '</tbody></table></div></div>';

    body.innerHTML = issuesPanel + summary + metrics + answers + competitors + recommendations;
  }

  /* ------------------------------------------------------------ drawer -- */

  function openAnswerDrawer(answerId) {
    var answer = (state.detail.answers || []).filter(function (a) { return a.id === answerId; })[0];
    if (!answer) return;
    text($('#drawer-title'), answer.prompt || 'Answer');
    var mentionBadge = '<span class="badge ' + (answer.brand_mentioned ? 'yes' : 'no') + '">' +
      (answer.brand_mentioned ? ('Mentioned' + (answer.brand_rank ? ' · #' + answer.brand_rank : '')) : 'Not mentioned') + '</span>';
    var citedBadge = '<span class="badge ' + (answer.brand_cited ? 'yes' : 'no') + '">' + (answer.brand_cited ? 'Cited' : 'Not cited') + '</span>';
    var answerBlock = answer.answer_text ? '<div class="answer">' + esc(answer.answer_text) + '</div>' : '<p class="hint">No answer text recorded.</p>';
    var sources = (answer.sources || []);
    var sourcesBlock = sources.length
      ? sources.map(function (s) {
          return '<div class="cite"><span class="u">' + esc(s.domain || s.url || '') + '</span>' +
            '<span class="badge thin">' + esc(s.category || '') + '</span>' +
            (s.rank ? '<span class="hint">#' + s.rank + '</span>' : '') + '</div>';
        }).join('')
      : '';
    var metadata =
      '<dl class="kv" style="margin-top:10px">' +
      '<dt>Status</dt><dd>' + esc(answer.status) + '</dd>' +
      '<dt>Latency</dt><dd>' + (answer.latency_ms != null ? answer.latency_ms + ' ms' : '—') + '</dd>' +
      '<dt>Timestamp</dt><dd>' + fmtDateTime(answer.created_at) + '</dd>' +
      '<dt>Search request</dt><dd>' + esc(answer.search_request_id || '—') + '</dd>' +
      '<dt>Answer request</dt><dd>' + esc(answer.answer_request_id || '—') + '</dd>' +
      (answer.error ? '<dt>Error</dt><dd>' + esc(answer.error) + '</dd>' : '') +
      '</dl>';
    $('#drawer-body').innerHTML =
      '<div class="panel"><div class="ph"><h4>' + esc(answer.provider || 'Engine') + '</h4>' +
      mentionBadge + citedBadge + '</div><div class="pb">' + answerBlock +
      (sourcesBlock ? '<div style="margin-top:10px">' + sourcesBlock + '</div>' : '') +
      metadata + '</div></div>';
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
  }

  function closeDrawer() {
    $('#drawer-scrim').classList.remove('open');
    $('#answer-drawer').classList.remove('open');
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#search-input').addEventListener('input', function (evt) {
      state.searchQuery = evt.target.value;
      renderScansTable();
    });

    $('#status-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-status-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-status-filter');
      state.statusFilter = raw === '' ? null : raw;
      renderStatusChips();
      renderScansTable();
    });

    $('#provider-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-provider-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-provider-filter');
      state.providerFilter = raw === '' ? null : raw;
      renderProviderChips();
      renderScansTable();
    });

    $('#scans-table-body').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-view-scan]');
      if (!btn) return;
      openDetail(Number(btn.getAttribute('data-view-scan')));
    });

    $('#back-to-list-btn').addEventListener('click', backToList);

    $('#detail-body').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-view-answer]');
      if (!btn) return;
      openAnswerDrawer(Number(btn.getAttribute('data-view-answer')));
    });

    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-scrim').addEventListener('click', closeDrawer);
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
