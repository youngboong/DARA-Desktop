import assert from 'node:assert/strict';
import { cautionDisplay, cautionRemedies, remedyForCaution, splitUndefinedParameters } from '../src/dara/server/ui/src/components/fitCautions.mjs';
import { preciseRwp, reasonsText } from '../src/dara/server/ui/src/components/confidence.mjs';
import { listContext, positionText, resultsHref, seriesOf, taskHref } from '../src/dara/server/ui/src/components/sampleNav.mjs';
import { fillKeys } from '../src/dara/server/ui/src/components/composition.mjs';
import { spacegroupOfPhase } from '../src/dara/server/ui/src/components/versions.mjs';

// Task 43 v1 (Li2CO3 + ZrO2): the cautions and confidence as the API returns them.
const P = ['ZrO2_14_(cod_1010912)-0', 'ZrO2_14_(cod_2300296)-0', 'Li2CO3_15_(cod_2310703)-0', 'ZrO2_57_(cod_9005834)-None', 'LiO8_8_(mp_mp-1235059)-0'];
const weights = { [P[0]]: 1.2422, [P[1]]: 10.1557, [P[2]]: 88.1959, [P[3]]: 0.4062, [P[4]]: 0 };
const symbols = ['P12_1/c1', 'C12/c1', 'Pbcm', 'C1m1'];
const symbolOf = name => ({ 14: 'P12_1/c1', 15: 'C12/c1', 57: 'Pbcm', 8: 'C1m1' })[spacegroupOfPhase(name)];
const confidence = {
  [P[0]]: { level: 'weak', evidence: { sibling: { kind: 'duplicate', phase: P[1] } } },
  [P[1]]: { level: 'ambiguous', evidence: { sibling: { kind: 'duplicate', phase: P[0] } } },
  [P[3]]: { level: 'weak', evidence: { sibling: { kind: 'polymorph', phase: P[0] } } },
};
const cautions = [
  { code: 'insignificant_phase', level: 'warning', phase: P[3], message: 'ZrO2 · Pbcm: 0.41 wt% (± 0.03) is below 1 wt%, near the typical detection limit of laboratory XRD, so its presence may be hard to confirm.' },
  { code: 'insignificant_phase', level: 'warning', phase: P[4], message: 'LiO8 · C1m1: 0.00 wt% is below 1 wt%, near the typical detection limit of laboratory XRD, so its presence may be hard to confirm.' },
  { code: 'refinement_failed', level: 'warning', phase: P[4], message: 'LiO8 · C1m1: refinement failed (A, B, B1, BETA, C, GrainSize(1,1,1), k1, k2 undefined); its amount is not reliable.' },
  { code: 'duplicate_phase', level: 'warning', phase: P[0], message: 'ZrO2 · P121/c1: Same compound as ZrO2 · P121/c1 (P121/c1); the split of its amount between the two entries is not reliable.' },
  { code: 'duplicate_phase', level: 'warning', phase: P[1], message: 'ZrO2 · P121/c1: Same compound as ZrO2 · P121/c1 (P121/c1); the split of its amount between the two entries is not reliable.' },
  { code: 'lattice_at_bound', level: 'warning', phase: P[0], message: 'ZrO2 · P121/c1: lattice parameter a, beta ended within 10% of its refinement limit; the reference structure may not match.' },
  { code: 'correlated_residuals', level: 'info', phase: null, message: 'Durbin–Watson 0.65 < 1 indicates strongly correlated residuals; esd values are likely underestimated.' },
];

// One remedy per phase, only what the cautions state: the failed and the sub-detection phases, the lighter duplicate.
const remedies = cautionRemedies(cautions, { weights, confidence, phases: P });
assert.deepEqual(remedies.map(entry => [entry.phase, entry.codes]), [
  [P[3], ['insignificant_phase']], [P[4], ['insignificant_phase', 'refinement_failed']], [P[0], ['duplicate_phase']]]);
assert.equal(remedies[0].reasons[0], '0.41 wt%, below the detection limit');
assert.deepEqual(remedies[2].reasons[0], { duplicateOf: P[1] });
assert.equal(remedyForCaution(cautions[4], remedies, confidence).phase, P[0], 'the caution of the heavier entry removes the lighter one');
assert.equal(remedyForCaution(cautions[3], remedies, confidence), remedies[2]);
assert.equal(remedyForCaution(cautions[5], remedies, confidence), null, 'a lattice at its limit has no remedy of its own');
assert.equal(remedyForCaution(cautions[6], remedies, confidence), null);
assert.deepEqual(cautionRemedies(cautions, { weights, confidence, phases: [P[2]] }), [], 'phases outside the fit are never proposed');

// Caution text: one space-group format, the database record where formula + space group repeat, no raw BGMN list.
const fit = { phases: P, symbolOf, symbols, confidence, details: {} };
assert.equal(cautionDisplay(cautions[3], fit).text,
  'ZrO2 · P2₁/c · COD 1010912: same compound as ZrO2 · P2₁/c · COD 2300296; the split of the amount between the two entries is not reliable.');
assert.deepEqual(cautionDisplay(cautions[2], fit), { text: 'LiO8 · Cm: refinement failed; its amount is not reliable.', parameters: 'A, B, B1, BETA, C, GrainSize(1,1,1), k1, k2' });
assert.equal(cautionDisplay(cautions[5], fit).text.startsWith('ZrO2 · P2₁/c · COD 1010912: lattice parameter a, beta'), true);
assert.equal(cautionDisplay(cautions[0], fit).text.startsWith('ZrO2 · Pbcm: 0.41 wt%'), true);
const uncertain = { code: 'weight_uncertain', phase: P[2], message: 'Li2CO3 · C12/c1: the weight fraction is uncertain (ambiguous identification or lattice held at the database values).' };
assert.equal(cautionDisplay(uncertain, { ...fit, details: { [P[2]]: { refinement_status: { codes: ['lattice_fixed'] } } } }).text,
  'Li2CO3 · C2/c: the weight fraction is uncertain because the lattice was held at the database values.');
