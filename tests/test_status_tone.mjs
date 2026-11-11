import assert from 'node:assert/strict';
import { adoptedTone, gofTone, levelTone, peakCountTone, severityTone, stateTone, toneColor } from '../src/dara/server/ui/src/components/statusTone.mjs';

// Completed is plain text; only problems and activity carry colour.
assert.equal(stateTone('COMPLETED'), null);
assert.equal(stateTone('FIZZLED'), 'fail');
assert.equal(stateTone('CANCELLED'), 'neutral');
assert.equal(stateTone('RUNNING'), 'info');
assert.equal(stateTone('CANCELLING'), 'review');
assert.equal(stateTone(undefined), null);

// Red only for failures; amber for review.
assert.equal(levelTone('high'), 'ok');
assert.equal(levelTone('check'), 'review');
assert.equal(levelTone('ambiguous'), 'review');
assert.equal(levelTone('expert'), 'fail');
assert.equal(levelTone('weak'), 'fail');
assert.equal(levelTone('unknown'), 'neutral');
assert.equal(severityTone('warning'), 'review');
assert.equal(severityTone('severe'), 'fail');

// Metrics stay neutral unless a threshold is violated.
assert.equal(gofTone(1.4), 'neutral');
assert.equal(gofTone(2.5), 'review');
assert.equal(gofTone(undefined), 'neutral');
assert.equal(peakCountTone(0), 'neutral');
assert.equal(peakCountTone(3), 'review');
assert.equal(adoptedTone(), 'info');

assert.equal(toneColor(null), undefined);
assert.equal(toneColor('neutral'), 'default');
assert.equal(toneColor('fail'), 'red');
console.log('status tone tests passed');
