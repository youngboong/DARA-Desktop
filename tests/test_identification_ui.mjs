import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isIdentification, median, peakStatusText, phasesFoundText, refinedTasks, rirText, scherrerByPhase, sizeText,
} from '../src/dara/server/ui/src/components/identification.mjs';

test('identification mode and summary texts', () => {
  assert.equal(isIdentification({ analysis_mode: 'identification' }), true);
  assert.equal(isIdentification({ analysis_mode: 'known_mixture' }), false);
  assert.equal(isIdentification(null), false);
  assert.equal(peakStatusText({ observed: 32, assigned: 32, unexplained: 0 }), 'All 32 peaks assigned');
  assert.equal(peakStatusText({ observed: 30, assigned: 18, unexplained: 12 }), '12 unexplained peaks');
  assert.equal(peakStatusText({ observed: 5, assigned: 4, unexplained: 1 }), '1 unexplained peak');
  assert.equal(peakStatusText(null), null);
  assert.equal(phasesFoundText([{}, {}]), '2 phases found');
  assert.equal(phasesFoundText([{}]), '1 phase found');
  assert.deepEqual(refinedTasks({ refined_as: [122, 'x'] }), [122]);
  assert.deepEqual(refinedTasks({}), []);
});

test('Scherrer size per phase: the median over its peaks with a size', () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([4, 1, 2, 3]), 2.5);
  assert.equal(median([null, 'x']), null);
  const sizes = scherrerByPhase([
    { phase: 'A', size_nm: 40 }, { phase: 'A', size_nm: 50 }, { phase: 'A', size_nm: null }, { phase: 'A', size_nm: 44 },
    { phase: 'B', size_nm: 30 }, { phase: null, size_nm: 99 },
  ]);
  assert.deepEqual(sizes, { A: { median: 44, n: 3 }, B: { median: 30, n: 1 } });
  assert.equal(sizeText(sizes.A), '44 nm');
  assert.equal(sizeText(undefined), '—');
  assert.equal(rirText(56.02), '56.0');
  assert.equal(rirText(null), null);
});
