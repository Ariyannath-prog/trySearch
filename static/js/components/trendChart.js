/* Single-series visibility-score trend line over metrics_daily history rows.
   Inline SVG, no library, following this project's dataviz conventions:
   honest gaps across unmeasured days (no interpolation over a null score),
   hairline gridlines, a confidence-band wash under the line, an end-value
   marker, and a synced pointermove tooltip. Renders into `container`. */
(function () {
  'use strict';
  window.TS = window.TS || {};

  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).slice(0, 10);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function fmtScore(v) {
    return v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1);
  }

  /* history: chronological metrics_daily rows, each with .date and a nullable
     .visibility_score (0-100). */
  TS.renderTrendChart = function (container, history) {
    history = history || [];
    var points = history.filter(function (r) { return r.visibility_score != null; });
    if (points.length < 2) {
      container.innerHTML = '<div class="empty"><b>Not enough history yet</b>' +
        '<p>The trend line appears once at least two days of scored data exist.</p></div>';
      return;
    }

    var W = 640, H = 200, padL = 30, padR = 12, padT = 14, padB = 22;
    var plotW = W - padL - padR, plotH = H - padT - padB;
    var n = history.length;
    var xAt = function (i) { return n <= 1 ? padL : padL + (i / (n - 1)) * plotW; };
    var yAt = function (v) { return padT + (1 - Math.max(0, Math.min(100, v)) / 100) * plotH; };

    var parts = [];
    [0, 25, 50, 75, 100].forEach(function (tick) {
      var y = yAt(tick);
      parts.push('<line class="gridline" x1="' + padL + '" x2="' + (W - padR) +
        '" y1="' + y + '" y2="' + y + '"/>');
      parts.push('<text x="2" y="' + (y + 4) +
        '" fill="var(--ink-4)" font-size="10" font-family="var(--font-data)">' + tick + '</text>');
    });

    var segments = [], current = [];
    history.forEach(function (row, idx) {
      if (row.visibility_score == null) {
        if (current.length) { segments.push(current); current = []; }
        return;
      }
      current.push({ idx: idx, row: row });
    });
    if (current.length) segments.push(current);

    segments.forEach(function (seg) {
      if (seg.length < 2) return;
      var d = seg.map(function (p, i) {
        return (i === 0 ? 'M' : 'L') + xAt(p.idx).toFixed(1) + ' ' + yAt(p.row.visibility_score).toFixed(1);
      }).join(' ');
      var last = seg[seg.length - 1], first = seg[0];
      var areaD = d + ' L' + xAt(last.idx).toFixed(1) + ' ' + (padT + plotH) +
        ' L' + xAt(first.idx).toFixed(1) + ' ' + (padT + plotH) + ' Z';
      parts.push('<path class="band" d="' + areaD + '"/>');
      parts.push('<path class="serie you" d="' + d + '"/>');
    });

    var lastPoint = points[points.length - 1];
    var lastIdx = history.lastIndexOf(lastPoint);
    var lx = xAt(lastIdx), ly = yAt(lastPoint.visibility_score);
    parts.push('<circle cx="' + lx + '" cy="' + ly + '" r="4" fill="var(--brand)" class="endcap"/>');

    container.innerHTML = '<div class="chartwrap">' +
      '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '">' + parts.join('') +
      '<line class="trend-crosshair" y1="' + padT + '" y2="' + (padT + plotH) +
      '" stroke="var(--ink-4)" stroke-width="1" visibility="hidden"/>' +
      '<rect class="trend-hit" x="' + padL + '" y="' + padT + '" width="' + plotW +
      '" height="' + plotH + '" fill="transparent"/>' +
      '</svg><div class="tip trend-tip"></div></div>';

    var svgEl = container.querySelector('svg');
    var hit = container.querySelector('.trend-hit');
    var crosshair = container.querySelector('.trend-crosshair');
    var tip = container.querySelector('.trend-tip');

    function onMove(evt) {
      var rect = svgEl.getBoundingClientRect();
      var relX = ((evt.clientX - rect.left) / rect.width) * W;
      var idx = Math.max(0, Math.min(n - 1, Math.round(((relX - padL) / plotW) * (n - 1))));
      var row = history[idx];
      if (!row) return;
      var x = xAt(idx);
      crosshair.setAttribute('x1', x);
      crosshair.setAttribute('x2', x);
      crosshair.setAttribute('visibility', 'visible');
      tip.classList.add('show');
      tip.style.left = ((x / W) * rect.width) + 'px';
      tip.style.top = ((row.visibility_score != null ? yAt(row.visibility_score) : H / 2) / H * rect.height) + 'px';
      tip.innerHTML = '<div class="tt">' + fmtDate(row.date) + '</div>' +
        '<div class="tiprow you"><span class="nm">Visibility score</span><span class="vv">' +
        (row.visibility_score == null ? 'No score' : fmtScore(row.visibility_score)) + '</span></div>';
    }
    function onLeave() {
      crosshair.setAttribute('visibility', 'hidden');
      tip.classList.remove('show');
    }
    hit.addEventListener('pointermove', onMove);
    hit.addEventListener('pointerleave', onLeave);
  };
})();
