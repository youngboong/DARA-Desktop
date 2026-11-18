/**
 * "Advanced analysis tools": one per-browser preference (off by default) that shows the
 * specialist tools (size / strain, unit cell, RIR, Le Bail, instrument FWHM curve). The default
 * screens keep what analysts use on most patterns.
 */

export const ADVANCED_KEY = 'dara-advanced-tools';
// Fired on window when the preference changes in this page (other tabs get the storage event).
export const ADVANCED_EVENT = 'dara-advanced-tools';

/** The preference from `storage` (localStorage); off when it is unset or storage is unavailable. */
export function readAdvanced(storage) {
  try {
    return storage?.getItem(ADVANCED_KEY) === '1';
  } catch (error) {
    return false;
  }
}

/** Stores the preference; false when storage is unavailable (the page keeps its own state). */
export function writeAdvanced(storage, on) {
  try {
    if (on) storage.setItem(ADVANCED_KEY, '1');
    else storage.removeItem(ADVANCED_KEY);
    return true;
  } catch (error) {
    return false;
  }
}

/** Peaks-tab views that need the advanced tools. */
export const ADVANCED_PEAK_VIEWS = new Set(['size', 'cell']);

/** The Peaks-tab view to show: an advanced view falls back to "Observed peaks" while the tools are off. */
export const shownPeakView = (view, advanced) => (!advanced && ADVANCED_PEAK_VIEWS.has(view) ? 'observed' : view);
