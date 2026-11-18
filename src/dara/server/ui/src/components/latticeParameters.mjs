const GREEK_NAMES = { alpha: 'α', beta: 'β', gamma: 'γ' };

export function crystalSystemFromSpaceGroup(spaceGroup) {
  if (!spaceGroup) return undefined;
  if (spaceGroup <= 2) return 'triclinic';
  if (spaceGroup <= 15) return 'monoclinic';
  if (spaceGroup <= 74) return 'orthorhombic';
  if (spaceGroup <= 142) return 'tetragonal';
  if (spaceGroup <= 167) return 'trigonal';
  if (spaceGroup <= 194) return 'hexagonal';
  return 'cubic';
}

function reported(names, parameters) {
  return names
    .filter(name => parameters?.[name])
    .map(name => ({ names: [name], parameter: parameters[name], constrained: false }));
}

function fixed(names, value) {
  return { names, parameter: { value, esd: null, unit: 'deg' }, constrained: true, fixed: true };
}

function equal(names, parameter) {
  return parameter ? { names, parameter, constrained: names.length > 1 } : null;
}

// Rhombohedral space groups (R3, R-3, R32, R3m, R3c, R-3m, R-3c): a CIF gives them on hexagonal
// axes (a = b, c, γ = 120°) or on rhombohedral axes (a = b = c, α = β = γ).
const RHOMBOHEDRAL = new Set([146, 148, 155, 160, 161, 166, 167]);

/** True when the reported angle is not 90°: the cell is on rhombohedral axes. */
function rhombohedralAxes(parameters) {
  const alpha = Number((parameters?.alpha || parameters?.beta || parameters?.gamma)?.value);
  return Number.isFinite(alpha) && Math.abs(alpha - 90) > 0.01;
}

/** Return conventional-cell rows while marking symmetry-derived equalities. */
export function latticeParameterRows(parameters = {}, spaceGroup) {
  // Angles alone say nothing: a phase without any axis length (old results) shows "—".
  if (!['a', 'b', 'c'].some(name => Number.isFinite(Number(parameters?.[name]?.value)))) return [];
  const system = crystalSystemFromSpaceGroup(Number(spaceGroup));
  const rows = [];
  const add = (...items) => rows.push(...items.filter(Boolean));

  if (system === 'cubic') {
    add(equal(['a', 'b', 'c'], parameters.a || parameters.b || parameters.c));
    add(fixed(['alpha', 'beta', 'gamma'], 90));
  } else if (system === 'tetragonal') {
    add(equal(['a', 'b'], parameters.a || parameters.b));
    add(...reported(['c'], parameters));
    add(fixed(['alpha', 'beta', 'gamma'], 90));
  } else if (RHOMBOHEDRAL.has(Number(spaceGroup)) && rhombohedralAxes(parameters)) {
    // An R group on rhombohedral axes (a = b = c, α = β = γ ≠ 90°): the angle comes from the data.
    add(equal(['a', 'b', 'c'], parameters.a || parameters.b || parameters.c));
    add(equal(['alpha', 'beta', 'gamma'], parameters.alpha || parameters.beta || parameters.gamma));
  } else if (system === 'trigonal' || system === 'hexagonal') {
    add(equal(['a', 'b'], parameters.a || parameters.b));
    add(...reported(['c'], parameters));
    add(fixed(['alpha', 'beta'], 90), fixed(['gamma'], 120));
  } else if (system === 'orthorhombic') {
    add(...reported(['a', 'b', 'c'], parameters));
    add(fixed(['alpha', 'beta', 'gamma'], 90));
  } else if (system === 'monoclinic') {
    add(...reported(['a', 'b', 'c'], parameters));
    add(fixed(['alpha', 'gamma'], 90));
    add(...reported(['beta'], parameters));
  } else {
    add(...reported(['a', 'b', 'c', 'alpha', 'beta', 'gamma'], parameters));
  }

  return rows.map(row => ({
    ...row,
    label: row.names.map(name => GREEK_NAMES[name] || name).join(' = '),
    state: row.parameter?.state ?? null,
  }));
}

/** Short state word after a lattice value (design 2.1 E): `null` for refined, dependent and symmetry-fixed values. */
export function latticeStateNote(row) {
  if (row?.state === 'at_limit') return 'at limit';
  if (row?.state === 'fixed' || row?.state === 'fixed_reference') return 'fixed (DB)';
  return null;
}

export function formatParameterWithEsd(parameter) {
  if (!parameter || !Number.isFinite(Number(parameter.value))) return null;
  const value = Number(parameter.value);
  const esd = parameter.esd == null ? null : Math.abs(Number(parameter.esd));
  let valueText;
  let esdText = null;

  if (Number.isFinite(esd) && esd > 0) {
    const decimals = Math.max(0, Math.min(8, -Math.floor(Math.log10(esd)) + 1));
    valueText = value.toFixed(decimals);
    esdText = esd.toFixed(decimals);
  } else {
    // No e.s.d. (held fixed, at a limit, set by symmetry): the value's own digits, not padded.
    valueText = String(Number(value.toFixed(5)));
  }

  const unitText = parameter.unit === 'deg' ? '°' : ` ${parameter.unit}`;
  return `${valueText}${esdText === null ? '' : ` ± ${esdText}`}${unitText}`;
}
