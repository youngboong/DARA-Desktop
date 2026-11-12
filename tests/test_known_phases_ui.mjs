import assert from 'node:assert/strict';
import {
  knownPhaseNotes, mpFallbackNotes, phaseSetIdFromSearch, phaseSetLabel, phaseSetUrl, withoutPhaseSet,
} from '../src/dara/server/ui/src/components/knownPhases.mjs';

assert.equal(phaseSetIdFromSearch('?phase_set=12&series=1'), 12);
assert.equal(phaseSetIdFromSearch('?phase_set=0'), null);
assert.equal(phaseSetIdFromSearch('?phase_set=abc'), null);
assert.equal(phaseSetIdFromSearch(''), null);
assert.equal(phaseSetUrl(12), '/?phase_set=12');
assert.equal(phaseSetUrl(12, true), '/?phase_set=12&series=1');
assert.equal(withoutPhaseSet('?phase_set=12&series=1'), '?series=1');
assert.equal(withoutPhaseSet('?phase_set=12'), '');

assert.equal(
  phaseSetLabel(12, [{ formula: 'ZrO2', spacegroup: 'P2_1/c' }, { formula: 'Li2CO3' }]),
  'Phases from Task 12: ZrO2 · P2_1/c, Li2CO3',
);

assert.deepEqual(knownPhaseNotes([
  { formula: 'Li2CO3', status: 'refined', weight_percent: 80 },
  { formula: 'ZrO2', status: 'not_detected', removed_weight_percent: 0.2, reason: 'negligible' },
  { formula: 'La(OH)3', status: 'no_structure' },
  { formula: 'NaCl', status: 'not_detected', removed_weight_percent: null },
  { formula: 'ZnO', status: 'not_detected', removed_weight_percent: 0.17, reason: 'no_peak_evidence' },
  { formula: 'KCl', status: 'not_detected', removed_weight_percent: null, reason: 'failed' },
  { formula: 'LiCl', status: 'not_detected', removed_weight_percent: null, reason: 'no_fit' },
  { formula: 'MgO', status: 'not_detected', removed_weight_percent: null, reason: 'not_checked' },
  { formula: 'ZrO2', status: 'not_detected', removed_weight_percent: 0.3, reason: 'removed_no_peak_evidence' },
  { formula: 'ZnO', status: 'not_detected', removed_weight_percent: null, reason: 'no_unexplained_peaks' },
]), [
  'ZrO2: not detected (refined to 0.2 wt%, removed)',
  'La(OH)3: no reference structure found (enable Materials Project or add a CIF)',
  'NaCl: not detected',
  'ZnO: not detected (a trial fit gave 0.17 wt% but explained no additional peak)',
  'KCl: not detected (a trial fit with it failed)',
  'LiCl: not detected (no phase combination fitted the pattern)',
  'MgO: not checked (the time limit was reached)',
  'ZrO2: not detected (refined to 0.3 wt%, but removing it left no additional peak unexplained)',
  'ZnO: not detected (the fit leaves no unexplained peak for it to explain)',
]);
assert.deepEqual(knownPhaseNotes(undefined), []);
assert.deepEqual(mpFallbackNotes(['La(OH)3']), ['La(OH)3: structure taken from Materials Project (no COD structure)']);
assert.deepEqual(mpFallbackNotes(null), []);

console.log('known phases ui ok');
