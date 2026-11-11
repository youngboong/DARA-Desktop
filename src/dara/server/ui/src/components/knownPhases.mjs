// Known-phase refinement: reuse of a result's phase set, and the outcome of each listed phase.

/** Task id of ``?phase_set=<id>`` (a positive integer), else ``null``. */
export function phaseSetIdFromSearch(search) {
  const value = new URLSearchParams(search || '').get('phase_set');
  return value && /^[1-9]\d{0,11}$/.test(value) ? Number(value) : null;
}

/** Submit URL that reuses the phases of ``taskId`` (``series``: for a series of files). */
export function phaseSetUrl(taskId, series = false) {
  return `/?phase_set=${encodeURIComponent(taskId)}${series ? '&series=1' : ''}`;
}

/** The query string without ``phase_set`` (``series`` and the rest are kept). */
export function withoutPhaseSet(search) {
  const params = new URLSearchParams(search || '');
  params.delete('phase_set');
  const text = params.toString();
  return text ? `?${text}` : '';
}

/** "Phases from Task 12: ZrO2 · P2_1/c, Li2CO3 · C2/c" (``phases``: [{formula, spacegroup}]). */
export function phaseSetLabel(taskId, phases) {
  const text = (phases || []).map(item => (item.spacegroup ? `${item.formula} · ${item.spacegroup}` : item.formula)).join(', ');
  return `Phases from Task ${taskId}: ${text}`;
}

/** One sentence for each listed phase that is not in the fit (``search_summary.known_phases``). */
export function knownPhaseNotes(entries) {
  const notes = [];
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (entry?.status === 'not_detected' && entry.reason === 'not_checked') {
      notes.push(`${entry.formula}: not checked (the time limit was reached)`);
    } else if (entry?.status === 'not_detected') {
      const weight = entry.removed_weight_percent;
      let detail = weight == null ? '' : ` (refined to ${Number(weight)} wt%, removed)`;
      if (entry.reason === 'no_fit') detail = ' (no phase combination fitted the pattern)';
      else if (entry.reason === 'no_peak_evidence') detail = ` (a trial fit gave ${Number(weight ?? 0)} wt% but explained no additional peak)`;
      else if (entry.reason === 'failed' && weight == null) detail = ' (a trial fit with it failed)';
      notes.push(`${entry.formula}: not detected${detail}`);
    } else if (entry?.status === 'no_structure') {
      notes.push(`${entry.formula}: no reference structure found (enable Materials Project or add a CIF)`);
    }
  }
  return notes;
}

/** "La(OH)3 taken from Materials Project (no COD structure)" for each fallback formula. */
export function mpFallbackNotes(formulas) {
  return (Array.isArray(formulas) ? formulas : []).map(formula => `${formula}: structure taken from Materials Project (no COD structure)`);
}
