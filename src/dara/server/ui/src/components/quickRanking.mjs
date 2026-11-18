// Pure helpers of the quick ranking workspace (search-match). No React, no fetch: tested with node.
import { TRAY_LINES } from './composition.mjs';

export const MAX_REFINE = 12; // Known-phase refinement takes at most 12 phases
const PREVIEW_COLOR = '#c99a2e'; // --dara-shell-mark: apart from the grey measured line and the TRAY_LINES of selected phases

const FLAG_COLORS = { picked: 'green', weak: 'orange', polymorph: 'purple', theoretical: 'gold' };

/** Table key of a ranking row: its source id (unique per row). */
export const rowKey = row => row.source;

/** `{label, color}` tags for a row's flags (variant tags keep their text). */
export function flagTags(flags) {
  return (flags || []).map(flag => ({
    label: flag,
    color: FLAG_COLORS[flag] || (String(flag).startsWith('variant of') ? 'blue' : 'default'),
  }));
}

/** "34 %" from a 0..1 fraction; an em dash for a missing value. */
export function percent(value, digits = 0) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(digits)} %` : '—';
}

/** Signed lattice change in percent of a lattice scale ("+0.4 %"). */
export function latticePercent(scale) {
  if (!Number.isFinite(scale)) return '—';
  const value = (scale - 1) * 100;
  const text = Math.abs(value) < 0.05 ? '0.0' : value.toFixed(1);
  return `${value > 0.049 ? '+' : ''}${text} %`;
}

/** Human label and link of a source id ("cod:1000041" / "mp:mp-19770"). */
export function sourceInfo(source) {
  const [kind, id] = String(source || '').split(/:(.*)/s);
  if (kind === 'cod') return { label: `COD ${id}`, url: `https://www.crystallography.net/cod/${id}.html` };
  if (kind === 'mp') return { label: `MP ${id}`, url: `https://next-gen.materialsproject.org/materials/${id}` };
  return { label: String(source || ''), url: null };
}

/** Table rows of a quick ranking response with display strings. */
export function rankingRows(response) {
  return (response?.ranking || []).map(row => ({
    ...row,
    key: rowKey(row),
    scoreText: percent(row.score),
    explainedText: percent(row.explained),
    peaksText: `${row.matched}/${row.expected}`,
    latticeText: latticePercent(row.lattice_scale),
    spacegroupText: row.spacegroup_symbol ? `${row.spacegroup_symbol} (${row.spacegroup})` : (row.spacegroup ?? '—'),
    tags: flagTags(row.flags),
    sourceLabel: sourceInfo(row.source).label,
  }));
}

/**
 * One line summary of the greedy combination ("Fe2O3 + ZnO (weak)"). Phase names only: a step's
 * gain is a share of the intensity explained, not an amount, and read as wt% beside a formula.
 */
export function combinationText(combination) {
  return (combination || [])
    .map(step => `${step.formula}${step.weak ? ' (weak)' : ''}`)
    .join(' + ');
}

/**
 * Stick colour of each selected row: a row keeps its colour while it stays selected; a newly
 * selected row takes the first colour no other selected row uses.
 */
export function assignColors(previous, selectedKeys) {
  const colors = {};
  const keys = selectedKeys || [];
  keys.forEach(key => { if (previous?.[key]) colors[key] = previous[key]; });
  keys.forEach((key, index) => {
    if (colors[key]) return;
    const used = new Set(Object.values(colors));
    colors[key] = TRAY_LINES.find(color => !used.has(color)) || TRAY_LINES[index % TRAY_LINES.length];
  });
  return colors;
}

const stickXY = sticks => {
  const x = [];
  const y = [];
  for (const [tt, height] of sticks || []) {
    if (!Number.isFinite(tt) || !Number.isFinite(height)) continue;
    x.push(tt, tt, null);
    y.push(0, Math.max(0, height), null);
  }
  return { x, y };
};

/**
 * Plotly traces: the net pattern, vertical sticks of each selected row in its colour, and the
 * focused row (the preview) drawn dotted when it is not selected, bold when it is.
 */
