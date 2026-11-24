// User-defined background curve: anchor points joined by PCHIP (scipy's PchipInterpolator),
// constant beyond the end anchors and clipped at zero, exactly as dara/background_curve.py
// writes it for BGMN (tests/test_data/pchip_fixture.json keeps the two in step).

export const MIN_ANCHORS = 2;
export const MAX_ANCHORS = 40;
export const SEED_MAX = 20;
export const HIT_PX = 8;

const sign = value => (value > 0 ? 1 : value < 0 ? -1 : 0);

// scipy.interpolate.PchipInterpolator._edge_case
function edgeSlope(h0, h1, m0, m1) {
  let d = ((2 * h0 + h1) * m0 - h0 * m1) / (h0 + h1);
  if (sign(d) !== sign(m0)) d = 0;
  else if (sign(m0) !== sign(m1) && Math.abs(d) > Math.abs(3 * m0)) d = 3 * m0;
  return d;
}

// scipy.interpolate.PchipInterpolator._find_derivatives
function slopes(xs, ys) {
  const n = xs.length;
  const h = [];
  const m = [];
  for (let k = 0; k < n - 1; k += 1) {
    h.push(xs[k + 1] - xs[k]);
    m.push((ys[k + 1] - ys[k]) / h[k]);
  }
  if (n === 2) return [m[0], m[0]];
  const d = new Array(n).fill(0);
  for (let k = 1; k < n - 1; k += 1) {
    const [a, b] = [m[k - 1], m[k]];
    if (sign(a) !== sign(b) || a === 0 || b === 0) continue;
    const w1 = 2 * h[k] + h[k - 1];
    const w2 = h[k] + 2 * h[k - 1];
    d[k] = 1 / ((w1 / a + w2 / b) / (w1 + w2));
  }
  d[0] = edgeSlope(h[0], h[1], m[0], m[1]);
  d[n - 1] = edgeSlope(h[n - 2], h[n - 3], m[n - 2], m[n - 3]);
  return d;
}

/** Curve through `anchors` ([[2θ, y], ...], sorted) at every x of `xs`. */
export function pchip(anchors, xs) {
  const ax = anchors.map(point => Number(point[0]));
  const ay = anchors.map(point => Number(point[1]));
  const n = ax.length;
  const out = new Array(xs.length);
  if (n < 2) return out.fill(n ? Math.max(ay[0], 0) : 0);
  const d = slopes(ax, ay);
  let k = 0;
  for (let i = 0; i < xs.length; i += 1) {
    const x = Math.min(Math.max(Number(xs[i]), ax[0]), ax[n - 1]);
    // xs is usually sorted: walk forward, and restart only when it goes back.
    if (x < ax[k]) k = 0;
    while (k < n - 2 && x > ax[k + 1]) k += 1;
    const h = ax[k + 1] - ax[k];
    const t = (x - ax[k]) / h;
    const t2 = t * t;
    const t3 = t2 * t;
    const y = (2 * t3 - 3 * t2 + 1) * ay[k] + (t3 - 2 * t2 + t) * h * d[k]
      + (-2 * t3 + 3 * t2) * ay[k + 1] + (t3 - t2) * h * d[k + 1];
    out[i] = Math.max(y, 0);
  }
  return out;
}

const inside = (x, ranges) => (ranges || []).some(([lo, hi]) => lo < x && x < hi);

