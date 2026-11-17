import assert from 'node:assert/strict';
import test from 'node:test';
import { cagliotiFwhm, cagliotiLine, curveRangeText, curveText, instrumentQuery } from '../src/dara/server/ui/src/components/instrumentCurve.mjs';

const curve = { u: 0.01, v: -0.004, w: 0.003, two_theta_min: 20, two_theta_max: 120 };

test('Caglioti FWHM: sqrt(U tan² + V tan + W), edge value outside the covered range', () => {
  const t = Math.tan((60 * Math.PI) / 360);
  assert.ok(Math.abs(cagliotiFwhm(curve, 60) - Math.sqrt(0.01 * t * t - 0.004 * t + 0.003)) < 1e-12);
  assert.equal(cagliotiFwhm(curve, 5), cagliotiFwhm(curve, 20));
  assert.equal(cagliotiFwhm(curve, 150), cagliotiFwhm(curve, 120));
  assert.equal(cagliotiFwhm({ ...curve, u: 0, v: 0, w: -1 }, 60), null);
  const line = cagliotiLine(curve, 11);
  assert.equal(line.x.length, 11);
  assert.deepEqual([line.x[0], line.x[10]], [20, 120]);
});

test('curve texts', () => {
  assert.equal(curveText({ standard: 'LaB6', measured_on: '2026-11-17' }), 'LaB6, 2026-11-17');
  assert.equal(curveText(null), '');
  assert.equal(curveRangeText([21.43, 137.5]), '2θ 21.4–137.5°');
  assert.equal(curveRangeText({ two_theta_min: 20, two_theta_max: 120 }), '2θ 20.0–120.0°');
  assert.equal(curveRangeText(null), '');
});

test('instrument query: server default, explicit curve, constant (none while empty), none', () => {
  assert.equal(instrumentQuery({ k: 0.89 }), 'k=0.89');
  assert.equal(instrumentQuery({ k: 0.89, constant: 0.05 }), 'k=0.89&instrument_fwhm=0.05');
  assert.equal(instrumentQuery({ k: 1, mode: 'curve', constant: 0.05 }), 'k=1&instrument=curve');
  assert.equal(instrumentQuery({ k: 1, mode: 'constant', constant: 0.05 }), 'k=1&instrument=constant&instrument_fwhm=0.05');
  assert.equal(instrumentQuery({ k: 1, mode: 'constant', constant: null }), 'k=1&instrument=none');
  assert.equal(instrumentQuery({ k: 1, mode: 'none', constant: 0.05 }), 'k=1&instrument=none');
});
