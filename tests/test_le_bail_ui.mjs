import assert from 'node:assert/strict';
import test from 'node:test';
import { deltaStrip, deltaText, leBailPhases, leBailRequest, weightRows } from '../src/dara/server/ui/src/components/versions.mjs';

const ZN = 'Zn1.7Co0.3O2_186_(cod_1533020)';
const CO = 'Co3O4_227_(cod_9005896)';
const v1 = { rwp: 1.73, weight_percent: { [ZN]: 49.6493, [CO]: 50.3507 }, phase_details: { [ZN]: {}, [CO]: {} } };
const v2 = { rwp: 1.6, weight_percent: { [ZN]: 100 }, weights_relative: true,
  phase_details: { [ZN]: { weight_percent: 100 }, [CO]: { le_bail: true, weight_percent: null } } };

test('Le Bail phases of a result, a version and a compare side', () => {
  assert.deepEqual(leBailPhases(v2), [CO]);
  assert.deepEqual(leBailPhases({ le_bail_phases: [CO] }), [CO]);
  assert.deepEqual(leBailPhases(v1), []);
  assert.deepEqual(leBailPhases(null), []);
});

test('request: Le Bail on, back to Rietveld, and only one Le Bail phase per fit', () => {
  assert.deepEqual(leBailRequest(CO, []), { origin: 'le_bail', le_bail: { [CO]: true } });
  assert.deepEqual(leBailRequest(CO, [CO]), { origin: 'le_bail', le_bail: { [CO]: false } });
  assert.deepEqual(leBailRequest(ZN, [CO]), { origin: 'le_bail', le_bail: { [CO]: false, [ZN]: true } });
});

test('version delta: the Le Bail phase is not "removed" and relative fractions get no difference', () => {
  const rows = deltaStrip(v1, v2).filter(row => row.key.startsWith('wt:'));
  const co = rows.find(row => row.phase === CO);
  const zn = rows.find(row => row.phase === ZN);
  assert.equal(deltaText(co), '50.35 → Le Bail');
  assert.equal(zn.delta, null);
  assert.equal(deltaText(zn), '49.65 → 100.00 (relative)');
  assert.equal(deltaText(deltaStrip(v2, v1).find(row => row.phase === CO)), 'Le Bail → 50.35');
  const plain = deltaStrip(v1, v1).find(row => row.phase === ZN);
  assert.equal(deltaText(plain), '49.65 → 49.65 (±0.00)');
});

test('compare rows keep the Le Bail phase', () => {
  const a = { weight_percent: v1.weight_percent, phase_identity: { [ZN]: 'Zn1.7Co0.3O2_186', [CO]: 'Co3O4_227' } };
  const b = { weight_percent: { [ZN]: 100 }, phase_identity: { [ZN]: 'Zn1.7Co0.3O2_186', [CO]: 'Co3O4_227' }, le_bail_phases: [CO], weights_relative: true };
  const co = weightRows(a, b).find(row => row.names.includes(CO));
  assert.equal(co.a, 50.3507);
  assert.equal(co.b, null);
  assert.equal(co.bLeBail, true);
  assert.equal(co.aLeBail, false);
});
