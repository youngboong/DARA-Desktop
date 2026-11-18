import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MAX_REFINE, SORTERS, assignColors, combinationText, defaultSelection, flagTags, latticePercent, overlayTraces, percent,
  pollDelay, poolText, progressText as rankingProgress, rankingRows, refineSelection, requestKey, rowKeyAction, sourceInfo,
  toggleKey,
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

assert.equal(combinationText(response.combination), 'Y2O3 + YFeO3 (weak)', 'phase names only: a gain is no amount');
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

// stable colours: a row keeps its colour when another is deselected; a new row takes a free colour
const colors = assignColors({}, ['a', 'b', 'c']);
assert.equal(new Set(Object.values(colors)).size, 3);
const fewer = assignColors(colors, ['a', 'c']);
assert.equal(fewer.c, colors.c);
assert.equal(assignColors(fewer, ['a', 'c', 'd']).d, colors.b, 'the freed colour is reused');
assert.equal(Object.keys(assignColors({}, Array.from({ length: 12 }, (_, i) => `k${i}`))).length, 12, 'more rows than colours');

// focused row: a dotted preview when not selected, bold when selected; colours follow the map
const preview = overlayTraces(response, ['cod:1513300'], { 'cod:1513300': '#123456' }, 'cod:2108027');
assert.equal(preview.length, 3);
assert.equal(preview[1].line.color, '#123456');
assert.equal(preview[2].line.dash, 'dot');
assert.equal(overlayTraces(response, ['cod:1513300'], {}, 'cod:1513300')[1].line.width, 3);
assert.equal(overlayTraces(response, [], {}, 'nope').length, 1);

// pool line: new optional counts appear only when > 0
assert.equal(poolText({ ...response, pool: { ...response.pool, downloaded: 12, unavailable: 3, missing_cif: 0, simulated: 0 } }),
  'Ranked 54 candidates (COD 50, MP 4) in 1.2 s · 12 downloaded now · 3 unavailable');
assert.equal(poolText({ pool: { candidates: 0, downloaded: 0, unavailable: 0 } }), 'Ranked 0 candidates');

// progress: phase, counts and time are optional (old servers send only elapsed_s)
assert.equal(rankingProgress({ phase: 'downloading', done: 1200, total: 2000, elapsed_s: 8.4 }), 'Downloading structures · 1,200 of 2,000 · 8 s');
assert.equal(rankingProgress({ phase: 'simulating', total: 0, elapsed_s: 0.2 }), 'Simulating reference patterns');
assert.equal(rankingProgress({ elapsed_s: 3 }), 'Ranking candidates · 3 s');
assert.equal(rankingProgress(undefined), 'Ranking candidates');

// sorting
const byRank = [...rows].sort(SORTERS.score).map(row => row.rank);
assert.deepEqual(byRank, [3, 2, 1]);
assert.deepEqual([...rows].sort(SORTERS.lattice).map(row => row.rank), [3, 1, 2]);
assert.deepEqual([...rows].sort(SORTERS.peaks).map(row => row.rank), [1, 2, 3], 'equal share: more peaks last');
assert.equal(SORTERS.lattice({}, {}), 0);
assert.equal(SORTERS.peaks({ matched: 0, expected: 0 }, { matched: 1, expected: 2 }) < 0, true);

// keyboard and selection
assert.equal(rowKeyAction('ArrowDown'), 'next');
assert.equal(rowKeyAction('ArrowUp'), 'previous');
assert.equal(rowKeyAction(' '), 'toggle');
assert.equal(rowKeyAction('Enter'), null);
assert.deepEqual(toggleKey(['a', 'b'], 'a'), ['b']);
assert.deepEqual(toggleKey(['b'], 'a'), ['b', 'a']);

// request identity: same inputs, same key; another file or value, another key
const file = { name: 'x.xy', size: 10, lastModified: 1 };
assert.equal(requestKey([['pattern_file', file], ['elements', '["Fe"]']]), requestKey([['pattern_file', { ...file }], ['elements', '["Fe"]']]));
assert.notEqual(requestKey([['pattern_file', file]]), requestKey([['pattern_file', { ...file, size: 11 }]]));
assert.notEqual(requestKey([['include_theoretical', 'true']]), requestKey([['include_theoretical', 'false']]));

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

