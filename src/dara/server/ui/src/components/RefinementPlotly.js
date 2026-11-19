import React from 'react';
import createPlotlyComponent from 'react-plotly.js/factory';
import Plotly from 'plotly.js/lib/core';
import Scatter from 'plotly.js/lib/scatter';
import {
  clampRange, finiteRange, insideRange, panRange, viewChangeFromRelayout, wheelNotches, wheelZoomRange, xRangeUpdate,
} from './plotNavigation.mjs';
import { HIT_PX } from './backgroundAnchors.mjs';
import { FIGURE_WIDTHS, exportOptions, publicationFigure } from './figureExport.mjs';

// The refinement view only renders scatter traces. Importing react-plotly.js
// directly pulls every Plotly trace type (maps, 3-D, finance, etc.) into the
// lazy chunk and makes the first "Visualize result" click unnecessarily slow.
Plotly.register([Scatter]);

const Plot = createPlotlyComponent(Plotly);

/** The shown view (zoom, phases, labels) as a journal-style figure file, double-column wide. */
function downloadFigure(gd, format, name) {
  const figure = publicationFigure(gd.data, gd.layout, { widthMm: FIGURE_WIDTHS.double });
  return Plotly.downloadImage({ data: figure.data, layout: figure.layout, config: { displaylogo: false } },
    exportOptions(figure, format, name));
}

// Plotly's camera saved the screen theme; these save the view in journal style instead.
const figureButtons = name => [
  { name: 'Figure (SVG, 170 mm)', icon: Plotly.Icons.camera, click: gd => downloadFigure(gd, 'svg', name) },
  { name: 'Figure (PNG 600 dpi, 170 mm)', icon: Plotly.Icons.disk, click: gd => downloadFigure(gd, 'png', name) },
];

const PAN_BUTTONS = new Set([1, 2]); // middle, right
const CLICK_SLOP_PX = 3;

function dataXBounds(gd) {
  let lo = Infinity;
  let hi = -Infinity;
  (gd._fullData || []).forEach(trace => {
    if (trace.visible === false || !trace.x) return;
    for (let i = 0; i < trace.x.length; i += 1) {
      const value = Number(trace.x[i]);
      if (Number.isFinite(value)) { lo = Math.min(lo, value); hi = Math.max(hi, value); }
    }
  });
  return lo < hi ? [lo, hi] : null;
}

/** `{xa, px}` of the main 2θ axis under a client position inside either panel, or `null`. */
function plotAreaTarget(gd, clientX, clientY) {
  const full = gd?._fullLayout;
  if (!full?.xaxis) return null;
  const rect = gd.getBoundingClientRect();
  const px = clientX - rect.left;
  const py = clientY - rect.top;
  const xa = full.xaxis;
  if (px < xa._offset || px > xa._offset + xa._length) return null;
  for (const name of ['yaxis', 'yaxis2']) {
    const ya = full[name];
    if (ya && py >= ya._offset && py <= ya._offset + ya._length) return { xa, px: px - xa._offset };
  }
  return null;
}

/**
 * 2θ under the mouse of a click, or `null`. Peak selection uses this instead of the hovered
 * point: Plotly's hover is throttled and picks the closest point in 2-D, so right after the mouse
 * moved it can still name the previous peak (or a point of another curve).
 */
export function cursorTwoTheta(gd, mouseEvent) {
  if (!mouseEvent || !Number.isFinite(mouseEvent.clientX)) return null;
  const target = plotAreaTarget(gd, mouseEvent.clientX, mouseEvent.clientY);
  if (!target) return null;
  const x = Number(target.xa.p2l(target.px));
  return Number.isFinite(x) ? x : null;
}

