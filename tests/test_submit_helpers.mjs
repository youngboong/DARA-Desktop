import assert from 'node:assert/strict';
import { runAfterSuccess } from '../src/dara/server/ui/src/components/submitHelpers.mjs';

// every step runs even if an earlier one throws, and nothing propagates
{
  const calls = [];
  const logged = [];
  const boom = new TypeError('n is not a function');
  const errors = runAfterSuccess([
    () => calls.push('reset'),
    () => { throw boom; },
    () => calls.push('notify'),
  ], (...args) => logged.push(args));
  assert.deepEqual(calls, ['reset', 'notify']);
  assert.deepEqual(errors, [boom]);
  assert.equal(logged.length, 1);
  assert.equal(logged[0][1], boom);
}

// no failures -> no errors, nothing logged
{
  const logged = [];
  assert.deepEqual(runAfterSuccess([() => {}, () => {}], (...a) => logged.push(a)), []);
  assert.equal(logged.length, 0);
}

// a throwing logger does not break the helper
assert.equal(runAfterSuccess([() => { throw new Error('x'); }], () => { throw new Error('log'); }).length, 1);

console.log('submit helpers ok');

{
  const { REMEMBERED_FIELDS, pickRemembered, readRemembered, writeRemembered, forgetRemembered } = await import('../src/dara/server/ui/src/components/submitHelpers.mjs');
  const store = new Map();
  const storage = { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k) };
  const values = { user: 'Y.An', analysis_mode: 'reaction', temperature: 900, elements: ['Fe', 'O'],
    precursor_formulas: 'Fe2O3', instrument_profile: 'Aeris', wavelength_lab: 'Cu', two_theta_min: 10 };
  assert.ok(writeRemembered(storage, values, { lattice: 'fixed' }));
  const saved = readRemembered(storage);
  assert.deepEqual(saved.fields, { user: 'Y.An', analysis_mode: 'reaction', temperature: 900, instrument_profile: 'Aeris', wavelength_lab: 'Cu' });
  assert.deepEqual(saved.controls, { lattice: 'fixed' });
  assert.ok(!('elements' in saved.fields) && !('two_theta_min' in saved.fields), 'sample-specific inputs are not kept');
  assert.ok(REMEMBERED_FIELDS.includes('user'));
  assert.deepEqual(pickRemembered(null).fields, {});
  store.set('dara.submitDefaults.v1', '{broken');
  assert.equal(readRemembered(storage), null);
  forgetRemembered(storage);
  assert.equal(readRemembered(storage), null);
  assert.equal(readRemembered({ getItem: () => { throw new Error('blocked'); } }), null);
  assert.equal(writeRemembered({ setItem: () => { throw new Error('quota'); } }, values), false);
  console.log('remembered submit settings ok');
}

// advanced options: only real differences from the defaults are reported
{
  const { advancedChanges, ADVANCED_DEFAULTS } = await import('../src/dara/server/ui/src/components/submitHelpers.mjs');
  const controls = { lattice: 'inherit', lattice_percent: 2, orientation: 'inherit', peak_width: 'inherit' };
  assert.deepEqual(advancedChanges({}, controls, controls), []);
  assert.deepEqual(advancedChanges(null), []);
  assert.deepEqual(advancedChanges({ ...ADVANCED_DEFAULTS, library_cif_ids: [], additional_phases: [], reuse_additional_cifs: true }, { ...controls }, controls), []);
  assert.deepEqual(advancedChanges({ max_analysis_minutes: undefined, screening_timeout_s: undefined }), []);
  assert.deepEqual(advancedChanges({ screening_timeout_s: null }), ['screening limit']);
  assert.deepEqual(advancedChanges({ use_mp: false, max_analysis_minutes: 30 }), ['MP', 'time limit']);
  assert.deepEqual(advancedChanges({ mp_e_hull_cutoff: 0.1, candidate_prefilter: false }), ['E-hull cutoff', 'pre-filter']);
  assert.deepEqual(advancedChanges({}, { ...controls, orientation: 'SPHAR4' }, controls), ['refinement controls']);
  assert.deepEqual(advancedChanges({ library_cif_ids: ['a'], additional_phases: [{}], reuse_additional_cifs: false }),
    ['library CIFs', 'additional CIFs', 'saved CIFs not reused']);
  console.log('advanced changes ok');
}

