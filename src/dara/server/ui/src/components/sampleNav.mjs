// Review loop over many samples: the task page knows the Results list it was opened from.
//
// Contract (Results builds these links; the task page reads them):
//   /task/?id=41&list=38,41,44&back=<encodeURIComponent of the Results query, without "?">
// `list` is the ordered task ids of the Results view (the current page, in its sort order);
// `back` is that view's own query string (filters, sort, page), so returning restores it.
// Both are optional: without `list` there is no previous/next, without `back` the breadcrumb
// returns to plain /results.

const MAX_IDS = 1000;
const MAX_BACK = 2000;

/** Results query string from `back`, re-serialized (only `key=value` pairs survive), or ''. */
function cleanBack(value) {
  if (!value || value.length > MAX_BACK) return '';
  return new URLSearchParams(String(value).replace(/^\?/, '')).toString();
}

/**
 * `{ids, back, index, prev, next}` of a task page address: `ids` the ordered list (empty when not
 * given), `index` the position of `id` in it (-1 when absent), `prev`/`next` the neighbours or null.
 */
export function listContext(search, id) {
  const params = new URLSearchParams(search || '');
  const ids = (params.get('list') || '').split(',').map(item => item.trim())
    .filter(item => /^\d{1,9}$/.test(item)).map(Number).slice(0, MAX_IDS);
  const index = ids.indexOf(Number(id));
  return {
    ids,
    back: cleanBack(params.get('back')),
    index,
    prev: index > 0 ? ids[index - 1] : null,
    next: index >= 0 && index < ids.length - 1 ? ids[index + 1] : null,
  };
}

/** Breadcrumb target: the Results view the task was opened from. */
export function resultsHref(context) {
  return context?.back ? `/results?${context.back}` : '/results';
}

/** Address of task `id` that keeps the list and the way back (no open fit). */
export function taskHref(id, context) {
  const params = new URLSearchParams({ id: String(id) });
  if (context?.ids?.length) params.set('list', context.ids.join(','));
  if (context?.back) params.set('back', context.back);
  // `,` stays readable in the address.
  return `/task/?${params.toString().replace(/%2C/g, ',')}`;
}

/** "Sample 3 of 12" (null without a list holding the task). */
export function positionText(context) {
  return context?.index >= 0 && context.ids.length > 1 ? `Sample ${context.index + 1} of ${context.ids.length}` : null;
}
