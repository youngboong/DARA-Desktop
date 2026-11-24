import assert from 'node:assert/strict';
import {
  compareVersions, isBusy, parseVersion, refusal, showUpdateTag, updateStateText,
} from '../src/dara/server/ui/src/components/appUpdate.mjs';

// Numeric per component, leading "v" ignored, missing parts count as 0.
assert.deepEqual(parseVersion('v1.3.10'), [1, 3, 10]);
assert.equal(parseVersion('1.3.1-rc1'), null);
assert.equal(compareVersions('1.3.10', '1.3.9'), 1);
assert.equal(compareVersions('1.3.1', 'v1.3.1.0'), 0);
assert.equal(compareVersions('1.3.0', '1.3.1'), -1);
assert.equal(compareVersions('x', '1.3.1'), null);

const base = { current: '1.3.1', latest: '1.3.2', packaged: true, available: true, state: 'idle', progress: null, error: null };
assert.equal(showUpdateTag(base), true);
assert.equal(showUpdateTag({ ...base, packaged: false }), false);  // a source checkout never offers updates
assert.equal(showUpdateTag({ ...base, latest: '1.3.1' }), false);
assert.equal(showUpdateTag({ ...base, available: false }), false);
assert.equal(showUpdateTag(null), false);

assert.equal(updateStateText({ ...base, available: false, latest: '1.3.1' }), 'DARA 1.3.1 is up to date');
assert.equal(updateStateText({ ...base, state: 'downloading', progress: 42 }), 'Downloading 42 %');
assert.equal(updateStateText({ ...base, state: 'verifying' }), 'Verifying...');
assert.equal(updateStateText({ ...base, state: 'installing' }), 'Installing: DARA will close and reopen');
assert.equal(updateStateText({ ...base, state: 'error', error: 'damaged' }), 'damaged');
assert.equal(updateStateText({ ...base, available: false, error: 'offline' }), 'offline');
assert.equal(updateStateText(null), 'Checking...');
assert.ok(isBusy({ state: 'verifying' }) && !isBusy({ state: 'error' }) && !isBusy(null));

assert.deepEqual(refusal({ detail: { message: 'Analyses are running', force: true } }), { message: 'Analyses are running', force: true });
assert.deepEqual(refusal({ detail: 'Up to date' }), { message: 'Up to date', force: false });
assert.equal(refusal(null).force, false);

console.log('app update UI helpers ok');
assert.equal(updateStateText({ state: 'ready', available: false }), 'The installed version could not be read.', 'never "DARA undefined"');
