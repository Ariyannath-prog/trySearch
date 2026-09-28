/* Content Studio page (/content-studio). Data source: the existing
   app/routes/content.py endpoints, unchanged - GET/POST .../documents (the
   list is cross-workspace: every document in every workspace this user can
   reach, per app.tenancy.documents_for_user()), GET/PATCH/DELETE
   .../documents/<id>, POST .../documents/<id>/generate. The "generate" draft
   is a structured starter draft (app/routes/content.py::make_content_draft),
   not a live model response - this page's copy says so plainly. brand_name/
   keyword/content_type/tone are the brief and are immutable once a document
   exists (the backend's PATCH endpoint deliberately does not accept them);
   only title/content/seo_title/meta_description/status are ever edited. */
(function () {
  'use strict';

  var CONTENT_TYPES = ['Blog post', 'Landing page', 'Comparison page', 'Product page', 'Email'];
  var TONES = ['Expert', 'Conversational', 'Confident', 'Educational'];
  var STATUSES = ['Brief', 'Draft', 'Ready for review', 'Published'];

  var state = {
    projects: [],
    documents: [],
    searchQuery: '',
    statusFilter: null,
    typeFilter: null,
    activeDocumentId: null
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
  function statusBadgeClass(status) {
    if (status === 'Published') return 'yes';
    if (status === 'Ready for review') return 'thin';
    return 'no';
  }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Content Studio</h1><p>Briefs and drafts across every project you can reach.</p></div></div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>New document</h4></div>' +
    '  <div class="pb">' +
    '    <div class="field">' +
    '      <input type="text" id="new-title" placeholder="Title" style="flex:1 1 200px">' +
    '      <input type="text" id="new-brand" placeholder="Brand name" style="max-width:180px">' +
    '      <input type="text" id="new-keyword" placeholder="Target topic or keyword" style="max-width:220px">' +
    '    </div>' +
    '    <div class="field" style="margin-top:8px">' +
    '      <select id="new-content-type"></select>' +
    '      <select id="new-tone"></select>' +
    '      <select id="new-workspace"></select>' +
    '      <button type="button" class="btn sm" id="create-document-btn">Create</button>' +
    '    </div>' +
    '    <p class="form-error" id="create-error" hidden></p>' +
    '  </div>' +
    '</div>' +

    '<div class="panel">' +
    '  <div class="ph"><h4>Documents</h4><span class="badge thin" id="doc-count-badge"></span></div>' +
    '  <div class="pb">' +
    '    <div class="cols" style="grid-template-columns:1fr auto;align-items:center;margin-bottom:10px">' +
    '      <input type="text" id="search-input" placeholder="Search title, brand or keyword&hellip;" style="max-width:260px">' +
    '      <div id="status-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '    </div>' +
    '    <div id="type-filter-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>' +
    '  </div>' +
    '  <div class="tw"><table class="data cards"><thead><tr>' +
    '    <th>Title</th><th>Brand</th><th>Keyword</th><th>Type</th><th>Status</th><th>Updated</th><th></th>' +
    '  </tr></thead><tbody id="doc-table-body"></tbody></table></div>' +
    '  <div class="empty" id="doc-empty" hidden><p>No documents match.</p></div>' +
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
      state.projects = projects;
      views.innerHTML = VIEWS_HTML;
      populateSelects();
      wireControls();
      loadDocuments();
    });
  }

  function populateSelects() {
    var typeSelect = $('#new-content-type'), toneSelect = $('#new-tone'), workspaceSelect = $('#new-workspace');
    typeSelect.innerHTML = CONTENT_TYPES.map(function (t) { return '<option value="' + esc(t) + '">' + esc(t) + '</option>'; }).join('');
    toneSelect.innerHTML = TONES.map(function (t) { return '<option value="' + esc(t) + '">' + esc(t) + '</option>'; }).join('');
    var activeId = TS.shell.activeProjectId();
    workspaceSelect.innerHTML = state.projects.map(function (p) {
      var selected = String(p.id) === String(activeId) ? ' selected' : '';
      return '<option value="' + p.id + '"' + selected + '>' + esc(p.brand_name || p.domain) + '</option>';
    }).join('');
  }

  function loadDocuments() {
    TS.api.listContentDocuments().then(function (res) {
      if (!res.ok) return;
      state.documents = res.body.documents || [];
      renderAll();
    });
  }

  /* ----------------------------------------------------------- render -- */

  function renderAll() {
    renderStatusChips();
    renderTypeChips();
    renderTable();
  }

  function renderStatusChips() {
    var container = $('#status-filter-chips');
    var chips = [{ id: null, label: 'All statuses' }].concat(STATUSES.map(function (s) { return { id: s, label: s }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.statusFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-status-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function renderTypeChips() {
    var container = $('#type-filter-chips');
    var chips = [{ id: null, label: 'All types' }].concat(CONTENT_TYPES.map(function (t) { return { id: t, label: t }; }));
    container.innerHTML = chips.map(function (c) {
      var on = state.typeFilter === c.id;
      return '<button type="button" class="chip' + (on ? ' on' : '') + '" data-type-filter="' + (c.id == null ? '' : esc(c.id)) + '">' +
        esc(c.label) + '</button>';
    }).join('');
  }

  function filteredDocuments() {
    var q = state.searchQuery.trim().toLowerCase();
    return state.documents.filter(function (d) {
      if (state.statusFilter != null && d.status !== state.statusFilter) return false;
      if (state.typeFilter != null && d.content_type !== state.typeFilter) return false;
      if (q) {
        var hay = (d.title || '') + ' ' + (d.brand_name || '') + ' ' + (d.keyword || '');
        if (hay.toLowerCase().indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function renderTable() {
    var body = $('#doc-table-body'), empty = $('#doc-empty');
    var list = filteredDocuments();
    text($('#doc-count-badge'), state.documents.length + ' total');
    clearChildren(body);
    if (!list.length) { empty.hidden = false; return; }
    empty.hidden = true;
    list.forEach(function (d) {
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td data-l="Title">' + esc(d.title) + '</td>' +
        '<td data-l="Brand">' + esc(d.brand_name) + '</td>' +
        '<td data-l="Keyword">' + esc(d.keyword) + '</td>' +
        '<td data-l="Type">' + esc(d.content_type) + '<div class="hint">' + esc(d.tone) + '</div></td>' +
        '<td data-l="Status"><span class="badge ' + statusBadgeClass(d.status) + '">' + esc(d.status) + '</span></td>' +
        '<td data-l="Updated">' + fmtDateTime(d.updated_at) + '</td>' +
        '<td data-l="" style="white-space:nowrap">' +
        '  <button type="button" class="btn ghost sm" data-edit-doc="' + d.id + '">Edit</button> ' +
        '  <button type="button" class="btn ghost sm" data-delete-doc="' + d.id + '">Delete</button>' +
        '</td>';
      body.appendChild(tr);
    });
  }

  /* ------------------------------------------------------------ drawer -- */

  function renderDrawer(doc) {
    text($('#drawer-title'), doc.title);
    var statusOptions = STATUSES.map(function (s) {
      return '<option value="' + esc(s) + '"' + (s === doc.status ? ' selected' : '') + '>' + esc(s) + '</option>';
    }).join('');
    $('#drawer-body').innerHTML =
      '<div class="panel"><div class="ph"><h4>Brief</h4><span class="badge thin">' + esc(doc.content_type) + '</span>' +
      '<span class="badge thin">' + esc(doc.tone) + '</span></div>' +
      '<div class="pb"><p class="hint" style="margin:0">Brand: ' + esc(doc.brand_name) + ' &middot; Keyword: ' + esc(doc.keyword) +
      '<br>The brief is fixed once a document exists; only the draft below can be edited.</p></div></div>' +

      '<div class="panel"><div class="ph"><h4>Draft</h4>' +
      '<button type="button" class="btn ghost sm" id="generate-btn" style="margin-left:auto">Generate draft</button></div>' +
      '<div class="pb">' +
      '  <p class="hint" style="margin:0 0 10px">Generating replaces the draft below with a structured starter draft ' +
      '    built from the brief - not a live AI response. Edit it before publishing.</p>' +
      '  <div class="field"><label style="display:block;width:100%">Title<input type="text" id="edit-title" value="' + esc(doc.title) + '"></label></div>' +
      '  <div class="field" style="margin-top:8px"><label style="display:block;width:100%">Content<textarea id="edit-content" rows="14" style="width:100%;font-family:var(--font-data)">' + esc(doc.content) + '</textarea></label></div>' +
      '  <div class="field" style="margin-top:8px"><label style="display:block;width:100%">SEO title<input type="text" id="edit-seo-title" value="' + esc(doc.seo_title) + '"></label></div>' +
      '  <div class="field" style="margin-top:8px"><label style="display:block;width:100%">Meta description<textarea id="edit-meta-description" rows="2" style="width:100%">' + esc(doc.meta_description) + '</textarea></label></div>' +
      (doc.outline ? '  <div class="field" style="margin-top:8px"><label style="display:block;width:100%">Outline (read-only)<textarea rows="6" style="width:100%" disabled>' + esc(doc.outline) + '</textarea></label></div>' : '') +
      (doc.recommendations ? '  <div class="field" style="margin-top:8px"><label style="display:block;width:100%">Recommendations (read-only)<textarea rows="4" style="width:100%" disabled>' + esc(doc.recommendations) + '</textarea></label></div>' : '') +
      '  <div class="field" style="margin-top:8px"><label>Status<select id="edit-status">' + statusOptions + '</select></label>' +
      '  <button type="button" class="btn sm" id="save-doc-btn">Save</button></div>' +
      '  <p class="form-error" id="edit-error" hidden style="margin-top:8px"></p>' +
      '</div></div>';
  }

  function openDocumentDrawer(documentId) {
    state.activeDocumentId = documentId;
    text($('#drawer-title'), 'Loading…');
    $('#drawer-body').innerHTML = '';
    $('#drawer-scrim').classList.add('open');
    $('#answer-drawer').classList.add('open');
    TS.api.getContentDocument(documentId).then(function (res) {
      if (!res.ok) {
        $('#drawer-body').innerHTML = '<div class="empty"><b>Could not load this document.</b></div>';
        return;
      }
      renderDrawer(res.body.document);
    });
  }

  function closeDrawer() {
    $('#drawer-scrim').classList.remove('open');
    $('#answer-drawer').classList.remove('open');
    state.activeDocumentId = null;
  }

  /* ---------------------------------------------------------- controls */

  function wireControls() {
    $('#create-document-btn').addEventListener('click', function () {
      var errorEl = $('#create-error');
      errorEl.hidden = true;
      var data = {
        title: $('#new-title').value.trim(),
        brand_name: $('#new-brand').value.trim(),
        keyword: $('#new-keyword').value.trim(),
        content_type: $('#new-content-type').value,
        tone: $('#new-tone').value,
        workspace_id: Number($('#new-workspace').value),
      };
      TS.api.createContentDocument(data).then(function (res) {
        if (res.ok) {
          $('#new-title').value = ''; $('#new-brand').value = ''; $('#new-keyword').value = '';
          loadDocuments();
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not create that document.');
          errorEl.hidden = false;
        }
      });
    });

    $('#search-input').addEventListener('input', function (evt) {
      state.searchQuery = evt.target.value;
      renderTable();
    });

    $('#status-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-status-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-status-filter');
      state.statusFilter = raw === '' ? null : raw;
      renderStatusChips();
      renderTable();
    });

    $('#type-filter-chips').addEventListener('click', function (evt) {
      var btn = evt.target.closest('[data-type-filter]');
      if (!btn) return;
      var raw = btn.getAttribute('data-type-filter');
      state.typeFilter = raw === '' ? null : raw;
      renderTypeChips();
      renderTable();
    });

    $('#doc-table-body').addEventListener('click', function (evt) {
      var editBtn = evt.target.closest('[data-edit-doc]');
      var deleteBtn = evt.target.closest('[data-delete-doc]');
      if (editBtn) {
        openDocumentDrawer(Number(editBtn.getAttribute('data-edit-doc')));
      } else if (deleteBtn) {
        if (!window.confirm('Delete this document? This cannot be undone.')) return;
        TS.api.deleteContentDocument(Number(deleteBtn.getAttribute('data-delete-doc'))).then(function (res) {
          if (res.ok) loadDocuments();
        });
      }
    });

    $('#drawer-body').addEventListener('click', function (evt) {
      if (evt.target.closest('#generate-btn')) {
        TS.api.generateContentDocument(state.activeDocumentId).then(function (res) {
          if (res.ok) { renderDrawer(res.body.document); loadDocuments(); }
        });
      } else if (evt.target.closest('#save-doc-btn')) {
        var errorEl = $('#edit-error');
        errorEl.hidden = true;
        var data = {
          title: $('#edit-title').value.trim(),
          content: $('#edit-content').value,
          seo_title: $('#edit-seo-title').value.trim(),
          meta_description: $('#edit-meta-description').value.trim(),
          status: $('#edit-status').value,
        };
        TS.api.updateContentDocument(state.activeDocumentId, data).then(function (res) {
          if (res.ok) {
            renderDrawer(res.body.document);
            loadDocuments();
          } else {
            text(errorEl, (res.body && res.body.error) || 'Could not save this document.');
            errorEl.hidden = false;
          }
        });
      }
    });

    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-scrim').addEventListener('click', closeDrawer);
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
