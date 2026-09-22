/* First-run onboarding wizard (/onboarding). Domain -> AI-generated profile
   -> review/edit/approve -> choose engines -> start scan -> dashboard.
   Every step calls a real existing endpoint (app/routes/onboarding.py,
   app/routes/prompts.py's new engine-selection routes,
   app/routes/evidence.py) - nothing here is simulated. */
(function () {
  'use strict';

  var PHASES = ['Domain', 'Confirm', 'Approve', 'Engines', 'Analysis'];

  var state = {
    domain: '',
    profile: null,        // {brand_name, aliases[], domains[], competitors[], prompts[], unbranded_fraction}
    competitorKeep: [],    // parallel boolean array to state.profile.competitors
    promptKeep: [],        // parallel boolean array to state.profile.prompts
    workspaceId: null,
    availableEngines: [],  // [{id, key, display_name, enabled}]
    selectedEngineIds: [],
    pollTimer: null
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function text(el, value) { el.textContent = value == null ? '' : value; }

  function renderPhases(activePhase) {
    $('#phases').innerHTML = PHASES.map(function (label, i) {
      var n = i + 1;
      var st = n < activePhase ? 'done' : n === activePhase ? 'now' : '';
      return '<div class="phase" data-state="' + st + '"><span class="pn">' + n + '/5</span>' +
        '<span class="pt">' + esc(label) + '</span></div>';
    }).join('');
  }

  function mount(html) { $('#onbmain').innerHTML = html; }

  /* ---------------------------------------------------------- step 1 -- */

  function renderDomainStep() {
    renderPhases(1);
    mount(
      '<div class="onbcard">' +
      '  <div><div class="lbl">Step 1 of 5 &mdash; Domain</div>' +
      '    <h1 style="margin-top:9px">What should the models be saying about you?</h1>' +
      '    <p class="lede" style="margin-top:8px">One domain. We read your site, work out your ' +
      '      category and your rivals, then generate the questions your buyers actually ask.</p></div>' +
      '  <div class="field"><input id="dom" type="text" placeholder="example.com" spellcheck="false">' +
      '    <button class="btn" id="start-scan-btn">Start scan</button></div>' +
      '  <p class="form-error" id="domain-error" hidden></p>' +
      '  <div class="killers"><b>No card</b><span>&middot;</span><b>About a minute</b></div>' +
      '</div>'
    );
    $('#start-scan-btn').addEventListener('click', submitDomain);
    $('#dom').addEventListener('keydown', function (evt) {
      if (evt.key === 'Enter') submitDomain();
    });
    $('#dom').focus();
  }

  function renderScanningStep(domain) {
    mount(
      '<div class="onbcard">' +
      '  <div><div class="lbl">Step 1 of 5 &mdash; Domain</div>' +
      '    <h1 style="margin-top:9px">Reading ' + esc(domain) + '</h1>' +
      '    <p class="lede" style="margin-top:8px">Nothing here is billed yet. We are only working ' +
      '      out what to ask and who to compare you against.</p></div>' +
      '  <div class="panel"><div class="ph"><h4>Scan</h4></div>' +
      '    <div class="pb" style="padding:4px 14px">' +
      '      <div class="scanrow active"><span class="spin"></span>' +
      '        <span>Reading the homepage and generating a profile&hellip;</span><span></span></div>' +
      '    </div></div>' +
      '  <span class="hint">This can take up to a minute.</span>' +
      '</div>'
    );
  }

  function submitDomain() {
    var domain = $('#dom').value.trim();
    var errorEl = $('#domain-error');
    errorEl.hidden = true;
    if (!domain) {
      text(errorEl, 'Enter a website domain.');
      errorEl.hidden = false;
      return;
    }
    state.domain = domain;
    renderScanningStep(domain);
    TS.api.previewOnboarding(domain).then(function (res) {
      if (!res.ok) {
        var msg = (res.body && res.body.error) || 'Could not generate a profile for that domain.';
        renderDomainStep();
        $('#dom').value = domain;
        text($('#domain-error'), msg);
        $('#domain-error').hidden = false;
        return;
      }
      state.profile = res.body.profile;
      state.competitorKeep = (state.profile.competitors || []).map(function () { return true; });
      state.promptKeep = (state.profile.prompts || []).map(function () { return true; });
      renderConfirmStep();
    });
  }

  /* ---------------------------------------------------------- step 2 -- */

  function renderConfirmStep() {
    renderPhases(2);
    var profile = state.profile;
    var aliasChips = (profile.aliases || []).map(function (alias, idx) {
      return '<button type="button" class="chip on" data-remove-alias="' + idx + '">' +
        esc(alias) + ' &times;</button>';
    }).join('');

    var competitorRows = (profile.competitors || []).map(function (c, idx) {
      var label = c.name + (c.domains && c.domains[0] ? ' — ' + c.domains[0] : '');
      var kept = state.competitorKeep[idx];
      return '<div class="revrow' + (kept ? '' : ' rejected') + '"><span>' + esc(label) + '</span>' +
        '<span></span><button type="button" class="mini" data-competitor-toggle="' + idx +
        '" aria-pressed="' + kept + '">&check;</button></div>';
    }).join('') || '<p class="hint">No competitors were found.</p>';

    var promptRows = (profile.prompts || []).map(function (p, idx) {
      var kept = state.promptKeep[idx];
      return '<div class="revrow' + (kept ? '' : ' rejected') + '">' +
        '<input type="text" data-prompt-text="' + idx + '" value="' + esc(p.text) +
        '" style="border:0;background:none;padding:0;font:inherit;color:inherit;width:100%">' +
        '<span class="badge thin">' + esc(p.category) + '</span>' +
        '<button type="button" class="mini" data-prompt-toggle="' + idx +
        '" aria-pressed="' + kept + '">&check;</button></div>';
    }).join('');

    mount(
      '<div class="onbcard">' +
      '  <div><div class="lbl">Step 2 of 5 &mdash; Confirm</div>' +
      '    <h1 style="margin-top:9px">We guessed. You decide.</h1>' +
      '    <p class="lede" style="margin-top:8px">Reject anything wrong now &mdash; a bad ' +
      '      competitor set or prompt makes every number downstream meaningless.</p></div>' +

      '  <div class="panel"><div class="ph"><h4>Brand</h4><span class="badge thin">inferred</span></div>' +
      '    <div class="pb" style="display:flex;flex-direction:column;gap:12px">' +
      '      <dl class="kv"><dt>Name</dt><dd>' + esc(profile.brand_name) + '</dd></dl>' +
      '      <div><span class="lbl" style="display:block;margin-bottom:7px">Aliases</span>' +
      '        <div id="alias-box" style="display:flex;gap:6px;flex-wrap:wrap">' + aliasChips + '</div></div>' +
      '      <label>Industry<input type="text" id="industry-input" placeholder="e.g. Expense management"></label>' +
      '    </div></div>' +

      '  <div class="panel"><div class="ph"><h4>Competitors</h4>' +
      '    <span class="badge thin">' + (profile.competitors || []).length + ' found</span></div>' +
      '    <div class="pb" id="competitor-rows">' + competitorRows + '</div></div>' +

      '  <div class="panel"><div class="ph"><h4>Prompts</h4>' +
      '    <span class="badge thin">' + (profile.prompts || []).length + ' generated</span></div>' +
      '    <div class="pb" id="prompt-rows">' + promptRows + '</div></div>' +

      '  <p class="form-error" id="approve-error" hidden></p>' +
      '  <div style="display:flex;gap:10px;flex-wrap:wrap">' +
      '    <button class="btn" id="approve-btn">Approve &amp; create project</button>' +
      '    <button class="btn ghost" id="back-to-domain-btn">Back</button>' +
      '  </div>' +
      '</div>'
    );

    $('#alias-box').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-remove-alias]');
      if (!btn) return;
      state.profile.aliases.splice(Number(btn.getAttribute('data-remove-alias')), 1);
      renderConfirmStep();
    });
    $('#competitor-rows').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-competitor-toggle]');
      if (!btn) return;
      var idx = Number(btn.getAttribute('data-competitor-toggle'));
      state.competitorKeep[idx] = !state.competitorKeep[idx];
      renderConfirmStep();
    });
    $('#prompt-rows').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-prompt-toggle]');
      if (!btn) return;
      var idx = Number(btn.getAttribute('data-prompt-toggle'));
      state.promptKeep[idx] = !state.promptKeep[idx];
      renderConfirmStep();
    });
    $('#prompt-rows').addEventListener('change', function (evt) {
      var input = evt.target.closest('[data-prompt-text]');
      if (!input) return;
      var idx = Number(input.getAttribute('data-prompt-text'));
      state.profile.prompts[idx].text = input.value;
    });
    $('#approve-btn').addEventListener('click', submitApprove);
    $('#back-to-domain-btn').addEventListener('click', renderDomainStep);
  }

  function submitApprove() {
    var errorEl = $('#approve-error');
    errorEl.hidden = true;
    var profile = state.profile;
    var industry = $('#industry-input').value.trim();
    var keptCompetitors = (profile.competitors || []).filter(function (_, idx) { return state.competitorKeep[idx]; });
    var keptPrompts = (profile.prompts || []).filter(function (_, idx) { return state.promptKeep[idx]; });
    if (!keptPrompts.length) {
      text(errorEl, 'Keep at least one prompt.');
      errorEl.hidden = false;
      return;
    }
    var payload = {
      profile: {
        domain: state.domain,
        brand_name: profile.brand_name,
        aliases: profile.aliases,
        domains: profile.domains,
        industry: industry,
        competitors: keptCompetitors,
        prompts: keptPrompts
      }
    };
    TS.api.approveOnboarding(payload).then(function (res) {
      if (!res.ok) {
        text(errorEl, (res.body && res.body.error) || 'Could not create the project.');
        errorEl.hidden = false;
        return;
      }
      state.workspaceId = res.body.workspace.id;
      try { localStorage.setItem('ts_active_project_id', String(state.workspaceId)); } catch (e) { /* ignore */ }
      renderEnginesStep();
    });
  }

  /* ---------------------------------------------------------- step 4 -- */

  function renderEnginesStep() {
    renderPhases(4);
    mount(
      '<div class="onbcard">' +
      '  <div><div class="lbl">Step 4 of 5 &mdash; Engines</div>' +
      '    <h1 style="margin-top:9px">Which engines should we track?</h1>' +
      '    <p class="lede" style="margin-top:8px">These are the AI engines currently available on ' +
      '      this platform. Choose which ones to include when scanning this project.</p></div>' +
      '  <div class="panel"><div class="ph"><h4>Available engines</h4></div>' +
      '    <div class="pb" id="engine-list" style="display:flex;gap:8px;flex-wrap:wrap"></div>' +
      '    <div class="empty" id="engines-empty" hidden style="margin:14px">' +
      '      <p>No engines are currently available. An admin needs to enable one before scans can run.</p></div>' +
      '  </div>' +
      '  <p class="form-error" id="engines-error" hidden></p>' +
      '  <div style="display:flex;gap:10px;flex-wrap:wrap">' +
      '    <button class="btn" id="save-engines-btn">Save &amp; continue</button>' +
      '  </div>' +
      '</div>'
    );
    $('#engine-list').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-engine-id]');
      if (!btn) return;
      var id = Number(btn.getAttribute('data-engine-id'));
      var pos = state.selectedEngineIds.indexOf(id);
      if (pos === -1) state.selectedEngineIds.push(id); else state.selectedEngineIds.splice(pos, 1);
      renderEngineChips();
    });
    $('#save-engines-btn').addEventListener('click', submitEngines);
    TS.api.getWorkspaceEngines(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      state.availableEngines = (res.body && res.body.engines) || [];
      state.selectedEngineIds = state.availableEngines
        .filter(function (e) { return e.enabled; })
        .map(function (e) { return e.id; });
      renderEngineChips();
    });
  }

  function renderEngineChips() {
    var list = $('#engine-list'), empty = $('#engines-empty');
    if (!state.availableEngines.length) {
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    list.innerHTML = state.availableEngines.map(function (eng) {
      var on = state.selectedEngineIds.indexOf(eng.id) !== -1;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-engine-id="' +
        eng.id + '">' + esc(eng.display_name) + '</button>';
    }).join('');
  }

  function submitEngines() {
    var errorEl = $('#engines-error');
    errorEl.hidden = true;
    TS.api.setWorkspaceEngines(state.workspaceId, state.selectedEngineIds).then(function (res) {
      if (!res.ok) {
        text(errorEl, (res.body && res.body.error) || 'Could not save your engine selection.');
        errorEl.hidden = false;
        return;
      }
      renderAnalysisStep();
    });
  }

  /* ---------------------------------------------------------- step 5 -- */

  function renderAnalysisStep() {
    renderPhases(5);
    mount(
      '<div class="onbcard">' +
      '  <div><div class="lbl">Step 5 of 5 &mdash; Analysis</div>' +
      '    <h1 style="margin-top:9px">Running your first scan</h1>' +
      '    <p class="lede" style="margin-top:8px">This checks each tracked prompt against your ' +
      '      chosen engines and measures whether your brand is mentioned.</p></div>' +
      '  <div class="panel"><div class="ph"><h4>Progress</h4></div>' +
      '    <div class="pb" id="analysis-status"><span class="hint">Starting&hellip;</span></div></div>' +
      '  <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">' +
      '    <button class="btn lg" id="open-dashboard-btn">Open the dashboard</button>' +
      '    <span class="hint" id="analysis-hint">You can leave this page any time &mdash; the scan ' +
      '      keeps running.</span>' +
      '  </div>' +
      '</div>'
    );
    $('#open-dashboard-btn').addEventListener('click', function () {
      window.location.href = '/analytics';
    });
    TS.api.startScan(state.workspaceId).then(handleScanResponse);
  }

  function handleScanResponse(res) {
    var status = $('#analysis-status');
    if (res.status === 202) {
      pollAnalysis();
      return;
    }
    var msg = (res.body && res.body.error) || 'Could not start the scan.';
    status.innerHTML = '<div class="empty"><b>Could not start the scan</b><p>' + esc(msg) + '</p></div>';
  }

  function pollAnalysis() {
    TS.api.getEvidence(state.workspaceId).then(function (res) {
      if (!res.ok) return;
      var job = res.body.active_job;
      var status = $('#analysis-status');
      if (job) {
        var pct = job.progress == null ? 0 : job.progress;
        status.innerHTML =
          '<div class="meter"><div class="mtrack"><i style="width:' + pct + '%;background:var(--brand)"></i></div>' +
          '<span class="tnum">' + pct + '%</span></div>' +
          '<p class="hint" style="margin-top:10px">Status: ' + esc(job.status) + '</p>';
        state.pollTimer = setTimeout(pollAnalysis, 4000);
      } else {
        status.innerHTML = '<p class="hint">The scan has finished (or is queued and waiting for the ' +
          'next scheduled run). Open the dashboard to see the results.</p>';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', renderDomainStep);
})();