// Mouse navigation for XRD plots (see plotNavigation.mjs): Plotly draws the left-drag zoom
// box (layout dragmode 'zoom'); 2θ panning, the wheel and the range clamp are handled here,
// because Plotly's own pan and scroll zoom neither stop at the data nor use a fixed step.
function attachNavigation(gd, boundsRef) {
  let drag = null;
  let frame = null;
  let pending = null;

  const bounds = () => finiteRange(boundsRef.current) || dataXBounds(gd);
  const hasX2 = () => Boolean(gd._fullLayout?.xaxis2);
  const currentRange = () => finiteRange(gd._fullLayout?.xaxis?.range);

  const inPlotArea = (clientX, clientY) => plotAreaTarget(gd, clientX, clientY);

  // One relayout per animation frame: wheel and drag events come faster than Plotly redraws.
  // The timer is a fallback for pages that get no animation frames (hidden or throttled).
  const flush = () => {
    if (frame === null) return;
    window.cancelAnimationFrame(frame.raf);
    window.clearTimeout(frame.timer);
    frame = null;
    const next = pending;
    pending = null;
    const shown = currentRange();
    if (next && !(shown && Math.abs(shown[0] - next[0]) < 1e-12 && Math.abs(shown[1] - next[1]) < 1e-12)) {
      Plotly.relayout(gd, xRangeUpdate(next, { xaxis2: hasX2() }));
    }
  };
  const apply = range => {
    pending = range;
    if (frame !== null) return;
    frame = { raf: window.requestAnimationFrame(flush), timer: window.setTimeout(flush, 50) };
  };

  const stopDrag = () => {
    drag = null;
    gd.style.cursor = '';
    window.removeEventListener('mousemove', onMove, true);
    window.removeEventListener('mouseup', onUp, true);
  };

  function onMove(event) {
    if (!drag) return;
    event.preventDefault();
    if (!drag.moved && Math.abs(event.clientX - drag.x) < CLICK_SLOP_PX && Math.abs(event.clientY - drag.y) < CLICK_SLOP_PX) return;
    drag.moved = true;
    const degreesPerPixel = (drag.range[1] - drag.range[0]) / drag.length;
    apply(panRange(drag.range, -(event.clientX - drag.x) * degreesPerPixel, bounds()));
  }

  function onUp(event) {
    if (!drag) return;
    event.preventDefault();
    event.stopPropagation();
    const { moved, button } = drag;
    stopDrag();
    // Shift+click (several peaks) must still reach the plot: Plotly never saw this mousedown,
    // so emit its click event. The 2θ comes from the mouse position: `gd._hoverdata` can still
    // hold the previously hovered peak when the click follows a quick mouse move.
    const cursorX = !moved && button === 0 ? cursorTwoTheta(gd, event) : null;
    if (cursorX !== null) gd.emit('plotly_click', { points: gd._hoverdata || [], event, cursorX });
  }

  const onDown = event => {
    const pan = PAN_BUTTONS.has(event.button) || (event.button === 0 && event.shiftKey);
    if (!pan) return;
    const target = inPlotArea(event.clientX, event.clientY);
    const range = currentRange();
    if (!target || !range) return;
    // Capture phase: Plotly's drag layer must not start a zoom box or its own (unclamped) pan.
    event.preventDefault();
    event.stopPropagation();
    drag = { x: event.clientX, y: event.clientY, button: event.button, moved: false, range, length: Math.max(target.xa._length, 1) };
    gd.style.cursor = 'grabbing';
    window.addEventListener('mousemove', onMove, true);
    window.addEventListener('mouseup', onUp, true);
  };

  const onWheel = event => {
    const target = inPlotArea(event.clientX, event.clientY);
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    const range = pending || currentRange();
    const notches = wheelNotches(event);
    if (!range || !notches) return;
    const center = target.xa.p2l(target.px);
    apply(wheelZoomRange(range, center, notches, bounds()));
  };

  const onContextMenu = event => {
    if (inPlotArea(event.clientX, event.clientY)) event.preventDefault();
  };

  // Safety net for the gestures Plotly still handles (axis drags, box zoom): a 2θ window that
  // leaves the measured range is moved back inside it.
  const onRelayout = event => {
    const change = viewChangeFromRelayout(event || {});
    if (!change.xRange) return;
    const full = bounds();
    if (insideRange(change.xRange, full)) return;
    const fixed = clampRange(change.xRange, full);
    if (fixed) Plotly.relayout(gd, xRangeUpdate(fixed, { xaxis2: hasX2() }));
  };

  gd.addEventListener('mousedown', onDown, true);
  gd.addEventListener('wheel', onWheel, { capture: true, passive: false });
  gd.addEventListener('contextmenu', onContextMenu);
  if (typeof gd.on === 'function') gd.on('plotly_relayout', onRelayout);
  return () => {
    stopDrag();
    if (frame !== null) {
      window.cancelAnimationFrame(frame.raf);
      window.clearTimeout(frame.timer);
      frame = null;
    }
    gd.removeEventListener('mousedown', onDown, true);
    gd.removeEventListener('wheel', onWheel, { capture: true });
    gd.removeEventListener('contextmenu', onContextMenu);
    if (typeof gd.removeListener === 'function') gd.removeListener('plotly_relayout', onRelayout);
  };
}

