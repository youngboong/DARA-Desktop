
const STATE_TEXT = {
  READY: 'Queued', PENDING: 'Queued', RUNNING: 'Running', COMPLETED: 'Completed', FIZZLED: 'Failed',
  CANCELLING: 'Stopping', CANCELLED: 'Cancelled',
};

/** Practitioner wording of a job state ("READY" is a queued job that has not started). */
export function stateLabel(state) {
  return STATE_TEXT[state] || state || '—';
}

/** Two-decimal number or an em dash. */
export function formatFixed(value, digits = 2) {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number) ? '—' : number.toFixed(digits);
}

/** Rwp of the adopted fit: the list summary describes the adopted fit (`final_rwp`, which the server
 * sorts by); `adopted_rwp` (reports) and `best_rwp` (older summaries) are the fallbacks. */
export function adoptedRwp(summary) {
  return summary?.adopted_rwp ?? summary?.final_rwp ?? summary?.best_rwp;
}

/**
 * Short code for the "Adopted" column (`{text, title}`): `v1` for `"best"`, `vN` for a version,
 * `#N` for a search candidate by stored order (its C-code is only known on the task page).
 */
export function adoptedLabel(adopted) {
  if (adopted === 'best') return { text: 'v1', title: 'Automatic fit (v1) adopted' };
  if (Number.isInteger(adopted) && adopted >= 0) {
    return { text: `#${adopted + 1}`, title: `Search candidate #${adopted + 1} (stored order) adopted` };
  }
  const version = typeof adopted === 'string' ? /^version:([2-9]|[1-9]\d+)$/.exec(adopted) : null;
  return version ? { text: `v${version[1]}`, title: `Version ${version[1]} adopted` } : null;
}

/** Comma-joined phase labels, shortened with an ellipsis after `limit` phases. */
export function phasesLabel(summary, limit = 3) {
  const labels = Array.isArray(summary?.final_phase_labels) ? summary.final_phase_labels : [];
  if (!labels.length) return null;
  const shown = labels.slice(0, limit).join(', ');
  return labels.length > limit ? `${shown}, … (+${labels.length - limit})` : shown;
}

export function phasesTooltip(summary) {
  const names = Array.isArray(summary?.final_phases) ? summary.final_phases : [];
  return names.length ? names.join('\n') : null;
}

/** Query string for the task list; empty values are omitted and text is encoded. */
export function tasksQuery({ page = 1, limit = 10, user, userExact = false, name, state, verdict, sort, order, selected = [], ids = [] } = {}) {
  const parts = [`page=${page}`, `limit=${limit}`];
  if (user) parts.push(`user=${encodeURIComponent(user)}`);
  if (user && userExact) parts.push('user_exact=true');
  if (ids.length) parts.push(`ids=${ids.join(',')}`);
  if (name) parts.push(`name=${encodeURIComponent(name)}`);
  if (state) parts.push(`state=${encodeURIComponent(state)}`);
  if (verdict) parts.push(`verdict=${encodeURIComponent(verdict)}`);
  if (sort) parts.push(`sort=${sort}`);
  if (sort && order) parts.push(`order=${order}`);
  if (selected.length) parts.push(`selected=${selected.join(',')}`);
  return parts.join('&');
}

// --- Results view (filters, sort, page, selection) kept in the address ---------------------
export const PAGE_SIZES = [25, 50, 100];
export const MAX_LIMIT = 100; // the list API's upper limit
export const EXPORT_CHUNK = 50; // ids per table.csv request (the server's limit)
export const SORT_KEYS = ['name', 'submitted', 'rwp', 'gof', 'verdict'];
export const STATES = ['READY', 'RUNNING', 'CANCELLING', 'COMPLETED', 'FIZZLED', 'CANCELLED'];
export const VERDICTS = ['high', 'check', 'expert', 'no-fit'];
export const ACTIVE_STATES = ['READY', 'RUNNING', 'CANCELLING'];
const MAX_SELECTED = 1000;

const listOf = (value, allowed) => [...new Set(String(value || '').split(',').map(item => item.trim())
  .filter(item => allowed.includes(item)))];

