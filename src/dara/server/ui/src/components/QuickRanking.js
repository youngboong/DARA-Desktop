import React, { useEffect, useRef, useState } from 'react';
import Loadable from 'react-loadable';
import styled from 'styled-components';
import { Button, Progress, Spin, Switch, Table, Tag, Tooltip } from 'antd';
import { ArrowLeftOutlined, CloseOutlined, PlusOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons';
import ChemicalFormula from './ChemicalFormula';
import { progressPercent } from './structureLibrary.mjs';
import {
  MAX_REFINE, SORTERS, assignColors, defaultSelection, nextMatches, overlayTraces, percent, pollDelay, poolText, progressText,
  rankingRows, refineSelection, requestKey, rowKeyAction, sourceInfo, toggleKey, unexplainedMarkers, unexplainedStatus,
  windowRange, withResidualProfile, zoomWindow,
} from './quickRanking.mjs';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <div className="dara-qr-plot-area" /> });
const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

// A cancelled job stops on the server; older servers have no DELETE, so its errors are ignored.
const cancelJob = id => { if (id) fetch(`${URL}/quick-ranking/${id}`, { method: 'DELETE' }).catch(() => {}); };

/**
 * State of the quick ranking (search-match) of the submission page; it lives with the page, so the
 * last ranking survives "Back to setup". `buildRequest()` returns the multipart body (pattern file,
 * elements, formulas, wavelength, range) or throws an Error with a user message.
 */
