import assert from 'node:assert/strict';
import test from 'node:test';
import { comparisonTraces, measuredAngularBounds, parseComparisonTaskIds } from '../src/dara/server/ui/src/components/patternComparison.mjs';

test('task selection is unique, positive and bounded', () => {
  assert.deepEqual(parseComparisonTaskIds('1,2,2,-3,NaN,, 8 '), [1, 2, 8]);
  assert.equal(parseComparisonTaskIds(Array.from({length: 30}, (_, i) => i + 1).join(',')).length, 20);
});

test('normalization and stacking preserve original measured values', () => {
  const patterns = [
    {task_id: 1, name: 'Before', angles: [10, 20], intensities: [10, 20]},
    {task_id: 2, name: 'After', angles: [12, 22], intensities: [5, 10]},
  ];
  const before = JSON.stringify(patterns);
  const traces = comparisonTraces(patterns);
  assert.deepEqual(traces[0].y, [50, 100]);
  assert.deepEqual(traces[1].y, [160, 210]);
  assert.deepEqual(traces[1].customdata, [5, 10]);
  assert.deepEqual(comparisonTraces(patterns, {normalize: false, offset: 0})[0].y, [10, 20]);
  assert.equal(JSON.stringify(patterns), before);
  assert.deepEqual(measuredAngularBounds(patterns), [10, 22]);
});

test('zero and negative intensity data remain finite', () => {
  const traces = comparisonTraces([
    {task_id: 1, name: 'Zero', angles: [10, 20], intensities: [0, 0]},
    {task_id: 2, name: 'Negative', angles: [10, 20], intensities: [-2, -4]},
  ], {normalize: true, offset: 0});
  assert.deepEqual(traces[0].y, [0, 0]);
  assert.deepEqual(traces[1].y, [-50, -100]);
  assert.equal(measuredAngularBounds([]), undefined);
});

{
  const sqrtTraces = comparisonTraces([{ task_id: 1, name: 'a', angles: [10, 20], intensities: [100, 25] }], { normalize: false, offset: 0, sqrt: true });
  assert.deepEqual(sqrtTraces[0].y, [10, 5]);
  assert.deepEqual(sqrtTraces[0].customdata, [100, 25], 'hover keeps the measured intensity');
  console.log('sqrt scale ok');
}

test('common grid: overlap of the ranges at the finest step, flagged when ranges differ', async () => {
  const { commonGrid, angularStep } = await import('../src/dara/server/ui/src/components/patternComparison.mjs');
  assert.equal(angularStep([10, 10.02, 10.04, 10.06]).toFixed(3), '0.020');
  const grid = commonGrid([
    { angles: [10, 10.5, 11, 11.5, 12, 12.5, 13], intensities: [] },
    { angles: [11, 11.25, 11.5, 11.75, 12, 12.25, 12.5, 12.75, 13, 13.25, 13.5, 13.75, 14], intensities: [] },
  ]);
  assert.deepEqual(grid.range, [11, 13]);
  assert.equal(grid.step, 0.25);
  assert.deepEqual(grid.x, [11, 11.25, 11.5, 11.75, 12, 12.25, 12.5, 12.75, 13]);
  assert.equal(grid.differs, true);
  assert.equal(commonGrid([{ angles: [10, 11, 12] }, { angles: [10, 11, 12] }]).differs, false);
  assert.equal(commonGrid([{ angles: [10, 11] }, { angles: [20, 21] }]), null);
  // A very fine step over a wide range is coarsened to the point limit.
  const fine = Array.from({ length: 100001 }, (_, i) => i / 1000);
  assert.equal(commonGrid([{ angles: fine }], { maxPoints: 101 }).x.length, 101);
});

test('linear interpolation, null outside the measured range', async () => {
  const { interpolate } = await import('../src/dara/server/ui/src/components/patternComparison.mjs');
  assert.deepEqual(interpolate([10, 11, 12], [0, 10, 30], [9.5, 10, 10.5, 11.5, 12, 12.5]), [null, 0, 5, 20, 30, null]);
  assert.deepEqual(interpolate([12, 10, 11], [30, 0, 10], [10.5]), [5], 'unsorted input');
});

test('map matrix: one row per pattern, list order or series-variable order', async () => {
  const { mapMatrix, parseSeriesValues } = await import('../src/dara/server/ui/src/components/patternComparison.mjs');
  const patterns = [
    { task_id: 1, name: 'hot', angles: [10, 11, 12], intensities: [0, 50, 100] },
    { task_id: 2, name: 'cold', angles: [10, 11, 12], intensities: [10, 20, 40] },
  ];
  const listed = mapMatrix(patterns, { normalize: false });
  assert.deepEqual(listed.x, [10, 11, 12]);
  assert.deepEqual(listed.y, [0, 1]);
  assert.deepEqual(listed.z, [[0, 50, 100], [10, 20, 40]]);
  assert.deepEqual(listed.labels, ['#1 hot', '#2 cold']);
  const values = parseSeriesValues('300, 25', 2);
  assert.deepEqual(values, [300, 25]);
  const ordered = mapMatrix(patterns, { normalize: true, values });
  assert.deepEqual(ordered.y, [25, 300]);
  assert.deepEqual(ordered.z[0], [25, 50, 100]);
  assert.equal(ordered.numeric, true);
  assert.equal(parseSeriesValues('1,x', 2), null);
  assert.equal(parseSeriesValues('1,2', 3), null);
  assert.equal(parseSeriesValues('', 0), null);
});

test('difference A − k·B on the grid of A, with the least-squares k', async () => {
  const { differenceCurves, differenceCsv, leastSquaresScale } = await import('../src/dara/server/ui/src/components/patternComparison.mjs');
  assert.equal(leastSquaresScale([2, 4, 6], [1, 2, 3]), 2);
  assert.equal(leastSquaresScale([1], [0]), 1);
  const a = { angles: [10, 11, 12, 13], intensities: [4, 8, 12, 99] };
  const b = { angles: [9.5, 10.5, 11.5, 12.5], intensities: [1, 3, 5, 7] }; // B = 2θ·4 − 39 → 2, 4, 6 at 10, 11, 12
  const fixed = differenceCurves(a, b, 1);
  assert.deepEqual(fixed.x, [10, 11, 12], '13° lies outside B');
  assert.deepEqual(fixed.b, [2, 4, 6]);
  assert.deepEqual(fixed.difference, [2, 4, 6]);
  const auto = differenceCurves(a, b, 'auto');
  assert.equal(auto.k, 2);
  assert.deepEqual(auto.difference, [0, 0, 0]);
  const csv = differenceCsv(auto, 'A sample', 'B sample').split('\n');
  assert.equal(csv[0], '# A = A sample; B = B sample; k = 2; B interpolated linearly onto the 2theta grid of A');
  assert.equal(csv[1], 'two_theta_deg,A,k*B,A_minus_kB');
  assert.equal(csv[2], '10,4,4,0');
});