export function overlayTraces(response, selectedKeys, colors = assignColors({}, selectedKeys), focusedKey = null) {
  const traces = [];
  const profile = response?.profile;
  if (profile?.two_theta?.length) {
    traces.push({
      type: 'scatter', mode: 'lines', name: 'Measured (background removed)', x: profile.two_theta, y: profile.net,
      line: { color: '#4a504c', width: 1 }, hoverinfo: 'x+y',
    });
  }
  const chosen = new Set(selectedKeys || []);
  (response?.ranking || []).forEach(row => {
    const key = rowKey(row);
    const selected = chosen.has(key);
    if (!selected && key !== focusedKey) return;
    traces.push({
      type: 'scatter', mode: 'lines', name: `${row.formula} (${sourceInfo(row.source).label})`, ...stickXY(row.sticks),
      connectgaps: false, hoverinfo: 'name',
      line: selected
        ? { color: colors[key] || TRAY_LINES[0], width: key === focusedKey ? 3 : 2 }
        : { color: PREVIEW_COLOR, width: 2, dash: 'dot' },
    });
  });
  return traces;
}

/** Rows to refine: source ids (for `reference_ids`) and formulas, in ranking order. */
export function refineSelection(response, selectedKeys) {
  const chosen = new Set(selectedKeys || []);
  const rows = (response?.ranking || []).filter(row => chosen.has(rowKey(row)));
  return { referenceIds: rows.map(row => row.source), formulas: rows.map(row => row.formula) };
}

const comboKeys = (response, steps) => {
  const picked = new Set(steps.map(step => step.source));
  return (response?.ranking || []).filter(row => picked.has(row.source)).map(rowKey).slice(0, MAX_REFINE);
};

/** Rows ticked by default: the greedy combination's non-weak picks. */
export const defaultSelection = response => comboKeys(response, (response?.combination || []).filter(step => !step.weak));

/** Every suggested row, weak picks included ("Use suggestion"). */
export const suggestedKeys = response => comboKeys(response, response?.combination || []);

/** Weak suggested phases that are not selected: `[{key, formula}]` for the "not selected" notice. */
export function weakNotSelected(response, selectedKeys) {
  const chosen = new Set(selectedKeys || []);
  const known = new Set((response?.ranking || []).map(rowKey));
  return (response?.combination || [])
    .filter(step => step.weak && known.has(step.source) && !chosen.has(step.source))
    .map(step => ({ key: step.source, formula: step.formula }));
}

const HIDDEN_THEORETICAL = /^(\d+) computed-only \(theoretical\) .* hidden/;

/** The caveats of a ranking without the routine "N theoretical structures hidden" note (it goes into the pool line). */
export const rankingNotes = response => (response?.caveats || []).filter(text => !HIDDEN_THEORETICAL.test(text));

/** Pool line: "Ranked 54 candidates (COD 50, MP 4) in 1.2 s · 12 downloaded now · 3 unavailable". */
export function poolText(response) {
  const pool = response?.pool;
  if (!pool) return '';
  const sources = Object.entries(pool.sources || {}).map(([kind, n]) => `${kind.toUpperCase()} ${n}`).join(', ');
  const parts = [`Ranked ${pool.candidates ?? 0} candidates${sources ? ` (${sources})` : ''}`];
  if (Number.isFinite(response.elapsed_s)) parts[0] += ` in ${response.elapsed_s.toFixed(1)} s`;
  if (pool.downloaded > 0) parts.push(`${pool.downloaded} downloaded now`);
  if (pool.simulated) parts.push(`${pool.simulated} simulated now`);
  if (pool.unavailable > 0) parts.push(`${pool.unavailable} unavailable`);
  if (pool.missing_cif) parts.push(`${pool.missing_cif} not downloaded`);
  const hidden = (response.caveats || []).map(text => HIDDEN_THEORETICAL.exec(text)).find(Boolean);
  if (hidden) parts.push(`${hidden[1]} theoretical hidden`);
  return parts.join(' · ');
}

/** Delay before the next poll of a pending ranking (ms): quick first, then every 2 s. */
export function pollDelay(attempt) {
  return attempt < 3 ? 700 : 2000;
}

const PHASES = {
  listing: 'Listing candidate structures',
  downloading: 'Downloading structures',
  simulating: 'Simulating reference patterns',
  ranking: 'Ranking candidates',
};