export function useQuickRanking(buildRequest) {
  const [shown, setShown] = useState(false);
  const [state, setState] = useState({ status: 'idle' }); // + key, data | error | progress
  const [selected, setSelectedKeys] = useState([]);
  const [colors, setColors] = useState({});
  const [focused, setFocused] = useState(null);
  const [theoretical, setTheoretical] = useState(false);
  const [refining, setRefining] = useState(false);
  const [identifying, setIdentifying] = useState(false);
  const [residual, setResidual] = useState(null); // {key, data} | {key, error} of the current selection
  const run = useRef(0);
  const job = useRef(null);
  const scroll = useRef(0);

  const setSelected = keys => {
    setSelectedKeys(keys);
    setColors(previous => assignColors(previous, keys));
  };
  const request = includeTheoretical => {
    const body = buildRequest();
    body.set('include_theoretical', String(includeTheoretical));
    return body;
  };
  let currentKey = null;
  try { currentKey = requestKey(request(theoretical).entries()); } catch (error) { /* no pattern yet */ }

  const start = async (includeTheoretical = theoretical) => {
    const sequence = ++run.current;
    cancelJob(job.current);
    job.current = null;
    setSelected([]); // a ticked row of an earlier ranking must not be refined with this one
    setFocused(null);
    let body;
    try {
      body = request(includeTheoretical);
    } catch (error) {
      setState({ status: 'error', error: String(error.message || error) });
      return;
    }
    const key = requestKey(body.entries());
    setState({ status: 'pending', key, progress: {} });
    try {
      let response = await fetch(`${URL}/quick-ranking`, { method: 'POST', body });
      let data = await response.json();
      for (let attempt = 0; response.ok && data.status === 'pending'; attempt += 1) {
        if (sequence !== run.current) { cancelJob(data.job_id); return; }
        job.current = data.job_id;
        setState({ status: 'pending', key, progress: data });
        await new Promise(resolve => setTimeout(resolve, pollDelay(attempt)));
        if (sequence !== run.current) return;
        response = await fetch(`${URL}/quick-ranking/${data.job_id}`);
        data = await response.json();
      }
      if (sequence !== run.current) return;
      job.current = null;
      if (!response.ok) throw new Error(data.detail || 'The search-match failed.');
      if (data.status === 'error') throw new Error(data.error || 'The search-match failed.');
      setState({ status: 'done', key, data });
      setSelected(defaultSelection(data));
    } catch (error) {
      if (sequence === run.current) setState({ status: 'error', key, error: String(error.message || error) });
    }
  };
  const cancel = () => {
    run.current += 1;
    cancelJob(job.current);
    job.current = null;
    setState({ status: 'idle' });
  };

  useEffect(() => () => { run.current += 1; cancelJob(job.current); }, []);

  // What is left after the selected phases: unexplained peaks and the best matches for them.
  // Debounced; answered in well under a second from the finished job's pool.
  const jobId = state.status === 'done' ? state.data?.job_id : null;
  const selectionKey = selected.join('|');
  useEffect(() => {
    if (!jobId || !selected.length) { setResidual(null); return undefined; }
    const key = `${jobId}|${selectionKey}`;
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`${URL}/quick-ranking/${jobId}/residual`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ selected }),
        });
        const body = await response.json();
        if (!response.ok) {
          throw new Error(response.status === 404 ? 'This search-match has expired: run it again to see what is left.'
            : response.status === 409 ? 'The search-match is still running.' : body.detail || 'What is left could not be calculated.');
        }
        if (current) setResidual({ key, data: body });
      } catch (error) {
        if (current) setResidual({ key, error: String(error.message || error) });
      }
    }, 250);
    return () => { current = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, selectionKey]);
  const residualKey = jobId && selected.length ? `${jobId}|${selectionKey}` : null;

  const ranked = state.status === 'done' && state.key === currentKey ? state.data : null;
  return {
    shown, state, selected, setSelected, colors, focused, setFocused, theoretical, refining, identifying, ranked, start, cancel,
    // The residual of the current selection (`stale` while the newer one is on its way).
    residual: residual ? { ...residual, stale: residual.key !== residualKey } : null,
    /** Shows the workspace: the last ranking when its inputs are unchanged, else a new one. */
    open() {
      scroll.current = window.scrollY;
      setShown(true);
      window.scrollTo(0, 0);
      if (!ranked && state.status !== 'pending') start(theoretical);
    },
    /** Back to the setup; a pending ranking is cancelled. */
    close(restoreScroll = true) {
      if (state.status === 'pending') cancel();
      setShown(false);
      if (restoreScroll) requestAnimationFrame(() => window.scrollTo(0, scroll.current));
    },
    toggleTheoretical(checked) {
      setTheoretical(checked);
      start(checked);
    },
    /** `onRefine({referenceIds, formulas})` resolves to whether a refinement was submitted. */
    async refine(onRefine) {
      setRefining(true);
      let submitted = false;
      try {
        submitted = await onRefine(refineSelection(state.data, selected));
      } finally {
        setRefining(false);
      }
      if (submitted) setShown(false);
    },
    /** `onIdentify({referenceIds})` (selection order) saves the identification; resolves to whether it was saved. */
    async identify(onIdentify) {
      setIdentifying(true);
      try {
        await onIdentify({ referenceIds: [...selected] });
      } finally {
        setIdentifying(false);
      }
    },
  };
}

/** Steps of the greedy suggestion: "Fe2O3 34 % + ZnO 12 % (weak)" with formula subscripts. */
function Suggestion({ combination }) {
  return combination.map((step, index) => <React.Fragment key={step.source}>
    {index ? ' + ' : ''}<ChemicalFormula formula={step.formula} /> <span className="dara-num">{percent(step.gain)}</span>
    {step.weak ? ' (weak)' : ''}
  </React.Fragment>);
}

/** "Ia-3 (206)" with the symbol set as a space group; a bare number when there is no symbol. */
const SpaceGroup = ({ row }) => (row.spacegroup_symbol
  ? <><ChemicalFormula formula={row.spacegroup_symbol} />{` (${row.spacegroup})`}</> : String(row.spacegroup ?? '—'));

/**
 * What the selected phases leave: the number of unexplained peaks (the selection's status) and the
 * best matches for the remaining peaks, each with "Add".
 */
