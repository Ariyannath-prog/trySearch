/* Citations & Sources page controller (/citations). Reads the one existing
   endpoint (GET .../citations) which now returns three grains of the same
   stored evidence: domain-level rollup (own/competitor/third-party share),
   competitor citation gaps, and the per-URL listing this page's main table
   and drawer are built on. No metric here is recomputed client-side beyond
   plain search/filter/sort over what the server already returns. */
(function () {
  'use strict';

  var state = {
    workspaceId: null,
    project: null,
    totalCitations: 0,
    domains: [],
    gaps: [],
    citations: [],
    searchQuery: '',
    bucketFilter: null,   // null = all, else 'own' | 'competitor' | 'third_party'
    engineFilter: null,   // null = all, else an engine/provider name
    sortKey: 'citations'  // 'citations' | 'recent' | 'domain'
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
  function fmtPct(value) { return value == null ? '—' : (Math.round(value * 1000) / 10) + '%'; }
  function bucketLabel(bucket) {
    return bucket === 'own' ? 'Own' : bucket === 'competitor' ? 'Competitor' : 'Third-party';
  }
  function bucketBadgeClass(bucket) {
    return bucket === 'own' ? 'yes' : bucket === 'competitor' ? 'no' : 'thin';
  }
  function shortUrl(url) {
    if (!url) return '—';
    return url.length > 64 ? url.slice(0, 61) + '…' : url;
  }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Citations & Sources</h1><p>Every URL AI engines have cited for <span id="project-name"></span>.</p></div></div>' +

    '<div class="cols c4">' +
    '  <div class="panel"><div class="pb"><span class="hint">Total citations</span><h2 id="stat-total">0</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Own domain</span><h2 id="stat-own">0</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Competitor</span><h2 id="stat-competitor">0</h2></div></div>' +
    '  <div class="panel"><div class="pb"><span class="hint">Third-party</span><h2 id="stat-third">0</h2></div></div>' +
    '</div>' +

    '<div class="cols c2">' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Most-cited domains</h4></div>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>Domain</th><th>Category</th><th>Citations</th><th>Share</th>' +
    '    </tr></thead><tbody id="domain-table-body"></tbody></table></div>' +
    '    <div class="empty" id="domain-empty" hidden><p>No citations recorded yet.</p></div>' +
    '  </div>' +
    '  <div class="panel">' +
    '    <div class="ph"><h4>Competitor citation gaps</h4></div>' +
    '    <p class="hint" style="margin:0 0 10px">Domains that cite a competitor and have never cited you.</p>' +
    '    <div class="tw"><table class="data cards"><thead><tr>' +
    '      <th>Domain</th><th>Category</th><th>Your mentions there</th>' +
    '    </tr></thead><tbody id="gaps-table-body"></tbody></table></div>' +
    '    <div class="empty" id="gaps-empty" hidden><p>No gaps found yet.</p></div>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Cited URLs</h4><span class="badge thin" id="citation-count-badge"></span></div>' +
    '  <div class="pb">' +
    '    <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
    '        <input type="text" id="search-input" placeholder="Search URL, domain or prompt&hellip;" style="max-width:260px">' +
    '        <div id="bucket-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '      </div>' +
    '      <select id="sort-select">' +
    '        <option value="citations">Most cited</option>' +
    '        <option value="recent">Most recently seen</option>' +
    '        <option value="domain">Domain A–Z</option>' +
    '      </select>' +
    '    </div>' +
    '    <div id="engine-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px"></div>' +
    '  </div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>URL</th><th>Domain</th><th>Category</th><th>Engines</th><th>Prompts</th>' +
    '    <th>Citations</th><th>First seen</th><th>Last seen</th><th></th>' +
    '  </tr></thead><tbody id="citations-table-body"></tbody></table></div>' +
    '  <div class="empty" id="citations-empty" hidden><p>No citations match.</p></div>' +
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
      TS.api.getTracking(id),
      TS.api.getCitations(id)
    ]).then(function (results) {
      var trackingRes = results[0], citationsRes = results[1];
      if (trackingRes.ok) {
        state.project = trackingRes.body.project;
        text($('#project-name'), state.project ? (state.project.brand_name || state.project.domain) : '');
      }
      if (citationsRes.ok) {
        state.totalCitations = citationsRes.body.total_citations || 0;
        state.domains = citationsRes.body.domains || [];
        state.gaps = citationsRes.body.competitor_gaps || [];
        state.citations = citationsRes.body.citations || [];
      }
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderStatTiles();
    renderDomainTable();
    renderGapsTable();
    renderBucketChips();
    renderEngineChips();
    renderCitationsTable();
  }

  function renderStatTiles() {
    var byBucket = { own: 0, competitor: 0, third_party: 0 };
    state.domains.forEach(function (d) { byBucket[d.bucket] = (byBucket[d.bucket] || 0) + d.citations; });
    text($('#stat-total'), String(state.totalCitations));
    text($('#stat-own'), String(byBucket.own));
    text($('#stat-competitor'), String(byBucket.competitor));
    text($('#stat-third'), String(byBucket.third_party));
  }

  function renderDomainTable() {
    var body = $('#domain-table-body'), empty = $('#domain-empty');
    clearChildren(body);
    if (!state.domains.length) { empty.hidden = false; return; }
    empty.hidden = true;
    state.domains.slice(0, 25).forEach(function (d) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Domain">' + esc(d.domain || '—') + '</td>' +
        '<td data-l="Category"><span class="badge ' + bucketBadgeClass(d.bucket) + '">' + esc(bucketLabel(d.bucket)) + '</span></td>' +
        '<td data-l="Citations">' + d.citations + '</td>' +
        '<td data-l="Share">' + fmtPct(d.share) + '</td>';
      body.appendChild(tr);
    });
  }

  function renderGapsTable() {
    var body = $('#gaps-table-body'), empty = $('#gaps-empty');
    clearChildren(body);
    if (!state.gaps.length) { empty.hidden = false; return; }
    empty.hidden = true;
    state.gaps.forEach(function (g) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Domain">' + esc(g.domain || '—') + '</td>' +
        '<td data-l="Category">' + esc(g.category || '—') + '</td>' +
        '<td data-l="Your mentions there">' + (g.brand_answers || 0) + '</td>';
      body.appendChild(tr);
    });
  }

  function renderBucketChips() {
    var container = $('#bucket-filter-chips');
    var chips = [
      { id: null, label: 'All' }, { id: 'own', label: 'Own' },
      { id: 'competitor', label: 'Competitor' }, { id: 'third_party', label: 'Third-party' }
    ];
    container.innerHTML = chips.map(function (c) {
      var on = state.bucketFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-bucket-filter="' + (c.id == null ? '' : c.id) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function allEngines() {
    var seen = {};
    state.citations.forEach(function (c) { (c.engines || []).forEach(function (e) { seen[e] = true; }); });
    return Object.keys(seen).sort();
  }

  function renderEngineChips() {
    var container = $('#engine-filter-chips');
    var engines = allEngines();
    if (!engines.length) { container.innerHTML = ''; return; }
    var chips = [{ id: null, label: 'All engines' }].concat(engines.map(function (e) { return { id: e, label: e }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.engineFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-engine-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function filteredCitations() {
    var q = state.searchQuery.trim().toLowerCase();
    var list = state.citations.filter(function (c) {
      if (state.bucketFilter != null && c.bucket !== state.bucketFilter) return false;
      if (state.engineFilter != null && (c.engines || []).indexOf(state.engineFilter) === -1) return false;
      if (q) {
        var hay = (c.url || '') + ' ' + (c.domain || '') + ' ' + (c.prompts || []).join(' ');
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
    list = list.slice();
    if (state.sortKey === 'recent') {
      list.sort(function (a, b) { return new Date(b.last_seen) - new Date(a.last_seen); });
    } else if (state.sortKey === 'domain') {
      list.sort(function (a, b) { return (a.domain || '').localeCompare(b.domain || ''); });
    } else {
      list.sort(function (a, b) { return b.citation_count - a.citation_count; });
    }
    return list;
  }

  function renderCitationsTable() {
    var body = $('#citations-table-body'), empty = $('#citations-empty');
    var list = filteredCitations();
    text($('#citation-count-badge'), state.citations.length + ' URLs');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (c) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="URL"><a href="' + esc(c.url) + '" target="_blank" rel="noopener noreferrer" title="' + esc(c.url) + '">' + esc(shortUrl(c.url)) + '</a></td>' +
        '<td data-l="Domain">' + esc(c.domain || '—') + '</td>' +
        '<td data-l="Category"><span class="badge ' + bucketBadgeClass(c.bucket) + '">' + esc(bucketLabel(c.bucket)) + '</span></td>' +
        '<td data-l="Engines">' + esc((c.engines || []).join(', ') || '—') + '</td>' +
        '<td data-l="Prompts">' + (c.prompts || []).length + '</td>' +
        '<td data-l="Citations">' + c.citation_count + '</td>' +
        '<td data-l="First seen">' + fmtDateTime(c.first_seen) + '</td>' +
        '<td data-l="Last seen">' + fmtDateTime(c.last_seen) + '</td>' +
        '<td data-l=""><button type="button" class="btn ghost sm" data-view-citation="' + esc(c.url) + '">View</button></td>';
      body.appendChild(tr);
    });
  }

  /* ------------------------------------------------------------ drawer -- */

  function openCitationDrawer(url) {
    var citation = state.citations.filter(function (c) { return c.url === url; })[0];
    if (!citation) return;
    text($('#drawer-title'), citation.domain || 'Citation');
    var body = $('#drawer-body');
    var header = '<p class="hint" style="word-break:break-all">' + esc(citation.url) + '</p>';
    var rows = (citation.occurrences || []).map(function (o) {
      return '<div class="panel"><div class="ph"><h4>' + esc(o.engine || 'Engine') + '</h4>' +
        (o.rank ? '<span class="badge thin">Rank #' + o.rank + '</span>' : '') + '</div>' +
        '<div class="pb"><p style="margin:0 0 6px">' + esc(o.prompt || '—') + '</p>' +
        '<span class="hint">' + fmtDateTime(o.created_at) + '</span></div></div>';
    }).join('');
    body.innerHTML = header + (rows || '<div class="empty"><b>No occurrences recorded.</b></div>');
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
      renderCitationsTable();
    });

    $('#sort-select').addEventListener('change', function (evt) {
      state.sortKey = evt.target.value;
      renderCitationsTable();
    });

    $('#bucket-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-bucket-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-bucket-filter');
      state.bucketFilter = raw === '' ? null : raw;
      renderBucketChips();
      renderCitationsTable();
    });

    $('#engine-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-engine-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-engine-filter');
      state.engineFilter = raw === '' ? null : raw;
      renderEngineChips();
      renderCitationsTable();
    });

    $('#citations-table-body').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-view-citation]');
      if (!btn) return;
      openCitationDrawer(btn.getAttribute('data-view-citation'));
    });

    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-scrim').addEventListener('click', closeDrawer);
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
