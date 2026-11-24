import assert from 'node:assert/strict';
import test from 'node:test';
import {
  adoptedRwp, apiQuery, backQuery, chunks, deletable, displayName, exportFilename, filtersActive, formatDate, hasActiveRows,
  mergeCsv, mergeUsers, middleSplit, parseResultsView, partitionSelected, resultsSearch, retryable, rowStatusLine, verdictView,
} from '../src/dara/server/ui/src/components/resultsList.mjs';
import { listContext, resultsHref } from '../src/dara/server/ui/src/components/sampleNav.mjs';
import { guardHistory, leaveWarning, setLeaveGuard } from '../src/dara/server/ui/src/components/leaveGuard.mjs';

test('the Results view round-trips through the address', () => {
  const view = parseResultsView('?page=3&limit=500&user=Ann&name=ZnO&state=FIZZLED,BOGUS&verdict=expert,check&sort=rwp&order=desc&selected=41,9,9,x,22');
  assert.deepEqual(view, {
    page: 3, limit: 100, user: 'Ann', name: 'ZnO', state: ['FIZZLED'], verdict: ['expert', 'check'],
    sort: 'rwp', order: 'desc', selected: [41, 9, 22], exact: false,
  });
  assert.equal(resultsSearch(view), 'page=3&limit=100&user=Ann&name=ZnO&state=FIZZLED&verdict=expert,check&sort=rwp&order=desc&selected=41,9,22');
  assert.deepEqual(parseResultsView(`?${resultsSearch(view)}`), view);
  // The server gets no selection and no empty user.
  assert.equal(apiQuery(view), 'page=3&limit=100&user=Ann&name=ZnO&state=FIZZLED&verdict=expert%2Ccheck&sort=rwp&order=desc');
  assert.equal(apiQuery({ ...view, user: '' }, { page: 1, limit: 100 }).includes('user='), false);
});

test('no user in the address means the remembered user; an empty one means all users', () => {
  assert.equal(parseResultsView('', 'ann').user, 'ann');
  // My tasks: the whole user name, kept in the address and sent to the server
  const mine = parseResultsView('', 'ann');
  assert.equal(mine.exact, true);
  assert.equal(resultsSearch(mine), 'page=1&limit=50&user=ann&exact=1');
  assert.deepEqual(parseResultsView(`?${resultsSearch(mine)}`), mine);
  assert.ok(apiQuery(mine).includes('user=ann&user_exact=true'));
  assert.equal(parseResultsView('?user=ann').exact, false, 'a typed user filter stays "contains"');
  assert.equal(parseResultsView('?user=', 'ann').user, '');
  assert.equal(parseResultsView('', null).user, '');
  assert.equal(resultsSearch(parseResultsView('?user=', 'ann')), 'page=1&limit=50&user=');
  const defaults = parseResultsView('?page=0&limit=-5&sort=evil&order=up');
  assert.deepEqual([defaults.page, defaults.limit, defaults.sort, defaults.order], [1, 50, null, null]);
  assert.equal(filtersActive(defaults), false);
  assert.equal(filtersActive({ ...defaults, verdict: ['high'] }), true);
});

test('a task link returns to the same view, selection included', () => {
  const view = parseResultsView('?page=2&user=&state=COMPLETED&selected=1,2');
  const context = listContext(`?id=2&list=1,2&back=${encodeURIComponent(backQuery(view))}`, 2);
  assert.equal(resultsHref(context), '/results?page=2&limit=50&user=&state=COMPLETED&selected=1%2C2');
  const many = { ...view, selected: Array.from({ length: 600 }, (_, index) => 100000 + index) };
  assert.ok(backQuery(many).endsWith('&selected=kept'), 'too long for the task link: kept in the tab');
  assert.deepEqual(parseResultsView(`?${backQuery(many)}`).selected, [], 'restored by the page, not the parser');
});

test('verdicts, states and the line under the name', () => {
  const done = summary => ({ state: 'COMPLETED', summary: { schema: 5, final_rwp: 8, final_phases: ['a'], ...summary } });
  assert.equal(verdictView(done({ confidence_overall: 'high' })).text, 'High');
  assert.deepEqual([verdictView(done({ confidence_overall: 'expert' })).text, verdictView(done({ confidence_overall: 'expert' })).tone], ['Expert review', 'escalate']);
  assert.equal(verdictView(done({ final_rwp: null, final_phases: [] })).text, 'No fit');
  assert.equal(verdictView(done({ analysis_mode: 'identification' })), null);
  assert.equal(verdictView({ state: 'RUNNING' }), null);
  assert.deepEqual(rowStatusLine({ state: 'RUNNING', progress: { stage: 'Screening reference phases', percent: 45 } }),
    { tone: 'info', percent: 45, text: 'Screening reference phases · 45%' });
  assert.equal(rowStatusLine({ state: 'RUNNING' }), null, 'nothing beyond the Running tag yet');
  assert.equal(rowStatusLine({ state: 'RUNNING', progress: { percent: 10 } }).text, '10%');
  assert.equal(rowStatusLine({ state: 'READY', queue_position: 3 }).text, 'Position 3 in the queue');
  assert.equal(rowStatusLine({ state: 'READY' }), null);
  assert.equal(rowStatusLine({ state: 'CANCELLING' }), null);
  assert.equal(rowStatusLine({ state: 'FIZZLED', error_summary: { title: 'Out of memory' } }).text, 'Out of memory');
  assert.equal(rowStatusLine({ state: 'COMPLETED' }), null);
  assert.equal(hasActiveRows([{ state: 'COMPLETED' }, { state: 'READY' }]), true);
  assert.equal(hasActiveRows([{ state: 'COMPLETED' }]), false);
});