function ResidualPanel({ residual, rows, selected, setSelected }) {
  if (!residual) return <section className="dara-qr-left" aria-label="What is left"><p className="dara-qr-left-status is-stale">Checking what is left…</p></section>;
  if (residual.error) return <section className="dara-qr-left" aria-label="What is left"><p className="dara-qr-empty">{residual.error}</p></section>;
  const left = residual.data;
  const open = (left.unexplained_peaks || []).length > 0;
  const next = nextMatches(left, rows);
  return <section className="dara-qr-left" aria-label="What is left" aria-busy={residual.stale || undefined}>
    <Tooltip title={open ? `At ${left.unexplained_peaks.map(peak => `${Number(peak[0]).toFixed(2)}°`).join(', ')} (marked on the plot; click a marker to zoom)` : 'Every peak of the pattern is explained by the selected phases'}>
      <p className={`dara-qr-left-status${open ? ' is-open' : ''}${residual.stale ? ' is-stale' : ''}`} role="status">{unexplainedStatus(left)}</p>
    </Tooltip>
    {open && next.length ? <>
      <h3>Best matches for the remaining peaks</h3>
      <ul>{next.map(row => <li key={row.key}>
        <div className="dara-qr-phase">
          <div><ChemicalFormula formula={row.formula} /> <span className="meta"><SpaceGroup row={row} /></span></div>
          <div className="meta">{`${row.sourceLabel} · ${row.peaksText} peaks`}</div>
        </div>
        <span className="dara-num">{row.scoreText}</span>
        <Tooltip title={row.inRanking ? null : 'Not in the ranked list'}>
          <Button size="small" icon={<PlusOutlined />} disabled={!row.inRanking || selected.includes(row.key) || selected.length >= MAX_REFINE}
            onClick={() => setSelected([...selected, row.key])}>Add</Button>
        </Tooltip>
      </li>)}</ul>
    </> : null}
  </section>;
}

/** The last ranking on the Run card, when its inputs are still the form's. */
export function QuickRankingSummary({ quick }) {
  const data = quick.ranked;
  if (!data) return null;
  const count = (data.ranking || []).length;
  return <div className="dara-qr-summary">
    <div>
      <div><b>Search-match</b> <span className="dara-qr-summary-meta">
        {count ? `${data.pool?.candidates ?? count} candidates ranked · ${quick.selected.length} selected` : 'no candidate ranked'}</span></div>
      {data.combination?.length ? <div>Suggested: <Suggestion combination={data.combination} /></div> : null}
    </div>
    <Button size="small" onClick={quick.open}>Open</Button>
  </div>;
}

