// Crystallite-size cell of the phase table: the refined size, or one short reason.
// `crystallite_size_status` comes from the server (_phase_refinement_details).

const STATUS_TEXT = {
  not_resolvable: {
    text: 'Not resolvable',
    tooltip: 'No size broadening detected; crystallites are larger than this instrument can resolve.',
  },
  not_refined: {
    text: 'Not refined',
    tooltip: 'Peak width was held at the instrument profile; crystallite size was not refined.',
  },
  at_limit: {
    text: 'At refinement limit',
    tooltip: 'Peak broadening reached the upper bound of the refinement window; crystallites are very small and the size could not be determined.',
  },
  stale: {
    text: 'Not determined (older run)',
    tooltip: "Not determined by the DARA version that ran this task. Run 'Baseline (current code)' in Versions to get the size.",
  },
};

const FAILED = {
  text: 'Refinement failed',
  tooltip: 'BGMN could not refine this phase (see the status next to its amount); no size is reported.',
};

const isFailed = row => row?.refinement_status?.level === 'failed' || row?.size_strain?.size_state === 'failed';

export function formatGrainSize(value) {
  const esd = value.esd == null ? '' : ` ± ${Number(value.esd).toFixed(1)}`;
  return `${Number(value.value).toFixed(1)}${esd} ${value.unit}`;
}

/** `{ text, tooltip }` for one phase-details row; tooltip is null for a refined size. */
export function crystalliteSizeCell(row) {
  if (isFailed(row)) return FAILED; // "Not resolvable" is only for phases that did not fail
  if (row?.grain_sizes?.length) {
    return { text: row.grain_sizes.map(formatGrainSize).join('; '), tooltip: null };
  }
  // results stored before the status existed (old version summaries) fall back to a dash
  return STATUS_TEXT[row?.crystallite_size_status] || { text: '—', tooltip: null };
}

const STRAIN_TEXT = {
  not_refined: { text: 'Not refined', tooltip: 'Microstrain broadening was not refined.' },
  not_detected: { text: 'Not detected', tooltip: 'No strain broadening detected (refined to zero).' },
  at_limit: { text: 'At refinement limit', tooltip: 'Microstrain reached the upper bound of the refinement window; it could not be determined.' },
};

/**
 * Microstrain (%) cell: `{ text, tooltip, note }`. `note` is the server's reason sentence when size and
 * strain cannot be separated (the table shows it as an info icon between the two cells).
 */
export function microstrainCell(row) {
  if (isFailed(row)) return { ...FAILED, note: null };
  const info = row?.size_strain;
  if (!info) return { text: '—', tooltip: null, note: null };
  const note = info.separable === false ? info.note || null : null;
  const strain = info.microstrain_percent;
  const state = info.strain_state;
  if (STRAIN_TEXT[state]) return { ...STRAIN_TEXT[state], note };
  if (!Number.isFinite(Number(strain?.value))) return { text: '—', tooltip: null, note };
  const esd = strain.esd == null ? '' : ` ± ${Number(strain.esd).toFixed(3)}`;
  return {
    text: `${Number(strain.value).toFixed(3)}${esd}`,
    tooltip: state === 'poorly_determined' ? 'Poorly determined: the e.s.d. exceeds half of the value.' : null,
    note,
  };
}
