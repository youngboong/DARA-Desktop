// Pure helpers of the quick ranking drawer (search-match). No React, no fetch: tested with node.

export const MAX_REFINE = 12; // Known-phase refinement takes at most 12 phases
export const STICK_COLORS = ['#d4380d', '#1d39c4', '#389e0d', '#c41d7f', '#d48806', '#08979c', '#531dab', '#7cb305'];

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

/** One line summary of the greedy combination ("Fe2O3 34 % + ZnO 12 % (weak)"). */
export function combinationText(combination) {
  return (combination || [])
    .map(step => `${step.formula} ${percent(step.gain)}${step.weak ? ' (weak)' : ''}`)
    .join(' + ');
}

/** Plotly traces: the net pattern plus vertical sticks of each selected row. */
export function overlayTraces(response, selectedKeys) {
  const traces = [];
  const profile = response?.profile;
  if (profile?.two_theta?.length) {
    traces.push({
      type: 'scatter', mode: 'lines', name: 'Measured (background removed)', x: profile.two_theta, y: profile.net,
      line: { color: '#595959', width: 1 }, hoverinfo: 'x+y',
    });
  }
  const chosen = new Set(selectedKeys || []);
  (response?.ranking || []).filter(row => chosen.has(rowKey(row))).forEach((row, index) => {
    const x = [];
    const y = [];
    for (const [tt, height] of row.sticks || []) {
      if (!Number.isFinite(tt) || !Number.isFinite(height)) continue;
      x.push(tt, tt, null);
      y.push(0, Math.max(0, height), null);
    }
    traces.push({
      type: 'scatter', mode: 'lines', name: `${row.formula} (${sourceInfo(row.source).label})`, x, y,
      connectgaps: false, hoverinfo: 'name', line: { color: STICK_COLORS[index % STICK_COLORS.length], width: 2 },
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

/** Rows ticked by default: the greedy combination's non-weak picks. */
export function defaultSelection(response) {
  const picked = new Set((response?.combination || []).filter(step => !step.weak).map(step => step.source));
  return (response?.ranking || []).filter(row => picked.has(row.source)).map(rowKey).slice(0, MAX_REFINE);
}

/** Pool line: "Ranked 54 candidates (COD 50, MP 4) in 1.2 s · 3 not downloaded". */
export function poolText(response) {
  const pool = response?.pool;
  if (!pool) return '';
  const sources = Object.entries(pool.sources || {}).map(([kind, n]) => `${kind.toUpperCase()} ${n}`).join(', ');
  const parts = [`Ranked ${pool.candidates ?? 0} candidates${sources ? ` (${sources})` : ''}`];
  if (Number.isFinite(response.elapsed_s)) parts[0] += ` in ${response.elapsed_s.toFixed(1)} s`;
  if (pool.simulated) parts.push(`${pool.simulated} simulated now`);
  if (pool.missing_cif) parts.push(`${pool.missing_cif} not downloaded`);
  return parts.join(' · ');
}

/** Delay before the next poll of a pending ranking (ms): quick first, then every 2 s. */
export function pollDelay(attempt) {
  return attempt < 3 ? 700 : 2000;
}
