/* charts.js — Canvas 2D strip charts for the cockpit mock.
 *
 * Six fixed series per chart (one per joint, colors from tokens.css), a 15 s
 * rolling window, and one shared rAF loop that only redraws a chart when its
 * data changed or its canvas was resized. Dropouts longer than GAP_S break
 * the line instead of bridging it.
 */
(function () {
  'use strict';
  const NS = (window.COCKPIT = window.COCKPIT || {});

  const WINDOW_S = 15;
  const GAP_S = 0.5;
  const DPR_CAP = 2;
  const PAD_RATIO = 0.1;
  const LINE_W = 2;
  const SERIES_COUNT = 6;
  const LABEL_FONT = '10px ui-monospace, "SF Mono", Menlo, Consolas, monospace';

  let palette = null;

  function readPalette() {
    const style = getComputedStyle(document.documentElement);
    const token = (name) => style.getPropertyValue(name).trim();
    return {
      series: [1, 2, 3, 4, 5, 6].map((i) => token(`--color-j${i}`)),
      overlay: token('--color-ink'),
      label: token('--color-ink-muted'),
      zero: token('--color-line-strong'),
      empty: token('--color-ink-faint'),
    };
  }

  function evict(points, now) {
    while (points.length && now - points[0][0] > WINDOW_S) points.shift();
  }

  function visibleRange(chart) {
    let lo = Infinity;
    let hi = -Infinity;
    const scan = (points) =>
      points.forEach(([, v]) => {
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      });
    chart.buf.forEach(scan);
    scan(chart.overlayBuf);
    if (chart.zeroLine) {
      lo = Math.min(lo, 0);
      hi = Math.max(hi, 0);
    }
    if (lo > hi) {
      lo = -1;
      hi = 1;
    }
    const pad = (hi - lo) * PAD_RATIO + 1e-6;
    return { lo: lo - pad, hi: hi + pad };
  }

  function tracePath(ctx, points, t0, t1, lo, hi, w, h) {
    let started = false;
    let prevT = null;
    ctx.beginPath();
    points.forEach(([t, v]) => {
      const x = ((t - t0) / (t1 - t0)) * w;
      const y = h * (1 - (v - lo) / (hi - lo));
      const gap = prevT !== null && t - prevT > GAP_S;
      if (!started || gap) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
      prevT = t;
    });
    ctx.stroke();
  }

  function drawEmpty(chart, w, h) {
    const ctx = chart.ctx;
    ctx.font = LABEL_FONT;
    ctx.fillStyle = palette.empty;
    ctx.textAlign = 'center';
    ctx.fillText('NO DATA', w / 2, h / 2 + 3);
    ctx.textAlign = 'left';
  }

  function drawChart(chart) {
    const { ctx, canvas } = chart;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    ctx.clearRect(0, 0, w, h);
    if (chart.buf.every((points) => points.length < 2)) {
      drawEmpty(chart, w, h);
      return;
    }
    const { lo, hi } = visibleRange(chart);
    const t1 = chart.lastT;
    const t0 = t1 - WINDOW_S;
    if (chart.zeroLine) {
      const y0 = h * (1 - (0 - lo) / (hi - lo));
      ctx.strokeStyle = palette.zero;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, y0);
      ctx.lineTo(w, y0);
      ctx.stroke();
    }
    ctx.lineWidth = LINE_W;
    ctx.lineJoin = 'round';
    chart.buf.forEach((points, i) => {
      ctx.strokeStyle = palette.series[i];
      ctx.setLineDash([]);
      tracePath(ctx, points, t0, t1, lo, hi, w, h);
    });
    if (chart.overlayBuf.length > 1) {
      ctx.strokeStyle = palette.overlay;
      ctx.setLineDash([6, 5]);
      tracePath(ctx, chart.overlayBuf, t0, t1, lo, hi, w, h);
      ctx.setLineDash([]);
    }
    ctx.font = LABEL_FONT;
    ctx.fillStyle = palette.label;
    ctx.fillText(hi.toFixed(0), 4, 11);
    ctx.fillText(lo.toFixed(0), 4, h - 4);
  }

  function fitCanvas(chart) {
    const { canvas } = chart;
    const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    const w = Math.round(canvas.clientWidth * dpr);
    const h = Math.round(canvas.clientHeight * dpr);
    if (w === 0 || (w === canvas.width && h === canvas.height)) return false;
    canvas.width = w;
    canvas.height = h;
    chart.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return true;
  }

  const registry = [];

  function frameLoop() {
    registry.forEach((chart) => {
      const resized = fitCanvas(chart);
      if (chart.dirty || resized) {
        drawChart(chart);
        chart.dirty = false;
      }
    });
    requestAnimationFrame(frameLoop);
  }

  NS.charts = {
    /* opts: { zeroLine?: boolean, overlay?: boolean } */
    create(canvas, opts) {
      if (!palette) palette = readPalette();
      const chart = {
        canvas,
        ctx: canvas.getContext('2d'),
        buf: Array.from({ length: SERIES_COUNT }, () => []),
        overlayBuf: [],
        zeroLine: Boolean(opts && opts.zeroLine),
        lastT: 0,
        dirty: true,
      };
      registry.push(chart);
      if (registry.length === 1) requestAnimationFrame(frameLoop);
      return {
        /* values: array of SERIES_COUNT numbers; overlayValue optional */
        push(t, values, overlayValue) {
          chart.lastT = t;
          values.forEach((v, i) => {
            chart.buf[i].push([t, v]);
            evict(chart.buf[i], t);
          });
          if (overlayValue !== undefined) {
            chart.overlayBuf.push([t, overlayValue]);
            evict(chart.overlayBuf, t);
          }
          chart.dirty = true;
        },

        clear() {
          chart.buf = Array.from({ length: SERIES_COUNT }, () => []);
          chart.overlayBuf = [];
          chart.lastT = 0;
          chart.dirty = true;
        },
      };
    },
  };
})();
