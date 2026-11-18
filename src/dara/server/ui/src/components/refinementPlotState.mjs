import { addDSpacingHover, clampRange, finiteRange } from './plotNavigation.mjs';
import { D_TICK_PX, HKL_LABEL_PX, dAxisTicks, hklText, pickHklLabels } from './plotLabels.mjs';

// Keep legend selection in React state: Plotly's internal visibility is lost
// whenever a toolbar action supplies a fresh data array.
export function legendKey(trace, index) {
  return trace.legendgroup || `trace:${index}`;
}

export function isTraceSelected(trace, index, selection) {
  const explicit = selection[legendKey(trace, index)];
  if (explicit !== undefined) return explicit;
  // Phases start selected, so their Bragg rows show as in HighScore/TOPAS; the Profiles and
  // Bragg switches decide what of a selected phase is drawn.
  if (trace.meta === 'phase-profile' || trace.meta === 'reflection') return true;
  return trace.visible !== false && trace.visible !== 'legendonly';
}

function numbers(values) {
  // Plotly 6 may encode numeric arrays as typed-array JSON.
  if (Array.isArray(values) || ArrayBuffer.isView(values)) return Array.from(values);
  if (!values?.bdata) return [];
  const bytes = Uint8Array.from(atob(values.bdata), c => c.charCodeAt(0));
  const type = { f8: Float64Array, f4: Float32Array, i4: Int32Array, i2: Int16Array,
    i1: Int8Array, u4: Uint32Array, u2: Uint16Array, u1: Uint8Array }[values.dtype];
  return type ? Array.from(new type(bytes.buffer)) : [];
}

function positiveTicks(lower, upper) {
  const start = Math.max(0, lower);
  const rough = Math.max(upper - start, 1e-9) / 5;
  const power = 10 ** Math.floor(Math.log10(rough));
  const factor = [1, 2, 2.5, 5, 10].find(v => v * power >= rough) || 10;
  const step = factor * power;
  const ticks = [];
  for (let value = Math.ceil(start / step) * step; value <= upper && ticks.length < 30; value += step) {
    ticks.push(Number(value.toPrecision(12)));
  }
  return ticks;
}

const withDCache = new WeakMap();

// Hover shows d-spacing when the server sent the Kα1 wavelength (layout.meta).
function withDSpacing(plot) {
  const wavelength = Number(plot.layout?.meta?.wavelength_angstrom);
  if (!(wavelength > 0)) return plot;
  if (!withDCache.has(plot)) withDCache.set(plot, { ...plot, data: addDSpacingHover(plot.data, wavelength) });
  return withDCache.get(plot);
}

function cloneLayout(layout) {
  // Plotly writes zoom ranges back into the layout objects it is given. The
  // cached server layout is shared between renders (and xaxis2 "matches" x),
  // so a shallow copy let an old 2θ zoom survive "Reset zoom".
  return typeof structuredClone === 'function' ? structuredClone(layout) : JSON.parse(JSON.stringify(layout));
}

function intensityStats(plot, isShown, xRange) {
  const [lo, hi] = xRange ? [Math.min(...xRange), Math.max(...xRange)] : [-Infinity, Infinity];
  let maximum = -Infinity;
  let minimum = Infinity;
  let smallestPositive = Infinity;
  plot.data.forEach((trace, index) => {
    if (['reflection', 'difference', 'diagnostic'].includes(trace.meta) || !isShown(trace, index)) return;
    const xs = numbers(trace.x);
    numbers(trace.y).forEach((value, i) => {
      if (!Number.isFinite(value) || xs[i] < lo || xs[i] > hi) return;
      maximum = Math.max(maximum, value);
      minimum = Math.min(minimum, value);
      if (value > 0) smallestPositive = Math.min(smallestPositive, value);
    });
  });
  return { maximum, minimum, smallestPositive };
}

/**
 * Plot title with the pattern name: the server titles the figure after BGMN's working file
 * (`xrd_data.xy (Rwp=2.96%)`), so the name part is replaced and the Rwp kept.
 */
