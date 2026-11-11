/** Compartment fills of a candidate composition bar; text on each stays ink (>= 10:1). */
export const TRAY_FILLS = ['#d3e3d6', '#d4e1ec', '#efe0bd', '#ecd2c7', '#dfdbe9', '#d9e6e4', '#e8e4d8', '#e3d5dd'];
/** Line colour of each fill (same hue, dark enough for plot curves and Bragg ticks). */
export const TRAY_LINES = ['#3d7a4e', '#3f6c8f', '#a8770f', '#9c5b3f', '#6b5b9a', '#2f7d78', '#7d7356', '#9a4f72'];

/** Plot line colour of a tray fill, or null for an unknown fill. */
export const trayLine = fill => TRAY_LINES[TRAY_FILLS.indexOf(fill)] ?? null;

/** Plot traces with each phase's profile and Bragg ticks in its tray colour (`colorOf(phase)` or null keeps the server's). */
export function colorPhaseTraces(data, colorOf) {
  if (!Array.isArray(data) || !colorOf) return data;
  return data.map(trace => {
    if (trace?.meta !== 'phase-profile' && trace?.meta !== 'reflection') return trace;
    const color = colorOf(trace.legendgroup);
    if (!color) return trace;
    return trace.meta === 'reflection'
      ? { ...trace, marker: { ...trace.marker, color } }
      : { ...trace, line: { ...trace.line, color } };
  });
}

/**
 * Compartments of one candidate: `[{phase, wt, share}]` in the given phase order, where `share`
 * (0–100) sums to exactly 100. Missing or non-positive wt% values split what is left equally,
 * so a bar always fills its tray; `wt` stays null for them.
 */
export function compositionSegments(phases = [], weights = {}) {
  if (!phases.length) return [];
  const raw = phases.map(phase => {
    const value = Number(weights?.[phase]);
    return Number.isFinite(value) && value > 0 ? value : null;
  });
  const known = raw.filter(value => value !== null);
  const total = known.reduce((sum, value) => sum + value, 0);
  const missing = raw.length - known.length;
  // Unknown amounts get an equal share of the rest (all of it when nothing is known).
  const rest = total > 0 ? (missing ? Math.max(0, 100 - total) / missing : 0) : 100 / raw.length;
  const sizes = raw.map(value => (value === null ? rest : value));
  const sum = sizes.reduce((a, b) => a + b, 0) || 1;
  return phases.map((phase, index) => ({ phase, wt: raw[index], share: (100 * sizes[index]) / sum }));
}

/** One fill per distinct key, in first-seen order, so a phase keeps its colour in every tray. */
export function fillIndex(keys) {
  const map = new Map();
  keys.forEach(key => { if (!map.has(key)) map.set(key, TRAY_FILLS[map.size % TRAY_FILLS.length]); });
  return map;
}

const gcd = (a, b) => (b ? gcd(b, a % b) : a);

/**
 * Display form of a formula with fractional counts from a disordered CIF: near-integer counts are
 * rounded and reduced (Y31.99952O47.99904 -> Y2O3), other fractions keep at most 3 decimals
 * (Fe7.7841O12 -> Fe7.784O12), La(HO)3 -> La(OH)3. Anything else that is not a plain element-count
 * formula is returned as is.
 */
export function tidyFormula(text) {
  // pymatgen writes hydroxides as (HO); chemists read (OH).
  const value = String(text ?? '').replace(/\(HO\)/g, '(OH)');
  if (!/^(?:[A-Z][a-z]?\d*(?:\.\d+)?)+$/.test(value) || !/\.\d/.test(value)) return value;
  const pairs = [...value.matchAll(/([A-Z][a-z]?)(\d*(?:\.\d+)?)/g)].map(([, el, n]) => [el, n === '' ? 1 : Number(n)]);
  if (pairs.every(([, n]) => Math.abs(n - Math.round(n)) < 0.01 && Math.round(n) > 0)) {
    const ints = pairs.map(([, n]) => Math.round(n));
    const divisor = ints.reduce(gcd);
    return pairs.map(([el], i) => `${el}${ints[i] / divisor === 1 ? '' : ints[i] / divisor}`).join('');
  }
  return pairs.map(([el, n]) => `${el}${n === 1 ? '' : Number(n.toFixed(3))}`).join('');
}