/** Pixel position of data point (x, y) in the main panel, relative to the plot div, or `null`. */
function dataToPixel(full, x, y) {
  const xa = full.xaxis;
  const ya = full.yaxis;
  const ly = ya.type === 'log' ? (y > 0 ? Math.log10(y) : NaN) : y;
  const px = xa._offset + xa.l2p(x);
  const py = ya._offset + ya.l2p(ly);
  return Number.isFinite(px) && Number.isFinite(py) ? { px, py } : null;
}

/** Intensity (data units, >= 0) at a pixel row of the main panel. */
function pixelToIntensity(full, py) {
  const ya = full.yaxis;
  const value = ya.p2l(py - ya._offset);
  return Math.max(ya.type === 'log' ? 10 ** value : value, 0);
}

const roundTo = (value, digits) => Number(value.toFixed(digits));

// Background curve editing (see backgroundAnchors.mjs): drag an anchor vertically, click the curve
// to add one, Alt+click an anchor to remove it. `editRef.current` is null when not editing, so the
// listener is attached once and stays idle. While dragging only the curve and anchor traces are
// restyled (one per animation frame); React state gets the anchors on mouseup.
function attachAnchorEditing(gd, editRef) {
  let drag = null;
  let frame = null;

  const traceIndex = meta => (gd.data || []).findIndex(trace => trace.meta === meta);
  const mainPanel = (clientX, clientY) => {
    const full = gd._fullLayout;
    if (!full?.xaxis || !full?.yaxis) return null;
    const rect = gd.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const { xaxis: xa, yaxis: ya } = full;
    if (px < xa._offset || px > xa._offset + xa._length || py < ya._offset || py > ya._offset + ya._length) return null;
    return { full, px, py };
  };
  const hitAnchor = (edit, at) => {
    let best = -1;
    let bestDistance = HIT_PX;
    edit.anchors.forEach(([x, y], index) => {
      const point = dataToPixel(at.full, x, y);
      const distance = point ? Math.hypot(point.px - at.px, point.py - at.py) : Infinity;
      if (distance <= bestDistance) { best = index; bestDistance = distance; }
    });
    return best;
  };

  const draw = () => {
    frame = null;
    if (!drag) return;
    const curve = drag.edit.curveOf(drag.anchors);
    const [curveIndex, anchorIndex] = [traceIndex('bg-curve'), traceIndex('bg-anchors')];
    if (curveIndex < 0 || anchorIndex < 0) return;
    const update = { y: [curve, drag.anchors.map(point => point[1])] };
    if (drag.edit.moveX) update.x = [gd.data[curveIndex].x, drag.anchors.map(point => point[0])];
    Plotly.restyle(gd, update, [curveIndex, anchorIndex]);
  };

  // `edit.moveX` ([lo, hi] 2θ) lets a point move sideways too, between its neighbours and inside [lo, hi].
  const xAt = (edit, clientX, rect) => {
    const xa = gd._fullLayout.xaxis;
    const [lo, hi] = edit.moveX;
    const before = drag.anchors[drag.index - 1];
    const after = drag.anchors[drag.index + 1];
    const min = before ? before[0] + 1e-3 : lo;
    const max = after ? after[0] - 1e-3 : hi;
    return roundTo(Math.min(Math.max(xa.p2l(clientX - rect.left - xa._offset), min), max), 4);
  };

  function onMove(event) {
    if (!drag) return;
    event.preventDefault();
    const rect = gd.getBoundingClientRect();
    const y = roundTo(pixelToIntensity(gd._fullLayout, event.clientY - rect.top), 2);
    const x = drag.edit.moveX ? xAt(drag.edit, event.clientX, rect) : null;
    drag.anchors = drag.anchors.map((point, index) => (index === drag.index ? [x ?? point[0], y] : point));
    drag.moved = true;
    if (frame === null) frame = window.requestAnimationFrame(draw);
  }

  function onUp(event) {
    if (!drag) return;
    event.preventDefault();
    event.stopPropagation();
    window.removeEventListener('mousemove', onMove, true);
    window.removeEventListener('mouseup', onUp, true);
    if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; }
    const { edit, anchors, moved } = drag;
    drag = null;
    if (moved) edit.onChange(anchors);
  }

  const onDown = event => {
    const edit = editRef.current;
    if (!edit || event.button !== 0 || event.shiftKey) return;
    const at = mainPanel(event.clientX, event.clientY);
    if (!at) return;
    const index = hitAnchor(edit, at);
    const stop = () => { event.preventDefault(); event.stopPropagation(); };
    if (index >= 0) {
      stop();
      if (event.altKey) { edit.onRemove(index); return; }
      edit.onSelect(index);
      drag = { edit, index, anchors: edit.anchors, moved: false };
      window.addEventListener('mousemove', onMove, true);
      window.addEventListener('mouseup', onUp, true);
      return;
    }
    // A click on the curve (within HIT_PX) between the range ends adds an anchor there.
    const x = at.full.xaxis.p2l(at.px - at.full.xaxis._offset);
    const [y] = edit.curveOf(edit.anchors, [x]);
    const point = dataToPixel(at.full, x, y);
    if (!point || Math.abs(point.py - at.py) > HIT_PX || edit.blocked(x)) return;
    stop();
    edit.onAdd([roundTo(x, 4), roundTo(y, 2)]);
  };

  // Plotly's drag layer keeps a crosshair cursor; show what a press would do instead.
  const onHover = event => {
    const edit = editRef.current;
    const layer = gd.querySelector('.nsewdrag');
    if (!layer || drag) return;
    const at = edit ? mainPanel(event.clientX, event.clientY) : null;
    layer.style.cursor = at && hitAnchor(edit, at) >= 0 ? (event.altKey ? 'not-allowed' : edit.moveX ? 'move' : 'ns-resize') : '';
  };

  gd.addEventListener('mousedown', onDown, true);
  gd.addEventListener('mousemove', onHover);
  return () => {
    drag = null;
    if (frame !== null) window.cancelAnimationFrame(frame);
    window.removeEventListener('mousemove', onMove, true);
    window.removeEventListener('mouseup', onUp, true);
    gd.removeEventListener('mousedown', onDown, true);
    gd.removeEventListener('mousemove', onHover);
  };
}

