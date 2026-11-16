import assert from 'node:assert/strict';
import {
  MAX_REFINE, combinationText, defaultSelection, flagTags, latticePercent, overlayTraces, percent, pollDelay, poolText,
  rankingRows, refineSelection, sourceInfo,
} from '../src/dara/server/ui/src/components/quickRanking.mjs';
import {
  cachedText, durationText, jobOutcome, parseElements, progressPercent, progressText, sizeText,
} from '../src/dara/server/ui/src/components/structureLibrary.mjs';

const response = {
  status: 'done',
  elapsed_s: 1.234,
  pool: { rows: 60, cached: 55, missing_cif: 3, simulated: 2, sources: { cod: 50, mp: 4 }, candidates: 54 },
  profile: { two_theta: [10, 20, 30], net: [0, 5, 1] },
  ranking: [
    { rank: 1, formula: 'Y2O3', spacegroup: 206, spacegroup_symbol: 'Ia-3', source: 'cod:1513300', score: 0.3408,
      explained: 0.35, matched: 5, expected: 5, lattice_scale: 0.997, flags: ['polymorph', 'picked'], duplicates: 2,
      sticks: [[29.1, 120.5], [33.8, 30]] },
    { rank: 2, formula: 'YFeO3', spacegroup: 62, source: 'mp:mp-1', score: 0.31, explained: 0.32, matched: 11,
      expected: 11, lattice_scale: 1.004, flags: ['picked', 'weak', 'variant of Y2O3', 'theoretical'], duplicates: 0,
      sticks: [[32.0, 50]] },
    { rank: 3, formula: 'Fe2O3', spacegroup: 167, source: 'cod:2108027', score: 0.17, explained: 0.18, matched: 11,
      expected: 11, lattice_scale: 1.0, flags: [], duplicates: 0, sticks: [] },
  ],
  combination: [
    { step: 1, formula: 'Y2O3', source: 'cod:1513300', gain: 0.346, weak: false },
    { step: 2, formula: 'YFeO3', source: 'mp:mp-1', gain: 0.05, weak: true },
  ],
};

// display strings
assert.equal(percent(0.3408), '34 %');
assert.equal(percent(undefined), '—');
assert.equal(latticePercent(0.997), '-0.3 %');
assert.equal(latticePercent(1.004), '+0.4 %');
assert.equal(latticePercent(1.0), '0.0 %');
assert.deepEqual(sourceInfo('cod:1513300'), { label: 'COD 1513300', url: 'https://www.crystallography.net/cod/1513300.html' });
assert.equal(sourceInfo('mp:mp-1').label, 'MP mp-1');
assert.deepEqual(flagTags(['weak', 'variant of Y2O3', 'other']).map(tag => tag.color), ['orange', 'blue', 'default']);

const rows = rankingRows(response);
assert.equal(rows[0].key, 'cod:1513300');
assert.equal(rows[0].peaksText, '5/5');
assert.equal(rows[0].spacegroupText, 'Ia-3 (206)');
assert.equal(rows[1].spacegroupText, 62, 'no symbol: the number');
assert.equal(rows[0].scoreText, '34 %');
assert.deepEqual(rankingRows(null), []);

assert.equal(combinationText(response.combination), 'Y2O3 35 % + YFeO3 5 % (weak)');
assert.equal(poolText(response), 'Ranked 54 candidates (COD 50, MP 4) in 1.2 s · 2 simulated now · 3 not downloaded');

// selection: weak picks are not ticked by default; refine keeps ranking order
assert.deepEqual(defaultSelection(response), ['cod:1513300']);
assert.deepEqual(refineSelection(response, ['cod:2108027', 'cod:1513300']), {
  referenceIds: ['cod:1513300', 'cod:2108027'], formulas: ['Y2O3', 'Fe2O3'],
});
assert.equal(MAX_REFINE, 12);

// overlay: the net pattern plus one stick trace per selected row, sticks as vertical segments
const traces = overlayTraces(response, ['mp:mp-1', 'cod:1513300']);
assert.equal(traces.length, 3);
assert.equal(traces[0].name, 'Measured (background removed)');
assert.deepEqual(traces[1].x, [29.1, 29.1, null, 33.8, 33.8, null]);
assert.deepEqual(traces[1].y, [0, 120.5, null, 0, 30, null]);
assert.notEqual(traces[1].line.color, traces[2].line.color);
assert.deepEqual(overlayTraces(null, []), []);

assert.equal(pollDelay(0), 700);
assert.equal(pollDelay(10), 2000);

// structure library helpers
assert.deepEqual(parseElements('li, FE o;O  xx1 Zn'), ['Li', 'Fe', 'O', 'Zn']);
assert.equal(sizeText(340.4), '340 MB');
assert.equal(sizeText(2048), '2.0 GB');
assert.equal(durationText(230.7), 'about 3 h 51 min');
assert.equal(durationText(12), 'about 12 min');
assert.equal(progressPercent({ done: 5, total: 20 }), 25);
assert.equal(progressPercent({ done: 0, total: 0 }), null);
assert.equal(progressText({ done: 1200, total: 20167, eta_s: 600, message: '' }), '1,200 of 20,167 · 10 min left');
assert.equal(cachedText('COD', { total: 20167, cached: 1171 }), 'COD: 1,171 of 20,167 downloaded');
assert.equal(cachedText('MP', { total: null, cached: null }), 'MP: not listed yet (listed when downloading)');
assert.equal(jobOutcome({ running: true }), null);
assert.equal(jobOutcome({ running: false, exit_code: 0 }).type, 'success');
assert.equal(jobOutcome({ running: false, exit_code: 2, last: { message: 'Stopped' } }).text, 'Stopped');
assert.equal(jobOutcome({ running: false, exit_code: 1 }).type, 'error');
