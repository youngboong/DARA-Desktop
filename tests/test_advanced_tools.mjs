import assert from 'node:assert/strict';
import test from 'node:test';
import { ADVANCED_KEY, readAdvanced, shownPeakView, writeAdvanced } from '../src/dara/server/ui/src/components/advancedTools.mjs';

const memory = () => {
  const items = new Map();
  return { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, String(value)), removeItem: key => items.delete(key) };
};
const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); } };

test('off by default, remembered when turned on, off again', () => {
  const storage = memory();
  assert.equal(readAdvanced(storage), false);
  assert.equal(writeAdvanced(storage, true), true);
  assert.equal(storage.getItem(ADVANCED_KEY), '1');
  assert.equal(readAdvanced(storage), true);
  writeAdvanced(storage, false);
  assert.equal(readAdvanced(storage), false);
});

test('storage failure reads as off and is reported', () => {
  assert.equal(readAdvanced(broken), false);
  assert.equal(readAdvanced(null), false);
  assert.equal(writeAdvanced(broken, true), false);
  assert.equal(writeAdvanced(null, true), false);
});

test('an advanced Peaks view falls back to Observed peaks while the tools are off', () => {
  assert.equal(shownPeakView('size', false), 'observed');
  assert.equal(shownPeakView('cell', false), 'observed');
  assert.equal(shownPeakView('calculated', false), 'calculated');
  assert.equal(shownPeakView('size', true), 'size');
  assert.equal(shownPeakView('cell', true), 'cell');
});