test('Rwp of the adopted fit, as the server sorts it', () => {
  assert.equal(adoptedRwp({ final_rwp: 7.5, best_rwp: 9 }), 7.5);
  assert.equal(adoptedRwp({ adopted_rwp: 6, final_rwp: 7.5 }), 6);
  assert.equal(adoptedRwp({ best_rwp: 9 }), 9);
});

test('names: sample first, middle ellipsis keeps the distinguishing end, one date format', () => {
  assert.deepEqual(displayName({ name: 'a.xy', sample: 'ZnO 300' }), { primary: 'ZnO 300', secondary: 'a.xy' });
  assert.deepEqual(displayName({ name: 'a.xy', sample: '' }), { primary: 'a.xy', secondary: null });
  assert.deepEqual(displayName({ name: 'a.xy', sample: 'a.xy' }), { primary: 'a.xy', secondary: null });
  assert.deepEqual(middleSplit('V2O5-2ZnO_400C_long_measurement--8min.xrdml'), ['V2O5-2ZnO_400C_long_measurement', '--8min.xrdml']);
  assert.deepEqual(middleSplit('short.xy'), ['short.xy', '']);
  const [head, tail] = middleSplit('abcdefghijklmnopqrstuvwxyzabcdefghij');
  assert.equal(head + tail, 'abcdefghijklmnopqrstuvwxyzabcdefghij');
  assert.equal(tail.length, 10);
  assert.equal(formatDate('2026-11-12 15:40:07'), '2026-11-12 15:40');
  assert.equal(formatDate('2026-11-12T15:40:07+09:00'), '2026-11-12 15:40');
  assert.equal(formatDate(null), '');
});

test('bulk actions skip rows they do not apply to', () => {
  const rows = new Map([[1, { state: 'RUNNING' }], [2, { state: 'COMPLETED' }], [3, { state: 'FIZZLED', error_summary: { temporary: true } }]]);
  assert.deepEqual(partitionSelected([1, 2, 3, 4], rows, deletable), { ids: [2, 3], skipped: [1, 4] }, 'a row not loaded is never acted on');
  assert.equal(retryable(rows.get(3)), true);
  assert.equal(retryable({ state: 'FIZZLED', error_summary: { temporary: false } }), false);
});

test('exports in parts of 50 merge into one CSV with one header', () => {
  assert.deepEqual(chunks([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.equal(chunks(Array.from({ length: 120 }, (_, index) => index)).length, 3);
  const merged = mergeCsv(['﻿task_id,phase\r\n41,ZnO\r\n', 'task_id,phase\r\n9,Fe2O3\r\n22,Y2O3\r\n']);
  assert.equal(merged, '﻿task_id,phase\r\n41,ZnO\r\n9,Fe2O3\r\n22,Y2O3\r\n');
  assert.equal(mergeCsv(['h\r\n1']), '﻿h\r\n1\r\n');
  assert.equal(mergeCsv([]), '');
  assert.equal(exportFilename(30, new Date(2026, 10, 18)), 'dara_tasks_20261118_30.csv');
});

test('the leave guard asks only while a page registers unsaved work', () => {
  assert.equal(leaveWarning(), null);
  const off = setLeaveGuard(() => 'The pattern is not kept.');
  assert.equal(leaveWarning(), 'The pattern is not kept.');
  off();
  assert.equal(leaveWarning(), null);
});

test('Back to another page with unsaved work is held from the router and asked', () => {
  const listeners = [];
  const steps = [];
  globalThis.window = {
    location: { pathname: '/', search: '', hash: '' },
    addEventListener: (type, fn) => listeners.push(fn),
    history: { go: step => steps.push(step) },
  };
  const asked = [];
  guardHistory((text, leave) => asked.push([text, leave]), () => {});
  const pop = () => { const event = { stopped: false, stopImmediatePropagation() { this.stopped = true; } }; listeners.forEach(fn => fn(event)); return event.stopped; };
  window.location.pathname = '/results/';
  assert.equal(pop(), false, 'nothing unsaved: the router goes on');
  window.location.pathname = '/';
  const off = setLeaveGuard(() => 'The pattern is not kept.');
  window.location.pathname = '/results/';
  assert.equal(pop(), true, 'held');
  assert.deepEqual(steps, [1]);
  assert.equal(asked[0][0], 'The pattern is not kept.');
  window.location.pathname = '/';
  assert.equal(pop(), true, 'the step forward again is hidden too (the page is not mounted afresh)');
  assert.equal(pop(), false, 'Back within the page (the search-match view) is its own');
  off();
  delete globalThis.window;
});

test('user suggestions merge spellings that differ only in case', () => {
  const first = mergeUsers([], ['amy', 'Bob', 'unknown', '', null]);
  assert.deepEqual(first, ['amy', 'Bob']);
  assert.equal(mergeUsers(first, ['Amy', 'bob']), first, 'nothing new: the same array');
  assert.deepEqual(mergeUsers(first, ['carol']), ['amy', 'Bob', 'carol']);
});
