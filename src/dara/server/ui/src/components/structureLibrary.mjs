// Pure helpers of the structure library modal. No React, no fetch: tested with node.

const PHASES = { select: 'Selecting structures', cod: 'COD structures', mp: 'Materials Project structures',
  sticks: 'Simulating reflections', done: 'Finished', cancelled: 'Stopped', error: 'Error' };

/** Element symbols from free text ("li, Fe O") with a leading capital, unique, in input order. */
export function parseElements(text) {
  const seen = [];
  for (const raw of String(text || '').split(/[\s,;]+/)) {
    if (!/^[A-Za-z]{1,2}$/.test(raw)) continue;
    const symbol = raw[0].toUpperCase() + raw.slice(1).toLowerCase();
    if (!seen.includes(symbol)) seen.push(symbol);
  }
  return seen;
}

/** "1.2 GB" / "340 MB" from megabytes. */
export function sizeText(mb) {
  if (!Number.isFinite(mb)) return '—';
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.max(1, Math.round(mb))} MB`;
}

/** "about 3 h 40 min" / "about 25 min" from minutes. */
export function durationText(minutes) {
  if (!Number.isFinite(minutes)) return '—';
  const total = Math.max(1, Math.round(minutes));
  const hours = Math.floor(total / 60);
  return hours ? `about ${hours} h ${total % 60} min` : `about ${total} min`;
}

/** Label of a progress phase. */
export function phaseLabel(phase) {
  return PHASES[phase] || String(phase || '');
}

/** Percent (0..100) of the current phase, or `null` when unknown. */
export function progressPercent(line) {
  if (!line || !Number.isFinite(line.total) || line.total <= 0) return null;
  return Math.min(100, Math.round((100 * line.done) / line.total));
}

/** "12 of 20 167 · 2 h 10 min left" for a progress line. */
export function progressText(line) {
  if (!line) return '';
  const parts = [];
  if (Number.isFinite(line.total) && line.total > 0) parts.push(`${line.done.toLocaleString('en-US')} of ${line.total.toLocaleString('en-US')}`);
  if (Number.isFinite(line.eta_s) && line.eta_s > 0) parts.push(`${durationText(line.eta_s / 60).replace('about ', '')} left`);
  if (line.message) parts.push(line.message);
  return parts.join(' · ');
}

/** Cached / total line of a status block ("COD 1 171 of 20 167"). */
export function cachedText(label, block) {
  if (!block) return `${label}: —`;
  if (block.total == null) return `${label}: not listed yet (listed when downloading)`;
  const total = Number.isFinite(block.total) ? block.total.toLocaleString('en-US') : '?';
  return `${label}: ${Number(block.cached || 0).toLocaleString('en-US')} of ${total} downloaded`;
}

/** Outcome of a finished job: `{type, text}` for an Alert, or `null` while running / never run. */
export function jobOutcome(job) {
  if (!job || job.running || job.exit_code == null) return null;
  if (job.exit_code === 0) return { type: 'success', text: 'Download finished.' };
  if (job.exit_code === 2) return { type: 'warning', text: job.last?.message || 'Stopped before the end; download again to resume.' };
  return { type: 'error', text: job.last?.message || 'The download failed.' };
}
