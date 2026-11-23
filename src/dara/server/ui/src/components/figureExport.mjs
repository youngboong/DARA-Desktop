// Journal-style export of the shown plot view (pure: node tests import it). The interactive plot
// keeps its screen theme; the exported figure follows the report figure (report/figures.py).

/** Okabe-Ito colours, ordered so neighbouring phases also differ in grey (as the report figure). */
export const FIGURE_COLORS = ['#0072B2', '#E69F00', '#009E73', '#56B4E9', '#CC79A7'];
export const FIGURE_FONT = 'Arial, Helvetica, "DejaVu Sans", sans-serif';
/** Journal column widths (mm): single and double column. */
export const FIGURE_WIDTHS = { single: 85, double: 170 };
const PX_PER_MM = 96 / 25.4; // Plotly draws in CSS pixels (96 per inch)
const PT = 96 / 72; // pixels per point
const HEIGHT_RATIO = 0.68;
const BLACK = '#000000';
const CALCULATED = '#D7191C';
const BACKGROUND = '#8c8c8c';
const DIFFERENCE = '#5a5a5a';
// Screen-only traces: peak markers, the background editor and the reference preview.
const SCREEN_ONLY = new Set(['diagnostic', 'bg-curve', 'bg-anchors', 'preview']);

const ANODES = { Cu: 1.540598, Co: 1.78901, Cr: 2.28976, Fe: 1.936042, Mo: 0.7093, Ag: 0.559421 };

/** "Cu Kα" for a lab anode (setting or Kα1 wavelength), "λ = 0.7293 Å" otherwise, null when unknown. */
export function radiationLabel(meta) {
  const setting = String(meta?.wavelength ?? '').trim();
  const named = Object.keys(ANODES).find(name => name.toLowerCase() === setting.toLowerCase());
  if (named) return `${named} Kα`;
  const angstrom = Number(meta?.wavelength_angstrom);
  if (!(angstrom > 0)) return null;
  const anode = Object.keys(ANODES).find(name => Math.abs(ANODES[name] - angstrom) < 0.002);
  return anode ? `${anode} Kα` : `λ = ${angstrom.toFixed(4)} Å`;
}

const font = (points, color = BLACK) => ({ family: FIGURE_FONT, size: points * PT, color });

function frameAxis(axis, title) {
  return {
    ...axis,
    showgrid: false, zeroline: false, showline: true, linecolor: BLACK, linewidth: 0.75, mirror: 'ticks',
    ticks: 'inside', ticklen: 4, tickwidth: 0.75, tickcolor: BLACK, tickfont: font(8),
    title: title === undefined ? axis?.title : { text: title, font: font(8), standoff: 4 },
  };
}

/**
 * `{data, layout, width, height}` of the plot view `data`/`layout` (what is shown, with its zoom)
 * in journal style: Arial 7–8 pt, observed black, calculated red, background and difference grey,
 * phases in Okabe-Ito colours, closed frames with inward ticks, no grid and no title, `widthMm`
 * wide (85 single column, 170 double column). Screen-only traces are left out.
 */
