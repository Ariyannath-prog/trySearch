/* Mentions / AI Answer Intelligence page (/mentions). One data source: GET
   .../mentions, which is app/metrics.py::mention_listing() - every measured
   answer across every run for this workspace (not just the latest one),
   reusing answer_derivations() for the brand fields and the already-stored
   mentions table for context and competitor attribution. Nothing here
   re-scans an answer or re-derives a rank; this page only searches, filters
   and displays what extraction already computed once. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    mentions: [],
    searchQuery: '',
    engineFilter: null,
    topicFilter: null,     // null = all, 'none' = untagged, otherwise a topic name
    competitorFilter: null, // null = all, otherwise a competitor name
    statusFilter: null      // null = all, 'mentioned' | 'absent'
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

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Mentions</h1><p>Every measured AI answer for <span id="project-name"></span>, brand and competitor mentions together.</p></div></div>' +

    '<div class="panel">' +
    '  <div class="pb">' +
    '    <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '      <input type="text" id="search-input" placeholder="Search prompts, answers or competitors&hellip;" style="max-width:280px">' +
    '      <div id="status-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '    </div>' +
    '    <div id="engine-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px"></div>' +
    '    <div id="topic-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px"></div>' +
    '    <div id="competitor-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Answers</h4><span class="badge thin" id="mention-count-badge"></span></div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Status</th><th>Engine</th><th>Prompt</th><th>Topic</th><th>Answer preview</th>' +
    '    <th>Competitors mentioned</th><th>Date</th><th></th>' +
    '  </tr></thead><tbody id="mentions-table-body"></tbody></table></div>' +
    '  <div class="empty" id="mentions-empty" hidden><p>No answers match.</p></div>' +
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
    TS.api.getMentions(id).then(function (res) {
      if (!res.ok) return;
      state.project = res.body.project;
      state.mentions = res.body.mentions || [];
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderStatusChips();
    renderEngineChips();
    renderTopicChips();
    renderCompetitorChips();
    renderMentionsTable();
  }

  function renderStatusChips() {
    var container = $('#status-filter-chips');
    var chips = [
      { id: null, label: 'All' }, { id: 'mentioned', label: 'Mentioned' }, { id: 'absent', label: 'Absent' }
    ];
    container.innerHTML = chips.map(function (c) {
      var on = state.statusFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-status-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function allEngines() {
    var seen = {};
    state.mentions.forEach(function (m) { if (m.provider) seen[m.provider] = true; });
    return Object.keys(seen).sort();
  }

  function renderEngineChips() {
    var container = $('#engine-filter-chips');
    var engines = allEngines();
    var chips = [{ id: null, label: 'All engines' }].concat(engines.map(function (e) { return { id: e, label: e }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.engineFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-engine-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function allTopics() {
    var seen = {};
    var hasUntagged = false;
    state.mentions.forEach(function (m) {
      if (m.topic_name) seen[m.topic_name] = true; else hasUntagged = true;
    });
    var topics = Object.keys(seen).sort();
    if (hasUntagged) topics.push('__untagged__');
    return topics;
  }

  function renderTopicChips() {
    var container = $('#topic-filter-chips');
    var topics = allTopics();
    var chips = [{ id: null, label: 'All topics' }].concat(topics.map(function (t) {
      return t === '__untagged__' ? { id: 'none', label: 'Untagged' } : { id: t, label: t };
    }));
    container.innerHTML = chips.map(function (c) {
      var on = state.topicFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-topic-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function allCompetitorNames() {
    var seen = {};
    state.mentions.forEach(function (m) {
      (m.competitors || []).forEach(function (c) { if (c.name) seen[c.name] = true; });
    });
    return Object.keys(seen).sort();
  }

  function renderCompetitorChips() {
    var container = $('#competitor-filter-chips');
    var names = allCompetitorNames();
    if (!names.length) { container.innerHTML = ''; return; }
    var chips = [{ id: null, label: 'All competitors' }].concat(names.map(function (n) { return { id: n, label: n }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.competitorFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-competitor-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function filteredMentions() {
    var q = state.searchQuery.trim().toLowerCase();
    return state.mentions.filter(function (m) {
      if (state.statusFilter === 'mentioned' && !m.brand_mentioned) return false;
      if (state.statusFilter === 'absent' && m.brand_mentioned) return false;
      if (state.engineFilter != null && m.provider !== state.engineFilter) return false;
      if (state.topicFilter === 'none' && m.topic_name) return false;
      if (state.topicFilter != null && state.topicFilter !== 'none' && m.topic_name !== state.topicFilter) return false;
      if (state.competitorFilter != null) {
        var names = (m.competitors || []).map(function (c) { return c.name; });
        if (names.indexOf(state.competitorFilter) === -1) return false;
      }
      if (q) {
        var competitorNames = (m.competitors || []).map(function (c) { return c.name; }).join(' ');
        var hay = (m.prompt || '') + ' ' + (m.answer_preview || '') + ' ' + (m.context || '') + ' ' + competitorNames;
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function renderMentionsTable() {
    var body = $('#mentions-table-body'), empty = $('#mentions-empty');
    var list = filteredMentions();
    text($('#mention-count-badge'), state.mentions.length + ' answers');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (m) {
      var tr = document.createElement('tr');
      var statusBadge = '<span class="badge ' + (m.brand_mentioned ? 'yes' : 'no') + '">' +
        (m.brand_mentioned ? ('Mentioned' + (m.brand_rank ? ' · #' + m.brand_rank : '')) : 'Absent') + '</span>';
      var citedBadge = m.brand_cited ? ' <span class="badge thin">Cited</span>' : '';
      var competitorChips = (m.competitors || []).map(function (c) {
        return '<span class="chip plain">' + esc(c.name || 'Unknown') + (c.rank ? ' #' + c.rank : '') + '</span>';
      }).join(' ') || '<span class="hint">—</span>';
      var previewCell = esc(m.answer_preview || '—') +
        (m.context ? '<div class="hint" style="margin-top:4px">' + esc(m.context) + '</div>' : '');
      tr.innerHTML =
        '<td data-l="Status">' + statusBadge + citedBadge + '</td>' +
        '<td data-l="Engine">' + esc(m.provider || '—') + '</td>' +
        '<td data-l="Prompt">' + esc(m.prompt || '—') + '</td>' +
        '<td data-l="Topic">' + esc(m.topic_name || '—') + '</td>' +
        '<td data-l="Answer preview">' + previewCell + '</td>' +
        '<td data-l="Competitors mentioned">' + competitorChips + '</td>' +
        '<td data-l="Date">' + fmtDateTime(m.created_at) + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-answer="' + m.id + '">View</button></td>';
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
      var mentionBadge = '<span class="badge ' + (a.brand_mentioned ? 'yes' : 'no') + '">' +
        (a.brand_mentioned ? ('Mentioned' + (a.brand_rank ? ' · #' + a.brand_rank : '')) : 'Not mentioned') + '</span>';
      var citedBadge = '<span class="badge ' + (a.brand_cited ? 'yes' : 'no') + '">' + (a.brand_cited ? 'Cited' : 'Not cited') + '</span>';
      var answerBlock = a.answer_text ? '<div class="answer">' + esc(a.answer_text) + '</div>' : '<p class="hint">No answer text recorded.</p>';
      var sources = (a.sources || []).slice(0, 8);
      var sourcesBlock = sources.length
        ? sources.map(function (s) {
            return '<div class="cite"><span class="u">' + esc(s.domain || s.url || '') + '</span>' +
              '<span class="badge thin">' + esc(s.category || '') + '</span></div>';
          }).join('')
        : '';
      $('#drawer-body').innerHTML =
        '<div class="panel"><div class="ph"><h4>' + esc(a.provider || 'Engine') + '</h4>' +
        mentionBadge + citedBadge + '</div><div class="pb">' + answerBlock +
        (sourcesBlock ? '<div style="margin-top:10px">' + sourcesBlock + '</div>' : '') + '</div></div>';
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
      renderMentionsTable();
    });

    $('#status-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-status-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-status-filter');
      state.statusFilter = raw === '' ? null : raw;
      renderStatusChips();
      renderMentionsTable();
    });

    $('#engine-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-engine-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-engine-filter');
      state.engineFilter = raw === '' ? null : raw;
      renderEngineChips();
      renderMentionsTable();
    });

    $('#topic-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-topic-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-topic-filter');
      state.topicFilter = raw === '' ? null : raw;
      renderTopicChips();
      renderMentionsTable();
    });

    $('#competitor-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-competitor-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-competitor-filter');
      state.competitorFilter = raw === '' ? null : raw;
      renderCompetitorChips();
      renderMentionsTable();
    });

    $('#mentions-table-body').addEventListener('click', function (evt) {
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