test('residual helpers: status by unexplained peaks, next matches, residual profile, markers, zoom', async () => {
  const { unexplainedStatus, nextMatches, withResidualProfile, unexplainedMarkers, zoomWindow } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  assert.equal(unexplainedStatus({ unexplained_peaks: [[19.3, 400], [31.5, 1300]] }), '2 unexplained peaks');
  assert.equal(unexplainedStatus({ unexplained_peaks: [[19.3, 400]] }), '1 unexplained peak');
  assert.equal(unexplainedStatus({ unexplained_peaks: [] }), 'No unexplained peaks');
  const residual = { next: [
    { rank: 1, formula: 'Co3O4', source: 'cod:9005896', spacegroup: 227, spacegroup_symbol: 'Fd-3m', score: 0.2056, matched: 9, expected: 9, lattice_scale: 1 },
    { rank: 2, formula: 'X', source: 'cod:1', score: 0.1, matched: 1, expected: 2 },
    { rank: 3, formula: 'Y', source: 'cod:2', score: 0.1, matched: 1, expected: 2 },
    { rank: 4, formula: 'Z', source: 'cod:3', score: 0.1, matched: 1, expected: 2 }],
  profile: { two_theta: [10, 11], net: [1, 2] } };
  const next = nextMatches(residual, [{ key: 'cod:9005896' }]);
  assert.equal(next.length, 3);
  assert.deepEqual([next[0].key, next[0].scoreText, next[0].peaksText, next[0].inRanking], ['cod:9005896', '21 %', '9/9', true]);
  assert.equal(next[1].inRanking, false);
  assert.deepEqual(withResidualProfile({ profile: { two_theta: [1] }, ranking: [] }, residual).profile, residual.profile);
  assert.deepEqual(withResidualProfile({ profile: 'p' }, null).profile, 'p');
  const markers = unexplainedMarkers([[19.3, 400], [31.5, 1300]]);
  assert.deepEqual([markers.x, markers.y, markers.meta], [[19.3, 31.5], [0, 0], 'unexplained']);
  assert.deepEqual(unexplainedMarkers([[19.3, 400]], 80).y, [80]);
  assert.deepEqual(zoomWindow(11, [10, 100]), [10, 13]);
  assert.deepEqual(zoomWindow(50, [10, 100], 1), [49, 51]);
});

test('intensity range of a zoom window', async () => {
  const { windowRange } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  const profile = { two_theta: [10, 11, 12, 13], net: [5, 50, 20, 900] };
  assert.deepEqual(windowRange(profile, [10.5, 12.5]), [0, 55.00000000000001]);
  assert.equal(windowRange(profile, [20, 30]), null);
});

test('suggestion: names only, weak picks offered, routine theoretical note in the pool line', async () => {
  const { suggestedKeys, weakNotSelected, rankingNotes } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  assert.deepEqual(suggestedKeys(response), ['cod:1513300', 'mp:mp-1']);
  assert.deepEqual(weakNotSelected(response, ['cod:1513300']), [{ key: 'mp:mp-1', formula: 'YFeO3' }]);
  assert.deepEqual(weakNotSelected(response, ['cod:1513300', 'mp:mp-1']), []);
  const withNote = { ...response, caveats: ['21 computed-only (theoretical) Materials Project structures are hidden; tick it.', 'COD unreachable'] };
  assert.deepEqual(rankingNotes(withNote), ['COD unreachable']);
  assert.match(poolText(withNote), / · 21 theoretical hidden$/);
});

test('wavelength mismatch of a ranking: anode names from either shape', async () => {
  const { mismatchView } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  assert.deepEqual(mismatchView({ recorded: 'Cu', chosen: 'Co' }), { recorded: 'Cu', chosen: 'Co' });
  assert.deepEqual(mismatchView({ code: 'wavelength_mismatch', recorded_angstrom: 1.5406, selected_angstrom: 1.78901 }), { recorded: 'Cu', chosen: 'Co' });
  assert.deepEqual(mismatchView({ recorded_angstrom: 1.5406, selected_angstrom: 0.72932 }), { recorded: 'Cu', chosen: '0.7293 Å' });
  assert.equal(mismatchView(null), null);
  assert.equal(mismatchView({ recorded_angstrom: null }), null);
});

