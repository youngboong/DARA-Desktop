// Pure helpers of the structure refinement drawer (design 2.6): parameter count, request body,
// refusal wording and the result rows. The server applies the same rules (R1-R9) on submit.

export const MAX_PARAMETERS = 12;

/** Element with the largest occupancy on a site (the first one on a tie, as the server does). */
export function primaryElement(site) {
  let best = null;
  for (const item of site?.species || []) if (!best || item.occupancy > best.occupancy) best = item;
  return best?.element ?? null;
}

/** Free parameters of a draft `{positions, thermal, occupancy}` on `sites`. */
export function countParameters(draft, sites) {
  const bySite = new Map((sites || []).map(site => [site.index, site]));
  const coordinates = [...new Set(draft?.positions || [])].reduce((sum, index) => sum + (bySite.get(index)?.free?.length || 0), 0);
  let thermal = 0;
  if (draft?.thermal === 'overall') thermal = 1;
  else if (draft?.thermal === 'per_element') thermal = new Set((sites || []).map(primaryElement).filter(Boolean)).size;
  return coordinates + thermal + (draft?.occupancy?.length || 0);
}

/** "5 of 12 allowed"; an unknown limit shows the count alone. */
export function parameterText(count, maxParameters) {
  return Number.isFinite(maxParameters) ? `${count} of ${maxParameters} allowed` : `${count}`;
}

const KIND_FIELDS = {
  exchange: ['sites', 'elements'],
  substitution: ['site', 'host', 'dopant'],
  vacancy: ['site', 'element'],
};
const ELEMENT = /^[A-Z][a-z]?$/;

/** Whether a constraint row is filled in completely (the server rejects anything else). */
export function constraintComplete(item) {
  const fields = KIND_FIELDS[item?.kind];
  if (!fields) return false;
  return fields.every(name => {
    const value = item[name];
    if (name === 'sites') return Array.isArray(value) && value.length === 2 && value.every(Number.isInteger) && value[0] !== value[1];
    if (name === 'elements') return Array.isArray(value) && value.length === 2 && value.every(e => ELEMENT.test(e || ''));
    if (name === 'site') return Number.isInteger(value);
    return ELEMENT.test(value || '');
  });
}

export function constraintText(item, sites = []) {
  const label = index => sites.find(site => site.index === index)?.label ?? `site ${index}`;
  if (item.kind === 'exchange') {
    const [first, second] = item.sites || [];
    const [a, b] = item.elements || [];
    return `${a} on ${label(first)} exchanges with ${b} on ${label(second)}`;
  }
  if (item.kind === 'substitution') return `${item.dopant} replaces ${item.host} on ${label(item.site)}`;
  return `${item.element} vacancy on ${label(item.site)}`;
}

/** Request body for POST /task/{id}/quick-refine (`origin: "structure"`); the server names the version. */
export function structureRequest(base, phase, draft) {
  return {
    base,
    origin: 'structure',
    structure: {
      phase,
      positions: [...new Set(draft.positions || [])].sort((a, b) => a - b),
      thermal: draft.thermal || 'fixed',
      occupancy: (draft.occupancy || []).filter(constraintComplete).map(item => {
        const copy = {};
        for (const name of ['kind', ...KIND_FIELDS[item.kind]]) copy[name] = item[name];
        return copy;
      }),
    },
  };
}

export const isEmptyDraft = draft => !draft?.positions?.length && (draft?.thermal || 'fixed') === 'fixed' && !draft?.occupancy?.length;

// What to do next for each server refusal code (design 2.6, rules R1-R9); the server message states what and why.
const PROCEED = {
  lattice_not_stable: 'Refine the lattice of this phase first (Refine all phases), then try again.',
  minor_phase: 'Choose a phase with at least 5 wt%.',
  subgroup_setting: 'Choose a phase that is refined in its database space group.',
  too_many_parameters: 'Untick positions or remove a constraint until the count fits.',
  hydrogen: 'Leave hydrogen sites and hydrogen constraints out.',
  low_contrast: 'Pick two elements that differ more in atomic number, or drop this constraint.',
  poor_base_fit: 'Improve the phase model first, for example with Add phase.',
  occupancy_minor: 'Use a phase with at least 10 wt%, or refine positions only.',
  bad_site: 'Check the site and element choices.',
  unsupported_structure: 'This phase can only be refined without structure parameters.',
};

/** `{message, hint}` of one refusal `{code, message}`. */
export function refusalText(refusal) {
  return { message: refusal?.message || 'The structure refinement was refused.', hint: PROCEED[refusal?.code] || '' };
}

/** One line for tooltips and alerts: "<message> <hint>". */
export function refusalLine(refusal) {
  const { message, hint } = refusalText(refusal);
  return hint ? `${message} ${hint}` : message;
}

function numberText(item, digits = 4) {
  if (!item || !Number.isFinite(Number(item.value))) return '—';
  const value = Number(item.value);
  const esd = item.esd == null ? null : Math.abs(Number(item.esd));
  if (esd && esd > 0) {
    const decimals = Math.max(0, Math.min(8, -Math.floor(Math.log10(esd)) + 1));
    return `${value.toFixed(decimals)} ± ${esd.toFixed(decimals)}`;
  }
  return value.toFixed(digits);
}

/** Rows of the Structure tab for one phase's `structure_refinement`; every value carries its state. */
export function structureResultRows(result) {
  return (result?.sites || []).map(site => ({
    key: site.index,
    label: site.label,
    refined: Boolean(site.refined_positions),
    xyz: (site.xyz || []).map(item => ({ text: numberText(item), state: item?.state || (item?.esd == null ? 'fixed' : 'refined') })),
    species: (site.species || []).map(item => ({
      element: item.element,
      text: numberText(item.occupancy),
      refined: item.occupancy?.esd != null,
    })),
    b: site.b_iso ? { text: numberText(site.b_iso, 3), state: site.b_iso.state || 'fixed' } : null,
  }));
}

/** Phases of a summary's `phase_details` that carry a structure result: `[[phase, result], ...]`. */
export function structurePhases(details) {
  return Object.entries(details || {}).filter(([, detail]) => detail?.structure_refinement?.sites?.length).map(([phase, detail]) => [phase, detail.structure_refinement]);
}

/** Reader-facing name of a refined structure parameter (`occ0` -> "Occupancy 1", `Ball` -> "B (all atoms)"). */
export function parameterLabel(name) {
  const occupancy = /^occ(\d+)$/.exec(name);
  if (occupancy) return `occupancy ${Number(occupancy[1]) + 1}`;
  if (name === 'Ball') return 'B (all atoms)';
  const element = /^B([A-Z]{1,2})$/.exec(name);
  if (element) return `B (${element[1][0]}${element[1].slice(1).toLowerCase()})`;
  return name;
}
