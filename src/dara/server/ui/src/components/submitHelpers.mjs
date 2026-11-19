/**
 * Run UI follow-up work after the server has confirmed a change (e.g. a submission).
 *
 * The server-side change is already done, so an exception in a follow-up step (form reset, state
 * updates, notifications) must never be reported to the user as a failure of the operation itself.
 * Every step runs even when an earlier one throws; failures are logged and returned.
 */
export function runAfterSuccess(steps, log = console.error) {
  const errors = [];
  for (const step of steps) {
    try {
      step();
    } catch (error) {
      errors.push(error);
      try { log('Post-success UI step failed (the operation itself succeeded):', error); } catch (_) { /* ignore */ }
    }
  }
  return errors;
}

// --- settings carried over from the last submission (per browser) ---------------------------
export const SUBMIT_DEFAULTS_KEY = 'dara.submitDefaults.v1';

/** Submit-form fields that stay the same between runs. Sample-specific inputs (pattern, elements,
 * precursors, 2θ range, excluded intervals, CIFs) are not carried over. */
export const REMEMBERED_FIELDS = [
  'user', 'analysis_mode', 'temperature', 'atmosphere', 'use_cod', 'use_mp', 'mp_e_hull_cutoff',
  'max_mp_structures', 'candidate_prefilter', 'screening_timeout_s', 'max_analysis_minutes',
  'instrument_profile', 'synchrotron', 'wavelength_lab', 'wavelength_sync_a',
];

export function pickRemembered(values, controls = null) {
  const fields = {};
  for (const key of REMEMBERED_FIELDS) {
    if (values && values[key] !== undefined) fields[key] = values[key];
  }
  return { fields, controls: controls && typeof controls === 'object' ? controls : null };
}

/** Saved settings, or ``null`` when there are none or storage is unavailable or corrupt. */
export function readRemembered(storage) {
  try {
    const parsed = JSON.parse(storage?.getItem(SUBMIT_DEFAULTS_KEY) || 'null');
    if (!parsed || typeof parsed !== 'object' || !parsed.fields || typeof parsed.fields !== 'object') return null;
    return pickRemembered(parsed.fields, parsed.controls);
  } catch (_) {
    return null;
  }
}

export function writeRemembered(storage, values, controls = null) {
  try {
    storage?.setItem(SUBMIT_DEFAULTS_KEY, JSON.stringify(pickRemembered(values, controls)));
    return true;
  } catch (_) {
    return false;
  }
}

export function forgetRemembered(storage) {
  try { storage?.removeItem(SUBMIT_DEFAULTS_KEY); } catch (_) { /* storage unavailable */ }
}

// --- advanced submit options ----------------------------------------------------------------
/** Defaults of the options shown in the collapsible "Advanced" card of the submit form. */
export const ADVANCED_DEFAULTS = {
  use_cod: true, use_mp: true, mp_e_hull_cutoff: 0.05, max_mp_structures: 100,
  candidate_prefilter: true, screening_timeout_s: 300, max_analysis_minutes: null,
};

const ADVANCED_LABELS = {
  use_cod: 'COD', use_mp: 'MP', mp_e_hull_cutoff: 'E-hull cutoff', max_mp_structures: 'MP limit',
  candidate_prefilter: 'pre-filter', screening_timeout_s: 'screening limit', max_analysis_minutes: 'time limit',
};

const sameControls = (a, b) => Object.keys({ ...a, ...b }).every(key => a?.[key] === b?.[key]);

/** Labels of the advanced options that differ from their defaults. Fields not yet in the form
 * (``undefined``) count as default; a blank screening limit (``null``) is a change. */
export function advancedChanges(values, controls = null, defaultControls = null) {
  const v = values || {};
  const changed = [];
  for (const [key, fallback] of Object.entries(ADVANCED_DEFAULTS)) {
    const value = v[key];
    if (value === undefined) continue;
    if ((value ?? null) !== fallback) changed.push(ADVANCED_LABELS[key]);
  }
  if (controls && defaultControls && !sameControls(controls, defaultControls)) changed.push('refinement controls');
  if (Array.isArray(v.library_cif_ids) && v.library_cif_ids.length) changed.push('library CIFs');
  if (Array.isArray(v.additional_phases) && v.additional_phases.length) changed.push('additional CIFs');
  if (v.reuse_additional_cifs === false) changed.push('saved CIFs not reused');
  return changed;
}

// --- element entry --------------------------------------------------------------------------
export const ELEMENT_SYMBOLS = ('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se '
  + 'Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W '
  + 'Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh '
  + 'Fl Mc Lv Ts Og').split(' ');