// Text labels of the analyst (see plotAnnotations.mjs), while `annotateRef.current` is set:
// `{offset, ids, onAdd(x, intensity, px, py), onEdit(id, px, py), onMove(id, x, intensity)}`, where
// the labels are the layout annotations from index `offset` on, in the order of `ids`. A click on
// the main panel adds a label (a drag there still draws the zoom box); a label is dragged to move
// it or clicked to edit it. Pixel positions are relative to the plot div.
function attachAnnotating(gd, annotateRef) {
  let drag = null;
  let press = null;
  let frame = null;

  const panelAt = (clientX, clientY) => {
    const full = gd._fullLayout;
    if (!full?.xaxis || !full?.yaxis) return null;
    const rect = gd.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const { xaxis: xa, yaxis: ya } = full;
    if (px < xa._offset || px > xa._offset + xa._length || py < ya._offset || py > ya._offset + ya._length) return null;
    return { full, px, py };
  };
  const hitLabel = (annotate, clientX, clientY) => annotate.ids.findIndex((_, i) => {
    const element = gd.querySelector(`.annotation[data-index="${annotate.offset + i}"]`);
    const box = element?.getBoundingClientRect();
    return Boolean(box && box.width && clientX >= box.left && clientX <= box.right && clientY >= box.top && clientY <= box.bottom);
  });
  // Axis value (log10 on a log axis) and data intensity of a pixel row of the main panel.
  const yAt = (full, py) => full.yaxis.p2l(py - full.yaxis._offset);
  const intensity = (full, value) => (full.yaxis.type === 'log' ? 10 ** value : value);

  const draw = () => {
    frame = null;
    if (drag?.to) Plotly.relayout(gd, { [`annotations[${drag.index}].x`]: drag.to.x, [`annotations[${drag.index}].y`]: drag.to.y });
  };
  function onMove(event) {
    if (!drag) return;
    event.preventDefault();
    if (!drag.moved && Math.abs(event.clientX - drag.clientX) < CLICK_SLOP_PX && Math.abs(event.clientY - drag.clientY) < CLICK_SLOP_PX) return;
    drag.moved = true;
    const full = gd._fullLayout;
    const rect = gd.getBoundingClientRect();
    const px = event.clientX - rect.left - drag.dx;
    const py = event.clientY - rect.top - drag.dy;
    drag.to = { x: full.xaxis.p2l(px - full.xaxis._offset), y: yAt(full, py) };
    if (frame === null) frame = window.requestAnimationFrame(draw);
  }
  function onUp(event) {
    window.removeEventListener('mousemove', onMove, true);
    window.removeEventListener('mouseup', onUp, true);
    if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; }
    const current = drag;
    drag = null;
    const annotate = annotateRef.current;
    if (!current || !annotate) return;
    event.preventDefault();
    event.stopPropagation();
    if (current.moved && current.to) annotate.onMove(current.id, current.to.x, intensity(gd._fullLayout, current.to.y));
    else annotate.onEdit(current.id, current.anchor.px, current.anchor.py);
  }
  function onPressUp(event) {
    window.removeEventListener('mouseup', onPressUp, true);
    const start = press;
    press = null;
    const annotate = annotateRef.current;
    if (!start || !annotate || event.button !== 0) return;
    if (Math.abs(event.clientX - start.clientX) >= CLICK_SLOP_PX || Math.abs(event.clientY - start.clientY) >= CLICK_SLOP_PX) return;
    const at = panelAt(start.clientX, start.clientY);
    if (!at) return;
    const x = at.full.xaxis.p2l(at.px - at.full.xaxis._offset);
    annotate.onAdd(x, intensity(at.full, yAt(at.full, at.py)), at.px, at.py);
  }

  const onDown = event => {
    const annotate = annotateRef.current;
    if (!annotate || event.button !== 0 || event.shiftKey) return;
    const index = hitLabel(annotate, event.clientX, event.clientY);
    if (index >= 0) {
      event.preventDefault();
      event.stopPropagation();
      const full = gd._fullLayout;
      const shown = full.annotations?.[annotate.offset + index];
      const rect = gd.getBoundingClientRect();
      const anchor = shown ? { px: full.xaxis._offset + full.xaxis.l2p(shown.x), py: full.yaxis._offset + full.yaxis.l2p(shown.y) }
        : { px: event.clientX - rect.left, py: event.clientY - rect.top };
      drag = { id: annotate.ids[index], index: annotate.offset + index, clientX: event.clientX, clientY: event.clientY,
        dx: event.clientX - rect.left - anchor.px, dy: event.clientY - rect.top - anchor.py, anchor, moved: false };
      window.addEventListener('mousemove', onMove, true);
      window.addEventListener('mouseup', onUp, true);
      return;
    }
    // Not stopped: a drag from here still draws Plotly's zoom box; only a click adds a label.
    if (panelAt(event.clientX, event.clientY)) {
      press = { clientX: event.clientX, clientY: event.clientY };
      window.addEventListener('mouseup', onPressUp, true);
    }
  };
  const onHover = event => {
    const annotate = annotateRef.current;
    const layer = gd.querySelector('.nsewdrag');
    if (!layer || drag || !annotate) return;
    layer.style.cursor = hitLabel(annotate, event.clientX, event.clientY) >= 0 ? 'move'
      : panelAt(event.clientX, event.clientY) ? 'text' : '';
  };

  gd.addEventListener('mousedown', onDown, true);
  gd.addEventListener('mousemove', onHover);
  return () => {
    drag = null;
    press = null;
    if (frame !== null) window.cancelAnimationFrame(frame);
    window.removeEventListener('mousemove', onMove, true);
    window.removeEventListener('mouseup', onUp, true);
    window.removeEventListener('mouseup', onPressUp, true);
    gd.removeEventListener('mousedown', onDown, true);
    gd.removeEventListener('mousemove', onHover);
  };
}

