import React from 'react';
import { ADVANCED_EVENT, ADVANCED_KEY, readAdvanced, writeAdvanced } from './advancedTools.mjs';

const storage = () => {
  try { return typeof window === 'undefined' ? null : window.localStorage; } catch (e) { return null; }
};

/** Changes the preference for every open view of this page (and, through storage, other tabs). */
export function setAdvancedTools(on) {
  writeAdvanced(storage(), on);
  // Without storage the event still carries the value to this page's views.
  window.dispatchEvent(new CustomEvent(ADVANCED_EVENT, { detail: Boolean(on) }));
}

/**
 * `[on, setOn]` of the "Advanced analysis tools" preference. False on the server render and
 * until mounted, so the static markup matches the first client render.
 */
export default function useAdvancedTools() {
  const [on, setOn] = React.useState(false);
  React.useEffect(() => {
    setOn(readAdvanced(storage()));
    const own = event => setOn(Boolean(event.detail));
    const other = event => { if (event.key === ADVANCED_KEY) setOn(readAdvanced(storage())); };
    window.addEventListener(ADVANCED_EVENT, own);
    window.addEventListener('storage', other);
    return () => { window.removeEventListener(ADVANCED_EVENT, own); window.removeEventListener('storage', other); };
  }, []);
  return [on, setAdvancedTools];
}
