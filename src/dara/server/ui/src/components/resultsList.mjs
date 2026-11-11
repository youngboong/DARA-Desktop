
const STATE_TEXT = {
  READY: 'Queued', RUNNING: 'Running', COMPLETED: 'Completed', FIZZLED: 'Failed',
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

/** Rwp of the adopted fit (`adopted_rwp`); older summaries only carry the best Rwp. */
export function adoptedRwp(summary) {
  return summary?.adopted_rwp ?? summary?.best_rwp;
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
export function tasksQuery({ page = 1, limit = 10, user, name, state, selected = [] } = {}) {
  const parts = [`page=${page}`, `limit=${limit}`];
  if (user) parts.push(`user=${encodeURIComponent(user)}`);
  if (name) parts.push(`name=${encodeURIComponent(name)}`);
  if (state) parts.push(`state=${encodeURIComponent(state)}`);
  if (selected.length) parts.push(`selected=${selected.join(',')}`);
  return parts.join('&');
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