/**
 * Plotly scatter plot with the DARA navigation. `xBounds` is the measured 2θ range that zoom
 * and pan stay inside (default: the extent of the plotted data). `anchorEdit` turns on background
 * curve editing: `{anchors, curveOf(anchors, xs?), blocked(x), onChange, onAdd, onRemove, onSelect, moveX?}`
 * (`curveOf` without `xs` gives the curve on the plotted 2θ grid; `blocked(x)` is true where no
 * anchor may be added; `moveX` [lo, hi] lets a dragged anchor move in 2θ as well, between its
 * neighbours). The curve and anchors are the traces with meta 'bg-curve' / 'bg-anchors'.
 * `annotate` turns on label editing (see attachAnnotating). `figureName` puts journal-style figure
 * downloads (SVG, PNG 600 dpi) of the shown view in the modebar.
 */
export default function RefinementPlotly({ onInitialized, onClick, xBounds = null, anchorEdit = null, annotate = null, figureName = null, config, ...props }) {
  // `figureName`: the modebar saves the view as a journal-style figure file of this name.
  const shownConfig = React.useMemo(() => (figureName ? { ...config,
    modeBarButtonsToRemove: [...(config?.modeBarButtonsToRemove || []), 'toImage'],
    modeBarButtonsToAdd: figureButtons(figureName) } : config), [config, figureName]);
  const detach = React.useRef(null);
  const graph = React.useRef(null);
  const boundsRef = React.useRef(xBounds);
  boundsRef.current = xBounds;
  const editRef = React.useRef(anchorEdit);
  editRef.current = anchorEdit;
  const annotateRef = React.useRef(annotate);
  annotateRef.current = annotate;
  React.useEffect(() => () => { if (detach.current) detach.current(); }, []);
  // Every click event carries `cursorX`, the 2θ under the mouse (see cursorTwoTheta).
  const handleClick = onClick ? event => onClick({
    ...event, cursorX: Number.isFinite(event?.cursorX) ? event.cursorX : cursorTwoTheta(graph.current, event?.event),
  }) : undefined;
  return <Plot {...props} config={shownConfig} onClick={handleClick} onInitialized={(figure, gd) => {
    if (detach.current) detach.current();
    graph.current = gd;
    const detachNavigation = attachNavigation(gd, boundsRef);
    const detachEditing = attachAnchorEditing(gd, editRef);
    const detachAnnotating = attachAnnotating(gd, annotateRef);
    detach.current = () => { detachNavigation(); detachEditing(); detachAnnotating(); };
    if (onInitialized) onInitialized(figure, gd);
  }} />;
}
