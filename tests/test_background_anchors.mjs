import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  addAnchor, anchorError, anchorLabel, curveWarnings, fitSeries, pchip, removeAnchor, seedAnchors,
} from '../src/dara/server/ui/src/components/backgroundAnchors.mjs';

// PCHIP parity with scipy (fixture written by dara.background_curve.curve).
const fixture = JSON.parse(readFileSync(new URL('./test_data/pchip_fixture.json', import.meta.url)));
assert.ok(fixture.cases.length >= 5);
for (const item of fixture.cases) {
  const y = pchip(item.anchors, item.x);
  item.y.forEach((expected, i) => {
    assert.ok(Math.abs(y[i] - expected) <= 1e-6, `${item.name}: x=${item.x[i]} js=${y[i]} scipy=${expected}`);
  });
}
// Unsorted query points (a click somewhere) give the same values.
const anchors = fixture.cases[1].anchors;
assert.deepEqual(pchip(anchors, [80, 20, 50]), [pchip(anchors, [80])[0], pchip(anchors, [20])[0], pchip(anchors, [50])[0]]);

// Validation mirrors the server.
const ok = [[10, 5], [20, 6], [30, 0]];
assert.equal(anchorError(ok, { lo: 10, hi: 30 }), null);
assert.match(anchorError([[10, 5]]), /2 to 40/);
assert.match(anchorError(Array.from({ length: 41 }, (_, i) => [i, 1])), /2 to 40/);
assert.match(anchorError([[10, 5], [10, 6]]), /sorted/);
assert.match(anchorError([[20, 5], [10, 6]]), /sorted/);
assert.match(anchorError([[10, -1], [20, 6]]), /negative/);
assert.match(anchorError(ok, { lo: 12, hi: 30 }), /outside the refined range/);
assert.match(anchorError(ok, { excluded: [[15, 25]] }), /inside the excluded range 15–25/);
assert.equal(anchorError(ok, { excluded: [[20, 25]] }), null, 'a range end is not inside the range');

// Seeding: both ends, then the off-peak point where the curve misses the refined background most
// (in σ = √background) until it is within 1σ; y = refined background.
const x = Array.from({ length: 1001 }, (_, i) => 10 + i * 0.08);
const yBkg = x.map(t => 500 - 3 * t);
const peaks = t => 2000 * Math.exp(-(((t - 30) / 0.1) ** 2)) + 800 * Math.exp(-(((t - 61) / 0.1) ** 2));
const yCalc = x.map((t, i) => yBkg[i] + peaks(t) + 5 + Math.sin(t)); // phase intensity never zero
const yObs = yCalc.slice();
const curved = t => 500 - 3 * t + 0.05 * (t - 50) ** 2;
const curvedBkg = x.map(curved);
const curvedCalc = x.map((t, i) => curvedBkg[i] + peaks(t) + 5 + Math.sin(t));
const seeded = seedAnchors({ x, yObs: curvedCalc, yCalc: curvedCalc, yBkg: curvedBkg, lo: 12, hi: 88, excluded: [[40, 45]] });
assert.ok(seeded.length > 2 && seeded.length <= 20, `seeded ${seeded.length}`);
assert.equal(seeded[0][0], 12);
assert.ok(Math.abs(seeded[seeded.length - 1][0] - 88) < 1e-9);
assert.equal(anchorError(seeded, { lo: 12, hi: 88, excluded: [[40, 45]] }), null);
seeded.forEach(([t, y]) => {
  assert.ok(Math.abs(y - curved(t)) < 0.01, 'y is the refined background');
  assert.ok(peaks(t) < 1, `no anchor on a peak (${t})`);
});
const fitted = x.filter(t => t >= 12 && t <= 88 && !(t > 40 && t < 45));
pchip(seeded, fitted).forEach((y, k) => assert.ok(Math.abs(y - curved(fitted[k])) <= Math.sqrt(curved(fitted[k])) + 1e-6));
assert.equal(seedAnchors({ x, yObs, yCalc, yBkg, lo: 12, hi: 88 }).length, 2, 'a straight background needs only the ends');
assert.deepEqual(seedAnchors({ x: [1], yObs: [1], yCalc: [1], yBkg: [1] }), []);

// A steep convex low-angle background with peaks: the seeded curve raises no warning.
const steepX = Array.from({ length: 3501 }, (_, i) => 5 + i * 0.02);
const steepBkg = steepX.map(t => 40 + 6000 * Math.exp(-(t - 5) / 3));
const steepPeaks = t => [12, 18.5, 26, 33, 47, 58].reduce((sum, c) => sum + 1500 * Math.exp(-(((t - c) / 0.08) ** 2)), 0);
const steepCalc = steepX.map((t, i) => steepBkg[i] + steepPeaks(t));
const steep = { x: steepX, yObs: steepCalc, yCalc: steepCalc, yBkg: steepBkg, lo: 5, hi: 75 };
const steepSeed = seedAnchors(steep);
assert.ok(steepSeed.length <= 20, `steep seeded ${steepSeed.length}`);
assert.deepEqual(curveWarnings({ ...steep, anchors: steepSeed }), []);
// The refined background itself 4σ above the data at the start (seen in a real fit): still no warning.
const lowStart = steepCalc.map((y, i) => (steepX[i] < 6 ? y - 4 * Math.sqrt(y) : y));
assert.deepEqual(curveWarnings({ ...steep, yObs: lowStart, anchors: steepSeed }), []);

// Editing helpers.
assert.deepEqual(addAnchor(ok, [15, 3]), [[10, 5], [15, 3], [20, 6], [30, 0]]);
assert.equal(addAnchor(ok, [20, 3]), ok, 'an existing 2θ is not added twice');
assert.deepEqual(removeAnchor(ok, 1), [[10, 5], [30, 0]]);
assert.equal(removeAnchor([[1, 1], [2, 2]], 0).length, 2, 'two anchors stay');
assert.equal(anchorLabel(ok), 'Background: manual (3 points)');

// Guards: a raised curve is reported above the data; an anchor on the 30° peak is flagged.
const flat = [[12, 500 - 36], [88, 500 - 264]];
assert.deepEqual(curveWarnings({ x, yObs, yCalc, yBkg, anchors: flat, lo: 12, hi: 88 }), []);
const raised = [[12, 464], [50, 600], [88, 236]];
const [above] = curveWarnings({ x, yObs, yCalc, yBkg, anchors: raised, lo: 12, hi: 88 });
assert.match(above, /^Background above data at \d+\.\d°/);
const onPeak = curveWarnings({ x, yObs, yCalc, yBkg, anchors: [[12, 464], [30, 410], [88, 236]], lo: 12, hi: 88 });
assert.deepEqual(onPeak, ['Anchor on a peak at 30.00°']);

// The fit series come from the server's three main traces, typed arrays included.
const encoded = values => ({ dtype: 'f8', bdata: Buffer.from(new Float64Array(values).buffer).toString('base64') });
const series = fitSeries({ data: [
  { name: 'Observed', x: encoded([1, 2]), y: encoded([5, 6]) },
  { name: 'Calculated', x: [1, 2], y: [4, 5] },
  { name: 'Background', x: [1, 2], y: [1, 1] },
  { name: 'Difference', meta: 'difference', x: [1, 2], y: [1, 1] },
] });
assert.deepEqual(series, { x: [1, 2], yObs: [5, 6], yCalc: [4, 5], yBkg: [1, 1] });
assert.equal(fitSeries({ data: [] }), null);

console.log('background anchors ok');
