/* Thin fetch wrapper shared by every authenticated page. Page-specific scripts
   call TS.api.* only -- never fetch() directly -- so the request/response
   shape lives in exactly one place. Promoted from analytics.html's original
   inline apiCall/api pattern; extended only as later phases need new
   endpoints, not speculatively. */
(function () {
  'use strict';

  window.TS = window.TS || {};

  function apiCall(url, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    if (opts.body && !opts.headers) opts.headers = { 'Content-Type': 'application/json' };
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (body) {
        return { ok: res.ok, status: res.status, body: body };
      });
    });
  }

  TS.api = {
    getMe: function () { return apiCall('/api/me'); },
    listProjects: function () { return apiCall('/api/analytics/projects'); },
    createProject: function (data) {
      return apiCall('/api/analytics/projects', { method: 'POST', body: JSON.stringify(data) });
    },
    deleteProject: function (id) {
      return apiCall('/api/analytics/projects/' + id, { method: 'DELETE' });
    },
    getReport: function (id) { return apiCall('/api/analytics/projects/' + id + '/report'); },
    getEvidence: function (id) { return apiCall('/api/analytics/projects/' + id + '/evidence'); },
    addTrackedPrompt: function (id, data) {
      return apiCall('/api/analytics/projects/' + id + '/tracked-prompts',
        { method: 'POST', body: JSON.stringify(data) });
    },
    startScan: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/prompt-scans', { method: 'POST', body: '{}' });
    },
    logout: function () { return apiCall('/api/logout', { method: 'POST' }); },
    previewOnboarding: function (domain) {
      return apiCall('/api/onboarding/preview', { method: 'POST', body: JSON.stringify({ domain: domain }) });
    },
    approveOnboarding: function (payload) {
      return apiCall('/api/onboarding/approve', { method: 'POST', body: JSON.stringify(payload) });
    },
    getWorkspaceEngines: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/engines');
    },
    setWorkspaceEngines: function (id, engineIds) {
      return apiCall('/api/analytics/projects/' + id + '/engines',
        { method: 'PUT', body: JSON.stringify({ engine_ids: engineIds }) });
    },
  };
})();
