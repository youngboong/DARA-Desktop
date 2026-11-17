/** The analyst's text labels on the fit plot of a task (GET/PUT /api/task/{id}/annotations). */

export const MAX_ANNOTATIONS = 50;
export const MAX_ANNOTATION_TEXT = 120;

/** Annotation text as typed, made one line of plain text within the length limit ('' when nothing is left). */
export function cleanAnnotationText(text) {
  // eslint-disable-next-line no-control-regex
  return String(text ?? '').replace(/[\u0000-\u001f\u007f-\u009f]+/g, ' ').trim().slice(0, MAX_ANNOTATION_TEXT).trim();
}

/** Plotly draws a subset of HTML in annotation text: escape it so the label stays plain text. */
export const escapePlotText = text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** A new annotation id (the server accepts [A-Za-z0-9_-]{1,40}). */
export const newAnnotationId = () => `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;

/** `list` with `item` replaced (same id) or appended; null when the list is full. */
export function upsertAnnotation(list, item) {
  const index = list.findIndex(entry => entry.id === item.id);
  if (index >= 0) return list.map((entry, i) => (i === index ? item : entry));
  return list.length >= MAX_ANNOTATIONS ? null : [...list, item];
}

export const removeAnnotation = (list, id) => list.filter(entry => entry.id !== id);

/**
 * Plotly layout annotations of `list` on the main panel (`scale`: 'linear' | 'log'; a log axis
 * takes log10 positions). Each label stands just above the point that was clicked. `name` ties the
 * Plotly annotation to its id, so the editor can find it.
 */
export function plotAnnotations(list, { scale = 'linear' } = {}) {
  return list.map(item => ({
    x: item.two_theta,
    y: scale === 'log' ? Math.log10(Math.max(item.intensity, 1e-9)) : item.intensity,
    xref: 'x', yref: 'y', text: escapePlotText(item.text), name: `user:${item.id}`,
    showarrow: false, xanchor: 'center', yanchor: 'bottom', yshift: 2,
    font: { size: 12, color: '#1c1f1d' }, bgcolor: 'rgba(255,255,255,0.85)', bordercolor: '#c9cdc6', borderwidth: 1, borderpad: 2,
  }));
}
