/** Semi-quantitative RIR (I/Ic) view beside the Rietveld weight fractions (GET /api/task/{id}/rir). */

const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));

/** Rows of the RIR table: the response's phases with `delta` = RIR − Rietveld wt% (null when either is missing). */
export function rirRows(body) {
  return (body?.phases || []).map(phase => ({
    ...phase,
    key: phase.phase,
    delta: finite(phase.weight_percent_rir) && finite(phase.weight_percent_rietveld)
      ? Number(phase.weight_percent_rir) - Number(phase.weight_percent_rietveld) : null,
  }));
}

/** "(3 1 1) 37.10°" for the line a phase was quantified with, or null. */
export function lineText(phase) {
  if (!phase?.line_hkl || !finite(phase.line_two_theta)) return null;
  return `(${phase.line_hkl}) ${Number(phase.line_two_theta).toFixed(2)}°`;
}

/** Phases without an RIR fraction (no usable line): their labels, for the "incomplete" notice. */
export const missingPhases = body => (body?.phases || []).filter(phase => !finite(phase.weight_percent_rir))
  .map(phase => phase.phase_label || phase.phase);

/** A signed number with the minus sign (U+2212) and an explicit "+": "+6.4", "−9.8"; "—" when missing. */
export function signedText(value, digits = 1) {
  if (!finite(value)) return '—';
  const text = Math.abs(Number(value)).toFixed(digits);
  return Number(text) === 0 ? text : `${Number(value) < 0 ? '−' : '+'}${text}`;
}