/** Progress line of a pending ranking ("Downloading structures · 12 of 40 · 8 s"); phase, done and total are optional. */
export function progressText(view) {
  const parts = [PHASES[view?.phase] || 'Ranking candidates'];
  if (view?.total > 0) parts.push(`${(view.done ?? 0).toLocaleString('en-US')} of ${view.total.toLocaleString('en-US')}`);
  if (Number.isFinite(view?.elapsed_s) && view.elapsed_s >= 1) parts.push(`${Math.round(view.elapsed_s)} s`);
  return parts.join(' · ');
}

/** Table sorters (ascending): rank, score, share of strong peaks found, lattice change size. */
const peakShare = row => (row.expected > 0 ? row.matched / row.expected : 0);
const latticeOff = row => (Number.isFinite(row.lattice_scale) ? Math.abs(row.lattice_scale - 1) : Infinity);
export const SORTERS = {
  rank: (a, b) => a.rank - b.rank,
  score: (a, b) => (a.score ?? -1) - (b.score ?? -1),
  peaks: (a, b) => peakShare(a) - peakShare(b) || (a.matched ?? 0) - (b.matched ?? 0),
  lattice: (a, b) => latticeOff(a) - latticeOff(b) || 0, // NaN (both missing) counts as equal
};

/** Candidate table keys: Up/Down move the focused row, Space toggles its selection. */
export function rowKeyAction(key) {
  return { ArrowDown: 'next', ArrowUp: 'previous', ' ': 'toggle' }[key] || null;
}

/** `keys` with `key` added (at the end) or removed. */
export const toggleKey = (keys, key) => (keys.includes(key) ? keys.filter(item => item !== key) : [...keys, key]);

/** Identity of a ranking request (multipart entries); a file counts by name, size and date. */
export function requestKey(entries) {
  return JSON.stringify(Array.from(entries || [], ([name, value]) => [
    name, value && typeof value === 'object' ? `${value.name}|${value.size}|${value.lastModified}` : String(value),
  ]));
}

// --- what is left after the selected phases (POST /quick-ranking/{job}/residual) -------------

/**
 * Status of a selection from its residual: the number of unexplained peaks. (The `explained`
 * share is not a completeness measure: it stays well below 100 % for a complete phase set.)
 */
export function unexplainedStatus(residual) {
  const n = (residual?.unexplained_peaks || []).length;
  return n ? `${n} unexplained peak${n === 1 ? '' : 's'}` : 'No unexplained peaks';
}

/** Up to `limit` best matches for the remaining peaks as table rows; `inRanking` when the row can be selected. */
export function nextMatches(residual, rows = [], limit = 3) {
  const known = new Set(rows.map(row => row.key));
  return rankingRows({ ranking: (residual?.next || []).slice(0, limit) }).map(row => ({ ...row, inRanking: known.has(row.key) }));
}

/** The ranking response with the residual net pattern in place of the measured one (the "what is left" view). */
export const withResidualProfile = (response, residual) => (residual?.profile?.two_theta?.length ? { ...response, profile: residual.profile } : response);

/**
 * Quiet markers just above the 2θ axis (at `level`) at the unexplained peaks; `meta: 'unexplained'`
 * identifies them on click. (On the axis line itself half a marker would be outside the plot area.)
 */
export function unexplainedMarkers(peaks, level = 0) {
  const x = (peaks || []).map(peak => peak[0]).filter(Number.isFinite);
  return {
    type: 'scatter', mode: 'markers', name: 'Unexplained peaks', meta: 'unexplained', x, y: x.map(() => level), cliponaxis: false,
    marker: { symbol: 'triangle-up', size: 9, color: '#874d00', line: { width: 0 } },
    hovertemplate: 'Unexplained peak %{x:.2f}° 2θ<br>Click to zoom<extra></extra>',
  };
}

/** 2θ window of `half` degrees on either side of `x`, kept inside `bounds`. */
export function zoomWindow(x, bounds, half = 2) {
  const [lo, hi] = Array.isArray(bounds) && bounds.every(Number.isFinite) ? bounds : [-Infinity, Infinity];
  return [Math.max(lo, x - half), Math.min(hi, x + half)];
}

