/* AI Visibility page controller (/visibility-tracking). Visibility Score,
   Mention Rate, Citation Rate, Share of Voice, trend, per-engine breakdown,
   topic breakdown, competitor comparison, and supporting evidence -- every
   number sourced from GET .../report (metrics_daily only, per the
   product's own "dashboards never read raw provider answers" invariant),
   GET .../evidence (brand_rankings + latest-run answers, already the
   Dashboard/Prompts pages' data source), and GET .../tracking (schedule
   state). Also the one real path to turn on recurring scanning
   (PUT .../scan-schedule), since metrics_daily only ever populates from
   scheduled runs and nothing else in the product exposes that control yet. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    report: null,
    answerIndex: {},
    schedule: null
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function text(el, value) { el.textContent = value == null ? '' : value; }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function fmtPct(value) { return value == null ? '—' : (Math.round(value * 1000) / 10) + '%'; }
  function fmtScore(value) { return value == null ? '—' : (Math.round(value * 10) / 10).toFixed(1); }
  function fmtDateTime(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>AI Visibility</h1><p>How <span id="project-name"></span> shows up across AI engines.</p></div></div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Recurring scan</h4></div>' +
    '  <div class="pb">' +
    '    <p class="hint" style="margin:0 0 10px">The Visibility Score below is measured only from ' +
    '      <b>scheduled</b> scans, not one-off "Run scan" clicks -- so someone actively testing changes ' +
    '      never biases the official number. Turn on a recurring schedule to start measuring it.</p>' +
    '    <div class="field">' +
    '      <select id="schedule-frequency"><option value="daily">Daily</option>' +
    '        <option value="weekly" selected>Weekly</option><option value="monthly">Monthly</option></select>' +
    '      <input type="text" id="schedule-region" placeholder="Region (optional, e.g. US)" style="max-width:200px">' +
    '      <button type="button" class="mini" id="schedule-enabled-toggle" aria-pressed="false" title="Enabled">&check;</button>' +
    '      <button class="btn sm" id="schedule-save">Save</button>' +
    '    </div>' +
    '    <p class="hint" id="schedule-status" style="margin-top:8px"></p>' +
    '    <p class="form-error" id="schedule-error" hidden></p>' +
    '  </div>' +
    '</div>' +

    '<div class="cols c4">' +
    '  <div class="panel"><div class="pb" id="hero-tile"></div></div>' +
    '  <div class="panel"><div class="pb" id="mention-tile"></div></div>' +
    '  <div class="panel"><div class="pb" id="citation-tile"></div></div>' +
    '  <div class="panel"><div class="pb" id="sov-tile"></div></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Visibility score trend</h4></div>' +
    '  <div class="pb" id="trend-container"></div>' +
    '</div>' +

    '<div class="cols c2">' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Per-engine breakdown</h4></div>' +
    '    <div class="tw"><table class="data"><thead><tr>' +
    '      <th>Engine</th><th>Score</th><th>Mentions</th><th>Citations</th><th>SoV</th><th>Answers</th>' +
    '    </tr></thead><tbody id="engine-table-body"></tbody></table></div>' +
    '    <div class="empty" id="engine-table-empty" hidden style="margin:14px"><p>No engine data yet.</p></div>' +
    '  </div>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Topic breakdown</h4></div>' +
    '    <div class="tw"><table class="data"><thead><tr>' +
    '      <th>Topic</th><th>Mention rate</th><th>Citation rate</th>' +
    '    </tr></thead><tbody id="topic-table-body"></tbody></table></div>' +
    '    <div class="empty" id="topic-table-empty" hidden style="margin:14px"><p>No scheduled-scan data by topic yet.</p></div>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Competitor comparison</h4></div>' +
    '  <div class="pb" id="competitor-caption" style="padding-bottom:0"></div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>#</th><th>Brand</th><th>Mentions</th><th>Visibility</th><th>Share of voice</th><th>Avg. source position</th>' +
    '  </tr></thead><tbody id="competitor-table-body"></tbody></table></div>' +
    '  <div class="empty" id="competitor-table-empty" hidden style="margin:14px"><p>No scan has completed yet.</p></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Supporting evidence</h4></div>' +
    '  <div class="pb" id="evidence-caption" style="padding-bottom:0"></div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Prompt</th><th>Engine</th><th>Mentioned</th><th>Cited</th><th></th>' +
    '  </tr></thead><tbody id="evidence-table-body"></tbody></table></div>' +
    '  <div class="empty" id="evidence-table-empty" hidden style="margin:14px"><p>No scans have completed yet.</p></div>' +
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
    Promise.all([
      TS.api.getReport(id),
      TS.api.getEvidence(id),
      TS.api.getTracking(id)
    ]).then(function (results) {
      var reportRes = results[0], evidenceRes = results[1], trackingRes = results[2];
      if (reportRes.ok) {
        state.report = reportRes.body;
        text($('#project-name'), (state.report.project && (state.report.project.brand_name || state.report.project.domain)) || '');
        renderStatTiles();
        renderEngineTable();
        renderTopicTable();
        TS.renderTrendChart($('#trend-container'), state.report.history);
      }
      if (evidenceRes.ok) {
        state.answerIndex = buildAnswerIndex(evidenceRes.body.evidence.answers);
        renderCompetitorTable(evidenceRes.body.evidence);
        renderEvidenceTable(evidenceRes.body.evidence);
      }
      if (trackingRes.ok) {
        state.schedule = trackingRes.body.tracking.schedule;
        renderScheduleForm();
      }
    });
  }

  function buildAnswerIndex(answers) {
    var map = {};
    (answers || []).forEach(function (a) {
      if (!map[a.prompt_id]) map[a.prompt_id] = [];
      map[a.prompt_id].push(a);
    });
    return map;
  }

  /* ------------------------------------------------------- stat tiles -- */

  function renderStatTiles() {
    var v = state.report.visibility;
    $('#hero-tile').innerHTML = TS.statTile({
      label: 'Visibility score',
      envelope: v.state === 'ok' ? v.visibility_score : null,
      formatValue: fmtScore,
      delta: v.delta,
      emptyState: emptyCopyFor(v)
    });
    $('#mention-tile').innerHTML = TS.statTile({
      label: 'Mention rate', envelope: v.mention_rate, formatValue: fmtPct,
      emptyState: { title: 'No data yet', body: '' }
    });
    $('#citation-tile').innerHTML = TS.statTile({
      label: 'Citation rate', envelope: v.citation_rate, formatValue: fmtPct,
      emptyState: { title: 'No data yet', body: '' }
    });
    $('#sov-tile').innerHTML = TS.statTile({
      label: 'Share of voice',
      envelope: (v.sov && v.sov.value != null) ? v.sov : null,
      formatValue: fmtPct,
      emptyState: { title: 'No data yet', body: 'Needs at least one tracked competitor and a scheduled scan.' }
    });
  }

  function emptyCopyFor(v) {
    if (v.state === 'insufficient') {
      return {
        title: 'Collecting data',
        body: v.n + ' of ' + v.threshold + ' scheduled-scan answers gathered so far.'
      };
    }
    if (v.state === 'absent') {
      return { title: 'Brand not mentioned', body: 'The last scheduled scan completed, but the brand was not mentioned.' };
    }
    return { title: 'Not yet measured', body: 'Turn on a recurring scan above -- this score only counts scheduled runs.' };
  }

  /* ------------------------------------------------------ engine table -- */

  function renderEngineTable() {
    var body = $('#engine-table-body'), empty = $('#engine-table-empty');
    clearChildren(body);
    var byEngine = {};
    (state.report.engines || []).forEach(function (row) {
      var existing = byEngine[row.engine_id];
      if (!existing || String(row.date) > String(existing.date)) byEngine[row.engine_id] = row;
    });
    var rows = Object.keys(byEngine).map(function (k) { return byEngine[k]; });
    if (!rows.length) { empty.hidden = false; return; }
    empty.hidden = true;
    rows.forEach(function (row) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + esc(row.display_name || ('Engine ' + row.engine_id)) + '</td>' +
        '<td class="tnum">' + fmtScore(row.visibility_score) + '</td>' +
        '<td class="tnum">' + fmtPct(row.mention_rate) + '</td>' +
        '<td class="tnum">' + fmtPct(row.citation_rate) + '</td>' +
        '<td class="tnum">' + fmtPct(row.sov) + '</td>' +
        '<td class="tnum">' + (row.answer_count == null ? '—' : row.answer_count) + '</td>';
      body.appendChild(tr);
    });
  }

  /* ------------------------------------------------------- topic table -- */

  function renderTopicTable() {
    var body = $('#topic-table-body'), empty = $('#topic-table-empty');
    clearChildren(body);
    var rows = state.report.topic_breakdown || [];
    if (!rows.length) { empty.hidden = false; return; }
    empty.hidden = true;
    rows.forEach(function (row) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + esc(row.topic) + '</td>' +
        '<td class="tnum">' + fmtEnvelope(row.mention_rate) + '</td>' +
        '<td class="tnum">' + fmtEnvelope(row.citation_rate) + '</td>';
      body.appendChild(tr);
    });
  }

  function fmtEnvelope(envelope) {
    if (!envelope || envelope.value == null) return '—';
    var range = (envelope.low != null && envelope.high != null)
      ? ' (' + fmtPct(envelope.low) + '–' + fmtPct(envelope.high) + ')' : '';
    return fmtPct(envelope.value) + range + ' <span class="hint">&middot; n=' + envelope.n + '</span>';
  }

  /* ------------------------------------------------- competitor table -- */

  function renderCompetitorTable(evidence) {
    var body = $('#competitor-table-body'), empty = $('#competitor-table-empty'), caption = $('#competitor-caption');
    clearChildren(body);
    var rankings = evidence.brand_rankings || [];
    caption.innerHTML = evidence.run
      ? '<span class="hint">Based on the latest scan &middot; ' + fmtDateTime(evidence.run.completed_at || evidence.run.created_at) + '</span>'
      : '';
    if (!rankings.length) { empty.hidden = false; return; }
    empty.hidden = true;
    rankings.forEach(function (row) {
      var tr = document.createElement('tr');
      var nameCell = esc(row.name) + (row.tracked ? ' <span class="badge brand">You</span>' : '');
      tr.innerHTML =
        '<td data-l="#" class="tnum">' + (row.rank == null ? '—' : row.rank) + '</td>' +
        '<td data-l="Brand">' + nameCell + '</td>' +
        '<td data-l="Mentions" class="tnum">' + row.mention_count + ' / ' + row.answer_count + '</td>' +
        '<td data-l="Visibility" class="tnum">' + (row.visibility == null ? '—' : row.visibility + '%') + '</td>' +
        '<td data-l="Share of voice" class="tnum">' + (row.share_of_voice == null ? '—' : row.share_of_voice + '%') + '</td>' +
        '<td data-l="Avg. source position" class="tnum">' + (row.average_source_position == null ? '—' : row.average_source_position) + '</td>';
      body.appendChild(tr);
    });
  }

  /* --------------------------------------------------- evidence table -- */

  function renderEvidenceTable(evidence) {
    var body = $('#evidence-table-body'), empty = $('#evidence-table-empty'), caption = $('#evidence-caption');
    clearChildren(body);
    var answers = evidence.answers || [];
    caption.textContent = evidence.run
      ? 'Last scan ' + fmtDateTime(evidence.run.completed_at || evidence.run.created_at) +
        ' · ' + (evidence.run.completed_count || 0) + '/' + (evidence.run.prompt_count || 0) + ' prompts'
      : '';
    if (!answers.length) { empty.hidden = false; return; }
    empty.hidden = true;
    answers.slice(0, 25).forEach(function (answer) {
      var tr = document.createElement('tr');
      var mentionedBadge = '<span class="badge ' + (answer.brand_mentioned ? 'yes' : 'no') + '">' +
        (answer.brand_mentioned ? ('Yes' + (answer.brand_rank ? ' · #' + answer.brand_rank : '')) : 'No') + '</span>';
      var citedBadge = '<span class="badge ' + (answer.brand_cited ? 'yes' : 'no') + '">' + (answer.brand_cited ? 'Yes' : 'No') + '</span>';
      tr.innerHTML =
        '<td data-l="Prompt">' + esc(answer.prompt || '—') + '</td>' +
        '<td data-l="Engine">' + esc(answer.provider || '—') + '</td>' +
        '<td data-l="Mentioned">' + mentionedBadge + '</td>' +
        '<td data-l="Cited">' + citedBadge + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-answer="' + answer.prompt_id + '">View</button></td>';
      body.appendChild(tr);
    });
  }

  function openAnswerDrawer(promptId) {
    var answers = state.answerIndex[promptId] || [];
    var promptText = answers.length ? answers[0].prompt : 'Answer';
    text($('#drawer-title'), promptText);
    var body = $('#drawer-body');
    if (!answers.length) {
      body.innerHTML = '<div class="empty"><b>Not scanned</b><p>No evidence recorded for this prompt.</p></div>';
    } else {
      body.innerHTML = answers.map(function (a) {
        var mentionBadge = '<span class="badge ' + (a.brand_mentioned ? 'yes' : 'no') + '">' +
          (a.brand_mentioned ? ('Mentioned' + (a.brand_rank ? ' · #' + a.brand_rank : '')) : 'Not mentioned') + '</span>';
        var citedBadge = '<span class="badge ' + (a.brand_cited ? 'yes' : 'no') + '">' + (a.brand_cited ? 'Cited' : 'Not cited') + '</span>';
        var answerBlock = a.answer_text ? '<div class="answer">' + esc(a.answer_text) + '</div>' : '<p class="hint">No answer text recorded.</p>';
        return '<div class="panel"><div class="ph"><h4>' + esc(a.provider || 'Engine') + '</h4>' +
          mentionBadge + citedBadge + '</div><div class="pb">' + answerBlock + '</div></div>';
      }).join('');
    }
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
  }

  function closeDrawer() {
    $('#drawer-scrim').classList.remove('open');
    $('#answer-drawer').classList.remove('open');
  }

  /* --------------------------------------------------------- schedule -- */

  function renderScheduleForm() {
    var freqSelect = $('#schedule-frequency'), regionInput = $('#schedule-region'),
      toggle = $('#schedule-enabled-toggle'), status = $('#schedule-status');
    var s = state.schedule;
    if (s) {
      freqSelect.value = s.frequency || 'weekly';
      regionInput.value = s.region || '';
      toggle.setAttribute('aria-pressed', s.enabled ? 'true' : 'false');
      status.textContent = s.enabled
        ? ('On · next run ' + fmtDateTime(s.next_run_at) + (s.last_run_at ? (' · last run ' + fmtDateTime(s.last_run_at)) : ''))
        : 'Off';
    } else {
      toggle.setAttribute('aria-pressed', 'false');
      status.textContent = 'Not configured yet.';
    }
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#schedule-enabled-toggle').addEventListener('click', function () {
      var toggle = $('#schedule-enabled-toggle');
      toggle.setAttribute('aria-pressed', toggle.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    });

    $('#schedule-save').addEventListener('click', function () {
      var errorEl = $('#schedule-error');
      errorEl.hidden = true;
      var data = {
        enabled: $('#schedule-enabled-toggle').getAttribute('aria-pressed') === 'true',
        frequency: $('#schedule-frequency').value,
        region: $('#schedule-region').value.trim()
      };
      TS.api.updateSchedule(state.workspaceId, data).then(function (res) {
        if (res.ok) {
          state.schedule = res.body.tracking.schedule;
          renderScheduleForm();
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not save the schedule.');
          errorEl.hidden = false;
        }
      });
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
