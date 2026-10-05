/* Competitors page controller (/competitors). One data source: GET
   .../competitors, which now (alongside the existing POST/DELETE CRUD at
   the same path) also returns app/metrics.py::competitor_intelligence() -
   the brand plus every tracked competitor scored with the exact same
   formula and cohort (workspace + scheduled runs only) metrics_daily uses
   for the brand's own official Visibility Score, so the comparison is
   apples-to-apples rather than a lookalike computed a different way. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    entities: [],       // brand + competitors, from competitor_intelligence()
    trend: {},           // entity id (or 'brand') -> [{date, visibility_score}, ...]
    outperforms: [],
    measuredAnswerCount: 0,
    threshold: 20,
    searchQuery: '',
    sortKey: 'visibility_score',
    trendEntityId: 'brand',
    editingCompetitorId: null
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
  function fmtPct(envelope) {
    if (!envelope || envelope.value == null) return '—';
    var v = Math.round(envelope.value * 1000) / 10;
    if (envelope.low != null && envelope.high != null) {
      return v + '% (' + Math.round(envelope.low * 100) + '–' + Math.round(envelope.high * 100) + '%)';
    }
    return v + '%';
  }
  function fmtScore(v) { return v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1); }
  function fmtRank(v) { return v == null ? '—' : '#' + v; }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Competitors</h1><p>How <span id="project-name"></span> compares to the competitors it tracks.</p></div></div>' +
    '<p class="hint" id="threshold-hint" style="margin:0 0 10px"></p>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Add a competitor</h4></div>' +
    '  <div class="pb">' +
    '    <div class="field">' +
    '      <input type="text" id="add-competitor-name" placeholder="Competitor name" style="max-width:220px">' +
    '      <input type="text" id="add-competitor-domain" placeholder="Domain (optional, e.g. rival.com)" style="max-width:220px">' +
    '      <button class="btn sm" id="add-competitor-submit">Add</button>' +
    '    </div>' +
    '    <p class="form-error" id="add-competitor-error" hidden></p>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Comparison</h4><span class="badge thin" id="entity-count-badge"></span></div>' +
    '  <div class="pb">' +
    '    <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '      <input type="text" id="search-input" placeholder="Search competitors&hellip;" style="max-width:240px">' +
    '      <select id="sort-select">' +
    '        <option value="visibility_score">Visibility score</option>' +
    '        <option value="mention_rate">Mention rate</option>' +
    '        <option value="share_of_voice">Share of voice</option>' +
    '        <option value="name">Name A–Z</option>' +
    '      </select>' +
    '    </div>' +
    '  </div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Name</th><th>Mention rate</th><th>Citation rate</th><th>Avg. position</th>' +
    '    <th>Share of voice</th><th>Visibility score</th><th></th>' +
    '  </tr></thead><tbody id="entities-table-body"></tbody></table></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Visibility trend</h4>' +
    '    <select id="trend-entity-select" style="margin-left:auto;max-width:220px"></select></div>' +
    '  <div class="pb" id="trend-container"></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Prompts where a competitor outperforms you</h4><span class="badge thin" id="outperforms-count-badge"></span></div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Prompt</th><th>Engine</th><th>Competitor</th><th>Their rank</th><th>Your rank</th><th>Date</th><th></th>' +
    '  </tr></thead><tbody id="outperforms-table-body"></tbody></table></div>' +
    '  <div class="empty" id="outperforms-empty" hidden><p>No competitor has outranked you in a scheduled scan yet.</p></div>' +
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
    TS.api.getCompetitorIntelligence(id).then(function (res) {
      if (!res.ok) return;
      state.project = res.body.project;
      var intel = res.body.intelligence || {};
      state.entities = intel.entities || [];
      state.trend = intel.trend || {};
      state.outperforms = intel.outperforms || [];
      state.measuredAnswerCount = intel.measured_answer_count || 0;
      state.threshold = intel.threshold || 20;
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderThresholdHint();
    renderEntitiesTable();
    renderTrendSelect();
    renderTrend();
    renderOutperforms();
  }

  function renderThresholdHint() {
    var el = $('#threshold-hint');
    if (state.measuredAnswerCount === 0) {
      text(el, 'No scheduled scans measured yet. Visibility scores appear once scheduled scans have run ' +
        '(turn on a recurring scan from the AI Visibility page).');
    } else if (state.measuredAnswerCount < state.threshold) {
      text(el, 'Visibility scores need at least ' + state.threshold + ' scheduled answers to show; ' +
        state.measuredAnswerCount + ' measured so far. Mention and citation rates are shown below regardless.');
    } else {
      text(el, 'Scored from ' + state.measuredAnswerCount + ' scheduled answers, the same cohort the AI Visibility page uses.');
    }
  }

  function filteredSortedEntities() {
    var q = state.searchQuery.trim().toLowerCase();
    var list = state.entities.filter(function (e) {
      if (!q) return true;
      return (e.name || '').toLowerCase().indexOf(q) !== -1;
    });
    list = list.slice();
    if (state.sortKey === 'name') {
      list.sort(function (a, b) { return (a.name || '').localeCompare(b.name || ''); });
    } else if (state.sortKey === 'mention_rate') {
      list.sort(function (a, b) { return (b.mention_rate.value || 0) - (a.mention_rate.value || 0); });
    } else if (state.sortKey === 'share_of_voice') {
      list.sort(function (a, b) { return (b.share_of_voice || 0) - (a.share_of_voice || 0); });
    } else {
      list.sort(function (a, b) { return (b.visibility_score || -1) - (a.visibility_score || -1); });
    }
    return list;
  }

  function renderEntityRowView(tr, e) {
    var nameCell = e.tracked
      ? '<b>' + esc(e.name) + '</b> <span class="badge brand">You</span>'
      : esc(e.name) + (e.domain ? ' <span class="hint">' + esc(e.domain) + '</span>' : '');
    var actions = e.tracked ? '' :
      '<button type="button" class="btn ghost sm" data-edit-competitor="' + e.id + '">Edit</button> ' +
      '<button type="button" class="btn ghost sm" data-delete-competitor="' + e.id + '">Remove</button>';
    tr.innerHTML =
      '<td data-l="Name">' + nameCell + '</td>' +
      '<td data-l="Mention rate">' + fmtPct(e.mention_rate) + '</td>' +
      '<td data-l="Citation rate">' + fmtPct(e.citation_rate) + '</td>' +
      '<td data-l="Avg. position">' + fmtRank(e.average_rank) + '</td>' +
      '<td data-l="Share of voice">' + (e.share_of_voice == null ? '—' : Math.round(e.share_of_voice * 1000) / 10 + '%') + '</td>' +
      '<td data-l="Visibility score">' + fmtScore(e.visibility_score) + '</td>' +
      '<td data-l="">' + actions + '</td>';
  }

  function renderEntityRowEdit(tr, e) {
    tr.innerHTML =
      '<td data-l="Name"><input type="text" data-field="name" value="' + esc(e.name) + '" style="width:100%"></td>' +
      '<td data-l="Mention rate"><span class="hint">&mdash;</span></td>' +
      '<td data-l="Citation rate"><input type="text" data-field="domain" value="' + esc(e.domain || '') + '" placeholder="Domain" style="width:100%"></td>' +
      '<td data-l="Avg. position"><span class="hint">&mdash;</span></td>' +
      '<td data-l="Share of voice"><span class="hint">&mdash;</span></td>' +
      '<td data-l="Visibility score"><span class="hint">&mdash;</span></td>' +
      '<td data-l="" style="white-space:nowrap">' +
      '  <button type="button" class="btn sm" data-save-competitor="' + e.id + '">Save</button> ' +
      '  <button type="button" class="btn ghost sm" data-cancel-edit-competitor>Cancel</button>' +
      '  <p class="form-error" data-edit-error hidden style="margin-top:6px"></p>' +
      '</td>';
  }

  function renderEntitiesTable() {
    var body = $('#entities-table-body');
    var list = filteredSortedEntities();
    text($('#entity-count-badge'), (state.entities.length - 1) + ' competitors');
    clearChildren(body);
    list.forEach(function (e) {
      var tr = document.createElement('tr');
      if (!e.tracked && String(e.id) === String(state.editingCompetitorId)) {
        renderEntityRowEdit(tr, e);
      } else {
        renderEntityRowView(tr, e);
      }
      body.appendChild(tr);
    });
  }

  function renderTrendSelect() {
    var select = $('#trend-entity-select');
    var current = state.trendEntityId;
    clearChildren(select);
    state.entities.forEach(function (e) {
      var opt = document.createElement('option');
      opt.value = e.tracked ? 'brand' : String(e.id);
      opt.textContent = e.tracked ? (e.name || 'You') + ' (you)' : e.name;
      select.appendChild(opt);
    });
    select.value = current;
    if (select.value !== current) state.trendEntityId = select.value;
  }

  function renderTrend() {
    var container = $('#trend-container');
    var series = state.trend[state.trendEntityId] || [];
    TS.renderTrendChart(container, series);
  }

  function renderOutperforms() {
    var body = $('#outperforms-table-body'), empty = $('#outperforms-empty');
    text($('#outperforms-count-badge'), state.outperforms.length + ' prompts');
    clearChildren(body);
    if (!state.outperforms.length) { empty.hidden = false; return; }
    empty.hidden = true;
    state.outperforms.slice(0, 50).forEach(function (o) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Prompt">' + esc(o.prompt || '—') + '</td>' +
        '<td data-l="Engine">' + esc(o.engine || '—') + '</td>' +
        '<td data-l="Competitor">' + esc(o.competitor_name || '—') + '</td>' +
        '<td data-l="Their rank">' + fmtRank(o.competitor_rank) + '</td>' +
        '<td data-l="Your rank">' + (o.brand_rank == null ? '<span class="badge no">Not mentioned</span>' : fmtRank(o.brand_rank)) + '</td>' +
        '<td data-l="Date">' + fmtDateTime(o.created_at) + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-answer="' + o.answer_id + '">View</button></td>';
      body.appendChild(tr);
    });
  }

  /* ------------------------------------------------------------ drawer -- */

  function openAnswerDrawer(answerId) {
    text($('#drawer-title'), 'Loading…');
    $('#drawer-body').innerHTML = '';
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
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
    $('#add-competitor-submit').addEventListener('click', function () {
      var errorEl = $('#add-competitor-error');
      errorEl.hidden = true;
      var name = $('#add-competitor-name').value.trim();
      var domain = $('#add-competitor-domain').value.trim();
      if (!name) { text(errorEl, 'Enter a competitor name.'); errorEl.hidden = false; return; }
      TS.api.addCompetitor(state.workspaceId, { name: name, domain: domain }).then(function (res) {
        if (res.ok) {
          $('#add-competitor-name').value = '';
          $('#add-competitor-domain').value = '';
          loadWorkspace(state.workspaceId);
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not add that competitor.');
          errorEl.hidden = false;
        }
      });
    });

    $('#search-input').addEventListener('input', function (evt) {
      state.searchQuery = evt.target.value;
      renderEntitiesTable();
    });

    $('#sort-select').addEventListener('change', function (evt) {
      state.sortKey = evt.target.value;
      renderEntitiesTable();
    });

    $('#trend-entity-select').addEventListener('change', function (evt) {
      state.trendEntityId = evt.target.value;
      renderTrend();
    });

    $('#entities-table-body').addEventListener('click', function (evt) {
      var editBtn = evt.target.closest('[data-edit-competitor]');
      var deleteBtn = evt.target.closest('[data-delete-competitor]');
      var saveBtn = evt.target.closest('[data-save-competitor]');
      var cancelBtn = evt.target.closest('[data-cancel-edit-competitor]');
      if (editBtn) {
        state.editingCompetitorId = editBtn.getAttribute('data-edit-competitor');
        renderEntitiesTable();
      } else if (cancelBtn) {
        state.editingCompetitorId = null;
        renderEntitiesTable();
      } else if (saveBtn) {
        var id = saveBtn.getAttribute('data-save-competitor');
        var tr = saveBtn.closest('tr');
        var name = tr.querySelector('[data-field="name"]').value.trim();
        var domain = tr.querySelector('[data-field="domain"]').value.trim();
        var errorEl = tr.querySelector('[data-edit-error]');
        if (!name) {
          text(errorEl, 'Enter a competitor name.');
          errorEl.hidden = false;
          return;
        }
        TS.api.updateCompetitor(state.workspaceId, id, { name: name, domain: domain }).then(function (res) {
          if (res.ok) {
            state.editingCompetitorId = null;
            loadWorkspace(state.workspaceId);
          } else {
            text(errorEl, (res.body && res.body.error) || 'Could not save that competitor.');
            errorEl.hidden = false;
          }
        });
      } else if (deleteBtn) {
        var delId = deleteBtn.getAttribute('data-delete-competitor');
        if (!window.confirm('Remove this competitor from tracking?')) return;
        TS.api.deleteCompetitor(state.workspaceId, delId)
          .then(function (res) { if (res.ok) loadWorkspace(state.workspaceId); });
      }
    });

    $('#outperforms-table-body').addEventListener('click', function (evt) {
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
