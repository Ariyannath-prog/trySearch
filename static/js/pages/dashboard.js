/* Dashboard page controller (/analytics). GET .../report now accepts the
   shared date-range/region/engine filter system (app/analytics_filters.py)
   and returns already-filtered data - this page never filters client-side,
   it only renders what the backend already scoped. The filter bar's state
   is read from GET .../report's own available_filters (regions actually in
   this workspace's history, engines from the existing registry), same
   TS.filters component every other retrofitted page uses. Requires api.js,
   shell.js, statTile.js, trendChart.js, components/filters.js. */
(function () {
  'use strict';

  var state = {
    projects: [],
    activeWorkspaceId: null,
    report: null,
    pollTimer: null
  };

  var STATE_COPY = {
    not_yet_run: {
      title: 'Not yet run',
      body: 'No completed scan yet. Run a scan to start measuring visibility.'
    },
    absent: {
      title: 'Brand not mentioned',
      body: 'The last scan completed, but the brand was not mentioned in any tracked answer.'
    }
  };

  /* A non-2xx analytics response used to hit `if (!res.ok) return;` and
     leave the page on its initial empty render, so a 400 was indistinguishable
     from "you have no data". These map a status onto copy a user can act on.
     The server's own message is deliberately not shown: it is written for a
     developer and can carry validation/SQL detail. It goes to the console. */
  var ERROR_COPY = {
    0: {
      title: 'Cannot reach trySearch',
      body: 'Check your connection and try again.'
    },
    400: {
      title: 'Those filters could not be applied',
      body: 'The current date range, region or engine selection was rejected. Reset the filters to continue.',
      resettable: true
    },
    401: {
      title: 'Your session has expired',
      body: 'Sign in again to keep working.',
      signIn: true
    },
    403: {
      title: 'No access to this project',
      body: 'Your account cannot view this workspace. Switch projects, or ask an owner for access.'
    },
    404: {
      title: 'Project not found',
      body: 'This project may have been deleted. Pick another from the project switcher.'
    },
    429: {
      title: 'Too many requests',
      body: 'Slow down for a moment, then try again.'
    }
  };

  var GENERIC_ERROR = {
    title: 'Something went wrong',
    body: 'We could not load this dashboard. Try again in a moment.'
  };

  function errorCopyFor(res) {
    var status = res && res.status;
    if (ERROR_COPY[status]) return ERROR_COPY[status];
    if (status === 422) return ERROR_COPY[400];
    return GENERIC_ERROR;
  }

  /* Full detail for whoever is debugging, never for the page. */
  function logApiFailure(context, res) {
    if (!window.console || !console.error) return;
    console.error('[trySearch] ' + context + ' failed', {
      status: res && res.status,
      networkError: !!(res && res.networkError),
      body: res && res.body,
      error: res && res.error
    });
  }

  function $(sel, root) { return (root || document).querySelector(sel); }
  function text(el, value) { el.textContent = value == null ? '' : value; }
  function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  function fmtPct(value) { return value == null ? '—' : (Math.round(value * 1000) / 10) + '%'; }
  function fmtScore(value) { return value == null ? '—' : (Math.round(value * 10) / 10).toFixed(1); }
  function fmtDateTime(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  var VIEWS_HTML =
    '<div class="phead">' +
    '  <div><h1>Dashboard</h1><p>Visibility, mentions, and citations for <span id="project-name"></span>.</p></div>' +
    '  <div style="display:flex;align-items:center;gap:10px">' +
    '    <button class="btn" id="run-scan-btn">Run new scan</button>' +
    '  </div>' +
    '</div>' +
    '<div class="hint" id="scan-status"></div>' +
    '<div class="empty" id="load-error" role="alert" aria-live="polite" hidden style="margin:0 0 12px"></div>' +
    '<div class="panel"><div class="pb" id="filter-bar"></div></div>' +
    '<p class="hint" id="scan-summary-line" style="margin:0 0 10px"></p>' +
    '<div class="panel" id="quick-add-panel" hidden>' +
    '  <div class="ph"><h4>Add a tracked prompt</h4></div>' +
    '  <div class="pb">' +
    '    <div class="field">' +
    '      <input type="text" id="quick-add-input" placeholder="e.g. best project management tools for startups" minlength="8" maxlength="1000">' +
    '      <button class="btn sm" id="quick-add-submit">Add prompt</button>' +
    '    </div>' +
    '    <p class="form-error" id="quick-add-error" hidden></p>' +
    '  </div>' +
    '</div>' +
    '<div class="empty" id="state-banner" hidden></div>' +
    '<div class="cols c3">' +
    '  <div class="panel"><div class="pb" id="hero-tile"></div></div>' +
    '  <div class="panel"><div class="pb" id="mention-tile"></div></div>' +
    '  <div class="panel"><div class="pb" id="citation-tile"></div></div>' +
    '</div>' +
    '<div class="cols" style="grid-template-columns:2fr 1fr">' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Visibility score trend</h4></div>' +
    '    <div class="pb" id="trend-container"></div>' +
    '  </div>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Per-engine breakdown</h4></div>' +
    '    <div class="tw"><table class="data"><thead><tr><th>Engine</th><th>Score</th><th>Mentions</th><th>Answers</th></tr></thead>' +
    '      <tbody id="engine-table-body"></tbody></table></div>' +
    '    <div class="empty" id="engine-table-empty" hidden style="margin:14px"><p>No engine data yet.</p></div>' +
    '  </div>' +
    '</div>' +
    '<div class="cols c2">' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Site health</h4></div>' +
    '    <div class="pb" id="site-health-body"></div>' +
    '  </div>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Recent evidence</h4></div>' +
    '    <div class="pb" id="evidence-caption" style="padding-bottom:0"></div>' +
    '    <div class="tw"><table class="data cards"><thead><tr><th>Prompt</th><th>Mentioned</th><th>Cited</th><th>Sources</th></tr></thead>' +
    '      <tbody id="evidence-table-body"></tbody></table></div>' +
    '    <div class="empty" id="evidence-table-empty" hidden style="margin:14px"><p>No scans have completed yet.</p></div>' +
    '  </div>' +
    '</div>';

  var EMPTY_STATE_HTML =
    '<div class="empty" style="max-width:480px;margin:8vh auto 0;text-align:left">' +
    '  <b style="font-size:16px;display:block;margin-bottom:6px">Add your website to get started</b>' +
    '  <p style="margin:0 0 14px">trySearch tracks how AI engines answer questions about your brand. Add a website to create your first project.</p>' +
    '  <a class="btn" href="/onboarding">Get started</a>' +
    '</div>';

  function boot() {
    var views = $('#views');
    views.classList.add('skel');
    TS.api.listProjects().then(function (res) {
      views.classList.remove('skel');
      if (!res.ok) {
        logApiFailure('GET projects', res);
        var copy = errorCopyFor(res);
        views.innerHTML = '<div class="empty" role="alert"><b>' + esc(copy.title) + '</b><p>' +
          esc(copy.body) + '</p></div>';
        return;
      }
      state.projects = (res.body && res.body.projects) || [];
      if (!state.projects.length) {
        views.innerHTML = EMPTY_STATE_HTML;
        return;
      }
      views.innerHTML = VIEWS_HTML;
      wireDashboardControls();
      var activeId = TS.shell.activeProjectId();
      var initial = state.projects.filter(function (p) { return String(p.id) === String(activeId); })[0] || state.projects[0];
      loadWorkspace(initial.id);
    });
  }

  function wireDashboardControls() {
    $('#run-scan-btn').addEventListener('click', function () {
      if (!state.activeWorkspaceId) return;
      text($('#scan-status'), 'Starting scan…');
      TS.api.startScan(state.activeWorkspaceId).then(handleScanResponse);
    });
    $('#quick-add-submit').addEventListener('click', function () {
      var input = $('#quick-add-input');
      var errorEl = $('#quick-add-error');
      errorEl.hidden = true;
      var prompt = input.value.trim();
      if (prompt.length < 8 || prompt.length > 1000) {
        text(errorEl, 'Enter a prompt between 8 and 1,000 characters.');
        errorEl.hidden = false;
        return;
      }
      TS.api.addTrackedPrompt(state.activeWorkspaceId, { prompt: prompt }).then(function (res) {
        if (res.ok) {
          input.value = '';
          $('#quick-add-panel').hidden = true;
          text($('#scan-status'), 'Prompt added. Click "Run new scan" to start.');
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not add that prompt.');
          errorEl.hidden = false;
        }
      });
    });
  }

  function hideLoadError() {
    var el = $('#load-error');
    if (el) el.hidden = true;
  }

  /* Visible, actionable, and never a stack trace. The detail goes to the
     console; the page gets copy plus the one or two things worth doing. */
  function showLoadError(context, res) {
    logApiFailure(context, res);
    var el = $('#load-error');
    if (!el) return;
    var copy = errorCopyFor(res);
    var actions = '<button class="btn sm" id="load-error-retry" type="button">Try again</button>';
    if (copy.resettable) {
      actions += ' <button class="btn sm ghost" id="load-error-reset" type="button">Reset filters</button>';
    }
    if (copy.signIn) {
      actions = '<a class="btn sm" href="/login">Sign in</a>';
    }
    el.innerHTML =
      '<b>' + esc(copy.title) + '</b>' +
      '<p>' + esc(copy.body) + '</p>' +
      '<div style="margin-top:10px">' + actions + '</div>';
    el.hidden = false;

    var retry = $('#load-error-retry', el);
    if (retry) {
      retry.addEventListener('click', function () {
        hideLoadError();
        loadWorkspace(state.activeWorkspaceId);
      });
    }
    var reset = $('#load-error-reset', el);
    if (reset) {
      reset.addEventListener('click', function () {
        TS.filters.setState({ range: null, startDate: null, endDate: null, region: null, engineIds: [] });
        hideLoadError();
        loadWorkspace(state.activeWorkspaceId);
      });
    }
  }

  function loadWorkspace(id) {
    state.activeWorkspaceId = id;
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    var queryString = TS.filters.toQueryString(TS.filters.getState());
    TS.api.getReport(id, queryString).then(function (res) {
      if (!res.ok) {
        showLoadError('GET report', res);
        return;
      }
      hideLoadError();
      state.report = res.body;
      text($('#project-name'), (res.body.project && (res.body.project.brand_name || res.body.project.domain)) || '');
      renderFilterBar(res.body.available_filters || { regions: [], engines: [] });
      renderDashboard(res.body);
      refreshEvidence(id);
    });
  }

  function renderFilterBar(availableFilters) {
    TS.filters.render($('#filter-bar'), {
      regions: availableFilters.regions || [],
      engines: availableFilters.engines || [],
      onApply: function () { loadWorkspace(state.activeWorkspaceId); },
    });
  }

  function renderScanSummary(summary) {
    var el = $('#scan-summary-line');
    if (!summary || !summary.total) {
      text(el, 'No scans match the selected filters.');
      return;
    }
    text(el, summary.total + ' scan(s) in range — ' + summary.completed + ' succeeded, ' +
      summary.partial + ' partial, ' + summary.failed + ' failed — ' +
      summary.prompts_completed + ' of ' + summary.prompts_total + ' prompts answered.');
  }

  function renderDashboard(report) {
    var v = report.visibility;

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

    renderStateBanner(v);
    renderEngineTable(report.engines);
    renderScanSummary(report.scan_summary);
    TS.renderTrendChart($('#trend-container'), report.history);
    renderSiteHealth(report.site_health);
  }

  function emptyCopyFor(v) {
    if (v.state === 'insufficient') {
      return {
        title: 'Collecting data',
        body: v.n + ' of ' + v.threshold + ' answers gathered. The score appears once enough evidence exists.'
      };
    }
    var copy = STATE_COPY[v.state] || STATE_COPY.not_yet_run;
    return copy;
  }

  function renderStateBanner(v) {
    var banner = $('#state-banner');
    if (state.pollTimer) return; // active-scan banner takes priority, set in refreshEvidence
    if (v.state === 'not_yet_run') {
      banner.hidden = false;
      banner.classList.remove('running');
      banner.innerHTML = '<b>No scan yet</b><p>Run a scan above once you have at least one tracked prompt.</p>';
    } else {
      banner.hidden = true;
    }
  }

  function renderEngineTable(engines) {
    var body = $('#engine-table-body'), empty = $('#engine-table-empty');
    clearChildren(body);
    var byEngine = {};
    (engines || []).forEach(function (row) {
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
        '<td class="tnum">' + (row.answer_count == null ? '—' : row.answer_count) + '</td>';
      body.appendChild(tr);
    });
  }

  function renderSiteHealth(siteHealth) {
    var body = $('#site-health-body');
    if (!siteHealth || !siteHealth.run) {
      body.innerHTML = '<p class="hint">No site audit yet.</p>';
      return;
    }
    var run = siteHealth.run;
    var score = run.readiness_score == null ? null : Math.round(run.readiness_score);
    var findingCount = (siteHealth.findings || []).length;
    body.innerHTML =
      '<div class="meter"><div class="mtrack"><i style="width:' + (score || 0) + '%;background:' +
      (score == null ? 'var(--ink-4)' : score >= 70 ? 'var(--good)' : score >= 40 ? 'var(--warn)' : 'var(--crit)') +
      '"></i></div><span class="tnum">' + (score == null ? '—' : score) + '</span></div>' +
      '<p class="hint" style="margin-top:10px">' + esc(run.status || 'unknown') + ' · ' +
      findingCount + ' findings</p>';
  }

  function refreshEvidence(workspaceId) {
    var queryString = TS.filters.toQueryString(TS.filters.getState());
    TS.api.getEvidence(workspaceId, null, queryString).then(function (res) {
      // A workspace switch mid-flight is not a failure, just a stale reply.
      if (state.activeWorkspaceId !== workspaceId) return;
      if (!res.ok) {
        showLoadError('GET evidence', res);
        return;
      }
      renderEvidenceTable(res.body.evidence);
      if (res.body.active_job) {
        showScanRunning();
        state.pollTimer = setTimeout(function () { refreshEvidence(workspaceId); }, 5000);
      } else {
        hideScanRunning();
        if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
      }
    });
  }

  function showScanRunning() {
    $('#run-scan-btn').disabled = true;
    text($('#scan-status'), 'A scan is running…');
    var banner = $('#state-banner');
    banner.hidden = false;
    banner.classList.add('running');
    banner.innerHTML = '<b>Scan running</b><p>Figures below are from the last completed run and will update automatically.</p>';
  }

  function hideScanRunning() {
    $('#run-scan-btn').disabled = false;
    text($('#scan-status'), '');
    var banner = $('#state-banner');
    if (banner.classList.contains('running')) banner.hidden = true;
    banner.classList.remove('running');
    if (state.report) renderStateBanner(state.report.visibility);
  }

  function renderEvidenceTable(evidence) {
    var body = $('#evidence-table-body'), empty = $('#evidence-table-empty'), caption = $('#evidence-caption');
    clearChildren(body);
    var answers = (evidence && evidence.answers) || [];
    caption.textContent = (evidence && evidence.run)
      ? 'Last scan ' + fmtDateTime(evidence.run.completed_at || evidence.run.created_at) +
        ' · ' + (evidence.run.completed_count || 0) + '/' + (evidence.run.prompt_count || 0) + ' prompts'
      : '';
    if (!answers.length) { empty.hidden = false; return; }
    empty.hidden = true;
    answers.slice(0, 25).forEach(function (answer) {
      var tr = document.createElement('tr');
      var mentionedBadge = '<span class="badge ' + (answer.brand_mentioned ? 'yes' : 'no') + '">' +
        (answer.brand_mentioned ? ('Yes' + (answer.brand_rank ? ' · #' + answer.brand_rank : '')) : 'No') + '</span>';
      var citedBadge = '<span class="badge ' + (answer.brand_cited ? 'yes' : 'no') + '">' +
        (answer.brand_cited ? 'Yes' : 'No') + '</span>';
      tr.innerHTML =
        '<td data-l="Prompt">' + esc(answer.prompt || '—') + '</td>' +
        '<td data-l="Mentioned">' + mentionedBadge + '</td>' +
        '<td data-l="Cited">' + citedBadge + '</td>' +
        '<td data-l="Sources" class="tnum">' + (answer.sources || []).length + '</td>';
      body.appendChild(tr);
    });
  }

  function handleScanResponse(res) {
    var statusEl = $('#scan-status');
    if (res.status === 202) {
      text(statusEl, 'Scan queued…');
      $('#quick-add-panel').hidden = true;
      refreshEvidence(state.activeWorkspaceId);
      return;
    }
    var msg = (res.body && res.body.error) || 'Could not start a scan.';
    if (res.status === 409) {
      $('#quick-add-panel').hidden = false;
      text(statusEl, msg);
    } else if (res.status === 402) {
      var spend = res.body ? res.body.spend_usd : null;
      var ceiling = res.body ? res.body.ceiling_usd : null;
      text(statusEl, msg + (spend != null ? (' ($' + spend + ' of $' + ceiling + ')') : ''));
    } else {
      text(statusEl, msg);
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
