/** Plot-only transformations. Measured arrays are never modified. */
export function parseComparisonTaskIds(value) {
  return [...new Set(String(value || '').split(',').map(item => item.trim())
    .filter(item => /^\d+$/.test(item)).map(Number)
    .filter(id => Number.isSafeInteger(id) && id > 0))].slice(0, 20);
}

/** `sqrt`: plot √I (signed), which brings weak peaks of minor phases into view next to strong ones. */
export function comparisonTraces(patterns, { normalize = true, offset = 110, sqrt = false } = {}) {
  return patterns.map((pattern, index) => {
    const shown = shownIntensities(pattern.intensities, { normalize, sqrt });
    const displacement = index * offset;
    const wavelength = Number(pattern.wavelength_angstrom);
    const withD = wavelength > 0;
    return {
      type: 'scatter',
      mode: 'lines',
      name: `#${pattern.task_id} ${pattern.name.length > 65 ? `${pattern.name.slice(0, 62)}…` : pattern.name}`,
      meta: `#${pattern.task_id} ${pattern.name}`,
      x: pattern.angles,
      y: shown.map(value => value + displacement),
      // Each sample uses its own wavelength for d (Bragg's law).
      customdata: withD
        ? pattern.intensities.map((value, i) => [value, wavelength / (2 * Math.sin((pattern.angles[i] * Math.PI) / 360))])
        : pattern.intensities,
      line: { width: 1.25 },
      hovertemplate: withD
        ? '2θ %{x:.4f}°<br>d %{customdata[1]:.4f} Å<br>Measured intensity %{customdata[0]:.4g}<extra>%{meta}</extra>'
        : '2θ %{x:.4f}°<br>Measured intensity %{customdata:.4g}<extra>%{meta}</extra>',
    };
  });
}

export function measuredAngularBounds(patterns) {
  let lower = Infinity;
  let upper = -Infinity;
  for (const pattern of patterns) {
    for (const angle of pattern.angles) {
      if (Number.isFinite(angle)) {
        lower = Math.min(lower, angle);
        upper = Math.max(upper, angle);
      }
    }
  }
  return lower < upper ? [lower, upper] : undefined;
}

/** Intensities as plotted: √I (signed) when `sqrt`, then scaled to max |I| = 100 when `normalize`. */
export function shownIntensities(intensities, { normalize = true, sqrt = false } = {}) {
  const shown = sqrt ? intensities.map(value => Math.sign(value) * Math.sqrt(Math.abs(value))) : intensities;
  const peak = shown.reduce((maximum, value) => (Number.isFinite(value) ? Math.max(maximum, Math.abs(value)) : maximum), 0);
  const scale = normalize && peak > 0 ? 100 / peak : 1;
  return shown.map(value => value * scale);
}