/**
 * Traces with the observed pattern replaced by a display-treated one (`intensity` on the same 2θ
 * grid) and renamed `name`. The difference trace keeps the measured residual. Unchanged when
 * `treated` is null or its length does not match the observed trace.
 */
export function withTreatedObserved(data, treated, name) {
  if (!treated?.intensity) return data;
  const index = data.findIndex(trace => !trace.meta && trace.name === 'Observed');
  if (index < 0 || numbers(data[index].x).length !== treated.intensity.length) return data;
  return data.map((trace, i) => (i === index ? { ...trace, x: numbers(trace.x), y: treated.intensity, name } : trace));
}

export function patternTitle(title, name) {
  const text = typeof title === 'string' ? title : title?.text;
  if (!name || typeof text !== 'string') return title;
  const rwp = / \(Rwp=[^)]*\)$/.exec(text);
  const next = `${name}${rwp ? rwp[0] : ''}`;
  return typeof title === 'string' ? next : { ...title, text: next };
}

/** Largest share of the measured 2θ range that "Use current view" may exclude. */
export const MAX_EXCLUDED_SHARE = 0.9;

/**
 * 2θ window offered as an excluded interval ("Use current view"): only a real zoom, never the
 * whole pattern. `null` without a zoom or when the window covers more than 90 % of the
 * measured range (excluding it would leave almost nothing to fit).
 */
export function exclusionViewRange(xRange, fullRange) {
  const view = finiteRange(xRange);
  if (!view) return null;
  const [lo, hi] = [Math.min(...view), Math.max(...view)];
  const full = finiteRange(fullRange);
  if (full && hi - lo > MAX_EXCLUDED_SHARE * Math.abs(full[1] - full[0])) return null;
  return [lo, hi];
}

// Below this plot width the legend column gets smaller text and only the room it needs
// (the server reserves 290 px, which leaves too little for the pattern at 1280 px).
export const COMPACT_LEGEND_WIDTH = 1400;
// Below this plot width the legend is drawn in rows under the plot instead of beside it.
export const LEGEND_BELOW_WIDTH = 1200;
// Legend rows under a narrow plot: one row is about 24 px at font size 12 (the app's smallest
// text size), and an entry about 40 px (symbol and gap) plus 7 px per character.
export const LEGEND_ROW_PX = 24;
const LEGEND_FONT_SIZE = 12;
const LEGEND_ENTRY_PX = 40;
const LEGEND_CHAR_PX = 7;
const LEGEND_GAP_PX = 10; // between the 2θ title and the first legend row

/** Text of a legend entry without HTML tags (`TiFe<sub>2</sub>O<sub>5</sub>` -> `TiFe2O5`). */
const legendText = name => String(name ?? '').replace(/<[^>]*>/g, '');

/**
 * Rows a horizontal legend needs for `data` in `width` px: the entries Plotly shows (a trace with
 * `showlegend: false` has none, a legend group appears once) packed left to right.
 */
export function legendRowCount(data = [], width = 0) {
  const seen = new Set();
  const widths = [];
  (data || []).forEach(trace => {
    if (trace?.showlegend === false) return;
    if (trace?.legendgroup) {
      if (seen.has(trace.legendgroup)) return;
      seen.add(trace.legendgroup);
    }
    widths.push(LEGEND_ENTRY_PX + LEGEND_CHAR_PX * legendText(trace?.name).length);
  });
  if (!widths.length) return 0;
  const room = Math.max(Number(width) || 0, 200);
  let rows = 1;
  let used = 0;
  widths.forEach(entry => {
    if (used > 0 && used + entry > room) { rows += 1; used = 0; }
    used += entry;
  });
  return rows;
}

