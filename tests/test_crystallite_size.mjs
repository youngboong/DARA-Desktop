import assert from 'node:assert/strict';
import { crystalliteSizeCell, microstrainCell } from '../src/dara/server/ui/src/components/crystalliteSize.mjs';

const refined = crystalliteSizeCell({
  grain_sizes: [{ direction: 'GrainSize(1,1,1)', value: 47.67, esd: 0.53, unit: 'nm' }],
  crystallite_size_status: 'refined',
});
assert.deepEqual(refined, { text: '47.7 ± 0.5 nm', tooltip: null });

const noEsd = crystalliteSizeCell({ grain_sizes: [{ value: 84.18, esd: null, unit: 'nm' }] });
assert.equal(noEsd.text, '84.2 nm');

const unresolvable = crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'not_resolvable' });
assert.equal(unresolvable.text, 'Not resolvable');
assert.match(unresolvable.tooltip, /larger than this instrument can resolve/);

const stale = crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'stale' });
assert.equal(stale.text, 'Not determined (older run)');
assert.match(stale.tooltip, /Baseline \(current code\)/);

assert.equal(crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'not_refined' }).text, 'Not refined');
// summaries stored before the status existed
assert.deepEqual(crystalliteSizeCell({ grain_sizes: [] }), { text: '—', tooltip: null });
assert.deepEqual(crystalliteSizeCell(undefined), { text: '—', tooltip: null });

// a failed phase never reads "Not resolvable" (design 2.1 A)
for (const row of [
  { grain_sizes: [], crystallite_size_status: 'not_resolvable', refinement_status: { level: 'failed' } },
  { grain_sizes: [], crystallite_size_status: 'not_resolvable', size_strain: { size_state: 'failed' } },
]) assert.equal(crystalliteSizeCell(row).text, 'Refinement failed');
assert.equal(crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'not_resolvable', refinement_status: { level: 'limited' } }).text, 'Not resolvable');

// microstrain column
const note = 'All excess broadening was assigned to microstrain; crystallite size is beyond the resolvable range.';
assert.deepEqual(microstrainCell({ size_strain: {
  size_state: 'not_resolvable', strain_state: 'refined', separable: false, note,
  microstrain_percent: { value: 0.1494, esd: 0.0241 },
} }), { text: '0.149 ± 0.024', tooltip: null, note });
assert.equal(microstrainCell({ size_strain: { strain_state: 'refined', separable: true, note: 'ignored', microstrain_percent: { value: 0.2, esd: null } } }).note, null);
assert.equal(microstrainCell({ size_strain: { strain_state: 'not_detected', separable: false, note: 'n' } }).text, 'Not detected');
assert.equal(microstrainCell({ size_strain: { strain_state: 'at_limit', separable: false } }).text, 'At refinement limit');
assert.equal(microstrainCell({ refinement_status: { level: 'failed' }, size_strain: { strain_state: 'refined' } }).text, 'Refinement failed');
assert.deepEqual(microstrainCell({}), { text: '—', tooltip: null, note: null });

console.log('crystallite size cell tests passed');
