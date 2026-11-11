import assert from 'node:assert/strict';
import test from 'node:test';
import { TRAY_FILLS, compositionSegments, fillIndex } from '../src/dara/server/ui/src/components/composition.mjs';

const total = segments => segments.reduce((sum, s) => sum + s.share, 0);

test('composition shares follow wt% and fill exactly 100', () => {
  const s = compositionSegments(['a', 'b'], { a: 60, b: 30 });
  assert.ok(Math.abs(total(s) - 100) < 1e-9);
  assert.ok(Math.abs(s[0].share - 66.6667) < 1e-3);
  assert.equal(s[1].wt, 30);
});

test('missing amounts share the rest; nothing known splits equally', () => {
  const s = compositionSegments(['a', 'b', 'c'], { a: 80 });
  assert.deepEqual(s.map(x => x.wt), [80, null, null]);
  assert.ok(Math.abs(s[1].share - 10) < 1e-9 && Math.abs(total(s) - 100) < 1e-9);
  assert.deepEqual(compositionSegments(['a', 'b'], {}).map(x => x.share), [50, 50]);
  assert.deepEqual(compositionSegments([], {}), []);
});

test('a phase keeps its fill across trays', () => {
  const fills = fillIndex(['x', 'y', 'x']);
  assert.equal(fills.size, 2);
  assert.equal(fills.get('x'), TRAY_FILLS[0]);
  assert.equal(fills.get('y'), TRAY_FILLS[1]);
});

test('fractional formulas are tidied for display', async () => {
  const { tidyFormula } = await import('../src/dara/server/ui/src/components/composition.mjs');
  assert.equal(tidyFormula('Y31.99952O47.99904'), 'Y2O3');
  assert.equal(tidyFormula('Fe7.7841O12'), 'Fe7.784O12');
  assert.equal(tidyFormula('Fe2O3'), 'Fe2O3');
  assert.equal(tidyFormula('Ia-3'), 'Ia-3');
  assert.equal(tidyFormula('Zn0.5Co0.5O'), 'Zn0.5Co0.5O');
  assert.equal(tidyFormula('La(HO)3'), 'La(OH)3');
});
