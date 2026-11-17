import assert from 'node:assert/strict';
import { HKL_MAX_LABELS, dAxisTicks, hklText, pickHklLabels } from '../src/dara/server/ui/src/components/plotLabels.mjs';
import { buildRefinementView } from '../src/dara/server/ui/src/components/refinementPlotState.mjs';

assert.equal(hklText(1, 1, 0), '(110)');
assert.equal(hklText(-1, 0, 2), '(1̄02)');
assert.equal(hklText(1, 0, 12), '(1 0 12)');

{
  const reflections = [
    { x: 30, intensity: 100 }, { x: 30.1, intensity: 90 }, { x: 31, intensity: 50 }, { x: 50, intensity: 10 },
  ];
  // The weaker neighbour within the gap is dropped; the result is in 2θ order.
  assert.deepEqual(pickHklLabels(reflections, [20, 60], 0.5).map(item => item.x), [30, 31, 50]);
  // Zooming in (smaller gap in degrees) reveals the neighbour; the window filters.
  assert.deepEqual(pickHklLabels(reflections, [29, 32], 0.05).map(item => item.x), [30, 30.1, 31]);
  const many = Array.from({ length: 100 }, (_, i) => ({ x: 10 + i, intensity: i }));
  assert.equal(pickHklLabels(many, [0, 200], 0.5).length, HKL_MAX_LABELS);
}

{
  const lambda = 1.540598;
  const twoThetaOf = d => (360 / Math.PI) * Math.asin(lambda / (2 * d));
  const ticks = dAxisTicks([10, 80], lambda, 2);
  assert.equal(ticks.tickvals.length, ticks.ticktext.length);
  assert(ticks.ticktext.includes('5') && ticks.ticktext.includes('2') && ticks.ticktext.includes('1.5'));
  ticks.ticktext.forEach((text, i) => assert(Math.abs(twoThetaOf(Number(text)) - ticks.tickvals[i]) < 1e-9));
  ticks.tickvals.forEach((x, i) => { if (i) assert(x - ticks.tickvals[i - 1] >= 2); });
  // A zoomed window gets finer d values.
  const zoomed = dAxisTicks([40, 45], lambda, 0.5);
  assert(zoomed.ticktext.some(text => text.split('.')[1]?.length >= 2), zoomed.ticktext.join());
  assert.equal(dAxisTicks([10, 80], NaN, 2), null);
}

{
  const plot = { layout: { xaxis: { range: [20, 60] }, yaxis: {}, meta: { wavelength_angstrom: 1.540598 } }, data: [
    { name: 'Observed', x: [20, 30, 40, 50, 60], y: [10, 500, 10, 200, 10] },
    { name: 'Difference', meta: 'difference', x: [20, 60], y: [0, 0], xaxis: 'x2', yaxis: 'y2' },
    { name: 'A', meta: 'reflection', legendgroup: 'A', x: [30, 50], y: [-5, -5], marker: { color: 'red' },
      customdata: [[1, 1, 1, 100], [2, 0, 0, 40]], visible: 'legendonly' },
    { name: 'B', meta: 'reflection', legendgroup: 'B', x: [40], y: [-5], marker: { color: 'blue' },
      customdata: [[1, 0, 0, 80]], visible: 'legendonly' },
  ] };
  const off = buildRefinementView(plot, { plotWidth: 1600 });
  assert.equal(off.layout.annotations.length, 2); // Bragg-row labels only
  assert.equal(off.layout.xaxis3, undefined);
  const on = buildRefinementView(plot, { plotWidth: 1600, hklLabels: true, dAxis: true, selection: { B: false } });
  const labels = on.layout.annotations.filter(item => item.textangle === -90);
  assert.deepEqual(labels.map(item => item.text), ['(111)', '(200)']);
  assert.equal(labels[0].y, 500, 'label sits on the curve top');
  assert(labels.every(item => item.yref === 'y' && item.font.color === 'red'), 'main panel, phase colour');
  assert(on.layout.yaxis.range[1] > off.layout.yaxis.range[1], 'room for the labels');
  assert.equal(on.layout.xaxis3.overlaying, 'x');
  assert.deepEqual(on.layout.xaxis3.range, [20, 60]);
  assert(on.layout.xaxis3.tickvals.length > 2);
  assert.equal(on.data.at(-1).xaxis, 'x3', 'an empty trace makes Plotly draw the d axis');
  assert.equal(on.data.length, plot.data.length + 1);
  assert.equal(on.layout.legend.yref, 'container', 'legend above the d labels');
  const zoomed = buildRefinementView(plot, { plotWidth: 1600, hklLabels: true, xRange: [45, 55] });
  assert.deepEqual(zoomed.layout.annotations.filter(item => item.textangle === -90).map(item => item.text), ['(200)']);
  const noWavelength = buildRefinementView({ ...plot, layout: { ...plot.layout, meta: {} } }, { dAxis: true });
  assert.equal(noWavelength.layout.xaxis3, undefined, 'no d axis without a wavelength');
}
console.log('hkl labels and d-axis ticks passed.');
