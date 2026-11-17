/** Unit-cell refinement from peak positions (GET /api/task/{id}/cell-refinement) beside the Rietveld cell. */
import { formatParameterWithEsd } from './latticeParameters.mjs';

/** Shift models offered: zero and displacement together are degenerate on real data, so one at a time. */
export const SHIFT_MODELS = [
  { value: 'none', label: 'None' },
  { value: 'zero', label: 'Zero shift' },
  { value: 'displacement', label: 'Specimen displacement' },
];
export const DEFAULT_SHIFT_MODEL = 'displacement';

/** Query of the cell refinement for `model` ('none' | 'zero' | 'displacement' | 'standard:<phase>'). */
export function cellQuery(model) {
  if (typeof model === 'string' && model.startsWith('standard:')) return `correct_with=${encodeURIComponent(model.slice(9))}`;
  return `zero=${model === 'zero' ? 1 : 0}&displacement=${model === 'displacement' ? 1 : 0}`;
}

const finite = value => value !== null && value !== undefined && Number.isFinite(Number(value));
const LABELS = { a: 'a', b: 'b', c: 'c', alpha: 'α', beta: 'β', gamma: 'γ' };
const ANGLES = new Set(['alpha', 'beta', 'gamma']);

/**
 * Rows of the parameter table of one phase: the free parameters and the volume, each with the
 * value from the peaks, the Rietveld value and `z` = difference / combined e.s.d. (null when
 * it cannot be formed). Values carry `unit` for `formatParameterWithEsd`.
 */
export function parameterRows(phase) {
  if (!phase?.parameters) return [];
  const rietveld = phase.rietveld || {};
  const row = (key, label, peaks, reference, unit) => {
    const ref = reference && typeof reference === 'object' ? reference : { value: reference, esd: null };
    const ok = finite(peaks?.value) && finite(ref.value);
    const sigma = Math.hypot(finite(peaks?.esd) ? Number(peaks.esd) : 0, finite(ref.esd) ? Number(ref.esd) : 0);
    const delta = ok ? Number(peaks.value) - Number(ref.value) : null;
    return { key, label, peaks: peaks ? { ...peaks, unit } : null, rietveld: finite(ref.value) ? { ...ref, unit } : null,
      delta, z: ok && sigma > 0 ? delta / sigma : null, unit };
  };
  const rows = Object.entries(phase.parameters).map(([key, value]) => row(key, LABELS[key] || key, value, rietveld[key], ANGLES.has(key) ? 'deg' : 'Å'));
  if (phase.volume) rows.push(row('volume', 'V', phase.volume, rietveld.volume, 'Å³'));
  return rows;
}

/** "8.08550 ± 0.00082 Å" (the app's e.s.d. notation), or "—". */
export const valueText = parameter => formatParameterWithEsd(parameter) ?? '—';

/** A table cell without the unit (the column names it): value ± e.s.d., or the bare value (volumes to 0.01 Å³). */
export function cellText(parameter, unit) {
  if (!parameter || !finite(parameter.value)) return '—';
  if (finite(parameter.esd) && Number(parameter.esd) > 0) return formatParameterWithEsd({ ...parameter, unit: '' }).trim();
  return Number(parameter.value).toFixed(unit === 'Å³' ? 2 : 5);
}

/** A signed difference in combined e.s.d. units, "+3.1σ" / "−0.4σ", or "—". */
export function sigmaText(z) {
  if (!finite(z)) return '—';
  const text = Math.abs(z).toFixed(1);
  return `${Number(text) === 0 ? '' : z < 0 ? '−' : '+'}${text}σ`;
}

/** The fitted shift of a phase: "displacement −0.274 ± 0.007 mm", "zero +0.232 ± 0.006°" or null. */
export function shiftText(phase) {
  const signed = (value, esd, digits) => `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(digits)}${finite(esd) ? ` ± ${Number(esd).toFixed(digits)}` : ''}`;
  if (finite(phase?.displacement_mm?.value)) return `displacement ${signed(Number(phase.displacement_mm.value), phase.displacement_mm.esd, 3)} mm`;
  if (finite(phase?.zero_deg?.value)) return `zero ${signed(Number(phase.zero_deg.value), phase.zero_deg.esd, 3)}°`;
  return null;
}

/** `{used, total}` peaks of a phase (total counts every listed reflection). */
export const peakCounts = phase => ({ used: (phase?.peaks || []).filter(peak => peak.used).length, total: (phase?.peaks || []).length });
