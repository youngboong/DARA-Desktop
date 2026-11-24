// Pure helpers for the fit plot's hkl labels and d-spacing axis (node tests import them).

/** Most hkl labels drawn in one view (the report figure draws at most 14 on a whole pattern). */
export const HKL_MAX_LABELS = 25;
/** Least distance between two hkl labels, in pixels of the 2θ axis (one rotated text line). */
export const HKL_LABEL_PX = 15;
/** Least distance between two d-axis tick labels, in pixels. */
export const D_TICK_PX = 48;

/** Miller indices as printed in papers: (110), (1̄10); indices above 9 are spaced: (1 0 12). */
export function hklText(h, k, l) {
  const values = [h, k, l].map(Number);
  const parts = values.map(value => (value < 0 ? `${-value}̄` : String(value)));
  return `(${parts.join(values.some(value => Math.abs(value) > 9) ? ' ' : '')})`;
}

/**
 * Labels of the strongest reflections inside `[lo, hi]`: greedy by intensity, each at least
 * `minGap` degrees from every label already taken (the report figure's rule), at most `max`.
 * `reflections`: `{x, intensity, ...}`; the picked objects are returned in 2θ order.
 */
export function pickHklLabels(reflections, [lo, hi], minGap, max = HKL_MAX_LABELS) {
  const picked = [];
  const inside = reflections.filter(item => Number.isFinite(item.x) && item.x >= lo && item.x <= hi);
  inside.sort((a, b) => (Number(b.intensity) || 0) - (Number(a.intensity) || 0));
  for (const item of inside) {
    if (picked.length >= max) break;
    if (picked.some(other => Math.abs(other.x - item.x) < minGap)) continue;
    picked.push(item);
  }
  return picked.sort((a, b) => a.x - b.x);
}

/** Height in pixels of a rotated "(hkl)" label at `fontPx`, with its 3 px gap above the peak (overbars add no width). */
export function hklLabelPx(text, fontPx = 11) {
  return 3 + 0.62 * fontPx * [...String(text)].filter(char => !/\p{M}/u.test(char)).length;
}

/**
 * Top of the intensity axis (data units; log10 units on a log axis) that leaves each label room
 * above its anchor: a label of `px` pixels at `y` fits when (top − y) / (top − bottom) · areaPx ≥ px.
 * `labels`: `{y, px}`; never lower than the current top.
 */
export function topForLabels(labels, [bottom, top], areaPx) {
  let needed = top;
  labels.forEach(({ y, px }) => {
    if (areaPx > px && Number.isFinite(y)) needed = Math.max(needed, (y * areaPx - px * bottom) / (areaPx - px));
  });
  return needed;
}

// Steps of the d values tried, coarse first: 10 and 5, whole numbers, halves (2.5), then
// 0.2, 0.1 ... so the axis reads 10, 5, 4, 3, 2.5, 2, 1.8, 1.6, 1.5 ... as room allows.
const D_STEPS = [10, 5, 1, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01];

/**
 * Tick positions (2θ) and labels (d in Å) of a d-spacing axis over the 2θ window `[lo, hi]`
 * for `wavelength` Å: nice d values, coarse steps first, each at least `minGap` degrees from the
 * ticks already taken. `null` without a wavelength or window.
 */
export function dAxisTicks([lo, hi], wavelength, minGap) {
  if (!(wavelength > 0) || !(hi > lo)) return null;
  const dAt = twoTheta => wavelength / (2 * Math.sin((twoTheta * Math.PI) / 360));
  const dMin = dAt(hi);
  const dMax = lo > 0 ? dAt(lo) : Infinity;
  const ticks = [];
  for (const step of D_STEPS) {
    const first = Math.max(1, Math.ceil(dMin / step - 1e-9));
    const last = Math.min(Math.floor(dMax / step + 1e-9), first + 400); // bounded at tiny 2θ
    for (let n = first; n <= last; n += 1) {
      const d = Number((n * step).toFixed(4));
      // No odd values such as 3.42 between 4 and 3: a step finer than 1/40 of d is used only when
      // the window itself is that narrow (zoomed in).
      if (step < Math.min(d / 40, (dMax - dMin) / 8)) continue;
      const sine = wavelength / (2 * d);
      if (sine >= 1) continue;
      const x = (360 / Math.PI) * Math.asin(sine);
      if (x < lo || x > hi || ticks.some(tick => Math.abs(tick.x - x) < minGap)) continue;
      ticks.push({ x, d });
    }
  }
  ticks.sort((a, b) => a.x - b.x);
  return { tickvals: ticks.map(tick => tick.x), ticktext: ticks.map(tick => String(tick.d)) };
}
