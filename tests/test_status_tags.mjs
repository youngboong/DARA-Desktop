import assert from 'node:assert/strict';
import { amountTag, STATUS_TAGS } from '../src/dara/server/ui/src/components/statusTags.mjs';
import { TONE_COLOR } from '../src/dara/server/ui/src/components/statusTone.mjs';

assert.equal(amountTag({}, null), null);
assert.equal(amountTag(undefined, undefined), null);
assert.equal(amountTag({ refinement_status: { level: 'limited' } }, null), null);
assert.deepEqual(amountTag({ refinement_status: { level: 'failed', message: 'm' } }, 'unreliable'),
  { text: 'Refinement failed', tone: 'fail', tooltip: 'm' });
assert.equal(amountTag({}, 'unreliable').text, 'Refinement failed');
assert.equal(amountTag({ refinement_status: { level: 'limited', message: 'Lattice held' } }, 'uncertain').text, 'Uncertain amount');
assert.deepEqual(Object.values(STATUS_TAGS).map(tag => tag.text),
  ['Refinement failed', 'Uncertain amount', 'at limit', 'fixed (DB)', 'indicative', 'not resolvable']);
for (const tag of Object.values(STATUS_TAGS)) assert.ok(tag.tone in TONE_COLOR);

console.log('status tag tests passed');