export function buildRefinementView(plot, {
  selection = {}, showDiagnostics = true, showPhaseProfiles = true,
  showReflections = true, scale = 'linear', yRange = null, xRange = null, fitY = true, plotWidth = null, title = null,
  hklLabels = false, dAxis = false,
} = {}) {
  plot = withDSpacing(plot);
  const baseLayout = cloneLayout(plot.layout);
  // The plot div sets the size: a fixed server height would override autosize and
  // push the Obs − calc panel under the content below (or leave an empty band).
  delete baseLayout.height;
  delete baseLayout.width;
  baseLayout.autosize = true;
  const fullX = finiteRange(baseLayout.xaxis?.range);
  // The 2θ window never leaves the measured range.
  const xWindow = xRange ? (clampRange(xRange, fullX) || null) : null;
  const shown = (trace, index) => {
    if (!isTraceSelected(trace, index, selection)) return false;
    return trace.meta !== 'phase-profile' || showPhaseProfiles;
  };
  // A user intensity range (box zoom with Auto-Y off) is the data window: the marker band and
  // the Bragg rows are added below it, so they always stay inside the main panel.
  const userY = finiteRange(yRange);
  // Auto-Y scales the intensity axis to the shown traces in the visible 2θ window (the whole
  // pattern when not zoomed), so a high background does not leave the lower panel empty.
  // With Auto-Y off the axis starts at zero.
  let fitted = Boolean(fitY);
  let stats = fitted ? intensityStats(plot, shown, xWindow || null) : intensityStats(plot, () => true, null);
  if (!Number.isFinite(stats.maximum)) {
    fitted = false;
    stats = intensityStats(plot, () => true, null);
  }
  let maximum = Number.isFinite(stats.maximum) ? stats.maximum : 1;
  let minimum = fitted ? stats.minimum : Math.min(0, stats.minimum);
  if (!fitted) maximum = Math.max(1, maximum);
  let smallestPositive = stats.smallestPositive;
  if (!Number.isFinite(minimum)) minimum = 0;
  if (!Number.isFinite(smallestPositive)) smallestPositive = 1;
  if (userY) {
    if (scale === 'log') {
      smallestPositive = 10 ** userY[0];
      maximum = 10 ** userY[1];
    } else {
      [minimum, maximum] = userY;
    }
  }
  const span = Math.max(maximum - minimum, 1e-9);
  const unit = fitted || userY ? span : maximum;
  // Peak markers get their own band between the pattern and the Bragg rows, so the
  // observed points and the axis edge never cover them.
  const markersShown = showDiagnostics && plot.data.some((trace, index) =>
    trace.meta === 'diagnostic' && isTraceSelected(trace, index, selection) && numbers(trace.x).length);
  const markerBand = markersShown ? 0.08 : 0;
  const logMarkerBand = markersShown ? 4 : 1;
  const rows = [];
  const annotations = [];
  const data = plot.data.map((trace, index) => {
    const selected = isTraceSelected(trace, index, selection);
    let enabled = selected;
    if (trace.meta === 'diagnostic') enabled = selected && showDiagnostics;
    // Keep the phase legend clickable even when profile display is disabled.
    if (trace.meta === 'phase-profile') enabled = selected && showPhaseProfiles;
    if (trace.meta === 'reflection') enabled = selected && showReflections;
    const visible = enabled ? true : 'legendonly';
    if (trace.meta === 'diagnostic' && enabled) {
      // Peak markers arrive at intensity 0. Centre them in their band under the visible
      // minimum, so a zoom that rescales the intensity axis keeps them in view.
      const y = scale === 'log' ? smallestPositive / 2 : minimum - unit * markerBand / 2;
      return { ...trace, visible, cliponaxis: false, x: numbers(trace.x), y: numbers(trace.x).map(() => y) };
    }
    // A selected phase with profiles switched off keeps a full-colour legend entry (its Bragg row
    // is drawn) but plots no curve: same trace, no y values.
    if (trace.meta === 'phase-profile' && selected && !showPhaseProfiles) {
      const x = numbers(trace.x); // typed or encoded arrays come back as plain numbers
      return { ...trace, visible: true, hoverinfo: 'skip', x, y: x.map(() => null) };
    }
    if (trace.meta !== 'reflection' || !enabled) return { ...trace, visible };
    const row = rows.length;
    const position = scale === 'log'
      ? smallestPositive / (logMarkerBand * 4 ** (row + 1))
      : minimum - unit * (markerBand + 0.095 * (row + 1));
    rows.push(trace.legendgroup);
    const label = trace.name.replace(/ \([^)]* wt%\)$/, '');
    annotations.push({
      x: 0, xref: 'paper', xanchor: 'right', xshift: -10,
      y: scale === 'log' ? Math.log10(position) : position, yref: 'y',
      text: label, showarrow: false, align: 'right',
      font: { size: 11, color: trace.marker.color },
    });
    return { ...trace, visible, x: numbers(trace.x), y: numbers(trace.x).map(() => position) };
  });
  const top = userY ? 0 : 1;
  // hkl labels stand above the tallest peaks: more room at the top.
  const range = scale === 'log'
    ? [Math.log10(smallestPositive / logMarkerBand) - (rows.length + 0.5) * Math.log10(4), Math.log10(maximum) + (hklLabels ? 0.35 : 0.04) * top]
    : [minimum - unit * (markerBand + (rows.length ? 0.095 * (rows.length + 0.55) : 0.035)), maximum + unit * (hklLabels ? 0.2 : 0.05) * top];
  const yaxis = { ...baseLayout.yaxis, type: scale, range, autorange: false,
    // While the intensity axis follows the 2θ window, box zoom and pan act on 2θ only.
    fixedrange: Boolean(fitY && !userY),
    // Ticks cover the data window only (not the marker band and Bragg rows below it) and
    // hide the upper panel's negative intensities. Residuals keep their signed values.
    tickmode: scale === 'log' ? 'auto' : 'array',
    tickvals: scale === 'log' ? undefined : positiveTicks(minimum, range[1]),
    ticktext: undefined,
  };
  const xView = xWindow || fullX;
  // The 2θ title is drawn once, under the Obs − calc panel; the main panel keeps its tick
  // labels, which leaves a clear gap between the panels at small heights.
  const xaxis = { ...baseLayout.xaxis, showticklabels: true, title: { text: '' } };
  if (xView) Object.assign(xaxis, { range: [...xView], autorange: false });
  const layout = { ...baseLayout, annotations, yaxis, xaxis };
  // Bragg-row labels sit left of the axis: widen the left margin so the longest is not clipped.
  const labelWidth = Math.max(0, ...annotations.map(item => 20 + 6.5 * legendText(item.text).length));
  if (labelWidth > (Number(layout.margin?.l) || 0)) layout.margin = { ...(layout.margin || {}), l: Math.ceil(labelWidth) };
  if (title) layout.title = patternTitle(layout.title, title);
  if (layout.xaxis2 && xView) Object.assign(layout.xaxis2, { range: [...xView], autorange: false });
  // The residual intensity never zooms: a box there is a 2θ band.
  if (layout.yaxis2) layout.yaxis2 = { ...layout.yaxis2, fixedrange: true };
  const narrow = Number.isFinite(plotWidth) && plotWidth > 0;
  if (narrow && plotWidth < LEGEND_BELOW_WIDTH) {
    // Narrow plot (1366 px screen with the Add phase drawer open): a legend column would take a
    // third of the 2θ axis, so the legend goes in rows under the plot.
    // The bottom margin grows by the legend rows, and the legend sits at the bottom of the figure
    // (container coordinates), so it stays under the 2θ title and is never clipped.
    const margin = layout.margin || {};
    const rows = legendRowCount(data, plotWidth - (Number(margin.l) || 0) - 24);
    const bottom = Number.isFinite(Number(margin.b)) ? Number(margin.b) : 65;
    layout.margin = { ...margin, r: 24, b: bottom + LEGEND_GAP_PX + rows * LEGEND_ROW_PX };
    layout.legend = { ...(layout.legend || {}), orientation: 'h', xref: 'paper', x: 0, xanchor: 'left',
      yref: 'container', y: 0, yanchor: 'bottom', font: { ...(layout.legend?.font || {}), size: LEGEND_FONT_SIZE } };
  } else if (narrow && plotWidth < COMPACT_LEGEND_WIDTH) {
    // Plotly widens the right margin to the legend's own width when it needs more.
    layout.margin = { ...(layout.margin || {}), r: 24 };
    layout.legend = { ...(layout.legend || {}), font: { ...(layout.legend?.font || {}), size: LEGEND_FONT_SIZE }, itemwidth: 30 };
  }
  const wavelength = Number(plot.layout?.meta?.wavelength_angstrom);
  if ((hklLabels || (dAxis && wavelength > 0)) && xView) {
    // Degrees per pixel of the 2θ axis, so label and tick gaps stay the same on screen at any zoom.
    const margin = layout.margin || {};
    const areaPx = Math.max((narrow ? plotWidth : 1000) - (Number(margin.l) || 80) - (Number(margin.r) || 24), 200);
    const degPerPx = (xView[1] - xView[0]) / areaPx;
    if (hklLabels) annotations.push(...hklAnnotations(plot, data, selection, xView, HKL_LABEL_PX * degPerPx, scale));
    if (dAxis && wavelength > 0) {
      // A second 2θ axis on top of the main panel whose tick labels are d-spacings: the data
      // stay plotted against 2θ, so zoom and pan are unchanged.
      layout.xaxis3 = { ...dAxisTicks(xView, wavelength, D_TICK_PX * degPerPx), tickmode: 'array',
        overlaying: 'x', anchor: 'y', side: 'top', range: [...xView], autorange: false,
        showgrid: false, zeroline: false, showline: false, ticks: 'outside', ticklen: 4,
        tickcolor: baseLayout.xaxis?.tickcolor, title: { text: 'd (Å)', standoff: 4, font: { size: 12 } } };
      layout.margin = { ...(layout.margin || {}), t: Math.max(Number(layout.margin?.t) || 0, 40) + 34 };
      // A legend row above the panel would sit on the d labels: pin it to the top of the figure.
      if (layout.legend?.yref !== 'container') layout.legend = { ...(layout.legend || {}), yref: 'container', y: 1, yanchor: 'top' };
      // Plotly draws an axis only when a trace uses it: an empty trace, last, so trace indices hold.
      data.push({ type: 'scatter', mode: 'lines', x: [...xView], y: [null, null], xaxis: 'x3', yaxis: 'y',
        hoverinfo: 'skip', showlegend: false, meta: 'd-axis' });
    }
  }
  return { data, layout, rows, fullXRange: fullX || null };
}