/** Error text for anchors the server would reject (dara.background_curve.validate_anchors), or null. */
export function anchorError(anchors, { lo = -Infinity, hi = Infinity, excluded = [] } = {}) {
  if (!Array.isArray(anchors) || anchors.length < MIN_ANCHORS || anchors.length > MAX_ANCHORS) {
    return `Use ${MIN_ANCHORS} to ${MAX_ANCHORS} background anchor points.`;
  }
  for (let i = 0; i < anchors.length; i += 1) {
    const [x, y] = anchors[i].map(Number);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return 'Background anchors must be finite numbers.';
    if (y < 0) return `Background anchor at ${x}° has a negative intensity.`;
    if (x < lo - 1e-3 || x > hi + 1e-3) return `Background anchor at ${x}° is outside the refined range ${lo}–${hi}°.`;
    const range = (excluded || []).find(([a, b]) => a < x && x < b);
    if (range) return `Background anchor at ${x}° is inside the excluded range ${range[0]}–${range[1]}°.`;
    if (i && x <= Number(anchors[i - 1][0])) return 'Background anchors must be sorted by 2θ without repeats.';
  }
  return null;
}

/** Indices of the points that are fitted: inside [lo, hi] and outside the excluded ranges. */
function fittedIndices(x, lo, hi, excluded) {
  const out = [];
  for (let i = 0; i < x.length; i += 1) {
    if (x[i] >= lo && x[i] <= hi && !inside(x[i], excluded)) out.push(i);
  }
  return out;
}

const round = (value, digits) => Number(Number(value).toFixed(digits));

/**
 * Starting anchors, y = refined background: both range ends, then (greedy) the off-peak fitted
 * point where the curve misses the refined background most, in σ = √background, until the curve
 * is within 1σ everywhere off-peak or `maxAnchors` are placed. Off-peak: phase intensity
 * (calculated − background) ≤ 3√observed; all fitted points when none is.
 */
export function seedAnchors({ x, yObs, yCalc, yBkg, lo = -Infinity, hi = Infinity, excluded = [], maxAnchors = SEED_MAX }) {
  const idx = fittedIndices(x, lo, hi, excluded);
  if (idx.length < 2) return [];
  const offPeak = idx.filter(i => yCalc[i] - yBkg[i] <= 3 * Math.sqrt(Math.max(yObs[i], 1)));
  const candidates = offPeak.length ? offPeak : idx;
  const xs = candidates.map(i => x[i]);
  const point = i => [round(x[i], 4), round(Math.max(yBkg[i], 0), 2)];
  const chosen = [idx[0], idx[idx.length - 1]];
  while (chosen.length < maxAnchors) {
    const curve = pchip(chosen.map(point), xs);
    let worst = 1;
    let next = -1;
    candidates.forEach((i, k) => {
      const miss = Math.abs(curve[k] - yBkg[i]) / Math.sqrt(Math.max(yBkg[i], 1));
      if (miss > worst && !chosen.includes(i)) { worst = miss; next = i; }
    });
    if (next < 0) break;
    chosen.push(next);
    chosen.sort((a, b) => a - b);
  }
  const anchors = chosen.map(point);
  return anchors.filter((item, i) => !i || item[0] > anchors[i - 1][0]);
}

/** `anchors` with `[x, y]` inserted in 2θ order (unchanged when that 2θ is taken or the list is full). */
export function addAnchor(anchors, point) {
  if (anchors.length >= MAX_ANCHORS || anchors.some(([x]) => Math.abs(x - point[0]) < 1e-9)) return anchors;
  return [...anchors, point].sort((a, b) => a[0] - b[0]);
}

/** `anchors` without anchor `index` (at least two stay). */
export function removeAnchor(anchors, index) {
  return anchors.length <= MIN_ANCHORS ? anchors : anchors.filter((_, i) => i !== index);
}

export const anchorLabel = anchors => `Background: manual (${anchors.length} points)`;

function movingAverage(values, half) {
  const out = new Array(values.length);
  let sum = 0;
  let count = 0;
  let lo = 0;
  let hi = -1;
  for (let i = 0; i < values.length; i += 1) {
    while (hi < Math.min(values.length - 1, i + half)) { hi += 1; sum += values[hi]; count += 1; }
    while (lo < i - half) { sum -= values[lo]; lo += 1; count -= 1; }
    out[i] = sum / count;
  }
  return out;
}