import { checkElements, previewShape, readRecentElements, rememberElements, unrecognisedTokens } from '../src/dara/server/ui/src/components/submitHelpers.mjs';

// element entry: case normalised where the reading is unique, unknown entries named
{
  const typed = checkElements('ti zn,O; Xx');
  assert.deepEqual(typed.symbols, ['Ti', 'Zn', 'O']);
  assert.deepEqual(typed.invalid, ['Xx']);
  assert.equal(typed.text, 'Ti, Zn, O, Xx');
  assert.equal(typed.error, 'Not an element symbol: Xx');
  assert.deepEqual(checkElements('FE, ZN, o, Fe').symbols, ['Fe', 'Zn', 'O'], 'all-caps with one reading, duplicates once');
  assert.equal(checkElements('CO, Zn').error, 'Not an element symbol: CO (Co, or C and O?)');
  assert.equal(checkElements('').error, null);
  assert.deepEqual(checkElements(' , ').symbols, []);
}

// recent element sets: distinct sets, most recent first, at most 5, corrupt storage ignored
{
  const store = new Map();
  const storage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  rememberElements(storage, ['Co', 'O', 'Zn']);
  rememberElements(storage, ['Bi', 'Mo', 'O']);
  assert.deepEqual(rememberElements(storage, ['Zn', 'Co', 'O']), [['Zn', 'Co', 'O'], ['Bi', 'Mo', 'O']]);
  ['H', 'He', 'Li', 'Be', 'B'].forEach(symbol => rememberElements(storage, [symbol]));
  assert.equal(readRecentElements(storage).length, 5);
  assert.equal(rememberElements(storage, ['Xx']).length, 5, 'invalid sets are not stored');
  assert.deepEqual(readRecentElements({ getItem: () => '{bad' }), []);
  assert.deepEqual(readRecentElements({ getItem: () => { throw new Error('denied'); } }), []);
  assert.deepEqual(readRecentElements(null), []);
}

// file-name tokens the chemistry did not use
assert.deepEqual(unrecognisedTokens('SnO2_Pt_cat.xrdml', { formulas: ['SnO2'], elements: ['Sn', 'O'] }), ['Pt', 'cat']);
assert.deepEqual(unrecognisedTokens('CoO-ZnO_1100C_60min.xrdml', { formulas: ['CoO', 'ZnO'] }), []);
assert.deepEqual(unrecognisedTokens('Bi2O3-2MoO3_400C_60min.xrdml', { formulas: ['Bi2O3', 'MoO3'] }), []);
assert.deepEqual(unrecognisedTokens('C:/data/NCM811_2h.xy', { formulas: [] }), ['NCM811']);

// pattern preview geometry: line through the box, shaded range limits and excluded intervals
{
  const shape = previewShape({ two_theta: [10, 20, 30], intensity: [0, 10, 5] }, { width: 100, height: 10, start: 15, excluded: [[25, 40]] });
  assert.equal(shape.d, 'M0.0 9.0L50.0 1.0L100.0 5.0');
  assert.deepEqual(shape.shades, [{ x: 0, width: 25 }, { x: 75, width: 25 }]);
  assert.equal(previewShape({ two_theta: [10], intensity: [1] }), null);
  assert.equal(previewShape(null), null);
}

console.log('element entry, recent sets, file-name tokens and preview ok');

// bounded concurrency: never more than n at a time, every result delivered, a failure stays its own
{
  const { limiter } = await import('../src/dara/server/ui/src/components/submitHelpers.mjs');
  const run = limiter(2);
  let active = 0;
  let peak = 0;
  const job = (value, fail = false) => () => new Promise((resolve, reject) => {
    active += 1; peak = Math.max(peak, active);
    setTimeout(() => { active -= 1; (fail ? reject : resolve)(fail ? new Error(String(value)) : value); }, 5);
  });
  const results = await Promise.allSettled([1, 2, 3, 4, 5].map(value => run(job(value, value === 3))));
  assert.equal(peak, 2);
  assert.deepEqual(results.map(item => item.status), ['fulfilled', 'fulfilled', 'rejected', 'fulfilled', 'fulfilled']);
  assert.equal(results[4].value, 5);
  console.log('limiter ok');
}