/** Unique positive task ids from "3,7,7,x" (order kept). */
export function parseTaskIds(value) {
  return [...new Set(String(value || '').split(',').map(item => item.trim()).filter(item => /^\d{1,9}$/.test(item)).map(Number))]
    .slice(0, MAX_SELECTED);
}

/**
 * The Results view of an address. `user` absent means the user remembered by the Submit form
 * ("My tasks", matched as the whole name); `user=` (empty) means all users; `exact=1` matches the
 * whole user name, else the user filter is "contains". `limit` is clamped to what the server
 * accepts. `selected=kept`: the selection was too long for the address and is in this tab's storage.
 */
export function parseResultsView(search, rememberedUser = null) {
  const params = new URLSearchParams(search || '');
  const limit = Number(params.get('limit'));
  const page = Number(params.get('page'));
  const sort = params.get('sort');
  const order = params.get('order');
  return {
    page: Number.isInteger(page) && page > 0 ? page : 1,
    limit: Number.isInteger(limit) && limit > 0 ? Math.min(limit, MAX_LIMIT) : 50,
    user: params.has('user') ? params.get('user').trim() : String(rememberedUser || '').trim(),
    exact: params.has('user') ? params.get('exact') === '1' : Boolean(String(rememberedUser || '').trim()),
    name: (params.get('name') || '').trim(),
    state: listOf(params.get('state'), STATES),
    verdict: listOf(params.get('verdict'), VERDICTS),
    sort: SORT_KEYS.includes(sort) ? sort : null,
    order: ['asc', 'desc'].includes(order) ? order : null,
    selected: parseTaskIds(params.get('selected')),
  };
}

/** Address query of a view (no "?"); `user` is always written, so "All users" survives Back. */
export function resultsSearch(view, { withSelection = true } = {}) {
  const params = new URLSearchParams();
  params.set('page', String(view.page));
  params.set('limit', String(view.limit));
  params.set('user', view.user || '');
  if (view.user && view.exact) params.set('exact', '1');
  if (view.name) params.set('name', view.name);
  if (view.state?.length) params.set('state', view.state.join(','));
  if (view.verdict?.length) params.set('verdict', view.verdict.join(','));
  if (view.sort) params.set('sort', view.sort);
  if (view.sort && view.order) params.set('order', view.order);
  if (withSelection && view.selected?.length) params.set('selected', view.selected.join(','));
  return params.toString().replace(/%2C/g, ',');
}

/** `GET /api/tasks` query of a view (`page`/`limit` overridable to page through every match). */
export function apiQuery(view, overrides = {}) {
  const { page, limit } = { ...view, ...overrides };
  return tasksQuery({ page, limit, user: view.user, userExact: view.exact, name: view.name, state: view.state.join(','),
    verdict: view.verdict.join(','), sort: view.sort, order: view.order });
}

export const KEPT_SELECTION = 'kept';

/**
 * `back` of a task link: the view's query; a selection too long for the address is written as
 * `selected=kept` (the Results page keeps it in the tab's session storage).
 */
export function backQuery(view) {
  const full = resultsSearch(view);
  return full.length <= 1800 ? full : `${resultsSearch(view, { withSelection: false })}&selected=${KEPT_SELECTION}`;
}

/** Whether a row's user is `user` (the whole name, case and surrounding spaces ignored): "My tasks". */
export const sameUser = (a, b) => String(a ?? '').trim().toLowerCase() === String(b ?? '').trim().toLowerCase();

export const filtersActive = view => Boolean(view.name || view.state.length || view.verdict.length);

const isActive = entry => ACTIVE_STATES.includes(entry?.state);
export const hasActiveRows = tasks => (tasks || []).some(isActive);

