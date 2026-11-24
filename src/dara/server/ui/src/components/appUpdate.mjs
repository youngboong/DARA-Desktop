// In-app update (GET /api/update, POST /api/update/install): version order and the texts the
// header and the update dialog show. The server decides what can be installed.

export const UPDATE_CHECK_MS = 6 * 3600 * 1000;
const BUSY = ['downloading', 'verifying', 'installing'];

/** "v1.3.10" -> [1, 3, 10]; null for anything but dot-separated numbers. */
export function parseVersion(text) {
  const match = /^[vV]?(\d+(?:\.\d+)*)$/.exec(String(text ?? '').trim());
  return match ? match[1].split('.').map(Number) : null;
}

/** -1, 0 or 1 comparing each number in turn (1.3.10 > 1.3.9); null when either is not a version. */
export function compareVersions(a, b) {
  const left = parseVersion(a);
  const right = parseVersion(b);
  if (!left || !right) return null;
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const diff = (left[i] ?? 0) - (right[i] ?? 0);
    if (diff) return Math.sign(diff);
  }
  return 0;
}

export const isBusy = status => BUSY.includes(status?.state);

/** The header tag: only the installed app, and only for a strictly newer release. */
export function showUpdateTag(status) {
  return Boolean(status?.packaged && status.available && compareVersions(status.latest, status.current) === 1);
}

/** One line for the dialog: progress, the outcome of the check, or what went wrong. */
export function updateStateText(status) {
  if (!status) return 'Checking...';
  switch (status.state) {
    case 'checking': return 'Checking...';
    case 'downloading': return `Downloading ${status.progress ?? 0} %`;
    case 'verifying': return 'Verifying...';
    case 'installing': return 'Installing: DARA will close and reopen';
    case 'error': return status.error || 'The update failed.';
    default: break;
  }
  if (status.available) return `DARA ${status.latest} is available (you have ${status.current}).`;
  if (status.error) return status.error;
  return status.current ? `DARA ${status.current} is up to date` : 'The installed version could not be read.';
}

/** A refused install (HTTP 409): its message and whether "Update anyway" may retry with force=1. */
export function refusal(body) {
  const detail = body?.detail;
  if (detail && typeof detail === 'object') return { message: String(detail.message || ''), force: Boolean(detail.force) };
  return { message: String(detail || 'The update could not be started.'), force: false };
}