test('background view: measured and background only when the response carries the curve; settings clamped', async () => {
  const { backgroundTraces, backgroundSettings } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  assert.equal(backgroundTraces(undefined), null, 'older servers: no toggle');
  assert.equal(backgroundTraces({ settings: { method: 'rolling_minimum' } }), null, 'an unrankable pattern: settings only');
  const traces = backgroundTraces({ two_theta: [10, 20], observed: [11, 12], intensity: [10, 10] });
  assert.deepEqual(traces.map(trace => trace.name), ['Measured', 'Background']);
  assert.deepEqual(traces[1].y, [10, 10]);
  assert.equal(backgroundTraces({ two_theta: [10, 20], observed: [1], intensity: [1, 2] }), null);
  assert.deepEqual(backgroundSettings(null), { method: 'auto', granularity: 20, bending_factor: 1 });
  assert.deepEqual(backgroundSettings({ method: 'sonneveld_visser', granularity: 400, bending_factor: -1 }),
    { method: 'sonneveld_visser', granularity: 200, bending_factor: 0 });
  assert.equal(backgroundSettings({ method: 'rolling_minimum' }).method, 'auto', 'the server default is "auto"');
});

test('manual background: points seeded on the automatic curve, or the ones in use; settings with and without them', async () => {
  const { startingPoints, withPoints } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  const x = Array.from({ length: 201 }, (_, i) => 10 + i * 0.1);
  const intensity = x.map(value => 100 + 400 * Math.exp(-(value - 10) / 3)); // a low-angle rise
  const observed = intensity.map((value, i) => value + (Math.abs(x[i] - 20) < 0.3 ? 2000 : 0)); // one peak
  const points = startingPoints({ two_theta: x, observed, intensity, settings: { method: 'auto' } });
  assert.ok(points.length >= 3 && points.length <= 20, 'a modest number');
  assert.deepEqual([points[0][0], points.at(-1)[0]], [10, 30], 'both range ends');
  assert.ok(points.every(([at]) => Math.abs(at - 20) >= 0.3), 'never on the peak');
  assert.ok(points.every((point, i) => !i || point[0] > points[i - 1][0]), 'sorted');
  const inUse = [[10, 5], [30, 6]];
  assert.deepEqual(startingPoints({ two_theta: x, observed, intensity, settings: { anchors: inUse } }), inUse);
  assert.deepEqual(startingPoints(undefined), []);
  assert.deepEqual(withPoints({ method: 'sonneveld_visser', granularity: 30, bending_factor: 2, anchors: [[1, 1]] }, inUse),
    { method: 'sonneveld_visser', granularity: 30, bending_factor: 2, anchors: inUse });
  assert.deepEqual(withPoints({ method: 'auto', anchors: inUse }, []), { method: 'auto', granularity: 20, bending_factor: 1 },
    'back to automatic drops the points');
});

test('stored-pattern ranking: the setup options as typed JSON', async () => {
  const { taskRankingBody } = await import('../src/dara/server/ui/src/components/quickRanking.mjs');
  assert.deepEqual(taskRankingBody([
    ['source_task_id', '121'], ['elements', '["Co","O"]'], ['precursor_formulas', '[]'], ['wavelength', 'Cu'],
    ['two_theta_min', '15'], ['excluded_ranges', '[[20,21]]'], ['use_cod', 'true'], ['use_mp', 'false'],
    ['mp_e_hull_cutoff', '0.05'], ['max_mp_structures', '100'], ['library_cif_ids', '[]'], ['include_theoretical', 'false'],
    ['background', '{"method":"sonneveld_visser","granularity":30}'],
  ]), {
    include_theoretical: false, library_cif_ids: [], elements: ['Co', 'O'], precursor_formulas: [], wavelength: 'Cu',
    two_theta_min: 15, excluded_ranges: [[20, 21]], use_cod: true, use_mp: false, mp_e_hull_cutoff: 0.05,
    max_mp_structures: 100, background: { method: 'sonneveld_visser', granularity: 30 },
  });
  assert.deepEqual(taskRankingBody([['include_theoretical', 'true']]), { include_theoretical: true, library_cif_ids: [], elements: [], precursor_formulas: [] });
});