assert.equal(cautionDisplay(uncertain, { ...fit, confidence: { [P[2]]: { level: 'ambiguous' } } }).text,
  'Li2CO3 · C2/c: the weight fraction is uncertain because the identification is ambiguous.');
assert.equal(cautionDisplay(uncertain, fit).text, 'Li2CO3 · C2/c: the weight fraction is uncertain (ambiguous identification or lattice held at the database values).',
  'kept when the payload does not say which');
assert.deepEqual(splitUndefinedParameters('Refinement of X failed (A, k1 undefined); its amount is not reliable.'),
  { text: 'Refinement of X failed; its amount is not reliable.', parameters: 'A, k1' });

// Verdict reasons carry the two decimals of the tables (task 41 v3, Fe2O3: 2.65 vs 2.64).
const fe2o3 = { reasons: ['Present in 1 of 6 candidate models.', 'Candidate 1 without this phase fits almost as well (Rwp 2.6% vs 2.6%).'],
  evidence: { alternative: { rwp: 2.65, delta_rwp: 0.009999999999999787 } } };
assert.deepEqual(reasonsText(fe2o3), ['Present in 1 of 6 candidate models.', 'Candidate 1 without this phase fits almost as well (Rwp 2.65% vs 2.64%).']);
assert.equal(preciseRwp('Candidate 3 fits almost as well (Rwp 2.8% vs 2.6%) with a different polymorph of this phase.', { structure_model_alternative: { rwp: 2.83, delta_rwp: 0.19 } }),
  'Candidate 3 fits almost as well (Rwp 2.83% vs 2.64%) with a different polymorph of this phase.');
assert.equal(preciseRwp('Ambiguous evidence for X (Rwp 2.6% vs 2.6%).', {}), 'Ambiguous evidence for X (Rwp 2.6% vs 2.6%).');

// Sample navigation contract: ?id=41&list=38,41,44&back=<encoded Results query>.
const context = listContext(`?id=41&list=38,41,44&back=${encodeURIComponent('page=2&status=COMPLETED')}&fit=best`, '41');
assert.deepEqual([context.prev, context.next, context.index], [38, 44, 1]);
assert.equal(resultsHref(context), '/results?page=2&status=COMPLETED');
assert.equal(taskHref(44, context), '/task/?id=44&list=38,41,44&back=page%3D2%26status%3DCOMPLETED');
assert.equal(positionText(context), 'Sample 2 of 3');
const plain = listContext('?id=41', '41');
assert.deepEqual([plain.prev, plain.next, resultsHref(plain), positionText(plain)], [null, null, '/results', null]);
const edge = listContext('?id=44&list=38,41,44,x,-1', '44');
assert.deepEqual([edge.ids, edge.prev, edge.next], [[38, 41, 44], 41, null]);
assert.equal(listContext('?id=9&list=38,41', '9').prev, null, 'a task outside the list has no neighbours');
assert.equal(resultsHref(listContext('?back=javascript:alert(1)', '1')), '/results?javascript%3Aalert%281%29=', 'back stays a query string');
assert.equal(seriesOf(listContext(`?id=3&back=${encodeURIComponent('from=series&id=s-1')}`, '3')), 's-1', 'opened from a series');
assert.equal(seriesOf(context), null);

// Duplicates never share a tray fill; a compound keeps one fill across fits.
const keys = fillKeys([{ phases: P, weights }, { phases: [P[1], P[2]], weights }], name => `${name.split('_')[0]}:${spacegroupOfPhase(name)}`);
assert.equal(keys.get(P[1]), 'ZrO2:14');
assert.equal(keys.get(P[0]), `ZrO2:14|${P[0]}`);
assert.equal(keys.get(P[3]), 'ZrO2:57');

console.log('task page helpers ok');

// Copy phase table (TSV) and the controls-only version label.
import { controlsLabel, phaseTableTsv } from '../src/dara/server/ui/src/components/versions.mjs';
{
  const details = { [P[0]]: { weight_percent: 1.2422, weight_esd_percent: 0.052 }, [P[1]]: { weight_percent: 10.1557, weight_esd_percent: 0.059 },
    [P[2]]: { weight_percent: 88.1959, weight_esd_percent: 0.076 }, [P[4]]: { le_bail: true } };
  assert.equal(phaseTableTsv([P[0], P[1], P[2], P[4]], { details, symbolOf }), [
    'Phase\tSpace group\twt%\t± e.s.d.',
    'ZrO2 COD 1010912\tP2₁/c\t1.24\t0.05',
    'ZrO2 COD 2300296\tP2₁/c\t10.16\t0.06',
    'Li2CO3\tC2/c\t88.20\t0.08',
    'LiO8\tCm\tLe Bail\t',
  ].join('\n'));
  assert.equal(controlsLabel({ lattice: 'refine', lattice_percent: 2, orientation: 'SPHAR4', peak_width: 'inherit' }),
    'Controls: preferred orientation SPHAR4, lattice ±2 %');
  assert.equal(controlsLabel({ lattice: 'inherit', orientation: 'inherit', peak_width: 'inherit' }), null);
  console.log('phase table and controls label ok');
}
