/* Prompts page controller (/prompt-intelligence). Full ongoing prompt
   management: view/search/filter by topic, add/edit/delete, active toggle,
   latest-scan status per prompt (from the latest run's evidence, per
   engine), read-only engine coverage, and "Run scan" (scans every active
   prompt -- the backend has no per-prompt subset scan, so this mirrors the
   Dashboard's exact mechanism rather than faking a selection). */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    topics: [],
    prompts: [],
    answerIndex: {},   // prompt_id -> [answer, ...] from the latest run
    evidenceRun: null,
    engines: [],
    searchQuery: '',
    topicFilter: null, // null = all, 'none' = no topic, otherwise a topic id
    pollTimer: null
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
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
    '<div class="phead">' +
    '  <div><h1>Prompts</h1><p>Tracked prompts for <span id="project-name"></span>.</p></div>' +
    '  <button class="btn" id="run-scan-btn">Run scan</button>' +
    '</div>' +
    '<div class="hint" id="scan-status"></div>' +
    '<div class="empty" id="state-banner" hidden></div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Add a prompt</h4></div>' +
    '  <div class="pb">' +
    '    <div class="field">' +
    '      <input type="text" id="add-prompt-input" placeholder="e.g. best project management tools for startups" style="flex:2 1 260px">' +
    '      <select id="add-prompt-topic"><option value="">No topic</option></select>' +
    '      <input type="text" id="add-prompt-intent" placeholder="Intent (default: Discovery)" style="max-width:180px">' +
    '      <button class="btn sm" id="add-prompt-submit">Add</button>' +
    '    </div>' +
    '    <p class="form-error" id="add-prompt-error" hidden></p>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Topics</h4></div>' +
    '  <div class="pb">' +
    '    <div class="field">' +
    '      <input type="text" id="add-topic-input" placeholder="New topic name" style="max-width:240px">' +
    '      <button class="btn ghost sm" id="add-topic-submit">Add topic</button>' +
    '    </div>' +
    '    <div id="topic-manage-list" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px"></div>' +
    '    <p class="form-error" id="add-topic-error" hidden></p>' +
    '  </div>' +
    '</div>' +

    '<div class="cols" style="grid-template-columns:1fr auto;align-items:center">' +
    '  <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
    '    <input type="text" id="search-input" placeholder="Search prompts&hellip;" style="max-width:240px">' +
    '    <div id="topic-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '  </div>' +
    '  <div class="hint" id="engine-coverage"></div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Tracked prompts</h4><span class="badge thin" id="prompt-count-badge"></span></div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Prompt</th><th>Topic</th><th>Intent</th><th>Active</th><th>Latest scan</th><th></th>' +
    '  </tr></thead><tbody id="prompts-table-body"></tbody></table></div>' +
    '  <div class="empty" id="prompts-empty" hidden><p>No prompts match.</p></div>' +
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
      wirePageControls();
      loadWorkspace(initial.id);
    });
  }

  function loadWorkspace(id) {
    state.workspaceId = id;
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    Promise.all([
      TS.api.getTracking(id),
      TS.api.getEvidence(id),
      TS.api.getWorkspaceEngines(id)
    ]).then(function (results) {
      var trackingRes = results[0], evidenceRes = results[1], enginesRes = results[2];
      if (trackingRes.ok) {
        state.project = trackingRes.body.project;
        state.topics = trackingRes.body.tracking.topics || [];
        state.prompts = trackingRes.body.tracking.prompts || [];
        text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      }
      if (evidenceRes.ok) {
        state.evidenceRun = evidenceRes.body.evidence.run;
        state.answerIndex = buildAnswerIndex(evidenceRes.body.evidence.answers);
        handleActiveJob(evidenceRes.body.active_job);
      }
      if (enginesRes.ok) {
        state.engines = (enginesRes.body && enginesRes.body.engines) || [];
      }
      renderAll();
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

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderStateBanner();
    renderTopicOptions();
    renderTopicManageList();
    renderTopicFilterChips();
    renderEngineCoverage();
    renderPromptsTable();
  }

  function renderStateBanner() {
    var banner = $('#state-banner');
    if (state.pollTimer) return; // scan-running banner takes priority, set elsewhere
    if (!state.evidenceRun && !state.prompts.length) {
      banner.hidden = false;
      banner.innerHTML = '<b>No prompts yet</b><p>Add a prompt below, then run a scan.</p>';
    } else if (!state.evidenceRun) {
      banner.hidden = false;
      banner.innerHTML = '<b>Not scanned yet</b><p>Run a scan to see how these prompts perform.</p>';
    } else {
      banner.hidden = true;
    }
  }

  function renderTopicOptions() {
    var select = $('#add-prompt-topic');
    var current = select.value;
    clearChildren(select);
    var noneOpt = document.createElement('option');
    noneOpt.value = ''; noneOpt.textContent = 'No topic';
    select.appendChild(noneOpt);
    state.topics.forEach(function (t) {
      var opt = document.createElement('option');
      opt.value = t.id; opt.textContent = t.name;
      select.appendChild(opt);
    });
    select.value = current;
  }

  function renderTopicManageList() {
    var list = $('#topic-manage-list');
    if (!state.topics.length) {
      list.innerHTML = '<span class="hint">No topics yet.</span>';
      return;
    }
    list.innerHTML = state.topics.map(function (t) {
      return '<span class="chip plain" data-topic-manage="' + t.id + '">' + esc(t.name) +
        ' <button type="button" class="rowbtn" data-delete-topic="' + t.id + '" title="Delete topic" ' +
        'style="display:inline;margin-left:4px">&times;</button></span>';
    }).join('');
  }

  function renderTopicFilterChips() {
    var container = $('#topic-filter-chips');
    var chips = [{ id: null, label: 'All' }].concat(
      state.topics.map(function (t) { return { id: t.id, label: t.name }; })
    ).concat([{ id: 'none', label: 'No topic' }]);
    container.innerHTML = chips.map(function (c) {
      var on = state.topicFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-topic-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function renderEngineCoverage() {
    var el = $('#engine-coverage');
    var covered = state.engines.filter(function (e) { return e.enabled; }).map(function (e) { return e.display_name; });
    if (!state.engines.length) {
      text(el, 'No engines available.');
    } else if (!covered.length) {
      text(el, 'No engines selected for this project.');
    } else {
      text(el, 'Tracked engines: ' + covered.join(', '));
    }
  }

  function filteredPrompts() {
    var q = state.searchQuery.trim().toLowerCase();
    return state.prompts.filter(function (p) {
      if (state.topicFilter === 'none' && p.topic_id) return false;
      if (state.topicFilter != null && state.topicFilter !== 'none' && String(p.topic_id) !== String(state.topicFilter)) return false;
      if (q && p.prompt.toLowerCase().indexOf(q) === -1) return false;
      return true;
    });
  }

  function renderPromptsTable() {
    var body = $('#prompts-table-body'), empty = $('#prompts-empty');
    var list = filteredPrompts();
    text($('#prompt-count-badge'), state.prompts.length + ' total');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (p) {
      var tr = document.createElement('tr');
      tr.dataset.promptId = p.id;
      renderPromptRowView(tr, p);
      body.appendChild(tr);
    });
  }

  function renderPromptRowView(tr, p) {
    var answers = state.answerIndex[p.id] || [];
    var scanCell;
    if (!answers.length) {
      scanCell = '<span class="badge thin">Not scanned yet</span>';
    } else {
      var mentionedCount = answers.filter(function (a) { return a.brand_mentioned; }).length;
      scanCell = '<button type="button" class="badge ' + (mentionedCount > 0 ? 'yes' : 'no') + '" data-view-answer>' +
        mentionedCount + '/' + answers.length + ' engines</button>';
    }
    tr.innerHTML =
      '<td data-l="Prompt">' + esc(p.prompt) + '</td>' +
      '<td data-l="Topic">' + esc(p.topic_name || '—') + '</td>' +
      '<td data-l="Intent"><span class="badge thin">' + esc(p.intent) + '</span></td>' +
      '<td data-l="Active"><button type="button" class="mini" data-toggle-active aria-pressed="' + (p.active ? 'true' : 'false') + '" title="Active">&check;</button></td>' +
      '<td data-l="Latest scan">' + scanCell + '</td>' +
      '<td data-l="" style="white-space:nowrap">' +
      '  <button type="button" class="btn ghost sm" data-edit>Edit</button> ' +
      '  <button type="button" class="btn ghost sm" data-delete>Delete</button>' +
      '</td>';
  }

  function renderPromptRowEdit(tr, p) {
    var topicOptions = ['<option value="">No topic</option>'].concat(
      state.topics.map(function (t) {
        return '<option value="' + t.id + '"' + (t.id === p.topic_id ? ' selected' : '') + '>' + esc(t.name) + '</option>';
      })
    ).join('');
    tr.innerHTML =
      '<td data-l="Prompt"><input type="text" data-field="prompt" value="' + esc(p.prompt) + '" style="width:100%"></td>' +
      '<td data-l="Topic"><select data-field="topic_id">' + topicOptions + '</select></td>' +
      '<td data-l="Intent"><input type="text" data-field="intent" value="' + esc(p.intent) + '" style="width:100%"></td>' +
      '<td data-l="Active"><button type="button" class="mini" data-field="active" aria-pressed="' + (p.active ? 'true' : 'false') + '">&check;</button></td>' +
      '<td data-l="Latest scan"><span class="hint">&mdash;</span></td>' +
      '<td data-l="" style="white-space:nowrap">' +
      '  <button type="button" class="btn sm" data-save-edit>Save</button> ' +
      '  <button type="button" class="btn ghost sm" data-cancel-edit>Cancel</button>' +
      '  <p class="form-error" data-edit-error hidden style="margin-top:6px"></p>' +
      '</td>';
    var activeBtn = tr.querySelector('[data-field="active"]');
    activeBtn.addEventListener('click', function () {
      activeBtn.setAttribute('aria-pressed', activeBtn.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
    });
  }

  /* -------------------------------------------------------------- drawer */

  function openAnswerDrawer(prompt) {
    var answers = state.answerIndex[prompt.id] || [];
    text($('#drawer-title'), prompt.prompt);
    var body = $('#drawer-body');
    if (!answers.length) {
      body.innerHTML = '<div class="empty"><b>Not scanned yet</b><p>This prompt was not part of the most recent scan.</p></div>';
    } else {
      body.innerHTML = answers.map(function (a) {
        var mentionBadge = '<span class="badge ' + (a.brand_mentioned ? 'yes' : 'no') + '">' +
          (a.brand_mentioned ? ('Mentioned' + (a.brand_rank ? ' · #' + a.brand_rank : '')) : 'Not mentioned') + '</span>';
        var citedBadge = '<span class="badge ' + (a.brand_cited ? 'yes' : 'no') + '">' + (a.brand_cited ? 'Cited' : 'Not cited') + '</span>';
        var answerBlock = a.answer_text
          ? '<div class="answer">' + esc(a.answer_text) + '</div>'
          : '<p class="hint">No answer text recorded.</p>';
        var sources = (a.sources || []).slice(0, 8);
        var sourcesBlock = sources.length
          ? sources.map(function (s) {
              return '<div class="cite"><span class="u">' + esc(s.domain || s.url || '') + '</span>' +
                '<span class="badge thin">' + esc(s.category || '') + '</span></div>';
            }).join('')
          : '';
        return '<div class="panel"><div class="ph"><h4>' + esc(a.provider || 'Engine') + '</h4>' +
          mentionBadge + citedBadge + '</div><div class="pb">' + answerBlock +
          (sourcesBlock ? '<div style="margin-top:10px">' + sourcesBlock + '</div>' : '') + '</div></div>';
      }).join('');
    }
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
  }

  function closeDrawer() {
    $('#drawer-scrim').classList.remove('open');
    $('#answer-drawer').classList.remove('open');
  }

  /* --------------------------------------------------------------- scan */

  function handleActiveJob(job) {
    if (job) {
      showScanRunning();
      state.pollTimer = setTimeout(function () { refreshEvidence(); }, 5000);
    } else {
      hideScanRunning();
    }
  }

  function refreshEvidence() {
    TS.api.getEvidence(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      state.evidenceRun = res.body.evidence.run;
      state.answerIndex = buildAnswerIndex(res.body.evidence.answers);
      handleActiveJob(res.body.active_job);
      renderStateBanner();
      renderPromptsTable();
    });
  }

  function showScanRunning() {
    $('#run-scan-btn').disabled = true;
    text($('#scan-status'), 'A scan is running…');
    var banner = $('#state-banner');
    banner.hidden = false;
    banner.innerHTML = '<b>Scan running</b><p>Prompt results below are from the last completed run and will update automatically.</p>';
  }

  function hideScanRunning() {
    $('#run-scan-btn').disabled = false;
    text($('#scan-status'), '');
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    renderStateBanner();
  }

  function handleScanResponse(res) {
    var statusEl = $('#scan-status');
    if (res.status === 202) {
      text(statusEl, 'Scan queued…');
      refreshEvidence();
      return;
    }
    var msg = (res.body && res.body.error) || 'Could not start a scan.';
    if (res.status === 402) {
      var spend = res.body ? res.body.spend_usd : null, ceiling = res.body ? res.body.ceiling_usd : null;
      text(statusEl, msg + (spend != null ? (' ($' + spend + ' of $' + ceiling + ')') : ''));
    } else {
      text(statusEl, msg);
    }
  }

  /* ---------------------------------------------------------- controls */

  function wirePageControls() {
    $('#run-scan-btn').addEventListener('click', function () {
      text($('#scan-status'), 'Starting scan…');
      TS.api.startScan(state.workspaceId).then(handleScanResponse);
    });

    $('#search-input').addEventListener('input', function (evt) {
      state.searchQuery = evt.target.value;
      renderPromptsTable();
    });

    $('#topic-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-topic-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-topic-filter');
      state.topicFilter = raw === '' ? null : (raw === 'none' ? 'none' : Number(raw));
      renderTopicFilterChips();
      renderPromptsTable();
    });

    $('#add-prompt-submit').addEventListener('click', function () {
      var input = $('#add-prompt-input'), topicSelect = $('#add-prompt-topic'), intentInput = $('#add-prompt-intent');
      var errorEl = $('#add-prompt-error');
      errorEl.hidden = true;
      var promptText = input.value.trim();
      if (promptText.length < 8 || promptText.length > 1000) {
        text(errorEl, 'Enter a prompt between 8 and 1,000 characters.');
        errorEl.hidden = false;
        return;
      }
      var data = { prompt: promptText };
      if (topicSelect.value) data.topic_id = Number(topicSelect.value);
      if (intentInput.value.trim()) data.intent = intentInput.value.trim();
      TS.api.addTrackedPrompt(state.workspaceId, data).then(function (res) {
        if (res.ok) {
          input.value = ''; intentInput.value = ''; topicSelect.value = '';
          reloadTrackingOnly();
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not add that prompt.');
          errorEl.hidden = false;
        }
      });
    });

    $('#add-topic-submit').addEventListener('click', function () {
      var input = $('#add-topic-input');
      var errorEl = $('#add-topic-error');
      errorEl.hidden = true;
      var name = input.value.trim();
      if (!name) { text(errorEl, 'Enter a topic name.'); errorEl.hidden = false; return; }
      TS.api.createTopic(state.workspaceId, name).then(function (res) {
        if (res.ok) {
          input.value = '';
          reloadTrackingOnly();
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not add that topic.');
          errorEl.hidden = false;
        }
      });
    });

    $('#topic-manage-list').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-delete-topic]');
      if (!btn) return;
      if (!window.confirm('Delete this topic? Prompts assigned to it keep their data but lose the topic label.')) return;
      TS.api.deleteTopic(state.workspaceId, btn.getAttribute('data-delete-topic')).then(function (res) {
        if (res.ok) reloadTrackingOnly();
        else window.alert((res.body && res.body.error) || 'Could not delete that topic.');
      });
    });

    $('#prompts-table-body').addEventListener('click', function (evt) {
      var tr = evt.target.closest('tr');
      if (!tr) return;
      var promptId = Number(tr.dataset.promptId);
      var prompt = state.prompts.filter(function (p) { return p.id === promptId; })[0];
      if (!prompt) return;

      if (evt.target.closest('[data-view-answer]')) {
        openAnswerDrawer(prompt);
        return;
      }
      if (evt.target.closest('[data-toggle-active]')) {
        TS.api.updateTrackedPrompt(state.workspaceId, promptId, { active: !prompt.active }).then(function (res) {
          if (res.ok) reloadTrackingOnly();
          else window.alert((res.body && res.body.error) || 'Could not update that prompt.');
        });
        return;
      }
      if (evt.target.closest('[data-edit]')) {
        renderPromptRowEdit(tr, prompt);
        return;
      }
      if (evt.target.closest('[data-cancel-edit]')) {
        renderPromptRowView(tr, prompt);
        return;
      }
      if (evt.target.closest('[data-delete]')) {
        if (!window.confirm('Delete this prompt? This cannot be undone.')) return;
        TS.api.deleteTrackedPrompt(state.workspaceId, promptId).then(function (res) {
          if (res.ok) reloadTrackingOnly();
          else window.alert((res.body && res.body.error) || 'Could not delete that prompt.');
        });
        return;
      }
      if (evt.target.closest('[data-save-edit]')) {
        var errorEl = tr.querySelector('[data-edit-error]');
        errorEl.hidden = true;
        var promptText = tr.querySelector('[data-field="prompt"]').value.trim();
        if (promptText.length < 8 || promptText.length > 1000) {
          text(errorEl, 'Enter a prompt between 8 and 1,000 characters.');
          errorEl.hidden = false;
          return;
        }
        var topicValue = tr.querySelector('[data-field="topic_id"]').value;
        var intentValue = tr.querySelector('[data-field="intent"]').value.trim();
        var activeValue = tr.querySelector('[data-field="active"]').getAttribute('aria-pressed') === 'true';
        var updateData = {
          prompt: promptText,
          topic_id: topicValue ? Number(topicValue) : null,
          intent: intentValue || 'Discovery',
          active: activeValue
        };
        TS.api.updateTrackedPrompt(state.workspaceId, promptId, updateData).then(function (res) {
          if (res.ok) {
            reloadTrackingOnly();
          } else {
            text(errorEl, (res.body && res.body.error) || 'Could not save that prompt.');
            errorEl.hidden = false;
          }
        });
      }
    });

    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-scrim').addEventListener('click', closeDrawer);
  }

  function reloadTrackingOnly() {
    TS.api.getTracking(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      state.topics = res.body.tracking.topics || [];
      state.prompts = res.body.tracking.prompts || [];
      renderTopicOptions();
      renderTopicManageList();
      renderTopicFilterChips();
      renderStateBanner();
      renderPromptsTable();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
