// In-app navigation (header links are client-side route changes) does not fire `beforeunload`:
// a page with unsaved work registers a warning here and the header asks before leaving.

let guard = null;
let guardPath = null; // the page that registered it: Back within it (the search-match view) is its own

/** Registers `warning()` (text, or null when nothing would be lost); returns the unregister function. */
export function setLeaveGuard(warning) {
  guard = warning;
  guardPath = typeof window === 'undefined' ? null : window.location.pathname;
  return () => { if (guard === warning) guard = null; };
}

/** The current warning text, or null. */
export const leaveWarning = () => (guard ? guard() || null : null);

/**
 * The browser's Back (or Forward) to another page of the app is a route change too. Installed
 * before the router listens (gatsby-browser.js), this listener hides such a step from the router
 * while a page holds unsaved work: the page steps forward again (also hidden, so it is not
 * mounted afresh) and `ask(text, leave)` asks; `leave()` calls `go(address)` to go on.
 */
export function guardHistory(ask, go) {
  let restoring = false;
  window.addEventListener('popstate', event => {
    if (restoring) { restoring = false; event.stopImmediatePropagation(); return; }
    const warning = leaveWarning();
    if (!warning || window.location.pathname === guardPath) return;
    event.stopImmediatePropagation();
    const target = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    restoring = true;
    window.history.go(1);
    ask(warning, () => go(target));
  });
}
