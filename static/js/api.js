/* Thin fetch wrapper shared by every authenticated page. Page-specific scripts
   call TS.api.* only -- never fetch() directly -- so the request/response
   shape lives in exactly one place. Promoted from analytics.html's original
   inline apiCall/api pattern; extended only as later phases need new
   endpoints, not speculatively. */
(function () {
  'use strict';

  window.TS = window.TS || {};

  /* CSRF: the server holds a per-session token and refuses state-changing /api/
     requests without it (app/security.py). Every mutating call in the product
     already goes through apiCall, so attaching it here covers all of them rather
     than asking each page script to remember.

     The token is cached after the first fetch and re-fetched once on a 403
     csrf_invalid, which is what a rotated token after login looks like from here. */
  var MUTATING = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };
  var csrfToken = null;

  function fetchCsrfToken() {
    return fetch('/api/csrf-token', { credentials: 'same-origin' })
      .then(function (res) { return res.json(); })
      .then(function (body) {
        csrfToken = (body && body.csrf_token) || null;
        return csrfToken;
      })
      .catch(function () { return null; });
  }

  /* Exposed so a page that reads /api/me itself can seed the cache without a
     second round trip -- /api/me returns csrf_token too. */
  function setCsrfToken(token) {
    if (token) csrfToken = token;
  }

  function rawCall(url, opts) {
    return fetch(url, opts).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (body) {
        return { ok: res.ok, status: res.status, body: body };
      });
    }).catch(function (err) {
      return { ok: false, status: 0, body: null, networkError: true, error: err };
    });
  }

  /* Always resolves, never rejects: every caller branches on `ok`, so a
     dropped connection has to arrive as a result rather than as an
     unhandled rejection that silently skips the caller's .then(). A
     transport failure comes back as status 0 with networkError set. */
  function apiCall(url, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    if (opts.body && !opts.headers) opts.headers = { 'Content-Type': 'application/json' };

    var method = (opts.method || 'GET').toUpperCase();
    if (!MUTATING[method]) return rawCall(url, opts);

    function send(token, isRetry) {
      var headers = {};
      var key;
      for (key in (opts.headers || {})) {
        if (Object.prototype.hasOwnProperty.call(opts.headers, key)) headers[key] = opts.headers[key];
      }
      if (token) headers['X-CSRF-Token'] = token;
      var attempt = {};
      for (key in opts) {
        if (Object.prototype.hasOwnProperty.call(opts, key)) attempt[key] = opts[key];
      }
      attempt.headers = headers;
      return rawCall(url, attempt).then(function (res) {
        var rejected = res.status === 403 && res.body && res.body.code === 'csrf_invalid';
        if (rejected && !isRetry) {
          /* The session token changed under us (login rotates it). Re-fetch once
             and replay -- but only once, so a genuine refusal cannot loop. */
          return fetchCsrfToken().then(function (fresh) { return send(fresh, true); });
        }
        return res;
      });
    }

    if (csrfToken) return send(csrfToken, false);
    return fetchCsrfToken().then(function (token) { return send(token, false); });
  }

  TS.api = {
    setCsrfToken: setCsrfToken,
    refreshCsrfToken: fetchCsrfToken,
    getMe: function () {
      return apiCall('/api/me').then(function (res) {
        if (res.body && res.body.csrf_token) setCsrfToken(res.body.csrf_token);
        return res;
      });
    },
    listProjects: function () { return apiCall('/api/analytics/projects'); },
    createProject: function (data) {
      return apiCall('/api/analytics/projects', { method: 'POST', body: JSON.stringify(data) });
    },
    deleteProject: function (id) {
      return apiCall('/api/analytics/projects/' + id, { method: 'DELETE' });
    },
    getReport: function (id, queryString) {
      return apiCall('/api/analytics/projects/' + id + '/report' + (queryString || ''));
    },
    getEvidence: function (id, runId, queryString) {
      var url = '/api/analytics/projects/' + id + '/evidence';
      if (runId) {
        url += '?run_id=' + encodeURIComponent(runId);
      } else if (queryString) {
        url += queryString;
      }
      return apiCall(url);
    },
    getScanHistory: function (id, queryString) {
      return apiCall('/api/analytics/projects/' + id + '/scans' + (queryString || ''));
    },
    getEvidenceAnswer: function (id, answerId) {
      return apiCall('/api/analytics/projects/' + id + '/evidence/' + answerId);
    },
    getTracking: function (id) { return apiCall('/api/analytics/projects/' + id + '/tracking'); },
    createTopic: function (id, name) {
      return apiCall('/api/analytics/projects/' + id + '/topics',
        { method: 'POST', body: JSON.stringify({ name: name }) });
    },
    deleteTopic: function (id, topicId) {
      return apiCall('/api/analytics/projects/' + id + '/topics/' + topicId, { method: 'DELETE' });
    },
    addTrackedPrompt: function (id, data) {
      return apiCall('/api/analytics/projects/' + id + '/tracked-prompts',
        { method: 'POST', body: JSON.stringify(data) });
    },
    updateTrackedPrompt: function (id, promptId, data) {
      return apiCall('/api/analytics/projects/' + id + '/tracked-prompts/' + promptId,
        { method: 'PATCH', body: JSON.stringify(data) });
    },
    deleteTrackedPrompt: function (id, promptId) {
      return apiCall('/api/analytics/projects/' + id + '/tracked-prompts/' + promptId, { method: 'DELETE' });
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
    updateSchedule: function (id, data) {
      return apiCall('/api/analytics/projects/' + id + '/scan-schedule',
        { method: 'PUT', body: JSON.stringify(data) });
    },
    getCitations: function (id) { return apiCall('/api/analytics/projects/' + id + '/citations'); },
    getCompetitorIntelligence: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/competitors');
    },
    addCompetitor: function (id, data) {
      return apiCall('/api/analytics/projects/' + id + '/competitors',
        { method: 'POST', body: JSON.stringify(data) });
    },
    updateCompetitor: function (id, competitorId, data) {
      return apiCall('/api/analytics/projects/' + id + '/competitors/' + competitorId,
        { method: 'PATCH', body: JSON.stringify(data) });
    },
    deleteCompetitor: function (id, competitorId) {
      return apiCall('/api/analytics/projects/' + id + '/competitors/' + competitorId,
        { method: 'DELETE' });
    },
    getAudit: function (id) { return apiCall('/api/analytics/projects/' + id + '/audit'); },
    startAudit: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/audits', { method: 'POST', body: '{}' });
    },
    getSearchConsole: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/search-console');
    },
    disconnectSearchConsole: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/search-console', { method: 'DELETE' });
    },
    selectSearchConsoleProperty: function (id, siteUrl) {
      return apiCall('/api/analytics/projects/' + id + '/search-console/property',
        { method: 'PUT', body: JSON.stringify({ site_url: siteUrl }) });
    },
    syncSearchConsole: function (id, data) {
      return apiCall('/api/analytics/projects/' + id + '/search-console/sync',
        { method: 'POST', body: JSON.stringify(data || {}) });
    },
    getMentions: function (id) { return apiCall('/api/analytics/projects/' + id + '/mentions'); },
    getSentiment: function (id) { return apiCall('/api/analytics/projects/' + id + '/sentiment'); },
    getRecommendations: function (id) { return apiCall('/api/analytics/projects/' + id + '/recommendations'); },
    startSentimentClassification: function (id) {
      return apiCall('/api/analytics/projects/' + id + '/sentiment/classify', { method: 'POST', body: '{}' });
    },
    listContentDocuments: function () { return apiCall('/api/content-studio/documents'); },
    createContentDocument: function (data) {
      return apiCall('/api/content-studio/documents', { method: 'POST', body: JSON.stringify(data) });
    },
    getContentDocument: function (id) { return apiCall('/api/content-studio/documents/' + id); },
    updateContentDocument: function (id, data) {
      return apiCall('/api/content-studio/documents/' + id, { method: 'PATCH', body: JSON.stringify(data) });
    },
    deleteContentDocument: function (id) {
      return apiCall('/api/content-studio/documents/' + id, { method: 'DELETE' });
    },
    generateContentDocument: function (id) {
      return apiCall('/api/content-studio/documents/' + id + '/generate', { method: 'POST', body: '{}' });
    },
    /* Plans are read from the backend and never hardcoded here: prices, limits and
       feature availability all come from the admin-controlled plan record. */
    listAvailablePlans: function (accountType) {
      return apiCall('/api/plans' + (accountType ? '?account_type=' + encodeURIComponent(accountType) : ''));
    },
  };
})();
