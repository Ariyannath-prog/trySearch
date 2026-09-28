/* Shared analytics filter bar: date range, region, engines. One
   localStorage key (ts_analytics_filters) so the same selection persists
   across every analytics page - each page is its own URL/real link (no
   client-side router in this app), so cross-page consistency has to be a
   shared client-side store, the same way the workspace switcher already
   persists ts_active_project_id. Regions/engines are never hard-coded -
   callers pass in what the backend's available_filters actually reports. */
(function () {
  'use strict';
  window.TS = window.TS || {};

  var STORAGE_KEY = 'ts_analytics_filters';

  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

  function getState() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch (e) { /* ignore */ }
    return { range: null, startDate: null, endDate: null, region: null, engineIds: [] };
  }

  function setState(state) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
  }

  /* {range, start_date, end_date, region, engine_ids} shaped for the
     backend's query-string param names. */
  function toQueryParams(state) {
    var params = {};
    if (state.range) params.range = state.range;
    if (state.range === 'custom') {
      if (state.startDate) params.start_date = state.startDate;
      if (state.endDate) params.end_date = state.endDate;
    }
    if (state.region) params.region = state.region;
    if (state.engineIds && state.engineIds.length) params.engine_ids = state.engineIds.join(',');
    return params;
  }

  function toQueryString(state) {
    var params = toQueryParams(state);
    var parts = Object.keys(params).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); });
    return parts.length ? '?' + parts.join('&') : '';
  }

  var RANGE_OPTIONS = [
    { v: '', label: 'All time' }, { v: 'today', label: 'Today' }, { v: '7d', label: 'Last 7 days' },
    { v: '30d', label: 'Last 30 days' }, { v: '90d', label: 'Last 90 days' }, { v: 'custom', label: 'Custom range' },
  ];

  /* render(container, {regions, engines, onApply}) - regions is a plain
     string array, engines is [{id, display_name}, ...], both from the
     endpoint's own available_filters, never invented client-side. */
  function render(container, opts) {
    opts = opts || {};
    var regions = opts.regions || [];
    var engines = opts.engines || [];
    var state = getState();

    container.innerHTML =
      '<div class="field" style="align-items:center">' +
      '  <select id="tsf-range">' + RANGE_OPTIONS.map(function (o) {
        return '<option value="' + o.v + '"' + (state.range === o.v || (!state.range && o.v === '') ? ' selected' : '') + '>' + esc(o.label) + '</option>';
      }).join('') + '</select>' +
      '  <input type="date" id="tsf-start" style="max-width:150px" value="' + esc(state.startDate || '') + '"' + (state.range === 'custom' ? '' : ' hidden') + '>' +
      '  <input type="date" id="tsf-end" style="max-width:150px" value="' + esc(state.endDate || '') + '"' + (state.range === 'custom' ? '' : ' hidden') + '>' +
      '  <select id="tsf-region"><option value="">All regions</option>' +
      regions.map(function (r) { return '<option value="' + esc(r) + '"' + (state.region === r ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('') +
      '  </select>' +
      '  <select id="tsf-engine" multiple size="3" style="min-width:160px" title="Ctrl/Cmd-click to select several">' +
      engines.map(function (e) {
        var selected = (state.engineIds || []).indexOf(e.id) !== -1 ? ' selected' : '';
        return '<option value="' + e.id + '"' + selected + '>' + esc(e.display_name) + '</option>';
      }).join('') +
      '  </select>' +
      '  <button type="button" class="btn sm" id="tsf-apply">Apply</button>' +
      '</div>';

    container.querySelector('#tsf-range').addEventListener('change', function (evt) {
      var showCustom = evt.target.value === 'custom';
      container.querySelector('#tsf-start').hidden = !showCustom;
      container.querySelector('#tsf-end').hidden = !showCustom;
    });

    container.querySelector('#tsf-apply').addEventListener('click', function () {
      var engineSelect = container.querySelector('#tsf-engine');
      var engineIds = Array.prototype.slice.call(engineSelect.selectedOptions).map(function (o) { return Number(o.value); });
      var newState = {
        range: container.querySelector('#tsf-range').value || null,
        startDate: container.querySelector('#tsf-start').value || null,
        endDate: container.querySelector('#tsf-end').value || null,
        region: container.querySelector('#tsf-region').value || null,
        engineIds: engineIds,
      };
      setState(newState);
      if (typeof opts.onApply === 'function') opts.onApply(newState);
    });
  }

  TS.filters = { getState: getState, setState: setState, toQueryParams: toQueryParams, toQueryString: toQueryString, render: render };
})();
