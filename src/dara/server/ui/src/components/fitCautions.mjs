import { distinctPhaseLabel, formatSymbolsInText } from './versions.mjs';

const LEVEL_ORDER = { warning: 0, info: 1 };

// "(A, B, GrainSize(1,1,1), k1 undefined)": BGMN parameter names, one level of nested brackets.
const UNDEFINED_LIST = / \(((?:[^()]|\([^()]*\))*) undefined\)/;

/**
 * Server prose without the raw list of BGMN parameters that came out undefined:
 * `{text, parameters}` (`parameters` null when there was none), for a tooltip or details line.
 */
export function splitUndefinedParameters(text) {
  const value = String(text ?? '');
  const match = UNDEFINED_LIST.exec(value);
  return match ? { text: value.replace(UNDEFINED_LIST, ''), parameters: match[1] } : { text: value, parameters: null };
}

/**
 * Caution or reason text as the page shows it. `fit` = `{phases, symbolOf, symbols, details, confidence}`
 * of the fit it describes: space groups in the one short format, a phase whose formula and space group
 * repeat named with its database record, and the one cause of an uncertain weight when the payload
 * states it. Returns `{text, parameters}` (see `splitUndefinedParameters`).
 */
export function cautionDisplay(item, fit = {}) {
  const { phases = [], symbolOf = () => null, symbols = [], details = {}, confidence = {} } = fit;
  let text = formatSymbolsInText(item?.message ?? item, symbols);
  const phase = item?.phase;
  const label = phase ? distinctPhaseLabel(phase, phases, symbolOf) : null;
  const sibling = phase ? confidence?.[phase]?.evidence?.sibling : null;
  if (item?.code === 'duplicate_phase' && sibling?.phase && (phases || []).includes(sibling.phase)) {
    text = `${label}: same compound as ${distinctPhaseLabel(sibling.phase, phases, symbolOf)}; the split of the amount between the two entries is not reliable.`;
  } else if (item?.code === 'weight_uncertain' && phase) {
    const fixedLattice = (details?.[phase]?.refinement_status?.codes || []).includes('lattice_fixed');
    const level = confidence?.[phase]?.level;
    const causes = [level === 'ambiguous' ? 'the identification is ambiguous' : level === 'weak' ? 'the evidence for the phase is weak' : null,
      fixedLattice ? 'the lattice was held at the database values' : null].filter(Boolean);
    if (causes.length) text = `${label}: the weight fraction is uncertain because ${causes.join(' and ')}.`;
  } else if (label) {
    // The server names the phase by formula and space group; add the record where that repeats.
    const plain = label.split(' · ').slice(0, 2).join(' · ');
    if (label !== plain && text.startsWith(`${plain}:`)) text = `${label}${text.slice(plain.length)}`;
  }
  return splitUndefinedParameters(text);
}

/**
 * Phases the cautions of one fit propose to remove, each once: a phase whose refinement failed, a
 * phase below the detection limit or not above its e.s.d., and the lighter entry of two database
 * records of one compound. `weights` = `{name: wt%}`, `confidence` = `confidence.phases`.
 * Returns `[{phase, reason, codes}]` in caution order; nothing is proposed beyond what a caution states.
 */
export function cautionRemedies(cautions, { weights = {}, confidence = {}, phases = [] } = {}) {
  const out = new Map();
  const wt = name => (Number.isFinite(Number(weights?.[name])) ? Number(weights[name]) : null);
  const add = (phase, code, reason) => {
    if (!phase || !(phases || []).includes(phase)) return;
    const entry = out.get(phase) || { phase, reasons: [], codes: [] };
    if (!entry.codes.includes(code)) { entry.codes.push(code); entry.reasons.push(reason); }
    out.set(phase, entry);
  };
  sortCautions(cautions).forEach(item => {
    if (item.code === 'refinement_failed') add(item.phase, item.code, 'refinement failed');
    if (item.code === 'insignificant_phase') {
      const value = wt(item.phase);
      const below = /below 1 wt%/.test(item.message || '');
      add(item.phase, item.code, `${value === null ? '' : `${value.toFixed(2)} wt%, `}${below ? 'below the detection limit' : 'not clearly above its e.s.d.'}`);
    }
    if (item.code === 'duplicate_phase') {
      const other = confidence?.[item.phase]?.evidence?.sibling?.phase;
      if (!other || !(phases || []).includes(other)) return;
      // Keep the heavier record of the compound; the lighter one carries the unreliable split.
      const [lighter, heavier] = (wt(item.phase) ?? 0) <= (wt(other) ?? 0) ? [item.phase, other] : [other, item.phase];
      add(lighter, item.code, { duplicateOf: heavier });
    }
  });
  return [...out.values()].map(entry => ({ phase: entry.phase, codes: entry.codes, reasons: entry.reasons }));
}

/** The remedy that removes the phase a caution names (`cautionRemedies` entry), or null. */
export function remedyForCaution(item, remedies = [], confidence = {}) {
  if (!item?.phase) return null;
  const direct = remedies.find(entry => entry.phase === item.phase && entry.codes.includes(item.code));
  if (direct) return direct;
  if (item.code === 'duplicate_phase') {
    const other = confidence?.[item.phase]?.evidence?.sibling?.phase;
    return remedies.find(entry => entry.phase === other && entry.codes.includes('duplicate_phase')) || null;
  }
  return null;
}

/** Warnings first, otherwise keep the server's check order. */
export function sortCautions(cautions) {
  return (Array.isArray(cautions) ? cautions : [])
    .map((item, index) => ({ item, index }))
    .sort((a, b) => (LEVEL_ORDER[a.item.level] ?? 2) - (LEVEL_ORDER[b.item.level] ?? 2) || a.index - b.index)
    .map(({ item }) => item);
}

/** Counts used by the candidate table; `null` when the server sent no field (older builds). */
export function countCautions(cautions) {
  if (!Array.isArray(cautions)) return null;
  const warnings = cautions.filter(item => item.level === 'warning').length;
  return { total: cautions.length, warnings, info: cautions.length - warnings };
}

export function cautionSortValue(cautions) {
  const counts = countCautions(cautions);
  // Candidates without the field sort after those with one.
  return counts === null ? Infinity : counts.warnings * 1000 + counts.total;
}

export function cautionTagColor(cautions) {
  const counts = countCautions(cautions);
  return counts && counts.warnings > 0 ? 'orange' : 'default';
}

export function cautionTooltip(cautions) {
  return sortCautions(cautions).map(item => item.message).join('\n');
}

// Unexplained peaks, instrument lines and the mass balance are already stated by the confidence banner.
const BANNER_CODES = new Set(['missing_peaks_remain', 'minor_unexplained_peaks', 'instrument_lines', 'mass_balance']);

/**
 * Cautions to list next to a confidence banner: the ones the banner does not already state.
 * `bannerReasons` (its bullet texts) also drops the high-residual caution when it names GOF or Rwp.
 */
export function cautionsBesideBanner(cautions, hasBanner = true, bannerReasons = []) {
  const items = sortCautions(cautions);
  if (!hasBanner) return items;
  const statesResiduals = bannerReasons.some(text => /^(GOF|Rwp) is /.test(String(text)));
  return items.filter(item => !BANNER_CODES.has(item.code) && !(statesResiduals && item.code === 'poor_fit'));
}
