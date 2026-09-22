/* Projects/Workspaces page controller (/workspace). Lists every workspace the
   user can reach, lets them create a new one, and selecting a project sets
   the shared active-project id (shell.js's switcher reads the same key) and
   opens the Dashboard for it -- the "connect selection to the rest of the
   app" requirement. No new backend: GET/POST /api/analytics/projects and
   DELETE /api/analytics/projects/<id>, all already tenancy-checked. */
(function () {
  'use strict';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function text(el, value) { el.textContent = value == null ? '' : value; }
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  function clearChildren(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).slice(0, 10);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function fmtScore(v) { return v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1); }

  var VIEWS_HTML =
    '<div class="phead"><div><h1>Projects</h1><p>Every workspace you can reach.</p></div></div>' +
    '<div class="tw"><table class="data cards">' +
    '  <thead><tr><th>Project</th><th>Domain</th><th>Role</th><th>Latest run</th><th>Updated</th><th></th></tr></thead>' +
    '  <tbody id="projects-table-body"></tbody>' +
    '</table></div>' +
    '<div class="empty" id="projects-empty" hidden><p>No projects yet. Add your first website below.</p></div>' +
    '<div class="panel" style="margin-top:16px">' +
    '  <div class="ph"><h4>Add a project</h4></div>' +
    '  <div class="pb">' +
    '    <form id="add-project-form">' +
    '      <label>Website<input type="text" name="domain" placeholder="example.com" required></label>' +
    '      <label style="margin-top:10px">Brand name<input type="text" name="brand_name" required></label>' +
    '      <label style="margin-top:10px">Industry<input type="text" name="industry" placeholder="Optional"></label>' +
    '      <button type="submit" class="btn" style="margin-top:14px">Create project</button>' +
    '    </form>' +
    '    <p class="form-error" id="add-project-error" hidden></p>' +
    '  </div>' +
    '</div>';

  function openProject(id) {
    try { localStorage.setItem('ts_active_project_id', String(id)); } catch (e) { /* ignore */ }
    window.location.href = '/analytics';
  }

  function renderProjects(projects) {
    var body = $('#projects-table-body'), empty = $('#projects-empty');
    clearChildren(body);
    if (!projects.length) { empty.hidden = false; return; }
    empty.hidden = true;
    projects.forEach(function (project) {
      var tr = document.createElement('tr');
      var run = project.latest_run;
      var runCell = run
        ? (fmtScore(run.visibility_score) + ' <span class="badge">' + esc(run.status || '') + '</span>')
        : '<span class="hint">Not yet run</span>';
      tr.innerHTML =
        '<td data-l="Project"><a href="#" class="open-link" style="font-weight:600">' +
          esc(project.brand_name || project.domain || ('Project ' + project.id)) + '</a></td>' +
        '<td data-l="Domain">' + esc(project.domain || '—') + '</td>' +
        '<td data-l="Role"><span class="badge">' + esc(project.role || '—') + '</span></td>' +
        '<td data-l="Latest run" class="tnum">' + runCell + '</td>' +
        '<td data-l="Updated">' + fmtDate(project.updated_at) + '</td>' +
        '<td data-l=""><button class="btn ghost sm delete-btn" type="button">Delete</button></td>';
      tr.querySelector('.open-link').addEventListener('click', function (evt) {
        evt.preventDefault();
        openProject(project.id);
      });
      tr.querySelector('.delete-btn').addEventListener('click', function () {
        var label = project.brand_name || project.domain || ('Project ' + project.id);
        if (!window.confirm('Delete "' + label + '"? This permanently removes its prompts, ' +
            'scans, audits, and evidence. This cannot be undone.')) return;
        TS.api.deleteProject(project.id).then(function (res) {
          if (res.ok) {
            boot();
          } else {
            window.alert((res.body && res.body.error) || 'Could not delete that project.');
          }
        });
      });
      body.appendChild(tr);
    });
  }

  function wireAddForm() {
    var form = $('#add-project-form');
    if (form.dataset.wired) return;
    form.dataset.wired = '1';
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var errorEl = $('#add-project-error');
      errorEl.hidden = true;
      var data = {
        domain: form.domain.value.trim(),
        brand_name: form.brand_name.value.trim(),
        industry: form.industry.value.trim()
      };
      TS.api.createProject(data).then(function (res) {
        if (res.ok) {
          form.reset();
          try { localStorage.setItem('ts_active_project_id', String(res.body.project.id)); } catch (e) { /* ignore */ }
          boot();
        } else {
          text(errorEl, (res.body && res.body.error) || 'Could not create that project.');
          errorEl.hidden = false;
        }
      });
    });
  }

  function boot() {
    var views = $('#views');
    views.innerHTML = VIEWS_HTML;
    wireAddForm();
    views.classList.add('skel');
    TS.api.listProjects().then(function (res) {
      views.classList.remove('skel');
      if (!res.ok) {
        $('#projects-table-body').innerHTML = '';
        $('#projects-empty').hidden = false;
        text($('#projects-empty').querySelector('p'), 'Could not load your projects. Try reloading the page.');
        return;
      }
      renderProjects((res.body && res.body.projects) || []);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    TS.shell.boot();
    boot();
  });
})();
