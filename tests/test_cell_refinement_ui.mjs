import assert from 'node:assert/strict';
import test from 'node:test';
import { cellQuery, cellText, parameterRows, peakCounts, shiftText, sigmaText, valueText } from '../src/dara/server/ui/src/components/cellRefinement.mjs';

const phase = {
  parameters: { a: { value: 8.0855, esd: 0.0008 } }, volume: { value: 528.59, esd: 0.16 },
  displacement_mm: { value: -0.27413, esd: 0.00696 }, zero_deg: null,
  rietveld: { a: { value: 8.08825, esd: 0.00033 }, volume: 529.13 },
  peaks: [{ used: true }, { used: false }, { used: true }],
};

test('query of each shift model', () => {
  assert.equal(cellQuery('none'), 'zero=0&displacement=0');
  assert.equal(cellQuery('zero'), 'zero=1&displacement=0');
  assert.equal(cellQuery('displacement'), 'zero=0&displacement=1');
  assert.equal(cellQuery('standard:Si_227_(cod_1)'), 'correct_with=Si_227_(cod_1)');
});

test('parameter rows: peaks beside Rietveld, difference in combined e.s.d.', () => {
  const [a, volume] = parameterRows(phase);
  assert.equal(a.label, 'a');
  assert.ok(Math.abs(a.delta + 0.00275) < 1e-9);
  assert.ok(Math.abs(a.z - (-0.00275 / Math.hypot(0.0008, 0.00033))) < 1e-9);
  assert.equal(valueText(a.peaks), '8.08550 ± 0.00080 Å');
  assert.equal(volume.key, 'volume');
  assert.equal(volume.rietveld.value, 529.13, 'a bare Rietveld volume number');
  assert.ok(Math.abs(volume.z - (528.59 - 529.13) / 0.16) < 1e-9);
  assert.deepEqual(parameterRows({ parameters: null, note: 'refused' }), []);
  assert.equal(cellText(a.peaks, 'Å'), '8.08550 ± 0.00080');
  assert.equal(cellText(volume.rietveld, 'Å³'), '529.13');
  assert.equal(cellText(null, 'Å'), '—');
  assert.equal(sigmaText(-3.14), '−3.1σ');
  assert.equal(sigmaText(0.02), '0.0σ');
  assert.equal(sigmaText(null), '—');
});

test('fitted shift and peak counts', () => {
  assert.equal(shiftText(phase), 'displacement −0.274 ± 0.007 mm');
  assert.equal(shiftText({ zero_deg: { value: 0.2317, esd: 0.0061 } }), 'zero +0.232 ± 0.006°');
  assert.equal(shiftText({}), null);
  assert.deepEqual(peakCounts(phase), { used: 2, total: 3 });
});