const SYMBOLS = new Set(ELEMENT_SYMBOLS);

/** Symbol of one typed entry: as typed, or case-normalised when that is the only reading ("fe" → Fe, "ZN" → Zn;
 * "CO" stays unresolved: Co, or C and O). */
function symbolOf(raw) {
  if (SYMBOLS.has(raw)) return raw;
  if (!/^[A-Za-z]{1,2}$/.test(raw)) return null;
  const symbol = raw[0].toUpperCase() + raw.slice(1).toLowerCase();
  if (!SYMBOLS.has(symbol)) return null;
  if (raw.length === 2 && raw === raw.toUpperCase() && SYMBOLS.has(raw[0]) && SYMBOLS.has(raw[1])) return null;
  return symbol;
}

/**
 * An Elements entry ("Ti zn,O Xx"): `symbols` (distinct, typed order), `invalid` entries, `error` (a field message
 * naming them, or null) and the normalised `text` ("Ti, Zn, O, Xx"; invalid entries stay visible).
 */
export function checkElements(value) {
  const symbols = [];
  const invalid = [];
  for (const raw of String(value || '').split(/[\s,;]+/).filter(Boolean)) {
    const symbol = symbolOf(raw);
    const list = symbol ? symbols : invalid;
    if (!list.includes(symbol || raw)) list.push(symbol || raw);
  }
  const unresolved = invalid.filter(raw => /^[A-Z]{2}$/.test(raw) && symbolOf(raw[0] + raw[1].toLowerCase()));
  const error = invalid.length
    ? `Not ${invalid.length === 1 ? 'an element symbol' : 'element symbols'}: ${invalid.join(', ')}${
      unresolved.length ? ` (${unresolved.map(raw => `${raw[0]}${raw[1].toLowerCase()}, or ${raw[0]} and ${raw[1]}`).join('; ')}?)` : ''}`
    : null;
  return { symbols, invalid, error, text: [...symbols, ...invalid].join(', ') };
}

// --- recent element sets (per browser) ------------------------------------------------------
export const RECENT_ELEMENTS_KEY = 'dara.recentElements.v1';
const MAX_RECENT = 5;
const setKey = symbols => [...symbols].sort().join(',');

/** The last element sets used in this browser, most recent first ([] when unavailable or corrupt). */
export function readRecentElements(storage) {
  try {
    const list = JSON.parse(storage?.getItem(RECENT_ELEMENTS_KEY) || '[]');
    return Array.isArray(list)
      ? list.filter(item => Array.isArray(item) && item.length && item.every(symbol => SYMBOLS.has(symbol))).slice(0, MAX_RECENT)
      : [];
  } catch (_) {
    return [];
  }
}

/** Puts `symbols` first among the recent sets (an equal set moves up); returns the new list. */
export function rememberElements(storage, symbols) {
  const current = readRecentElements(storage);
  if (!symbols?.length || !symbols.every(symbol => SYMBOLS.has(symbol))) return current;
  const list = [[...symbols], ...current.filter(item => setKey(item) !== setKey(symbols))].slice(0, MAX_RECENT);
  try { storage?.setItem(RECENT_ELEMENTS_KEY, JSON.stringify(list)); } catch (_) { /* storage unavailable */ }
  return list;
}

// --- file name chemistry --------------------------------------------------------------------
// Measurement conditions and counters that never name a phase: 800C, 60min, 2h, 5K, 10x...
const CONDITION = /^\d+(?:\.\d+)?(?:°?c|k|min|mins|h|hr|hrs|s|sec|d|day|days|rpm|bar|mbar|atm|pa|mm|um|nm|mg|g|ml|wt|at|pct|x)?$/i;

/**
 * Tokens of the file name that the server's file-name chemistry did not turn into a formula
 * ("SnO2_Pt_cat.xrdml" → ["Pt", "cat"]), split as `dara.filename_chemistry` splits them.
 */
export function unrecognisedTokens(filename, chemistry) {
  const stem = String(filename || '').replace(/[\\]/g, '/').split('/').pop().replace(/\.[^.]*$/, '');
  const formulas = new Set(chemistry?.formulas || []);
  const tokens = [];
  for (const token of stem.split(/[\s_+,;-]+/)) {
    const bare = token.replace(/^\d+(?:\.\d+)?(?=[A-Z])/, '');
    if (!bare || !/[A-Za-z]/.test(bare) || formulas.has(bare) || CONDITION.test(token) || tokens.includes(bare)) continue;
    tokens.push(bare);
  }
  return tokens;
}

