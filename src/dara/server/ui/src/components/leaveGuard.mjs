// In-app navigation (header links are client-side route changes) does not fire `beforeunload`:
// a page with unsaved work registers a warning here and the header asks before leaving.

let guard = null;

/** Registers `warning()` (text, or null when nothing would be lost); returns the unregister function. */
export function setLeaveGuard(warning) {
  guard = warning;
  return () => { if (guard === warning) guard = null; };
}

/** The current warning text, or null. */
export const leaveWarning = () => (guard ? guard() || null : null);