/** Top of the observed, calculated and background curves within `half` degrees of `x`. */
function curveTop(curves, x, half) {
  let top = -Infinity;
  curves.forEach(({ xs, ys }) => {
    for (let i = 0; i < xs.length; i += 1) {
      if (Math.abs(xs[i] - x) <= half && ys[i] > top) top = ys[i];
    }
  });
  return top;
}

/** "(hkl)" annotations above the strongest reflections of the selected phases in the window. */
function hklAnnotations(plot, data, selection, window, minGap, scale) {
  const reflections = [];
  plot.data.forEach((trace, index) => {
    // Bragg ticks of a Rietveld fit, or the reference sticks of an identification (same hkl customdata).
    if (!['reflection', 'reference-sticks'].includes(trace.meta) || !isTraceSelected(trace, index, selection)) return;
    const xs = numbers(trace.x);
    const rows = Array.isArray(trace.customdata) ? trace.customdata : [];
    const color = data[index]?.marker?.color || trace.marker?.color;
    xs.forEach((x, i) => {
      const [h, k, l, intensity] = rows[i] || [];
      if ([h, k, l].every(value => Number.isFinite(Number(value)))) reflections.push({ x, h, k, l, intensity, color });
    });
  });
  const curves = plot.data.filter((trace, index) => !trace.meta && trace.visible !== false && isTraceSelected(trace, index, selection))
    .map(trace => ({ xs: numbers(trace.x), ys: numbers(trace.y) }));
  return pickHklLabels(reflections, window, minGap).flatMap(item => {
    const height = curveTop(curves, item.x, Math.max(minGap / 2, 0.02));
    if (!Number.isFinite(height) || (scale === 'log' && height <= 0)) return [];
    return [{ x: item.x, xref: 'x', y: scale === 'log' ? Math.log10(height) : height, yref: 'y',
      text: hklText(item.h, item.k, item.l), showarrow: false, textangle: -90, yanchor: 'bottom', yshift: 3,
      font: { size: 11, color: item.color } }];
  });
}
