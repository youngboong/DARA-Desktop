// Status words of the phase table (design 2.1 A/E, 2.2): one word list, colours from statusTone.mjs.

export const STATUS_TAGS = {
  failed: { text: 'Refinement failed', tone: 'fail' },
  uncertain: { text: 'Uncertain amount', tone: 'review' },
  at_limit: { text: 'at limit', tone: 'review' },
  fixed: { text: 'fixed (DB)', tone: 'neutral' },
  indicative: { text: 'indicative', tone: 'info' },
  not_resolvable: { text: 'not resolvable', tone: 'neutral' },
};

/**
 * Tag beside the quantity of a phase, or `null`: "Refinement failed" (status level `failed` or weight flag
 * `unreliable`) wins over "Uncertain amount". Old responses without the fields give `null`.
 */
export function amountTag(detail, weightFlag) {
  const status = detail?.refinement_status;
  if (status?.level === 'failed' || weightFlag === 'unreliable') {
    return { ...STATUS_TAGS.failed, tooltip: status?.message || null };
  }
  if (weightFlag === 'uncertain') return { ...STATUS_TAGS.uncertain, tooltip: status?.message || null };
  return null;
}