const Workspace = styled.section`
  font-size: var(--dara-font-14);
  .dara-qr-head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px;
    padding-bottom: 12px; border-bottom: 1px solid var(--dara-border);
  }
  .dara-qr-title { flex: 1 1 320px; min-width: 0; }
  .dara-qr-title h1 { margin: 0; font-size: var(--dara-font-20); font-weight: 600; line-height: 28px; }
  /* Focused on open so screen readers start here; it is not a control, so no focus ring. */
  .dara-qr-title h1:focus, .dara-qr-title h1:focus-visible { outline: none !important; }
  .dara-qr-context { margin: 0; color: var(--dara-text-secondary); font-size: var(--dara-font-12); overflow-wrap: anywhere; }
  .dara-qr-context > span + span::before { content: ' · '; }
  .dara-qr-note { margin: 8px 0 12px; color: var(--dara-text-hint); font-size: var(--dara-font-12); }
  > .ant-alert { margin-bottom: 12px; }
  .dara-qr-panel {
    background: var(--dara-surface); border: 1px solid var(--dara-border); border-radius: var(--dara-radius);
  }
  .dara-qr-plot { padding: 8px 12px 4px; margin-bottom: 16px; }
  .dara-qr-plot-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; min-height: 22px; font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-plot-head b { color: var(--dara-text); font-weight: 600; }
  .dara-qr-plot-area { height: max(280px, 40vh); }
  .dara-qr-preview-mark { display: inline-block; width: 0; height: 12px; margin: 0 6px -1px 0; border-left: 2px dotted var(--dara-text); }
  .dara-qr-wait { display: flex; flex-direction: column; justify-content: center; align-items: flex-start; gap: 12px; padding: 0 24px; }
  .dara-qr-wait .ant-progress { width: min(480px, 100%); }
  .dara-qr-body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 320px); gap: 16px; align-items: start; }
  .dara-qr-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-bottom: 8px; }
  .dara-qr-toolbar label { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
  .dara-qr-pool { margin-left: auto; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-caution-list { margin-bottom: 8px; font-size: var(--dara-font-13); }
  .dara-caution-list .anticon { color: var(--dara-review); }
  /* The list scrolls under the plot so the preview stays in view while browsing candidates. */
  .dara-qr-table { max-height: max(320px, calc(58vh - 250px)); overflow: auto; }
  .dara-qr-table .ant-table-thead > tr > th { position: sticky; top: 0; z-index: 2; }
  .dara-qr-table .ant-table-tbody > tr.ant-table-row { cursor: pointer; }
  /* The checkbox marks a selected row; the tint is kept for the previewed (focused) row. */
  .dara-qr-table .ant-table-tbody > tr.ant-table-row-selected:not(.dara-row-shown) > td { background: var(--dara-surface); }
  .dara-qr-table .ant-table-tbody > tr.ant-table-row-selected:not(.dara-row-shown):hover > td { background: var(--dara-surface-sunken); }
  .dara-qr-table .ant-table-tbody > tr.ant-table-row:focus-visible { outline-offset: -2px; }
  .dara-qr-dup { margin-left: 4px; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-qr-selected { padding: 12px; }
  .dara-qr-selected h2 { display: flex; justify-content: space-between; margin: 0 0 8px; font-size: var(--dara-font-14); font-weight: 600; }
  .dara-qr-selected h2 span { font-weight: 400; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-qr-selected ul { margin: 0; padding: 0; list-style: none; }
  .dara-qr-selected li {
    display: grid; grid-template-columns: 12px minmax(0, 1fr) auto auto; align-items: center; gap: 0 8px;
    padding: 4px 0; border-top: 1px solid var(--dara-border);
  }
  .dara-qr-selected li:first-child { border-top: 0; }
  .dara-qr-swatch { width: 12px; height: 12px; border-radius: var(--dara-radius); }
  .dara-qr-phase { min-width: 0; }
  .dara-qr-phase > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dara-qr-phase .meta { font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-empty { margin: 0; color: var(--dara-text-secondary); }
  .dara-qr-suggest { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--dara-border-strong); }
  .dara-qr-suggest p { margin: 0 0 8px; }
  .dara-qr-limit { margin: 8px 0 0; color: var(--dara-review); }
  .dara-qr-left { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--dara-border-strong); }
  .dara-qr-left-status { margin: 0 0 8px; font-weight: 600; }
  .dara-qr-left-status.is-open { color: var(--dara-review); }
  .dara-qr-left-status.is-stale { opacity: 0.6; }
  .dara-qr-left h3 { margin: 0 0 4px; font-size: var(--dara-font-12); font-weight: 400; color: var(--dara-text-secondary); }
  .dara-qr-left li { grid-template-columns: minmax(0, 1fr) auto auto; }
  .dara-qr-plot-tools { display: inline-flex; align-items: center; gap: 12px; }
  .dara-qr-plot-tools label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  @media (max-width: 1079px) {
    .dara-qr-body { grid-template-columns: minmax(0, 1fr); }
  }
`;

/**
 * The quick ranking workspace that replaces the submission form while it is open: the measured
 * pattern with reference sticks of the selected and the focused candidate, the candidate table and
 * the selected phases. `onRefine` submits a Known-phase refinement of the selected structures.
 */
