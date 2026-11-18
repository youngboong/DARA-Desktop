import assert from 'node:assert/strict';
import {
  formatParameterWithEsd,
  latticeParameterRows,
  latticeStateNote,
} from '../src/dara/server/ui/src/components/latticeParameters.mjs';

const cubic = latticeParameterRows({
  a: { value: 5.463821, esd: 0.000078, unit: 'Å' },
}, 225);
assert.deepEqual(cubic.map(row => row.label), ['a = b = c', 'α = β = γ']);
assert.equal(cubic[0].constrained, true);
assert.equal(cubic[1].fixed, true);
assert.equal(formatParameterWithEsd(cubic[0].parameter), '5.463821 ± 0.000078 Å');
assert.equal(formatParameterWithEsd(cubic[1].parameter), '90°');

const trigonal = latticeParameterRows({
  a: { value: 4.98948, esd: 0.00014, unit: 'Å' },
  c: { value: 17.06849, esd: 0.00055, unit: 'Å' },
}, 167);
assert.deepEqual(trigonal.map(row => row.label), ['a = b', 'c', 'α = β', 'γ']);
assert.equal(formatParameterWithEsd(trigonal[0].parameter), '4.98948 ± 0.00014 Å');
assert.equal(formatParameterWithEsd(trigonal[1].parameter), '17.06849 ± 0.00055 Å');
assert.equal(formatParameterWithEsd(trigonal[3].parameter), '120°');

const monoclinic = latticeParameterRows({
  a: { value: 6.3, esd: null, unit: 'Å' },
  b: { value: 4.9, esd: null, unit: 'Å' },
  c: { value: 8.1, esd: null, unit: 'Å' },
  beta: { value: 107.77, esd: 0.006, unit: 'deg' },
}, 14);
assert.deepEqual(monoclinic.map(row => row.label), ['a', 'b', 'c', 'α = γ', 'β']);
assert.equal(monoclinic.find(row => row.label === 'b').constrained, false);

// angles alone are not shown: no axis length gives no rows (Na12.67P4O64.67, lattice held fixed)
assert.deepEqual(latticeParameterRows({}, 165), []);
assert.deepEqual(latticeParameterRows({ alpha: { value: 90, esd: null, unit: 'deg' } }, 165), []);

// fixed_reference lengths bring a and c back for a hexagonal/trigonal cell
const fixedReference = latticeParameterRows({
  a: { value: 8.9, esd: null, unit: 'Å', state: 'fixed_reference' },
  c: { value: 21.1, esd: null, unit: 'Å', state: 'fixed_reference' },
}, 165);
assert.deepEqual(fixedReference.map(row => row.label), ['a = b', 'c', 'α = β', 'γ']);
assert.deepEqual(fixedReference.map(latticeStateNote), ['fixed (DB)', 'fixed (DB)', null, null]);

// at-limit value (design 2.1 E)
const atLimit = latticeParameterRows({
  a: { value: 4.9341, esd: 0.0002, unit: 'Å', state: 'refined' },
  c: { value: 13.4875, esd: null, unit: 'Å', state: 'at_limit' },
}, 167);
assert.deepEqual(atLimit.map(latticeStateNote), [null, 'at limit', null, null]);
assert.equal(latticeStateNote(undefined), null);

// R groups: rhombohedral axes come from the data (task 41 v3, hematite R-3c, COD 1011267)
const rhombohedral = latticeParameterRows({
  a: { value: 5.431, esd: null, unit: 'Å', state: 'fixed_reference' },
  b: { value: 5.431, esd: null, unit: 'Å', state: 'fixed_reference' },
  c: { value: 5.431, esd: null, unit: 'Å', state: 'fixed_reference' },
  alpha: { value: 55.23, esd: null, unit: 'deg', state: 'fixed_reference' },
  beta: { value: 55.23, esd: null, unit: 'deg', state: 'fixed_reference' },
  gamma: { value: 55.22999999999999, esd: null, unit: 'deg', state: 'fixed_reference' },
}, 167);
assert.deepEqual(rhombohedral.map(row => row.label), ['a = b = c', 'α = β = γ']);
assert.deepEqual(rhombohedral.map(row => formatParameterWithEsd(row.parameter)), ['5.431 Å', '55.23°']);
assert.deepEqual(rhombohedral.map(latticeStateNote), ['fixed (DB)', 'fixed (DB)']);
// the same group on hexagonal axes keeps a = b, c, γ = 120°
const hexagonalAxes = latticeParameterRows({
  a: { value: 5.0356, esd: 0.0001, unit: 'Å' }, c: { value: 13.7489, esd: 0.0004, unit: 'Å' },
  alpha: { value: 90, esd: null, unit: 'deg' }, gamma: { value: 120, esd: null, unit: 'deg' },
}, 167);
assert.deepEqual(hexagonalAxes.map(row => row.label), ['a = b', 'c', 'α = β', 'γ']);
assert.equal(formatParameterWithEsd(hexagonalAxes[3].parameter), '120°');
// a non-R trigonal group never switches to rhombohedral axes
assert.deepEqual(latticeParameterRows({ a: { value: 3.1, esd: 0.001, unit: 'Å' }, c: { value: 4.7, esd: 0.001, unit: 'Å' } }, 164)
  .map(row => row.label), ['a = b', 'c', 'α = β', 'γ']);

// values without an e.s.d. keep their own digits; with an e.s.d. the value ± e.s.d. rule
assert.equal(formatParameterWithEsd({ value: 7.6498, esd: null, unit: 'Å', state: 'at_limit' }), '7.6498 Å');
assert.equal(formatParameterWithEsd({ value: 5.431, esd: null, unit: 'Å' }), '5.431 Å');
assert.equal(formatParameterWithEsd({ value: 107.77, esd: null, unit: 'deg' }), '107.77°');
assert.equal(formatParameterWithEsd({ value: 9.788829999999999, esd: 0.00045000000000000004, unit: 'Å' }), '9.78883 ± 0.00045 Å');

console.log('Lattice symmetry constraints and e.s.d. formatting passed.');