const median = values => {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

/** Median 2θ step of a pattern (its nominal step size), or null. */
export function angularStep(angles) {
  const steps = [];
  for (let i = 1; i < angles.length; i += 1) {
    const step = angles[i] - angles[i - 1];
    if (Number.isFinite(step) && step > 0) steps.push(step);
  }
  return steps.length ? median(steps) : null;
}

/** Most grid points of a common grid; a finer step is coarsened to stay under it. */
export const MAX_GRID_POINTS = 20000;

/**
 * Common 2θ grid of `patterns`: the overlap of their ranges at the finest step among them.
 * `{x, step, range: [lo, hi], differs}` (`differs`: the measured ranges are not all the same)
 * or null when the ranges do not overlap.
 */
export function commonGrid(patterns, { maxPoints = MAX_GRID_POINTS } = {}) {
  const spans = patterns.map(pattern => measuredAngularBounds([pattern])).filter(Boolean);
  const steps = patterns.map(pattern => angularStep(pattern.angles)).filter(step => step !== null);
  if (!spans.length || spans.length !== patterns.length || !steps.length) return null;
  const lo = Math.max(...spans.map(span => span[0]));
  const hi = Math.min(...spans.map(span => span[1]));
  if (!(hi > lo)) return null;
  let step = Math.min(...steps);
  if ((hi - lo) / step + 1 > maxPoints) step = (hi - lo) / (maxPoints - 1);
  const count = Math.floor((hi - lo) / step + 1e-9) + 1;
  // Rounded to 1e-6° so that grid points read as the measured angles they stand for.
  const x = Array.from({ length: count }, (_, i) => Math.round((lo + i * step) * 1e6) / 1e6);
  const tolerance = step / 2;
  const differs = spans.some(span => Math.abs(span[0] - spans[0][0]) > tolerance || Math.abs(span[1] - spans[0][1]) > tolerance);
  return { x, step, range: [lo, hi], differs };
}

/** Linear interpolation of (`xs`, `ys`) at `grid` (any order of xs); null outside the measured range. */
export function interpolate(xs, ys, grid) {
  const points = xs.map((x, i) => [x, ys[i]]).filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
    .sort((a, b) => a[0] - b[0]);
  const out = new Array(grid.length).fill(null);
  if (!points.length) return out;
  let j = 0;
  grid.forEach((x, i) => {
    if (!(x >= points[0][0] && x <= points[points.length - 1][0])) return;
    while (j < points.length - 2 && points[j + 1][0] < x) j += 1;
    const [x0, y0] = points[j];
    const [x1, y1] = points[Math.min(j + 1, points.length - 1)];
    out[i] = x1 === x0 ? y0 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
  });
  return out;
}

/** Numbers of a `values` query (`25,50,75`) when there is one finite number per pattern, else null. */
export function parseSeriesValues(value, count) {
  const parts = String(value || '').split(',').map(part => part.trim());
  if (!value || parts.length !== count) return null;
  const numbers = parts.map(part => (part === '' ? NaN : Number(part)));
  return numbers.every(Number.isFinite) ? numbers : null;
}

/**
 * Heat map (2θ map) of `patterns` on their common grid: `{x, y, z, labels, numeric, grid}` with one
 * row per pattern in list order, or in the order of `values` (a series variable) when given.
 * Intensities as plotted (`shownIntensities`); null outside a pattern's range. null without overlap.
 */
export function mapMatrix(patterns, { normalize = true, sqrt = false, values = null } = {}) {
  const grid = commonGrid(patterns);
  if (!grid) return null;
  const rows = patterns.map((pattern, index) => ({
    label: `#${pattern.task_id} ${pattern.name}`,
    value: values ? values[index] : index,
    z: interpolate(pattern.angles, shownIntensities(pattern.intensities, { normalize, sqrt }), grid.x),
  }));
  // A heat map needs its rows in axis order; a numeric variable may come in any order.
  if (values) rows.sort((a, b) => a.value - b.value);
  return { x: grid.x, y: rows.map(row => row.value), z: rows.map(row => row.z), labels: rows.map(row => row.label),
    numeric: Boolean(values), grid };
}

/** Least-squares factor k minimising Σ(a − k·b)² over the points where both are finite (1 when undefined). */
export function leastSquaresScale(a, b) {
  let ab = 0;
  let bb = 0;
  a.forEach((value, i) => {
    if (Number.isFinite(value) && Number.isFinite(b[i])) { ab += value * b[i]; bb += b[i] * b[i]; }
  });
  return bb > 0 ? ab / bb : 1;
}

/**
 * A − k·B on A's grid within the overlap of the two (B interpolated onto A's angles).
 * `scale`: a number, or 'auto' for the least-squares k. Returns `{x, a, b, difference, k}` (b is k·B).
 */
export function differenceCurves(patternA, patternB, scale = 1) {
  const bOnA = interpolate(patternB.angles, patternB.intensities, patternA.angles);
  const keep = patternA.angles.map((x, i) => Number.isFinite(x) && Number.isFinite(patternA.intensities[i]) && bOnA[i] !== null);
  const pick = values => values.filter((_, i) => keep[i]);
  const x = pick(patternA.angles);
  const a = pick(patternA.intensities);
  const raw = pick(bOnA);
  const k = scale === 'auto' ? leastSquaresScale(a, raw) : (Number.isFinite(scale) ? scale : 1);
  const b = raw.map(value => k * value);
  return { x, a, b, difference: a.map((value, i) => value - b[i]), k };
}

/** CSV of `differenceCurves` output with a comment line naming A, B and k. */
export function differenceCsv(curves, nameA, nameB) {
  const k = Number(curves.k.toPrecision(6));
  const lines = [`# A = ${nameA}; B = ${nameB}; k = ${k}; B interpolated linearly onto the 2theta grid of A`.replace(/\n/g, ' '),
    'two_theta_deg,A,k*B,A_minus_kB'];
  curves.x.forEach((x, i) => lines.push([x, curves.a[i], curves.b[i], curves.difference[i]].map(value => Number(value.toPrecision(10))).join(',')));
  return `${lines.join('\n')}\n`;
}