export function publicationFigure(data, layout, { widthMm = FIGURE_WIDTHS.double } = {}) {
  const phases = [];
  const phaseColor = group => {
    if (!phases.includes(group)) phases.push(group);
    return FIGURE_COLORS[phases.indexOf(group) % FIGURE_COLORS.length];
  };
  const fitted = (data || []).some(trace => !trace.meta && trace.name === 'Calculated');
  const traces = (data || []).filter(trace => !SCREEN_ONLY.has(trace.meta) && trace.visible !== false).map(trace => {
    const name = String(trace.name || '');
    if (trace.meta === 'difference') return { ...trace, name: 'Difference', line: { color: DIFFERENCE, width: 0.8 } };
    if (trace.meta === 'reflection') {
      const color = phaseColor(trace.legendgroup || name);
      return { ...trace, marker: { ...trace.marker, color, size: 7, line: { color, width: 1 } } };
    }
    if (trace.meta === 'reference-sticks') return { ...trace, line: { color: phaseColor(trace.legendgroup || name), width: 1 } };
    if (trace.meta === 'phase-profile') {
      const color = phaseColor(trace.legendgroup || name);
      return { ...trace, fillcolor: 'rgba(0,0,0,0)', line: { ...trace.line, color, width: trace.line?.width === 0 ? 0 : 0.8 } };
    }
    if (trace.meta) return trace; // e.g. the d-axis carrier
    if (name === 'Calculated') return { ...trace, line: { color: CALCULATED, width: 1 } };
    if (name === 'Background') return { ...trace, opacity: 1, line: { color: BACKGROUND, width: 0.8 } };
    if (name.startsWith('Observed') || name.startsWith('Measured')) {
      return fitted
        ? { ...trace, mode: 'markers', marker: { symbol: 'circle-open', size: 3, color: BLACK, line: { color: BLACK, width: 0.5 } } }
        : { ...trace, mode: 'lines', line: { color: BLACK, width: 0.8 } };
    }
    return trace;
  });
  const width = Math.round(widthMm * PX_PER_MM);
  const height = Math.round(width * HEIGHT_RATIO);
  const counts = layout?.meta?.counts;
  const radiation = radiationLabel(layout?.meta);
  const twoTheta = radiation ? `2θ (°, ${radiation})` : '2θ (°)';
  const lower = layout?.xaxis2 ? 'xaxis2' : 'xaxis';
  const { title, template, ...rest } = layout || {};
  const rowNames = (layout?.annotations || []).filter(item => item.xref === 'paper').map(item => String(item.text || '').replace(/<[^>]*>/g, ''));
  const shown = {
    ...rest,
    width, height, autosize: false,
    font: font(8), paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff',
    // Room left of the axis for the Bragg-row names (7 pt, about 0.55 em per character).
    margin: { l: Math.max(58, ...rowNames.map(text => Math.ceil(16 + 0.55 * 7 * PT * text.length))), r: 10, t: layout?.xaxis3 ? 40 : 10, b: 40, pad: 0 },
    showlegend: true,
    legend: { x: 0.99, xanchor: 'right', y: 0.99, yanchor: 'top', xref: 'paper', yref: 'paper', orientation: 'v',
      bgcolor: 'rgba(0,0,0,0)', borderwidth: 0, font: font(7), tracegroupgap: 0 },
    xaxis: frameAxis(layout?.xaxis, lower === 'xaxis' ? twoTheta : ''),
    yaxis: frameAxis(layout?.yaxis, counts === false ? 'Intensity (a.u.)' : 'Intensity (counts)'),
    // Bragg-row names and hkl labels at 7 pt; the hkl labels in black, the row names in their phase colour.
    annotations: (layout?.annotations || []).map(item => ({ ...item, font: font(7, item.textangle === -90 ? BLACK : item.font?.color || BLACK) })),
    // Excluded ranges stay; peak and selection marks are screen-only.
    shapes: (layout?.shapes || []).filter(shape => shape.type === 'rect' && shape.fillcolor),
  };
  if (layout?.xaxis2) shown.xaxis2 = frameAxis(layout.xaxis2, twoTheta);
  if (layout?.yaxis2) {
    // The difference panel sits right under the pattern (the screen leaves room for the 2θ tick labels between).
    shown.yaxis = { ...shown.yaxis, domain: [0.27, 1] };
    shown.xaxis = { ...shown.xaxis, showticklabels: false };
    shown.yaxis2 = { ...frameAxis(layout.yaxis2, 'Difference'), domain: [0, 0.22] };
  }
  if (layout?.xaxis3) shown.xaxis3 = { ...frameAxis(layout.xaxis3, 'd (Å)'), mirror: false, ticks: 'inside' };
  // Bragg-row names take the phase colour of the exported rows.
  shown.annotations = shown.annotations.map(item => {
    if (item.xref !== 'paper') return item;
    const row = traces.find(trace => trace.meta === 'reflection' && String(trace.name || '').startsWith(item.text));
    return row ? { ...item, font: font(7, row.marker.color) } : item;
  });
  return { data: traces, layout: shown, width, height };
}

/** Plotly.toImage options of a figure export: SVG as vector, PNG at 600 dpi. */
export function exportOptions(figure, format, filename) {
  return { format, width: figure.width, height: figure.height, filename, scale: format === 'png' ? 600 / 96 : 1 };
}

/**
 * Look of a saved phase-identification figure (the server's `peaks`, `names`, `bars`) with its file
 * (`widthMm`, `format`); the defaults are the report figure's.
 */
export const FIGURE_STYLE_DEFAULT = { peaks: 'hkl', names: 'legend', bars: true, widthMm: 170, format: 'png' };
const FIGURE_STYLE_CHOICES = {
  peaks: ['hkl', 'symbols', 'none'], names: ['legend', 'strips'], bars: [true, false], widthMm: [170, 85], format: ['png', 'svg', 'pdf'],
};
export const FIGURE_STYLE_KEY = 'dara-figure-style';

/** `value` with every unknown or missing choice replaced by its default. */
export function figureStyle(value) {
  const style = { ...FIGURE_STYLE_DEFAULT };
  Object.keys(FIGURE_STYLE_CHOICES).forEach(key => {
    if (FIGURE_STYLE_CHOICES[key].includes(value?.[key])) style[key] = value[key];
  });
  return style;
}

/** The last choice of this browser (`storage`: localStorage, or null when blocked). */
export function readFigureStyle(storage) {
  try {
    return figureStyle(JSON.parse(storage?.getItem(FIGURE_STYLE_KEY) || 'null'));
  } catch (e) {
    return { ...FIGURE_STYLE_DEFAULT };
  }
}

export function writeFigureStyle(storage, style) {
  try {
    storage?.setItem(FIGURE_STYLE_KEY, JSON.stringify(figureStyle(style)));
  } catch (e) { /* private mode: the choice is not remembered */ }
}

/** `[name, value]` query pairs of the look, only where it differs from the server default. */
export function figureStylePairs(style) {
  const { peaks, names, bars } = figureStyle(style);
  return [...(peaks !== 'hkl' ? [['peaks', peaks]] : []), ...(names !== 'legend' ? [['names', names]] : []), ...(bars ? [] : [['bars', '0']])];
}