/** Intensity range [0, 1.1 × highest net value] of `profile` inside the 2θ `range` (null when there is none). */
export function windowRange(profile, range) {
  let top = -Infinity;
  (profile?.two_theta || []).forEach((x, i) => {
    const y = profile.net?.[i];
    if (x >= range[0] && x <= range[1] && Number.isFinite(y)) top = Math.max(top, y);
  });
  return top > 0 ? [0, top * 1.1] : null;
}

// Kα1 of the lab anodes (Å), as the setup lists them.
const ANODES = { Cu: 1.540598, Co: 1.78901, Cr: 2.28976, Fe: 1.936042, Mo: 0.7093 };
const anodeOf = angstrom => (Number.isFinite(angstrom)
  ? Object.keys(ANODES).find(name => Math.abs(ANODES[name] - angstrom) < 0.002) || `${angstrom.toFixed(4)} Å` : null);

/**
 * `{recorded, chosen}` of a ranking's `wavelength_mismatch` (anode names where they match one, else
 * "0.7293 Å"), from `{recorded, chosen}` or `{recorded_angstrom, selected_angstrom}`; null without one.
 */
export function mismatchView(mismatch) {
  if (!mismatch || typeof mismatch !== 'object') return null;
  const recorded = mismatch.recorded ?? anodeOf(mismatch.recorded_angstrom);
  const chosen = mismatch.chosen ?? anodeOf(mismatch.selected_angstrom);
  return recorded && chosen ? { recorded: String(recorded), chosen: String(chosen) } : null;
}

/**
 * Traces of the "Background" view (HighScore's measured pattern with its background): the measured
 * (smoothed) pattern and the curve the ranking removed, from the response's `background`
 * (`{two_theta, observed, intensity}`); null when the server sends no curve.
 */
export function backgroundTraces(view) {
  const x = view?.two_theta;
  if (!x?.length || view.observed?.length !== x.length || view.intensity?.length !== x.length) return null;
  return [
    { type: 'scatter', mode: 'lines', name: 'Measured', x, y: view.observed, line: { color: '#4a504c', width: 1 }, hoverinfo: 'x+y' },
    { type: 'scatter', mode: 'lines', name: 'Background', x, y: view.intensity, line: { color: PREVIEW_COLOR, width: 2 }, hoverinfo: 'x+y' },
  ];
}

/**
 * Background settings of the search-match with the server's defaults and limits: `method` "auto"
 * (the default method) or "sonneveld_visser" (HighScore's: granularity 2..200, bending factor 0..50).
 */
export function backgroundSettings(settings) {
  const granularity = Number(settings?.granularity);
  const bending = Number(settings?.bending_factor);
  return {
    method: settings?.method === 'sonneveld_visser' ? 'sonneveld_visser' : 'auto',
    granularity: Number.isFinite(granularity) ? Math.min(200, Math.max(2, Math.round(granularity))) : 20,
    bending_factor: Number.isFinite(bending) ? Math.min(50, Math.max(0, bending)) : 1,
  };
}

const json = (value, fallback) => { try { return JSON.parse(value); } catch (_) { return fallback; } };
const number = value => (value === null || value === undefined || value === '' ? undefined : Number(value));

/**
 * JSON body of `POST /task/{id}/quick-ranking` (a stored pattern) from the multipart ranking request
 * the setup builds (`entries` of its FormData): the same options, typed.
 */
export function taskRankingBody(entries) {
  const form = Object.fromEntries(entries || []);
  const body = {
    include_theoretical: form.include_theoretical === 'true',
    library_cif_ids: json(form.library_cif_ids, []),
    elements: json(form.elements, []),
    precursor_formulas: json(form.precursor_formulas, []),
    wavelength: form.wavelength === undefined ? undefined : String(form.wavelength),
    two_theta_min: number(form.two_theta_min),
    two_theta_max: number(form.two_theta_max),
    excluded_ranges: json(form.excluded_ranges, undefined),
    use_cod: form.use_cod === undefined ? undefined : form.use_cod === 'true',
    use_mp: form.use_mp === undefined ? undefined : form.use_mp === 'true',
    mp_e_hull_cutoff: number(form.mp_e_hull_cutoff),
    max_mp_structures: number(form.max_mp_structures),
    background: form.background ? json(form.background, undefined) : undefined,
  };
  return Object.fromEntries(Object.entries(body).filter(([, value]) => value !== undefined));
}