export default function QuickRankingWorkspace({ quick, context, onRefine, onIdentify, alert }) {
  const { state, selected, setSelected, focused, setFocused, colors, residual } = quick;
  const heading = useRef(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const [showResidual, setShowResidual] = useState(false);
  const [zoom, setZoom] = useState({ range: null, revision: 0 }); // a click on an unexplained-peak marker zooms there

  const data = state.status === 'done' ? state.data : null;
  const rows = rankingRows(data);
  const rowByKey = Object.fromEntries(rows.map(row => [row.key, row]));
  const chosen = selected.map(key => rowByKey[key]).filter(Boolean);
  const focusedRow = rowByKey[focused];
  const over = selected.length > MAX_REFINE;
  const suggestion = data ? defaultSelection(data) : [];
  const usingSuggestion = suggestion.length === selected.length && suggestion.every(key => selected.includes(key));

  const onRowKey = (event, row) => {
    const action = rowKeyAction(event.key);
    if (!action || (action === 'toggle' && event.target !== event.currentTarget)) return; // Space on the checkbox ticks it
    event.preventDefault();
    if (action === 'toggle') { setSelected(toggleKey(selected, row.key)); return; }
    const next = action === 'next' ? event.currentTarget.nextElementSibling : event.currentTarget.previousElementSibling;
    if (next?.classList.contains('ant-table-row')) next.focus();
  };

  const columns = [
    { title: 'Rank', dataIndex: 'rank', width: 64, className: 'dara-num', sorter: SORTERS.rank, defaultSortOrder: 'ascend' },
    { title: 'Formula', dataIndex: 'formula', render: formula => <ChemicalFormula formula={formula} /> },
    { title: 'Space group', dataIndex: 'spacegroupText', render: (_, row) => <span style={{ whiteSpace: 'nowrap' }}><SpaceGroup row={row} /></span> },
    {
      title: 'Source', dataIndex: 'source',
      render: (source, row) => {
        const { label, url } = sourceInfo(source);
        return <span style={{ whiteSpace: 'nowrap' }}>{url ? <a href={url} target="_blank" rel="noreferrer">{label}</a> : label}
          {row.duplicates ? <Tooltip title={`${row.duplicates} more record${row.duplicates === 1 ? '' : 's'} with the same structure`}>
            <span className="dara-qr-dup">+{row.duplicates}</span></Tooltip> : null}</span>;
      },
    },
    {
      title: <Tooltip title="Share of the measured net intensity the structure can explain (lattice-penalised)">Score</Tooltip>,
      dataIndex: 'scoreText', width: 84, className: 'dara-num', sorter: SORTERS.score, sortDirections: ['descend', 'ascend'],
    },
    {
      title: <Tooltip title="Strong calculated peaks found in the pattern / strong peaks in range">Peaks</Tooltip>,
      dataIndex: 'peaksText', width: 84, className: 'dara-num', sorter: SORTERS.peaks, sortDirections: ['descend', 'ascend'],
    },
    {
      title: <Tooltip title="Lattice change that fits best (d scale − 1)">Lattice</Tooltip>,
      dataIndex: 'latticeText', width: 92, className: 'dara-num', sorter: SORTERS.lattice,
    },
    {
      title: 'Flags', dataIndex: 'tags',
      render: tags => tags.map(tag => <Tag key={tag.label} color={tag.color} style={{ marginBottom: 2 }}>{tag.label}</Tag>),
    },
  ];

  const canRefine = selected.length > 0 && !over && !!data;
  const left = residual?.data || null;
  // 2 % of the strongest net intensity: the markers sit just above the axis, inside the plot area.
  const markerLevel = 0.02 * Math.max(0, ...((data?.profile?.net || []).filter(Number.isFinite)));
  const bounds = data?.profile ? [data.profile.two_theta?.[0], data.profile.two_theta?.slice(-1)[0]] : null;
  // The residual view shows what is left: the selected phases' sticks would cover it; the preview stays.
  const traces = data ? [...overlayTraces(showResidual && left ? withResidualProfile(data, left) : data, showResidual && left ? [] : selected, colors, focused),
    ...(left?.unexplained_peaks?.length ? [unexplainedMarkers(left.unexplained_peaks, markerLevel)] : [])] : [];
  // A click on an unexplained-peak marker zooms there. The hovered point can be the pattern's (closest
  // in 2-D), so a click within 0.3° of a marker, by the 2θ under the mouse, counts as well.
  const onPlotClick = event => {
    const point = event?.points?.find(item => item.data?.meta === 'unexplained');
    const near = (left?.unexplained_peaks || []).map(peak => Number(peak[0]))
      .filter(x => Number.isFinite(event?.cursorX) && Math.abs(x - event.cursorX) <= 0.3)
      .sort((x, y) => Math.abs(x - event.cursorX) - Math.abs(y - event.cursorX))[0];
    const x = point ? Number(point.x) : near;
    if (!Number.isFinite(x)) return;
    const range = zoomWindow(x, bounds);
    // The intensity axis follows the window, so a small peak fills the plot.
    const shownProfile = (showResidual && left ? withResidualProfile(data, left) : data).profile;
    setZoom(previous => ({ range, y: windowRange(shownProfile, range), revision: previous.revision }));
  };
  let body;
  if (state.status === 'pending') {
    const percentDone = progressPercent(state.progress);
    body = <div className="dara-qr-panel dara-qr-plot-area dara-qr-wait" role="status">
      <span>{percentDone === null ? <Spin size="small" style={{ marginRight: 8 }} /> : null}{progressText(state.progress)}</span>
      {percentDone !== null ? <Progress percent={percentDone} size="small" /> : null}
      <Button onClick={quick.cancel}>Cancel</Button>
    </div>;
  } else if (state.status === 'error') {
    body = <div className="dara-failure" role="alert">
      <h3>The search-match failed</h3>
      <p>{state.error}</p>
      <Button onClick={() => quick.start()}>Retry</Button>
    </div>;
  } else if (state.status === 'idle') {
    body = <div className="dara-empty">
      <h3>Search-match cancelled</h3>
      <p>Run it again to rank candidate structures for this pattern.</p>
      <Button type="primary" onClick={() => quick.start()}>Run search-match</Button>
    </div>;
  } else {
    const caveats = data.caveats || [];
    body = <>
      <section className="dara-qr-panel dara-qr-plot" aria-label="Measured pattern and reference sticks">
        <div className="dara-qr-plot-head">
          <span>{focusedRow
            ? <><span className="dara-qr-preview-mark" />Preview <b><ChemicalFormula formula={focusedRow.formula} /></b>
              {` · ${focusedRow.sourceLabel}`}{selected.includes(focusedRow.key) ? ' (selected)' : ''}</>
            : 'Click a candidate to preview its reflections; tick it to keep it.'}</span>
          <span className="dara-qr-plot-tools">
            <span>{showResidual && left ? 'What is left: measured minus the selected phases' : 'Measured pattern, background removed'}</span>
            <Tooltip title={selected.length ? 'Show what the selected phases leave unexplained (the residual net pattern)' : 'Select phases first'}>
              <label><Switch size="small" checked={showResidual && Boolean(left)} disabled={!left} onChange={setShowResidual} aria-label="Show the residual" />Residual</label>
            </Tooltip>
            {zoom.range ? <Button size="small" type="link" onClick={() => setZoom(previous => ({ range: null, revision: previous.revision + 1 }))}>Whole pattern</Button> : null}
          </span>
        </div>
        {data.profile ? <Plot data={traces} useResizeHandler xBounds={bounds} onClick={onPlotClick}
          onDoubleClick={() => setZoom(previous => ({ range: null, revision: previous.revision + 1 }))}
          className="dara-qr-plot-area" style={{ width: '100%' }}
          layout={{
            autosize: true, uirevision: `${state.key}-${zoom.revision}`, showlegend: false, hovermode: 'closest',
            margin: { l: 56, r: 12, t: 8, b: 40 }, font: { size: 12 },
            xaxis: { title: { text: '2θ (°)' }, ...(zoom.range ? { range: [...zoom.range], autorange: false } : {}) },
            yaxis: { title: { text: showResidual && left ? 'Residual net intensity' : 'Net intensity' }, rangemode: 'tozero',
              ...(zoom.range && zoom.y ? { range: [...zoom.y], autorange: false } : {}) },
          }}
          config={{ displaylogo: false, responsive: true }} /> : null}
      </section>
      <div className="dara-qr-body">
        <div>
          <div className="dara-qr-toolbar">
            <label><Switch size="small" checked={quick.theoretical} onChange={quick.toggleTheoretical} />
              Include theoretical (computed) structures</label>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => quick.start()}>Run again</Button>
            <span className="dara-qr-pool">{poolText(data)}</span>
          </div>
          {caveats.length ? <div className="dara-caution-list" role="note"><ul>
            {caveats.map(text => <li key={text}><WarningOutlined />{text}</li>)}</ul></div> : null}
          {rows.length ? <div className="dara-qr-table">
            <Table size="small" columns={columns} dataSource={rows} pagination={false} tableLayout="auto"
              rowSelection={{ selectedRowKeys: selected, onChange: setSelected, columnWidth: 36 }}
              rowClassName={row => (row.key === focused ? 'dara-row-shown' : '')}
              onRow={row => ({
                tabIndex: 0, title: 'Preview in the plot; Space selects', 'aria-current': row.key === focused || undefined,
                onFocus: () => setFocused(row.key), onKeyDown: event => onRowKey(event, row),
              })} />
          </div> : <div className="dara-empty">
            <h3>No candidate could be ranked</h3>
            <p>{caveats.length ? 'See the notes above.' : 'No reference structure matched the elements in this 2θ range.'}
              {' '}Check the elements and databases in the setup{quick.theoretical ? '.' : ', or include theoretical structures.'}</p>
          </div>}
        </div>
        {rows.length ? <aside className="dara-qr-panel dara-qr-selected" aria-label="Selected phases">
          <h2>Selected phases <span>{`${selected.length} of at most ${MAX_REFINE}`}</span></h2>
          {chosen.length ? <ul>{chosen.map(row => <li key={row.key}>
            <i className="dara-qr-swatch" style={{ background: colors[row.key] }} aria-hidden="true" />
            <div className="dara-qr-phase">
              <div><ChemicalFormula formula={row.formula} /> <span className="meta"><SpaceGroup row={row} /></span></div>
              <div className="meta">{row.sourceLabel}</div>
            </div>
            <span className="dara-num">{row.scoreText}</span>
            <Button type="text" size="small" icon={<CloseOutlined />} aria-label={`Remove ${row.formula}`}
              onClick={() => setSelected(selected.filter(key => key !== row.key))} />
          </li>)}</ul>
            : <p className="dara-qr-empty">No phase selected. Tick candidates in the table to draw their sticks on the pattern, then refine them together.</p>}
          {chosen.length ? <ResidualPanel residual={residual} rows={rows} selected={selected} setSelected={setSelected} /> : null}
          {over ? <p className="dara-qr-limit">{`A refinement takes at most ${MAX_REFINE} phases: remove ${selected.length - MAX_REFINE}.`}</p> : null}
          {data.combination?.length ? <div className="dara-qr-suggest">
            <p>Suggested: <Suggestion combination={data.combination} /></p>
            <Button size="small" onClick={() => setSelected(suggestion)} disabled={usingSuggestion}>Use suggestion</Button>
          </div> : null}
        </aside> : null}
      </div>
    </>;
  }

  return <Workspace aria-labelledby="dara-qr-title">
    <header className="dara-qr-head">
      <Button icon={<ArrowLeftOutlined />} onClick={() => quick.close()}>Back to setup</Button>
      <div className="dara-qr-title">
        <h1 id="dara-qr-title" ref={heading} tabIndex={-1}>Search-match</h1>
        <p className="dara-qr-context">{context.filter(Boolean).map((item, index) => <span key={index}>{item}</span>)}</p>
      </div>
      <Tooltip title={over ? `At most ${MAX_REFINE} phases per refinement` : 'Known-phase Rietveld refinement of the selected structures (minutes)'}>
        <Button onClick={() => quick.refine(onRefine)} loading={quick.refining} disabled={!canRefine || quick.identifying}>
          {`Refine with Rietveld (${selected.length})`}
        </Button>
      </Tooltip>
      <Tooltip title={over ? `At most ${MAX_REFINE} phases` : 'Save the selected phases as the result, without refinement'}>
        <Button type="primary" onClick={() => quick.identify(onIdentify)} loading={quick.identifying} disabled={!canRefine || quick.refining}>
          Save identification
        </Button>
      </Tooltip>
    </header>
    <p className="dara-qr-note">Each score is the share of the net intensity a structure explains on its own. Save identification gives a result in seconds without refinement (phase list, peak list, semi-quantitative RIR); Refine with Rietveld runs the full quantitative refinement (minutes).</p>
    {alert}
    {body}
  </Workspace>;
}