// --- pattern preview ------------------------------------------------------------------------
/**
 * SVG geometry of a small pattern preview in a `width` × `height` box: the line `d` and the shaded
 * 2θ intervals (outside `start`–`end`, and `excluded` [[a, b], ...]) as `{x, width}`. Null for fewer than 2 points.
 */
export function previewShape(points, { width = 600, height = 80, start = null, end = null, excluded = [] } = {}) {
  const xs = points?.two_theta || [];
  const ys = points?.intensity || [];
  const pairs = xs.map((x, i) => [x, ys[i]]).filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  if (pairs.length < 2) return null;
  const lo = pairs[0][0];
  const hi = pairs[pairs.length - 1][0];
  const top = Math.max(...pairs.map(([, y]) => y));
  const bottom = Math.min(...pairs.map(([, y]) => y));
  const sx = x => ((x - lo) / (hi - lo || 1)) * width;
  const sy = y => height - 1 - ((y - bottom) / (top - bottom || 1)) * (height - 2);
  const d = pairs.map(([x, y], i) => `${i ? 'L' : 'M'}${sx(x).toFixed(1)} ${sy(y).toFixed(1)}`).join('');
  const intervals = [
    ...(Number.isFinite(start) && start > lo ? [[lo, start]] : []),
    ...(Number.isFinite(end) && end < hi ? [[end, hi]] : []),
    ...(excluded || []).filter(item => Number.isFinite(item?.[0]) && Number.isFinite(item?.[1])),
  ];
  const shades = intervals
    .map(([a, b]) => [Math.max(lo, Math.min(a, b)), Math.min(hi, Math.max(a, b))])
    .filter(([a, b]) => b > a)
    .map(([a, b]) => ({ x: sx(a), width: sx(b) - sx(a) }));
  return { d, shades, lo, hi, ticks: previewTicks(lo, hi) };
}

/**
 * 2θ tick labels between the end values of the preview: a round step (1, 2, 5, 10 or 20°) giving
 * at most 8 ticks, each `{value, at}` (`at`: 0..1 across the box), none within 6 % of an end label.
 */
export function previewTicks(lo, hi) {
  const span = hi - lo;
  if (!(span > 0)) return [];
  const step = [1, 2, 5, 10, 20].find(value => span / value <= 8) || 50;
  const ticks = [];
  for (let value = Math.ceil(lo / step) * step; value < hi; value += step) {
    const at = (value - lo) / span;
    if (at >= 0.06 && at <= 0.94) ticks.push({ value, at });
  }
  return ticks;
}

/** `run(task)` runs `task()` (returning a promise) with at most `n` tasks at a time; resolves with its result. */
export function limiter(n) {
  let active = 0;
  const queue = [];
  const next = () => {
    if (active >= n || !queue.length) return;
    active += 1;
    const [task, resolve, reject] = queue.shift();
    Promise.resolve().then(task).then(resolve, reject).finally(() => { active -= 1; next(); });
  };
  return task => new Promise((resolve, reject) => { queue.push([task, resolve, reject]); next(); });
}

/**
 * Default name of a batch of files (a series): the common start of the file names without the
 * extension and trailing separators ("V2O5-2ZnO" of V2O5-2ZnO_300C.xrdml, V2O5-2ZnO_400C.xrdml),
 * else "Batch of N files".
 */
export function batchName(names) {
  const stems = (names || []).map(name => String(name).replace(/\.[^.]*$/, ''));
  if (!stems.length) return '';
  let common = stems[0];
  stems.forEach(stem => { while (!stem.startsWith(common)) common = common.slice(0, -1); });
  common = common.replace(/[\s_\-.,;+]+$/, '');
  return common.length >= 3 ? common : `Batch of ${stems.length} files`;
}

/**
 * Row of a file in "Identify all": `{text, tone}` from its state (`queued`, `sending`, `running`
 * {taskId}, `done` {taskId, phases}, `failed` {error}); tone `ok` / `fail` / undefined.
 */
export function batchRowView(entry) {
  if (!entry || entry.state === 'queued') return { text: 'Queued' };
  if (entry.state === 'sending') return { text: 'Uploading' };
  if (entry.state === 'running') return { text: 'Searching' };
  if (entry.state === 'done') {
    const phases = entry.phases || [];
    return { text: phases.length ? phases.join(' + ') : 'No phase', tone: 'ok' };
  }
  return { text: entry.error || 'Failed', tone: 'fail' };
}

/** A random lower-case hexadecimal id of `length` digits (a batch's series id). */
export function hexId(length = 12, random = Math.random) {
  return Array.from({ length }, () => Math.floor(random() * 16).toString(16)).join('');
}
