import { API } from './QuickRefineProgress';
import { chunks, exportFilename, mergeCsv } from './resultsList.mjs';

/** Error with the server's detail (or the status) of a failed response. */
export async function responseError(response, fallback) {
  const body = await response.json().catch(() => ({}));
  return new Error(typeof body.detail === 'string' ? body.detail : `${fallback} (HTTP ${response.status}).`);
}

/** Message of a failed request: a server that did not answer is told apart from an error it reported. */
export const errorText = error => (error instanceof TypeError
  ? 'The DARA server did not answer. Check that DARA is running, then retry.' : String(error?.message || error));

/** Saves `text` as a download named `filename` (a Blob: no second request). */
export function saveText(text, filename, type = 'text/csv;charset=utf-8') {
  const link = document.createElement('a');
  link.href = window.URL.createObjectURL(new Blob([text], { type }));
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => window.URL.revokeObjectURL(link.href), 1000);
}

/**
 * The results table of `ids` (adopted fits) as one CSV: requested in parts of the server's limit
 * (50 ids), merged with one header and saved. `onProgress(done)` after each part. Resolves to the file name.
 */
export async function downloadTable(ids, onProgress = () => {}) {
  const texts = [];
  let done = 0;
  for (const part of chunks(ids)) {
    const response = await fetch(`${API}/tasks/table.csv?ids=${part.join(',')}`);
    if (!response.ok) throw await responseError(response, 'The table could not be exported');
    texts.push(await response.text());
    done += part.length;
    onProgress(done);
  }
  const filename = exportFilename(ids.length);
  saveText(mergeCsv(texts), filename);
  return filename;
}

/** The result archives of `ids` (at most 50, one folder per task) as one ZIP, saved. Resolves to the file name. */
export async function downloadResults(ids) {
  const response = await fetch(`${API}/tasks/download.zip?ids=${ids.join(',')}`);
  if (!response.ok) throw await responseError(response, 'The results could not be downloaded');
  const filename = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1] || 'dara_results.zip';
  saveText(await response.blob(), filename, 'application/zip');
  return filename;
}
