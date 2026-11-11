import assert from 'node:assert/strict';
import {
  constraintComplete, constraintText, countParameters, isEmptyDraft, parameterText, refusalLine, structurePhases, structureRequest, structureResultRows,
} from '../src/dara/server/ui/src/components/structureModel.mjs';

const sites = [
  { index: 0, label: 'Y1', free: ['x', 'z'], species: [{ element: 'Y', occupancy: 1 }] },
  { index: 1, label: 'Fe1', free: [], species: [{ element: 'Fe', occupancy: 1 }] },
  { index: 2, label: 'O1', free: ['x', 'y', 'z'], species: [{ element: 'O', occupancy: 1 }] },
  { index: 3, label: 'O2', free: ['x', 'y', 'z'], species: [{ element: 'O', occupancy: 1 }] },
];
const exchange = { kind: 'exchange', sites: [0, 1], elements: ['Y', 'Fe'] };

assert.equal(countParameters({ positions: [], thermal: 'fixed', occupancy: [] }, sites), 0);
assert.equal(countParameters({ positions: [0, 0, 2], thermal: 'overall', occupancy: [exchange] }, sites), 2 + 3 + 1 + 1);
assert.equal(countParameters({ positions: [], thermal: 'per_element', occupancy: [] }, sites), 3); // Y, Fe, O
assert.equal(countParameters({ positions: [1], thermal: 'fixed' }, sites), 0); // special position, nothing free
assert.equal(parameterText(5, 12), '5 of 12 allowed');
assert.equal(parameterText(5, null), '5');

assert.ok(constraintComplete(exchange));
assert.ok(!constraintComplete({ ...exchange, sites: [0, 0] }));
assert.ok(!constraintComplete({ kind: 'substitution', site: 0, host: 'Y' }));
assert.ok(constraintComplete({ kind: 'vacancy', site: 2, element: 'O' }));
assert.equal(constraintText(exchange, sites), 'Y on Y1 exchanges with Fe on Fe1');

const request = structureRequest('best', 'YFeO3_62', { positions: [2, 0, 2], thermal: 'overall', occupancy: [exchange, { kind: 'vacancy' }] });
assert.deepEqual(request, {
  base: 'best', origin: 'structure',
  structure: { phase: 'YFeO3_62', positions: [0, 2], thermal: 'overall', occupancy: [exchange] },
});
assert.ok(isEmptyDraft({ positions: [], thermal: 'fixed', occupancy: [] }));
assert.ok(!isEmptyDraft({ positions: [], thermal: 'overall', occupancy: [] }));

const line = refusalLine({ code: 'lattice_not_stable', message: 'Refine the lattice first: X has lattice parameters that are fixed.' });
assert.match(line, /Refine all phases/);
assert.equal(refusalLine({ code: 'new_code', message: 'Only this.' }), 'Only this.');

const result = {
  sites: [{
    index: 0, label: 'Y1', refined_positions: true,
    xyz: [{ value: 0.5657, esd: 0.0007, state: 'refined' }, { value: 0.25, esd: null, state: 'fixed' }, { value: 0.5094, esd: null, state: 'at_limit' }],
    species: [{ element: 'Y', occupancy: { value: 0.9495, esd: null } }, { element: 'Fe', occupancy: { value: 0.0505, esd: 0.014 } }],
    b_iso: { value: 0, esd: null, state: 'at_limit' },
  }],
};
const [row] = structureResultRows(result);
assert.equal(row.xyz[0].text, '0.56570 ± 0.00070');
assert.equal(row.xyz[2].state, 'at_limit');
assert.deepEqual(row.species.map(item => item.refined), [false, true]);
assert.equal(row.species[1].text, '0.051 ± 0.014');
assert.equal(row.b.state, 'at_limit');
assert.deepEqual(structurePhases({ A: {}, B: { structure_refinement: result } }).map(([phase]) => phase), ['B']);
assert.deepEqual(structurePhases(undefined), []);

console.log('structure model ui tests passed');

{
  const { parameterLabel } = await import('../src/dara/server/ui/src/components/structureModel.mjs');
  assert.equal(parameterLabel('occ0'), 'occupancy 1');
  assert.equal(parameterLabel('Ball'), 'B (all atoms)');
  assert.equal(parameterLabel('BFE'), 'B (Fe)');
  assert.equal(parameterLabel('BY'), 'B (Y)');
}