const VERDICT_VIEW = {
  high: { text: 'High', tone: 'ok', title: 'High confidence' },
  check: { text: 'Check', tone: 'review', title: 'Check recommended' },
  expert: { text: 'Expert review', tone: 'escalate', title: 'Expert review recommended' },
};
/** Verdict of a completed list row: `{text, tone, title}` (High · Check · Expert review · No fit), or null. */
export function verdictView(entry) {
  if (entry?.state !== 'COMPLETED' || isIdentificationEntry(entry)) return null;
  const summary = entry.summary;
  if (VERDICT_VIEW[summary?.confidence_overall]) return VERDICT_VIEW[summary.confidence_overall];
  if (summary?.schema != null && summary.final_rwp == null && !(summary.final_phases || []).length) {
    return { text: 'No fit', tone: 'review', title: 'The search finished without a fit. Open the task for the settings to widen.' };
  }
  return null;
}

/**
 * The line under the name of an unfinished row: stage and percent, queue position or failure title;
 * null when it would only repeat the state tag ("Running" before the first progress report).
 */
export function rowStatusLine(entry) {
  if (entry?.state === 'RUNNING') {
    const percent = Number.isFinite(entry.progress?.percent) ? entry.progress.percent : null;
    const stage = entry.progress?.stage;
    if (!stage && percent === null) return null;
    return { tone: 'info', percent, text: [stage, percent === null ? null : `${percent}%`].filter(Boolean).join(' · ') };
  }
  if (entry?.state === 'READY') {
    return entry.queue_position ? { tone: 'neutral', text: `Position ${entry.queue_position} in the queue` } : null;
  }
  if (entry?.state === 'FIZZLED') return { tone: 'fail', text: entry.error_summary?.title || 'Failed' };
  return null;
}

/** "2026-11-12 15:40:07" (or ISO) -> "2026-11-12 15:40": the one date format of the lists. */
export function formatDate(value) {
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(String(value || ''));
  return match ? `${match[1]} ${match[2]}` : (value || '');
}

/**
 * `[head, tail]` of a name for a middle ellipsis (the head shrinks, the tail stays): the tail keeps
 * the distinguishing end ("--8min.xrdml"), from the last separator within the last `window`
 * characters, else the last `tail` characters. Short names come back whole (`tail` empty).
 */
export function middleSplit(text, { tail = 10, window = 16 } = {}) {
  const value = String(text ?? '');
  if (value.length <= window + 4) return [value, ''];
  const end = value.slice(-window);
  let cut = Math.max(end.lastIndexOf('_'), end.lastIndexOf(' '), end.lastIndexOf('-'));
  if (cut > 0 && end[cut - 1] === '-') cut -= 1; // "--8min" keeps both dashes
  const keep = cut > 0 && window - cut >= 6 ? window - cut : tail; // a separator right at the end is no help
  return [value.slice(0, -keep), value.slice(-keep)];
}

/** Sample name first, the file as secondary text (`secondary` null without a distinct sample). */
export function displayName(entry) {
  const file = String(entry?.name || '');
  const sample = String(entry?.sample || '').trim();
  return sample && sample !== file ? { primary: sample, secondary: file } : { primary: file || sample, secondary: null };
}

/**
 * Selected ids split by whether a bulk action applies (`applies(entry)`). A row not loaded yet (a
 * selection restored from the address) is skipped: an action never runs on a row it has not checked.
 */
export function partitionSelected(selected = [], rowsById = new Map(), applies = () => true) {
  const ids = [];
  const skipped = [];
  for (const id of selected) (rowsById.has(id) && applies(rowsById.get(id)) ? ids : skipped).push(id);
  return { ids, skipped };
}

/** Rows a bulk delete may remove: anything not queued or running. */
export const deletable = entry => !isActive(entry);
/** Failed rows that a retry with the same settings usually fixes. */
export const retryable = entry => entry?.state === 'FIZZLED' && entry.error_summary?.temporary === true;

/** `ids` in chunks of at most `size`. */
export function chunks(ids, size = EXPORT_CHUNK) {
  const out = [];
  for (let index = 0; index < ids.length; index += size) out.push(ids.slice(index, index + size));
  return out;
}

/**
 * One CSV from the server's chunks: the first chunk's header, every chunk's rows and a UTF-8 BOM
 * for Excel (the server writes one; `Response.text()` drops it). The server ends lines in CRLF.
 */
