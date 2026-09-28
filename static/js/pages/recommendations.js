/* Recommendations / Opportunities page (/recommendations). One data
   source: GET .../recommendations, which is app/metrics.py::
   recommendation_intelligence() - a read-only merge of the only two things
   in this backend that actually generate a title/rationale/priority
   recommendation: analytics_content_opportunities (written at the end of
   every prompt scan, app/scanning.py) and analytics_audit_findings (via the
   existing latest_site_audit(), each finding already carrying its own
   `recommendation` text). Nothing here invents a new recommendation rule,
   and neither source table has a status column, so this page has no
   "done" toggle - every item is evidence to act on elsewhere, not a
   workflow state owned by this page. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    recommendations: [],
    searchQuery: '',
    kindFilter: null,      // null = all, 'content_opportunity' | 'site_finding'
    priorityFilter: null,  // null = all, 'high' | 'medium' | 'low'
    areaFilter: null       // null = all, otherwise an area string
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
  function priorityBadgeClass(p) { return p === 'high' ? 'no' : p === 'medium' ? 'thin' : 'yes'; }
  function kindLabel(kind) { return kind === 'site_finding' ? 'Site audit' : 'AI Visibility'; }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Recommendations</h1><p>Prioritized opportunities for <span id="project-name"></span>, drawn from stored evidence.</p></div></div>' +

    '<div class="cols c4">' +
    '  <div class="panel"><div class="pb"><span class="hint">Total</span><h2 id="stat-total">0</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">High priority</span><h2 id="stat-high">0</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Medium priority</span><h2 id="stat-medium">0</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Low priority</span><h2 id="stat-low">0</h2></div></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="pb">' +
    '    <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '      <input type="text" id="search-input" placeholder="Search recommendations&hellip;" style="max-width:260px">' +
    '      <div id="priority-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '    </div>' +
    '    <div id="kind-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px"></div>' +
    '    <div id="area-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Recommendations</h4><span class="badge thin" id="rec-count-badge"></span></div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Priority</th><th>Title</th><th>Area</th><th>Source</th><th>Date</th><th></th>' +
    '  </tr></thead><tbody id="rec-table-body"></tbody></table></div>' +
    '  <div class="empty" id="rec-empty" hidden><p>No recommendations match.</p></div>' +
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
    TS.api.getRecommendations(id).then(function (res) {
      if (!res.ok) return;
      state.project = res.body.project;
      state.recommendations = res.body.recommendations || [];
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderStatTiles();
    renderPriorityChips();
    renderKindChips();
    renderAreaChips();
    renderTable();
  }

  function renderStatTiles() {
    var counts = { high: 0, medium: 0, low: 0 };
    state.recommendations.forEach(function (r) { counts[r.priority] = (counts[r.priority] || 0) + 1; });
    text($('#stat-total'), String(state.recommendations.length));
    text($('#stat-high'), String(counts.high));
    text($('#stat-medium'), String(counts.medium));
    text($('#stat-low'), String(counts.low));
  }

  function renderPriorityChips() {
    var container = $('#priority-filter-chips');
    var chips = [
      { id: null, label: 'All priorities' }, { id: 'high', label: 'High' },
      { id: 'medium', label: 'Medium' }, { id: 'low', label: 'Low' }
    ];
    container.innerHTML = chips.map(function (c) {
      var on = state.priorityFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-priority-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function renderKindChips() {
    var container = $('#kind-filter-chips');
    var chips = [
      { id: null, label: 'All sources' }, { id: 'content_opportunity', label: 'AI Visibility' },
      { id: 'site_finding', label: 'Site audit' }
    ];
    container.innerHTML = chips.map(function (c) {
      var on = state.kindFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-kind-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function allAreas() {
    var seen = {};
    state.recommendations.forEach(function (r) { if (r.area) seen[r.area] = true; });
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

  function filteredRecommendations() {
    var q = state.searchQuery.trim().toLowerCase();
    return state.recommendations.filter(function (r) {
      if (state.priorityFilter != null && r.priority !== state.priorityFilter) return false;
      if (state.kindFilter != null && r.kind !== state.kindFilter) return false;
      if (state.areaFilter != null && r.area !== state.areaFilter) return false;
      if (q) {
        var hay = (r.title || '') + ' ' + (r.rationale || '');
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function renderTable() {
    var body = $('#rec-table-body'), empty = $('#rec-empty');
    var list = filteredRecommendations();
    text($('#rec-count-badge'), state.recommendations.length + ' total');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Priority"><span class="badge ' + priorityBadgeClass(r.priority) + '">' + esc(r.priority) + '</span></td>' +
        '<td data-l="Title">' + esc(r.title) + '</td>' +
        '<td data-l="Area">' + esc(r.area || '—') + '</td>' +
        '<td data-l="Source">' + esc(r.source || '—') + '</td>' +
        '<td data-l="Date">' + fmtDateTime(r.created_at) + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-rec="' + esc(r.id) + '">View</button></td>';
      body.appendChild(tr);
    });
  }

  /* ------------------------------------------------------------ drawer -- */

  function openRecommendationDrawer(recId) {
    var rec = state.recommendations.filter(function (r) { return r.id === recId; })[0];
    if (!rec) return;
    text($('#drawer-title'), rec.title);
    var evidenceBlock;
    if (rec.kind === 'content_opportunity') {
      evidenceBlock = (rec.evidence || []).length
        ? (rec.evidence || []).map(function (e) {
            return '<div class="cite"><span class="u">' + esc(e.prompt || 'Prompt') + '</span>' +
              '<span class="badge thin">' + esc(e.provider || '') + '</span>' +
              '<button type="button" class="btn ghost sm" data-view-answer="' + e.answer_id + '">View answer</button></div>';
          }).join('')
        : '<p class="hint">No linked evidence recorded.</p>';
    } else {
      evidenceBlock = (rec.evidence || []).map(function (e) {
        return '<p>' + esc(e.evidence_text || '') + '</p>' +
          (e.url ? '<p><a href="' + esc(e.url) + '" target="_blank" rel="noopener noreferrer">' + esc(e.url) + '</a></p>' : '');
      }).join('');
    }
    $('#drawer-body').innerHTML =
      '<div class="panel"><div class="ph"><h4>' + esc(kindLabel(rec.kind)) + '</h4>' +
      '<span class="badge ' + priorityBadgeClass(rec.priority) + '">' + esc(rec.priority) + '</span></div>' +
      '<div class="pb"><p>' + esc(rec.rationale) + '</p>' +
      '<div style="margin-top:10px">' + evidenceBlock + '</div>' +
      '<p style="margin-top:14px"><a class="btn sm" href="' + esc(rec.link) + '">Open in ' + esc(kindLabel(rec.kind)) + '</a></p>' +
      '</div></div>';
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
  }

  function openAnswerDrawer(answerId) {
    text($('#drawer-title'), 'Loading…');
    $('#drawer-body').innerHTML = '';
    TS.api.getEvidenceAnswer(state.workspaceId, answerId).then(function (res) {
      if (!res.ok) {
        $('#drawer-body').innerHTML = '<div class="empty"><b>Could not load this evidence.</b></div>';
        return;
      }
      var a = res.body.evidence;
      text($('#drawer-title'), a.prompt || 'Answer');
      var answerBlock = a.answer_text ? '<div class="answer">' + esc(a.answer_text) + '</div>' : '<p class="hint">No answer text recorded.</p>';
      $('#drawer-body').innerHTML =
        '<div class="panel"><div class="ph"><h4>' + esc(a.provider || 'Engine') + '</h4></div>' +
        '<div class="pb">' + answerBlock + '</div></div>';
    });
  }

  function closeDrawer() {
    $('#drawer-scrim').classList.remove('open');
    $('#answer-drawer').classList.remove('open');
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#search-input').addEventListener('input', function (evt) {
      state.searchQuery = evt.target.value;
      renderTable();
    });

    $('#priority-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-priority-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-priority-filter');
      state.priorityFilter = raw === '' ? null : raw;
      renderPriorityChips();
      renderTable();
    });

    $('#kind-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-kind-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-kind-filter');
      state.kindFilter = raw === '' ? null : raw;
      renderKindChips();
      renderTable();
    });

    $('#area-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-area-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-area-filter');
      state.areaFilter = raw === '' ? null : raw;
      renderAreaChips();
      renderTable();
    });

    $('#rec-table-body').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-view-rec]');
      if (!btn) return;
      openRecommendationDrawer(btn.getAttribute('data-view-rec'));
    });

    $('#drawer-body').addEventListener('click', function (evt) {
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
