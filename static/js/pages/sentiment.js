/* Sentiment Intelligence page (/sentiment). One data source: GET
   .../sentiment, which is app/metrics.py::sentiment_intelligence() - scoped
   to scheduled runs and brand-mentioned answers only, the same cohort the
   classifier (app/sentiment.py, a real LLM call) and metrics_daily's
   sentiment_index are computed over. Brand-only, by design: the mentions
   table has no sentiment column, so a specific competitor's sentiment is
   not representable without a schema change this page does not attempt -
   it says so plainly instead of showing an empty chart that looks broken. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    sentiment: null,
    activeJob: null,
    pollTimer: null,
    labelFilter: null,   // null = all, 'positive' | 'neutral' | 'negative' | 'unclassified'
    searchQuery: ''
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
  function fmtScore(v) { return v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1); }
  function fmtPct(count, total) { return total ? Math.round(count / total * 1000) / 10 + '%' : '—'; }
  function sentimentBadgeClass(label) {
    return label === 'positive' ? 'yes' : label === 'negative' ? 'no' : 'thin';
  }
  function sentimentLabel(label) {
    if (label === 'positive') return 'Positive';
    if (label === 'neutral') return 'Neutral';
    if (label === 'negative') return 'Negative';
    return 'Not yet classified';
  }

  var VIEWS_HTML =
    '<div class="phead">' +
    '  <div><h1>Sentiment</h1><p>How AI answers talk about <span id="project-name"></span>, brand-mentioned answers only.</p></div>' +
    '  <button class="btn" id="classify-btn">Classify new answers</button>' +
    '</div>' +
    '<p class="hint" id="sentiment-status"></p>' +
    '<div class="empty" id="not-configured-banner" hidden>' +
    '  <b>Not available</b><p>Sentiment classification needs an LLM configured on this server (Hugging Face or Ollama). Ask an admin to set it up.</p>' +
    '</div>' +

    '<div class="cols c4">' +
    '  <div class="panel"><div class="pb"><span class="hint">Overall sentiment</span><h2 id="stat-overall">—</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Positive</span><h2 id="stat-positive">—</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Neutral</span><h2 id="stat-neutral">—</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Negative</span><h2 id="stat-negative">—</h2></div></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Sentiment trend</h4></div>' +
    '  <div class="pb" id="trend-container"></div>' +
    '</div>' +

    '<div class="cols c2">' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>By AI engine</h4></div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>Engine</th><th>Sentiment index</th><th>Answers</th><th>As of</th>' +
    '    </tr></thead><tbody id="engine-table-body"></tbody></table></div>' +
    '    <div class="empty" id="engine-empty" hidden><p>No scheduled scans measured yet.</p></div>' +
    '  </div>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>By topic</h4></div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>Topic</th><th>Sentiment index</th><th>Classified</th>' +
    '    </tr></thead><tbody id="topic-table-body"></tbody></table></div>' +
    '    <div class="empty" id="topic-empty" hidden><p>No mentioned answers yet.</p></div>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>By competitor</h4></div>' +
    '  <div class="pb"><div class="empty"><b>Not available yet</b>' +
    '    <p>Sentiment is stored per answer, not per named entity, so a specific competitor’s ' +
    '    sentiment cannot be shown without a schema change. Nothing is fabricated here.</p></div></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Supporting evidence</h4><span class="badge thin" id="evidence-count-badge"></span></div>' +
    '  <div class="pb">' +
    '    <div class="cols" style="grid-template-columns:1fr auto;align-items:center">' +
    '      <input type="text" id="search-input" placeholder="Search prompts or topics&hellip;" style="max-width:240px">' +
    '      <div id="label-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '    </div>' +
    '  </div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Sentiment</th><th>Confidence</th><th>Engine</th><th>Prompt</th><th>Topic</th><th>Date</th><th></th>' +
    '  </tr></thead><tbody id="evidence-table-body"></tbody></table></div>' +
    '  <div class="empty" id="evidence-empty" hidden><p>No answers match.</p></div>' +
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
    TS.api.getSentiment(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      state.project = res.body.project;
      state.sentiment = res.body.sentiment;
      state.activeJob = res.body.active_job;
      text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      handleActiveJob(state.activeJob);
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    var s = state.sentiment;
    $('#not-configured-banner').hidden = !!s.configured;
    $('#classify-btn').hidden = !s.configured;
    renderStatusHint();
    renderStatTiles();
    renderTrend();
    renderEngineTable();
    renderTopicTable();
    renderLabelChips();
    renderEvidenceTable();
  }

  function renderStatusHint() {
    var s = state.sentiment;
    var el = $('#sentiment-status');
    if (!s.mentioned_count) {
      text(el, 'No brand-mentioned answers from scheduled scans yet.');
    } else {
      text(el, s.classified_count + ' of ' + s.mentioned_count + ' brand-mentioned answers classified.');
    }
  }

  function renderStatTiles() {
    var s = state.sentiment;
    text($('#stat-overall'), fmtScore(s.overall_sentiment_index));
    text($('#stat-positive'), fmtPct(s.distribution.positive, s.classified_count));
    text($('#stat-neutral'), fmtPct(s.distribution.neutral, s.classified_count));
    text($('#stat-negative'), fmtPct(s.distribution.negative, s.classified_count));
  }

  function renderTrend() {
    var series = (state.sentiment.trend || []).map(function (row) {
      return { date: row.date, visibility_score: row.sentiment_index };
    });
    TS.renderTrendChart($('#trend-container'), series);
  }

  function renderEngineTable() {
    var body = $('#engine-table-body'), empty = $('#engine-empty');
    var rows = state.sentiment.engines || [];
    clearChildren(body);
    if (!rows.length) { empty.hidden = false; return; }
    empty.hidden = true;
    rows.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Engine">' + esc(r.engine) + '</td>' +
        '<td data-l="Sentiment index">' + fmtScore(r.sentiment_index) + '</td>' +
        '<td data-l="Answers">' + (r.answer_count || 0) + '</td>' +
        '<td data-l="As of">' + esc(r.date || '—') + '</td>';
      body.appendChild(tr);
    });
  }

  function renderTopicTable() {
    var body = $('#topic-table-body'), empty = $('#topic-empty');
    var rows = state.sentiment.topics || [];
    clearChildren(body);
    if (!rows.length) { empty.hidden = false; return; }
    empty.hidden = true;
    rows.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Topic">' + esc(r.topic) + '</td>' +
        '<td data-l="Sentiment index">' + fmtScore(r.sentiment_index) + '</td>' +
        '<td data-l="Classified">' + r.classified + ' / ' + r.mentioned + '</td>';
      body.appendChild(tr);
    });
  }

  function renderLabelChips() {
    var container = $('#label-filter-chips');
    var chips = [
      { id: null, label: 'All' }, { id: 'positive', label: 'Positive' },
      { id: 'neutral', label: 'Neutral' }, { id: 'negative', label: 'Negative' },
      { id: 'unclassified', label: 'Not classified' }
    ];
    container.innerHTML = chips.map(function (c) {
      var on = state.labelFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-label-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function filteredEvidence() {
    var q = state.searchQuery.trim().toLowerCase();
    return (state.sentiment.evidence || []).filter(function (row) {
      var label = row.sentiment || 'unclassified';
      if (state.labelFilter != null && label !== state.labelFilter) return false;
      if (q) {
        var hay = (row.prompt || '') + ' ' + (row.topic_name || '');
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function renderEvidenceTable() {
    var body = $('#evidence-table-body'), empty = $('#evidence-empty');
    var list = filteredEvidence();
    text($('#evidence-count-badge'), (state.sentiment.evidence || []).length + ' answers');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (row) {
      var tr = document.createElement('tr');
      var badge = '<span class="badge ' + sentimentBadgeClass(row.sentiment) + '">' + esc(sentimentLabel(row.sentiment)) + '</span>';
      tr.innerHTML =
        '<td data-l="Sentiment">' + badge + '</td>' +
        '<td data-l="Confidence">' + (row.sentiment_conf != null ? Math.round(row.sentiment_conf * 100) + '%' : '—') + '</td>' +
        '<td data-l="Engine">' + esc(row.provider || '—') + '</td>' +
        '<td data-l="Prompt">' + esc(row.prompt || '—') + '</td>' +
        '<td data-l="Topic">' + esc(row.topic_name || '—') + '</td>' +
        '<td data-l="Date">' + fmtDateTime(row.created_at) + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-answer="' + row.id + '">View</button></td>';
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

  /* --------------------------------------------------------- classify -- */

  function handleActiveJob(job) {
    if (job) {
      $('#classify-btn').disabled = true;
      text($('#sentiment-status'), 'Classifying…');
      state.pollTimer = setTimeout(refresh, 5000);
    } else {
      $('#classify-btn').disabled = false;
    }
  }

  function handleClassifyResponse(res) {
    if (res.status === 202) {
      refresh();
      return;
    }
    var msg = (res.body && res.body.error) || 'Could not start classification.';
    text($('#sentiment-status'), msg);
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#classify-btn').addEventListener('click', function () {
      TS.api.startSentimentClassification(state.workspaceId).then(handleClassifyResponse);
    });

    $('#search-input').addEventListener('input', function (evt) {
      state.searchQuery = evt.target.value;
      renderEvidenceTable();
    });

    $('#label-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-label-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-label-filter');
      state.labelFilter = raw === '' ? null : raw;
      renderLabelChips();
      renderEvidenceTable();
    });

    $('#evidence-table-body').addEventListener('click', function (evt) {
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
