// One mapping from every status, verdict and threshold to a colour role. Roles are drawn by the
// preset-colour rules in tokens.css; the antd preset name below is only the hook for those rules.
// Rules: Completed has no colour; red means failure only; "review" is amber; metrics are neutral
// unless a threshold the app already uses is violated. Tags always keep their words.
// "Check recommended" is amber; "Expert review recommended" is light red (tag and banner) to stand above it.

export const TONE_COLOR = { ok: 'green', review: 'gold', fail: 'red', info: 'blue', neutral: 'default' };

/** antd Tag `color` for a role (`undefined` role = no colour). */
export const toneColor = role => (role ? TONE_COLOR[role] || 'default' : undefined);

/** Job state: Completed is plain text (`null`); only problems and activity carry colour. */
export function stateTone(state) {
  switch (state) {
    case 'FIZZLED': return 'fail';
    case 'CANCELLING': return 'review';
    case 'RUNNING': return 'info';
    case 'READY': case 'PENDING': case 'CANCELLED': return 'neutral';
    default: return null;
  }
}

const LEVEL_TONE = { high: 'ok', confident: 'ok', check: 'review', ambiguous: 'review', expert: 'fail', weak: 'fail' };
/** Overall confidence or phase verdict level. */
export const levelTone = level => LEVEL_TONE[level] || 'neutral';

const SEVERITY_TONE = { info: 'info', warning: 'review', severe: 'fail' };
/** Displacement estimate severity. */
export const severityTone = level => SEVERITY_TONE[level] || 'neutral';

/** Fit-quality chips are neutral; GOF above 2 is the one threshold the task page flags. */
export const gofTone = gof => (Number(gof) > 2 ? 'review' : 'neutral');

/** Peak-diagnostic chips: only unexplained peaks ask for a look. */
export const peakCountTone = count => (Number(count) > 0 ? 'review' : 'neutral');

/** Colour of an adopted-result label. */
export const adoptedTone = () => 'info';

const SCREENING_TONE = { rejected: 'review', computed_only: 'info', mp_window: 'neutral', prefilter: 'neutral', screening_timeout: 'review', not_screened: 'review' };
export const screeningTone = key => SCREENING_TONE[key] || 'neutral';