export function mergeCsv(texts) {
  const parts = (texts || []).map(text => String(text || '').replace(/^﻿/, '')).filter(Boolean);
  if (!parts.length) return '';
  const withEnd = text => (text.endsWith('\n') ? text : `${text}\r\n`);
  const bodies = parts.slice(1).map(text => {
    const end = text.indexOf('\n');
    return end < 0 ? '' : text.slice(end + 1);
  }).filter(Boolean);
  return `﻿${[parts[0], ...bodies].map(withEnd).join('')}`;
}

/** File name of a merged export, as the server names one: dara_tasks_<yyyymmdd>_<n>.csv. */
export function exportFilename(count, date = new Date()) {
  const pad = value => String(value).padStart(2, '0');
  return `dara_tasks_${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${count}.csv`;
}

const OPTION_LABELS = { instrument_profile: 'Instrument profile', wavelength: 'Wavelength' };

/** "Instrument profile: … · Wavelength: Cu" for the saved search options (ranges are shown separately). */
export function searchOptionsText(options) {
  if (!options || typeof options !== 'object') return '—';
  const parts = Object.entries(options)
    .filter(([key, value]) => key !== 'refinement_params' && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => {
      const label = OPTION_LABELS[key] || key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
      return `${label}: ${typeof value === 'object' ? JSON.stringify(value) : value}`;
    });
  return parts.length ? parts.join(' · ') : '—';
}

/**
 * What to widen after a search that found no fit, from the run's saved settings
 * (`/rerun-settings`) and its search/candidate summaries. Plain sentences, most likely first.
 */
export function noFitHints(settings = {}, data = {}) {
  const hints = [];
  const screened = data?.candidate_summary?.after;
  const retained = data?.search_summary?.screening_results_retained;
  if (Number.isFinite(Number(screened)) && Number(retained) === 0) {
    hints.push(`None of the ${screened} candidate references fitted well enough during screening.`);
  }
  const elements = settings?.elements || [];
  if (settings?.analysis_mode === 'elemental' && elements.length && !elements.includes('O')) {
    hints.push('The element list has no O: add it if the sample may contain oxides (surface oxidation, calcination in air).');
  }
  if (settings?.candidate_prefilter) {
    hints.push('The candidate pre-filter was on: it skips references whose strongest lines are not found. Turn it off to screen every reference.');
  }
  if (settings?.use_mp && Number(settings?.mp_e_hull_cutoff) <= 0.05) {
    hints.push(`Materials Project entries were limited to E_hull ≤ ${settings.mp_e_hull_cutoff} eV/atom; raise it (for example 0.1) to include metastable phases.`);
  }
  if (!settings?.use_cod) hints.push('COD was not searched; add it for experimental structures.');
  hints.push('Very few or very sharp peaks can come from a single crystal, a textured film or the sample holder; those cannot be fitted by a powder phase search.');
  return hints;
}

/** Whether a results-list entry is an identification (search-match phases, no refinement). */
export const isIdentificationEntry = entry => entry?.summary?.analysis_mode === 'identification';

/**
 * Selected task ids for an action that needs a fit (table export): identification tasks among the
 * `tasks` rows known here are left out. `{ids, skipped}`.
 */
export function fitTaskIds(selected = [], tasks = []) {
  const identification = new Set((tasks || []).filter(isIdentificationEntry).map(entry => entry.fw_id));
  return { ids: selected.filter(id => !identification.has(id)), skipped: selected.filter(id => identification.has(id)) };
}

/**
 * Suggestions of the user filter: `previous` plus `names`, one entry per name ignoring case (the
 * filter ignores case; the first spelling seen stays), sorted.
 */
export function mergeUsers(previous = [], names = []) {
  const byKey = new Map(previous.map(name => [name.toLowerCase(), name]));
  names.forEach(name => {
    const text = typeof name === 'string' ? name.trim() : '';
    if (text && text !== 'unknown' && !byKey.has(text.toLowerCase())) byKey.set(text.toLowerCase(), text);
  });
  return byKey.size === previous.length ? previous : [...byKey.values()].sort((a, b) => a.localeCompare(b));
}
