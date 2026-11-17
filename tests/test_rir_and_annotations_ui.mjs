import assert from 'node:assert/strict';
import test from 'node:test';
import { lineText, missingPhases, rirRows, signedText } from '../src/dara/server/ui/src/components/rirView.mjs';
import {
  MAX_ANNOTATIONS, cleanAnnotationText, escapePlotText, newAnnotationId, plotAnnotations, removeAnnotation, upsertAnnotation,
} from '../src/dara/server/ui/src/components/plotAnnotations.mjs';

const body = { phases: [
  { phase: 'ZnO_186', phase_label: 'ZnO', line_hkl: '1 0 1', line_two_theta: 36.2531, weight_percent_rir: 56.05, weight_percent_rietveld: 49.65 },
  { phase: 'NiO_225', phase_label: 'NiO', line_hkl: null, line_two_theta: null, weight_percent_rir: null, weight_percent_rietveld: 0 },
] };

test('RIR rows carry RIR − Rietveld, and the phases without a line', () => {
  const rows = rirRows(body);
  assert.equal(rows[0].key, 'ZnO_186');
  assert.ok(Math.abs(rows[0].delta - 6.4) < 1e-9);
  assert.equal(rows[1].delta, null);
  assert.deepEqual(missingPhases(body), ['NiO']);
  assert.deepEqual(rirRows(null), []);
  assert.equal(lineText(body.phases[0]), '(1 0 1) 36.25°');
  assert.equal(lineText(body.phases[1]), null);
  assert.equal(signedText(6.4), '+6.4');
  assert.equal(signedText(-9.75), '−9.8');
  assert.equal(signedText(0.01), '0.0');
  assert.equal(signedText(null), '—');
});

test('annotation text is one line of plain text, escaped for Plotly', () => {
  assert.equal(cleanAnnotationText('  a\nb\tc  '), 'a b c');
  assert.equal(cleanAnnotationText('x'.repeat(130)).length, 120);
  assert.equal(cleanAnnotationText(null), '');
  assert.equal(escapePlotText('<a href="x">&</a>'), '&lt;a href="x"&gt;&amp;&lt;/a&gt;');
  assert.match(newAnnotationId(), /^[A-Za-z0-9_-]{1,40}$/);
});

test('annotation list edits and the Plotly annotations', () => {
  const one = { id: 'a', two_theta: 30, intensity: 100, text: '<b>' };
  let list = upsertAnnotation([], one);
  list = upsertAnnotation(list, { ...one, text: 'moved', two_theta: 31 });
  assert.deepEqual(list, [{ ...one, text: 'moved', two_theta: 31 }]);
  assert.deepEqual(removeAnnotation(list, 'a'), []);
  const full = Array.from({ length: MAX_ANNOTATIONS }, (_, i) => ({ ...one, id: `n${i}` }));
  assert.equal(upsertAnnotation(full, { ...one, id: 'new' }), null);
  assert.equal(upsertAnnotation(full, { ...one, id: 'n3', text: 'edit' })[3].text, 'edit');
  const [drawn] = plotAnnotations([one]);
  assert.equal(drawn.text, '&lt;b&gt;');
  assert.equal(drawn.name, 'user:a');
  assert.deepEqual([drawn.x, drawn.y, drawn.showarrow], [30, 100, false]);
  assert.equal(plotAnnotations([one], { scale: 'log' })[0].y, 2);
});
