/* Renders one {value,low,high,n} envelope (app/stats.py's contract) as a
   .readout stat tile -- shared by any page showing an enveloped rate. */
(function () {
  'use strict';
  window.TS = window.TS || {};

  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  /* opts: {label, envelope, formatValue(v), unit, emptyState:{title,body}, delta:{state}} */
  TS.statTile = function (opts) {
    var label = '<div class="lbl">' + esc(opts.label) + '</div>';
    var envelope = opts.envelope;
    if (!envelope || envelope.value == null) {
      var empty = opts.emptyState || { title: 'No data yet', body: '' };
      return '<div class="readout">' + label +
        '<div class="empty" style="margin-top:8px"><b>' + esc(empty.title) + '</b>' +
        (empty.body ? '<p>' + esc(empty.body) + '</p>' : '') + '</div></div>';
    }
    var valueStr = opts.formatValue(envelope.value);
    var rangeStr = (envelope.low != null && envelope.high != null)
      ? opts.formatValue(envelope.low) + '–' + opts.formatValue(envelope.high) : null;
    var deltaHtml = '';
    if (opts.delta) {
      if (opts.delta.state === 'up') deltaHtml = '<div class="verdict up">↑ improving</div>';
      else if (opts.delta.state === 'down') deltaHtml = '<div class="verdict down">↓ declining</div>';
      else if (opts.delta.state === 'no_measurable_change') deltaHtml = '<div class="verdict">No measurable change</div>';
    }
    return '<div class="readout">' + label +
      '<div class="rval"><b>' + esc(valueStr) + '</b>' +
      (opts.unit ? '<span class="u">' + esc(opts.unit) + '</span>' : '') + '</div>' +
      (rangeStr ? '<div class="ci">95% interval <b>' + esc(rangeStr) + '</b></div>' : '') +
      deltaHtml +
      '<div class="sample">Based on ' + envelope.n + ' answer' + (envelope.n === 1 ? '' : 's') + '</div>' +
      '</div>';
  };
})();
