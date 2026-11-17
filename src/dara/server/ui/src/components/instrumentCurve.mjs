/** Instrument FWHM curve (Caglioti) of a line-broadening standard, and the instrument-broadening query of the peak views. */

/** FWHM (°) of `curve` ({u, v, w, two_theta_min, two_theta_max}) at `twoTheta`, the edge value outside its range; null where not positive. */
export function cagliotiFwhm(curve, twoTheta) {
  const angle = Math.min(Math.max(Number(twoTheta), curve.two_theta_min), curve.two_theta_max);
  const t = Math.tan((angle * Math.PI) / 360);
  const square = curve.u * t * t + curve.v * t + curve.w;
  return square > 0 ? Math.sqrt(square) : null;
}

/** The Caglioti line over the covered 2θ range, `points` samples: `{x, y}`. */
export function cagliotiLine(curve, points = 120) {
  const lo = curve.two_theta_min;
  const hi = curve.two_theta_max;
  const x = Array.from({ length: points }, (_, i) => lo + ((hi - lo) * i) / (points - 1));
  return { x, y: x.map(angle => cagliotiFwhm(curve, angle)) };
}

/** "LaB6, 2026-11-17" for a stored curve or its summary. */
export const curveText = curve => [curve?.standard, curve?.measured_on].filter(Boolean).join(', ');

/** "2θ 21.4–137.5°" of a curve's covered range ([lo, hi] or a curve record). */
export function curveRangeText(range) {
  const [lo, hi] = Array.isArray(range) ? range : [range?.two_theta_min, range?.two_theta_max];
  return Number.isFinite(lo) && Number.isFinite(hi) ? `2θ ${lo.toFixed(1)}–${hi.toFixed(1)}°` : '';
}

/**
 * Query of the peak views for Scherrer constant `k` and the instrument broadening `mode`:
 * null lets the server choose (its curve when the profile has one, else the `constant` when
 * entered, else none); 'curve', 'constant' (with the `constant` FWHM in degrees; none while it
 * is empty) or 'none'.
 */
export function instrumentQuery({ k, mode = null, constant = null }) {
  const parts = [`k=${k}`];
  const value = Number.isFinite(constant) && constant > 0 ? constant : null;
  if (mode === null) {
    if (value) parts.push(`instrument_fwhm=${value}`);
  } else if (mode === 'constant' && value) {
    parts.push('instrument=constant', `instrument_fwhm=${value}`);
  } else {
    parts.push(`instrument=${mode === 'curve' ? 'curve' : 'none'}`);
  }
  return parts.join('&');
}
