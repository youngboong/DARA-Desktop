// Task result page fixes (bug sweep 2026-11-24, T1-T22).
import assert from 'node:assert/strict';
import { canonicalSelection } from '../src/dara/server/ui/src/components/versions.mjs';

// T1: adopting C1 (the candidate that is v1) saves and reads as 'best'.
{
  const results = [{ rwp: 7.15, source_index: 0 }, { rwp: 7.16, source_index: 2 }];
  const final = { rwp: 7.15 };
  assert.equal(canonicalSelection(0, results, final), 'best');
  assert.equal(canonicalSelection('0', results, final), 'best');
  assert.equal(canonicalSelection(2, results, final), 2);
  assert.equal(canonicalSelection('version:1', results, final), 'best');
  assert.equal(canonicalSelection('version:3', results, final), 'version:3');
  assert.equal(canonicalSelection(0, [], final), 0);
}

// T9: the strongest reflection keeps its hkl label even when the top cannot rise enough for it.
{
  const { buildRefinementView } = await import('../src/dara/server/ui/src/components/refinementPlotState.mjs');
  const xs = Array.from({ length: 401 }, (_, i) => 10 + i * 0.1);
  const plot = {
    data: [
      { name: 'Observed', meta: 'observed', visible: true, x: xs, y: xs.map(x => (Math.abs(x - 30) < 0.05 ? 1000 : 10)) },
      { name: 'ZnO', meta: 'reflection', legendgroup: 'ZnO', marker: { color: '#123456' }, x: [30], y: [0], customdata: [[1, 0, 0, 100]] },
    ],
    layout: { xaxis: { range: [10, 50] }, yaxis: { range: [0, 1000] }, margin: { l: 60, r: 24, t: 10, b: 40 } },
  };
  const view = buildRefinementView(plot, { hklLabels: true, plotHeight: 120, plotWidth: 800 });
  const label = (view.layout.annotations || []).find(item => item.text === '(100)');
  assert.ok(label, 'the strongest peak is labelled');
  assert.ok(label.y <= view.layout.yaxis.range[1], 'inside the frame');
}

// T14: an excluded interval outside the pattern or inverted is named, not silently refused.
{
  const { intervalProblem } = await import('../src/dara/server/ui/src/components/backgroundAnchors.mjs');
  assert.equal(intervalProblem([20, 25], [10, 100]), null);
  assert.match(intervalProblem([25, 20], [10, 100]), /below/);
  assert.match(intervalProblem([120, 130], [10, 100]), /outside the measured range \(10\.0–100\.0°\)/);
  assert.equal(intervalProblem([5, 15], [10, 100]), null); // overlaps the range
  assert.match(intervalProblem([null, 20]), /both ends/);
  assert.equal(intervalProblem([120, 130], [undefined, undefined]), null); // range unknown
}

// T4: a version whose cell the data did not determine is not offered for adoption.
{
  const { selectOptionGroups } = await import('../src/dara/server/ui/src/components/versions.mjs');
  const versions = [{ version: 1 }, { version: 2, label: 'Le Bail: ZnO', adoptable: false, rwp: 7.07 }, { version: 3, label: 'x', rwp: 7 }];
  const options = selectOptionGroups([], versions, { adoptable: true }).find(group => group.label === 'Versions').options;
  assert.deepEqual(options.map(item => Boolean(item.disabled)), [true, false]);
  const shown = selectOptionGroups([], versions).find(group => group.label === 'Versions').options;
  assert.ok(shown.every(item => !item.disabled)); // the fit view still shows it
}

// T10: an identification plot keeps its 2θ axis title (it has no Obs − calc panel to carry it).
{
  const { buildRefinementView } = await import('../src/dara/server/ui/src/components/refinementPlotState.mjs');
  const plot = { layout: { xaxis: { range: [20, 60], title: { text: '2θ (°)' } }, yaxis: {} }, data: [
    { name: 'Observed', x: [20, 30, 60], y: [10, 400, 10] },
    { name: 'TiO2', meta: 'reference-sticks', legendgroup: 'T', x: [30, 30, 30], y: [10, 420, null], customdata: [[1, 0, 1, 1], [1, 0, 1, 1], [1, 0, 1, 1]] },
  ] };
  assert.equal(buildRefinementView(plot).layout.xaxis.title.text, '2θ (°)');
}

// T21: d-axis ticks keep round values (no 3.42 between 4 and 3) unless zoomed in.
{
  const { dAxisTicks } = await import('../src/dara/server/ui/src/components/plotLabels.mjs');
  const ticks = dAxisTicks([10, 100], 1.5406, 1.9).ticktext.map(Number);
  assert.ok(ticks.every(d => Math.abs(d * 40 - Math.round(d * 40)) < 1e-6 || d >= 2), ticks.join());
  assert.ok(!ticks.includes(3.42));
}