/** Up to three "a°, b°, c°" positions where `flags` starts a run of true values. */
function runStarts(x, idx, flags) {
  const starts = [];
  idx.forEach((i, k) => { if (flags[k] && (k === 0 || !flags[k - 1] || idx[k - 1] !== i - 1)) starts.push(x[i]); });
  const shown = starts.slice(0, 3).map(value => `${value.toFixed(1)}°`).join(', ');
  return starts.length > 3 ? `${shown} …` : shown;
}

/**
 * Warnings (never blocks) for a curve being edited: the curve more than 3σ above the smoothed
 * data (σ = √counts), and anchors on a peak (phase intensity of the current fit above 3σ).
 */
export function curveWarnings({ x, yObs, yCalc, yBkg, anchors, lo = -Infinity, hi = Infinity, excluded = [], smoothHalf = 7 }) {
  const warnings = [];
  if (!anchors?.length || !x?.length) return warnings;
  const idx = fittedIndices(x, lo, hi, excluded);
  const smooth = movingAverage(idx.map(i => yObs[i]), smoothHalf);
  const curve = pchip(anchors, idx.map(i => x[i]));
  // measured from the refined background where that is higher: an unedited seed never warns
  const above = idx.map((i, k) => curve[k] - Math.max(smooth[k], yBkg[i]) > 3 * Math.sqrt(Math.max(smooth[k], 1)));
  if (above.some(Boolean)) warnings.push(`Background above data at ${runStarts(x, idx, above)}`);
  const onPeak = [];
  anchors.forEach(([ax]) => {
    let best = -1;
    idx.forEach(i => { if (best < 0 || Math.abs(x[i] - ax) < Math.abs(x[best] - ax)) best = i; });
    if (best < 0) return;
    const phase = yCalc[best] - yBkg[best];
    if (phase > 3 * Math.sqrt(Math.max(yObs[best], 1))) onPeak.push(`${ax.toFixed(2)}°`);
  });
  if (onPeak.length) warnings.push(`Anchor${onPeak.length > 1 ? 's' : ''} on a peak at ${onPeak.join(', ')}`);
  return warnings;
}

/** Plain number array of a Plotly trace array (plain, typed or `{bdata, dtype}` encoded). */
export function traceNumbers(values) {
  if (Array.isArray(values) || ArrayBuffer.isView(values)) return Array.from(values, Number);
  if (!values?.bdata) return [];
  const bytes = Uint8Array.from(atob(values.bdata), c => c.charCodeAt(0));
  const type = { f8: Float64Array, f4: Float32Array, i4: Int32Array, i2: Int16Array,
    i1: Int8Array, u4: Uint32Array, u2: Uint16Array, u1: Uint8Array }[values.dtype];
  return type ? Array.from(new type(bytes.buffer)) : [];
}

/** `{x, yObs, yCalc, yBkg}` of a fit plot (the server's Observed / Calculated / Background traces). */
export function fitSeries(plot) {
  const trace = name => (plot?.data || []).find(item => item.name === name && !item.meta);
  const [obs, calc, bkg] = ['Observed', 'Calculated', 'Background'].map(trace);
  if (!obs || !calc || !bkg) return null;
  return { x: traceNumbers(obs.x), yObs: traceNumbers(obs.y), yCalc: traceNumbers(calc.y), yBkg: traceNumbers(bkg.y) };
}

/**
 * Why an excluded 2θ interval `[lo, hi]` cannot be used, or `null`: both ends needed, `lo < hi`,
 * and some overlap with the measured `range` ([min, max] of 2θ; not checked when unknown).
 */
export function intervalProblem([lo, hi] = [], range = null) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return 'enter both ends';
  if (!(lo < hi)) return '"from" must be below "to"';
  const [min, max] = Array.isArray(range) ? range.map(Number) : [];
  if (Number.isFinite(min) && Number.isFinite(max) && (hi <= min || lo >= max)) {
    return `outside the measured range (${min.toFixed(1)}–${max.toFixed(1)}°)`;
  }
  return null;
}
