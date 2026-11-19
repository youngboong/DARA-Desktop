/** Identification results (search-match phases accepted without refinement): pure helpers. */

export const IDENTIFICATION = 'identification';

/** Whether a task payload (or a results-list summary) is an identification without refinement. */
export const isIdentification = data => data?.analysis_mode === IDENTIFICATION;

const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));

/** "All 32 peaks assigned" / "12 unexplained peaks" / null when the peak counts are unknown. */
export function peakStatusText(counts) {
  if (!counts || !finite(counts.observed)) return null;
  const open = Number(counts.unexplained) || 0;
  if (!open) return `All ${counts.observed} peak${Number(counts.observed) === 1 ? '' : 's'} assigned`;
  return `${open} unexplained peak${open === 1 ? '' : 's'}`;
}

/** "2 phases found". */
export const phasesFoundText = phases => `${(phases || []).length} phase${(phases || []).length === 1 ? '' : 's'} found`;

/** Median of the finite values, or null. */
export function median(values) {
  const sorted = values.filter(finite).map(Number).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** `{phase: {median, n}}`: the median Scherrer size (nm) of each phase over its peaks with a size. */
export function scherrerByPhase(rows) {
  const sizes = {};
  (rows || []).forEach(row => {
    if (!row?.phase || !finite(row.size_nm)) return;
    (sizes[row.phase] = sizes[row.phase] || []).push(Number(row.size_nm));
  });
  return Object.fromEntries(Object.entries(sizes).map(([phase, list]) => [phase, { median: median(list), n: list.length }]));
}

/** "46 nm" (median, rounded) or "—". */
export const sizeText = entry => (entry && finite(entry.median) ? `${Math.round(entry.median)} nm` : '—');

/** "56.0" (RIR wt%) or null when the phase has none. */
export const rirText = value => (finite(value) ? Number(value).toFixed(1) : null);

/** Links of the refinements made from an identification (`refined_as`), newest last. */
export const refinedTasks = data => (Array.isArray(data?.refined_as) ? data.refined_as.filter(id => Number.isInteger(Number(id))) : []);

/** Status of an identification: its phases come from peak matching; a Rietveld fit verifies them. */
export const NOT_VERIFIED = 'Peak matching only · not verified by a fit';

/**
 * Line of a phase-agreement item (GET /task/{id}/phase-agreement): the other task is the Rietveld
 * refinement on an identification's page (`otherKind` "Rietveld"), the identification on a refinement's.
 */
export function agreementText(item, otherKind) {
  const task = `${otherKind} Task ${item.task_id}`;
  if (item.agree === null || item.agree === undefined) return `${task}: ${String(item.status || 'pending').toLowerCase()}`;
  if (item.agree) return `${task}: same phases`;
  const parts = [];
  if (item.only_here?.length) parts.push(`only here ${item.only_here.join(', ')}`);
  if (item.only_there?.length) parts.push(`only in Task ${item.task_id} ${item.only_there.join(', ')}`);
  return `${task}: phases differ · ${parts.join(' · ')}`;
}
