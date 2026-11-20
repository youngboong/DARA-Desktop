import React, { useEffect, useRef, useState } from 'react';
import Loadable from 'react-loadable';
import styled from 'styled-components';
import { Alert, Button, Popover, Progress, Radio, Slider, Spin, Switch, Table, Tag, Tooltip } from 'antd';
import { ArrowLeftOutlined, CloseOutlined, PlusOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons';
import ChemicalFormula from './ChemicalFormula';
import { progressPercent } from './structureLibrary.mjs';
import {
  MAX_REFINE, SORTERS, assignColors, backgroundSettings, backgroundTraces, combinationText, taskRankingBody, defaultSelection, nextMatches, overlayTraces, pollDelay, poolText,
  progressText, rankingNotes, rankingRows, refineSelection, requestKey, rowKeyAction, sourceInfo, suggestedKeys, toggleKey,
  mismatchView, startingPoints, unexplainedMarkers, unexplainedStatus, weakNotSelected, windowRange, withPoints, withResidualProfile, zoomWindow,
  alternativeGroups, alternativeOf, alternativeRecords,
} from './quickRanking.mjs';
import { addAnchor, anchorError, curveWarnings, pchip, removeAnchor } from './backgroundAnchors.mjs';
import { BackgroundCurvePanel } from './BackgroundEditor';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <div className="dara-qr-plot-area" /> });
const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

// A cancelled job stops on the server; older servers have no DELETE, so its errors are ignored.
const cancelJob = id => { if (id) fetch(`${URL}/quick-ranking/${id}`, { method: 'DELETE' }).catch(() => {}); };

/**
 * State of the quick ranking (search-match) of the submission page; it lives with the page, so the
 * last ranking survives "Back to setup". `buildRequest()` returns the multipart body (pattern file,
 * elements, formulas, wavelength, range) or throws an Error with a user message; a body with
 * `source_task_id` and no file ranks the stored pattern of that task (a rerun).
 * Opening the workspace pushes a history entry, so the browser's Back returns to the setup.
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
  const status = useRef(state.status);
  status.current = state.status;
  const ownBack = useRef(false); // a history.back() of close(): its popstate is not the user's Back
  const background = useRef(null); // accepted "Background…" settings, sent with every ranking request
  const preselect = useRef(null); // reference ids to select when the next ranking finishes (a reopened identification)

  const setSelected = keys => {
    setSelectedKeys(keys);
    setColors(previous => assignColors(previous, keys));
  };
  const request = includeTheoretical => {
    const body = buildRequest();
    body.set('include_theoretical', String(includeTheoretical));
    if (background.current) body.set('background', JSON.stringify(background.current));
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
    const storedTask = body.get('source_task_id');
    try {
      let response = await fetch(storedTask ? `${URL}/task/${storedTask}/quick-ranking` : `${URL}/quick-ranking`, storedTask
        ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(taskRankingBody(body.entries())) }
        : { method: 'POST', body });
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
      const wanted = (preselect.current || []).filter(source => (data.ranking || []).some(row => row.source === source));
      preselect.current = null;
      setSelected(wanted.length ? wanted : defaultSelection(data));
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

  const closeView = restoreScroll => {
    if (status.current === 'pending') cancel();
    setShown(false);
    if (restoreScroll) requestAnimationFrame(() => window.scrollTo(0, scroll.current));
  };
  // Browser Back from the workspace: back to the setup, as "Back to setup".
  useEffect(() => {
    const onPop = event => {
      if (ownBack.current) { ownBack.current = false; return; }
      if (!event.state?.daraQuick) closeView(true);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    /**
     * Shows the workspace: the last ranking when its inputs are unchanged, else a new one. A stored
     * search-match reopens with `{selected, background, theoretical}`: a new ranking with those
     * settings, its reference ids selected where the ranking has them.
     */
    open(stored = null) {
      scroll.current = window.scrollY;
      setShown(true);
      if (!window.history.state?.daraQuick) window.history.pushState({ ...(window.history.state || {}), daraQuick: true }, '');
      window.scrollTo(0, 0);
      if (stored) {
        background.current = stored.background || null;
        preselect.current = stored.selected?.length ? [...stored.selected] : null;
        const withTheoretical = Boolean(stored.theoretical);
        setTheoretical(withTheoretical);
        start(withTheoretical);
        return;
      }
      if (!ranked && state.status !== 'pending') start(theoretical);
    },
    /** Back to the setup; a pending ranking is cancelled. */
    close(restoreScroll = true) {
      closeView(restoreScroll);
      if (window.history.state?.daraQuick) { ownBack.current = true; window.history.back(); }
    },
    toggleTheoretical(checked) {
      setTheoretical(checked);
      start(checked);
    },
    /** Ranks again with HighScore-style background settings ({method, granularity, bending_factor, anchors}). */
    rankWithBackground(settings) {
      background.current = settings;
      start(theoretical);
    },
    /**
     * `onRefine({referenceIds, formulas})` resolves to whether a refinement was submitted; `extra`
     * keys (alternatives to tell apart) are refined with the selected phases.
     */
    async refine(onRefine, extra = []) {
      setRefining(true);
      let submitted = false;
      try {
        submitted = await onRefine(refineSelection(state.data, [...selected, ...extra]));
      } finally {
        setRefining(false);
      }
      return submitted; // on success the page opens the new task
    },
    /**
     * `onIdentify({referenceIds, background})` (selection order; the ranking's background settings,
     * base points included) saves the identification; resolves to whether it was saved.
     */
    async identify(onIdentify) {
      setIdentifying(true);
      try {
        await onIdentify({ referenceIds: [...selected], background: background.current, alternatives: alternativeRecords(state.data, selected) });
      } finally {
        setIdentifying(false);
      }
    },
  };
}

/**
 * Steps of the greedy suggestion with formula subscripts: "Fe2O3 + ZnO (weak)". Names only: a step's
 * gain is a share of the intensity explained, and a percentage beside a formula reads as an amount.
 */
function Suggestion({ combination }) {
  return <span title={combinationText(combination)}>{combination.map((step, index) => <React.Fragment key={step.source}>
    {index ? ' + ' : ''}<ChemicalFormula formula={step.formula} />{step.weak ? ' (weak)' : ''}
  </React.Fragment>)}</span>;
}

/**
 * The background view of `settings` on the finished search-match `jobId` (its pattern, not ranked
 * again): `{two_theta, observed, intensity, settings}`. Throws an Error with a user message.
 */
async function backgroundView(jobId, settings) {
  let response;
  try {
    response = await fetch(`${URL}/quick-ranking/${jobId}/background`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings),
    });
  } catch (error) {
    throw new Error('The DARA server did not answer.');
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(response.status === 404 ? 'This search-match has expired: run it again.' : body.detail || `HTTP ${response.status}`);
  return body;
}

/**
 * HighScore's "Determine Background" for the search-match: the default method, or Sonneveld & Visser
 * with HighScore's granularity and bending factor. While the sliders move, the curve is drawn on the
 * pattern (`onPreview(view)`, debounced, from the finished job's pattern); "Accept" ranks again with them.
 * HighScore's manual mode (base points set by hand) is "Edit points" on the plot; Accept here replaces them.
 */
function BackgroundForm({ initial, jobId, onAccept, onPreview }) {
  const points = initial?.anchors?.length || 0;
  const [settings, setSettings] = useState(() => backgroundSettings(initial));
  const [previewError, setPreviewError] = useState('');
  const set = (key, value) => setSettings(previous => ({ ...previous, [key]: value }));
  const tuned = settings.method === 'sonneveld_visser';
  const chosen = tuned ? settings : { method: 'auto' };
  const chosenKey = JSON.stringify(chosen);
  useEffect(() => {
    if (!jobId) return undefined;
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const body = await backgroundView(jobId, chosen);
        if (current) { setPreviewError(''); onPreview(body); }
      } catch (error) {
        if (current) setPreviewError(String(error.message || error));
      }
    }, 300);
    return () => { current = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, chosenKey]);
  return <div className="dara-qr-bg-form">
    <Radio.Group size="small" value={settings.method} optionType="button" onChange={event => set('method', event.target.value)}
      options={[{ value: 'auto', label: 'Automatic' }, { value: 'sonneveld_visser', label: 'Granularity / bending' }]} />
    <label htmlFor="dara-qr-granularity">Granularity <span className="dara-num">{settings.granularity}</span></label>
    <Slider id="dara-qr-granularity" min={2} max={200} value={settings.granularity} disabled={!tuned}
      onChange={value => set('granularity', value)} tooltip={{ formatter: null }} />
    <label htmlFor="dara-qr-bending">Bending factor <span className="dara-num">{settings.bending_factor}</span></label>
    <Slider id="dara-qr-bending" min={0} max={50} step={0.5} value={settings.bending_factor} disabled={!tuned}
      onChange={value => set('bending_factor', value)} tooltip={{ formatter: null }} />
    {previewError ? <p className="dara-qr-bg-error" role="alert">{`Curve preview unavailable: ${previewError}`}</p> : null}
    {points ? <p className="dara-qr-bg-note">{`In use: ${points} base points set by hand. Accept replaces them with this curve.`}</p> : null}
    <p className="dara-qr-bg-note">Base points (Edit points on the plot) change the search-match only; a Rietveld refinement fits its own background.</p>
    <Button type="primary" size="small" onClick={() => onAccept(chosen)}>Accept and rank again</Button>
  </div>;
}

// Column definitions, shared by the candidate table and the side lists (one term per thing).
const HELP = {
  explains: 'Share of the measured net intensity this structure explains on its own (lattice-penalised). Not an amount: refine for wt%.',
  rest: 'Share of the intensity the selected phases leave unexplained that this structure explains',
  peaks: 'Strong calculated peaks found in the pattern / strong peaks in range',
  lattice: 'Lattice change that fits best (d scale − 1)',
  flags: 'picked: in the suggestion · weak: small share of the pattern · polymorph: other structures of the formula ranked · theoretical: computed only (MP) · variant of: same structure as a higher row',
};

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
      <ul className="dara-qr-next">
        <li className="dara-qr-list-head"><span>Next best match</span><Tooltip title={HELP.peaks}><span className="dara-num">Peaks</span></Tooltip>
          <Tooltip title={HELP.rest}><span className="dara-num">Explains</span></Tooltip><span /></li>
        {next.map(row => <li key={row.key}>
        <div className="dara-qr-phase">
          <div><ChemicalFormula formula={row.formula} /> <span className="meta"><SpaceGroup row={row} /></span></div>
          <div className="meta">{row.sourceLabel}</div>
        </div>
        <span className="dara-num" aria-label={`Peaks ${row.peaksText}`}>{row.peaksText}</span>
        <span className="dara-num" aria-label={`Explains ${row.scoreText} of the rest`}>{row.scoreText}</span>
        <Tooltip title={row.inRanking ? null : 'Not in the ranked list'}>
          <Button size="small" icon={<PlusOutlined />} disabled={!row.inRanking || selected.includes(row.key) || selected.length >= MAX_REFINE}
            onClick={() => setSelected([...selected, row.key])}>Add</Button>
        </Tooltip>
      </li>)}</ul>
    </> : null}
  </section>;
}

/** The last ranking on the Run card, when its inputs are still the form's (the Search-match button opens it). */
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
  .dara-qr-head + * { margin-top: 12px; }
  > .ant-alert { margin-bottom: 12px; }
  .dara-qr-mismatch { max-width: none; }
  .dara-qr-mismatch .ant-alert-message { font-weight: 600; }
  .dara-qr-panel {
    background: var(--dara-surface); border: 1px solid var(--dara-border); border-radius: var(--dara-radius);
  }
  .dara-qr-plot { padding: 8px 12px 4px; margin-bottom: 12px; }
  .dara-qr-plot-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; min-height: 22px; font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-plot-head b { color: var(--dara-text); font-weight: 600; }
  /* Plot, selection status and the first table rows share the first screen at 1366 x 768. */
  .dara-qr-plot-area { height: max(240px, 32vh); }
  .dara-qr-preview-mark { display: inline-block; width: 0; height: 12px; margin: 0 6px -1px 0; border-left: 2px dotted var(--dara-shell-mark); }
  .dara-qr-wait { display: flex; flex-direction: column; justify-content: center; align-items: flex-start; gap: 12px; padding: 0 24px; }
  .dara-qr-wait .ant-progress { width: min(480px, 100%); }
  .dara-qr-body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); gap: 16px; align-items: start; }
  .dara-qr-body > aside { position: sticky; top: 16px; }
  .dara-qr-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-bottom: 8px; }
  .dara-qr-toolbar label { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
  .dara-qr-pool { margin-left: auto; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-caution-list { margin-bottom: 8px; font-size: var(--dara-font-13); }
  .dara-caution-list .anticon { color: var(--dara-review); }
  /* The list scrolls under the plot so the preview stays in view while browsing candidates. */
  .dara-qr-table { max-height: max(240px, calc(100vh - 440px)); overflow: auto; }
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
    display: grid; grid-template-columns: 12px minmax(0, 1fr) 48px 24px; align-items: center; gap: 0 8px;
    padding: 4px 0; border-top: 1px solid var(--dara-border);
  }
  .dara-qr-selected li.dara-qr-list-head { padding: 0 0 2px; border-top: 0; font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-list-head > span { cursor: default; }
  .dara-qr-selected .dara-num { text-align: right; font-variant-numeric: tabular-nums; }
  .dara-qr-selected .dara-qr-next li { grid-template-columns: minmax(0, 1fr) 40px 48px auto; }
  .dara-qr-selected li:first-child, .dara-qr-selected li.dara-qr-list-head + li { border-top: 0; }
  .dara-qr-selected li.dara-qr-alt { display: block; padding: 0 0 6px 20px; border-top: 0; font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-selected li.dara-qr-alt .ant-btn { margin-top: 2px; }
  .dara-qr-alt-rank { color: var(--dara-text-secondary); }
  .dara-qr-swatch { width: 12px; height: 12px; border-radius: var(--dara-radius); }
  .dara-qr-phase { min-width: 0; }
  .dara-qr-phase > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dara-qr-phase .meta { font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-phase > div.meta { white-space: normal; }
  .dara-qr-empty { margin: 0; color: var(--dara-text-secondary); }
  .dara-qr-suggest { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--dara-border-strong); }
  .dara-qr-suggest p { margin: 0 0 8px; }
  .dara-qr-limit { margin: 8px 0 0; color: var(--dara-review); }
  .dara-qr-weak { margin: 8px 0 0; font-size: var(--dara-font-13); color: var(--dara-review); }
  .dara-qr-weak .ant-btn { padding: 0 4px; height: auto; }
  .dara-qr-left { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--dara-border-strong); }
  .dara-qr-left-status { margin: 0 0 8px; font-weight: 600; }
  .dara-qr-left-status.is-open { color: var(--dara-review); }
  .dara-qr-left-status.is-stale { opacity: 0.6; }
  .dara-qr-plot-tools { display: inline-flex; align-items: center; gap: 12px; }
  .dara-qr-plot-tools label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .dara-qr-body.single { grid-template-columns: minmax(0, 1fr); }
  @media (max-width: 1079px) {
    .dara-qr-body { grid-template-columns: minmax(0, 1fr); }
    .dara-qr-body > aside { position: static; }
  }
`;

/**
 * The quick ranking workspace that replaces the submission form while it is open: the measured
 * pattern with reference sticks of the selected and the focused candidate, the candidate table and
 * the selected phases. `onRefine` submits a Known-phase refinement of the selected structures.
 */
export default function QuickRankingWorkspace({ quick, context, onRefine, onIdentify, alert, mismatch: inspected, onUseRecorded }) {
  const { state, selected, setSelected, focused, setFocused, colors, residual } = quick;
  const heading = useRef(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const [showResidual, setShowResidual] = useState(false);
  const [showBackground, setShowBackground] = useState(false); // the measured pattern with its background
  const [backgroundOpen, setBackgroundOpen] = useState(false);
  const [backgroundPreview, setBackgroundPreview] = useState(null); // live curve while the Background popover is open
  const [zoom, setZoom] = useState({ range: null, revision: 0 }); // a click on an unexplained-peak marker zooms there
  const [points, setPoints] = useState(null); // {anchors, selected} while the base points are edited
  const [pointsPreview, setPointsPreview] = useState(null); // {key, view} | {key, error}: the server's curve through them

  const data = state.status === 'done' ? state.data : null;
  const rows = rankingRows(data);
  const rowByKey = Object.fromEntries(rows.map(row => [row.key, row]));
  const chosen = selected.map(key => rowByKey[key]).filter(Boolean);
  const focusedRow = rowByKey[focused];
  const over = selected.length > MAX_REFINE;
  const suggestion = data ? suggestedKeys(data) : [];
  const usingSuggestion = suggestion.every(key => selected.includes(key));
  const weakLeft = data ? weakNotSelected(data, selected) : [];
  // Candidates peak matching cannot tell apart from a selected phase: grouped under it, not ranked.
  const groups = alternativeGroups(rows, selected);
  const altOf = alternativeOf(rows, selected);
  // The ranking's own check (the server compares the file's anode with the request) wins over the setup's.
  const mismatch = mismatchView(data?.wavelength_mismatch) || inspected;

  // HighScore's manual background ("Edit points" in the Background view): base points dragged on
  // the plot; the server draws the curve through them (debounced) on this ranking's pattern.
  const bgView = data?.background;
  useEffect(() => { setPoints(null); }, [data?.job_id]); // a new ranking ends the editing
  const pointsKey = points ? JSON.stringify(points.anchors) : null;
  const pointsError = points ? anchorError(points.anchors) : null;
  useEffect(() => {
    if (!pointsKey || pointsError || !data?.job_id) return undefined;
    let current = true;
    const timer = setTimeout(async () => {
      try {
        const view = await backgroundView(data.job_id, withPoints(bgView.settings, JSON.parse(pointsKey)));
        if (current) setPointsPreview({ key: pointsKey, view });
      } catch (error) {
        if (current) setPointsPreview({ key: pointsKey, error: String(error.message || error) });
      }
    }, 300);
    return () => { current = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pointsKey, pointsError, data?.job_id]);
  // Only "background above the data": without a fit there is no phase intensity to tell a peak by.
  const pointWarnings = React.useMemo(() => (points && backgroundTraces(bgView) ? curveWarnings({
    x: bgView.two_theta, yObs: bgView.observed, yCalc: bgView.intensity, yBkg: bgView.intensity, anchors: points.anchors,
  }) : []),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [pointsKey, bgView]);
  const grid = points && backgroundTraces(bgView) ? bgView.two_theta : null;
  const shownCurve = pointsPreview?.key === pointsKey ? pointsPreview : null;
  const pointsEdit = grid ? {
    anchors: points.anchors, selected: points.selected, moveX: [grid[0], grid[grid.length - 1]],
    curveOf: (anchors, xs) => pchip(anchors, xs || grid),
    blocked: x => !(x >= grid[0] && x <= grid[grid.length - 1]),
    onChange: anchors => setPoints(previous => previous && { ...previous, anchors }),
    onAdd: point => setPoints(previous => {
      if (!previous) return previous;
      const anchors = addAnchor(previous.anchors, point);
      return { anchors, selected: anchors.findIndex(item => item[0] === point[0]) };
    }),
    onRemove: index => setPoints(previous => previous && { anchors: removeAnchor(previous.anchors, index), selected: null }),
    onSelect: index => setPoints(previous => previous && { ...previous, selected: index }),
  } : null;
  const pointTraces = grid ? [backgroundTraces(bgView)[0],
    { type: 'scatter', mode: 'lines', name: 'Background', meta: 'bg-curve', x: grid, hoverinfo: 'skip', line: { color: '#c99a2e', width: 2 },
      y: shownCurve?.view?.intensity?.length === grid.length ? shownCurve.view.intensity : pchip(points.anchors, grid) },
    { type: 'scatter', mode: 'markers', name: 'Base points', meta: 'bg-anchors', cliponaxis: false,
      x: points.anchors.map(point => point[0]), y: points.anchors.map(point => point[1]),
      marker: { size: 7, color: points.anchors.map((_, index) => (index === points.selected ? '#3f6c8f' : 'rgba(255,255,255,0)')), line: { color: '#3f6c8f', width: 1.5 } },
      hovertemplate: '2θ %{x:.3f}°<br>%{y:.1f}<extra>Base point</extra>' }] : null;
  const startPoints = () => {
    setPoints({ anchors: startingPoints(bgView), selected: null });
    setPointsPreview(null);
  };
  const rankWithPoints = anchors => {
    setPoints(null);
    setShowBackground(true);
    quick.rankWithBackground(withPoints(bgView.settings, anchors));
  };

  const onRowKey = (event, row) => {
    const action = rowKeyAction(event.key);
    if (!action || (action === 'toggle' && event.target !== event.currentTarget)) return; // Space on the checkbox ticks it
    event.preventDefault();
    if (action === 'toggle') { setSelected(toggleKey(selected, row.key)); return; }
    const next = action === 'next' ? event.currentTarget.nextElementSibling : event.currentTarget.previousElementSibling;
    if (next?.classList.contains('ant-table-row')) next.focus();
  };

  const columns = [
    { title: 'Rank', dataIndex: 'rank', width: 64, className: 'dara-num', sorter: SORTERS.rank, defaultSortOrder: 'ascend',
      render: (rank, row) => (altOf[row.key] ? <Tooltip title={`Alternative of ${altOf[row.key].formula} (rank ${altOf[row.key].rank}): the same strong peaks, so peak matching cannot tell them apart`}>
        <span className="dara-qr-alt-rank">alt.</span></Tooltip> : rank) },
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
      title: <Tooltip title={HELP.explains}>Explains</Tooltip>,
      dataIndex: 'scoreText', width: 84, className: 'dara-num', sorter: SORTERS.score, sortDirections: ['descend', 'ascend'],
    },
    {
      title: <Tooltip title={HELP.peaks}>Peaks</Tooltip>,
      dataIndex: 'peaksText', width: 84, className: 'dara-num', sorter: SORTERS.peaks, sortDirections: ['descend', 'ascend'],
    },
    {
      title: <Tooltip title={HELP.lattice}>Lattice</Tooltip>,
      dataIndex: 'latticeText', width: 92, className: 'dara-num', sorter: SORTERS.lattice,
    },
    {
      title: <Tooltip title={HELP.flags}>Flags</Tooltip>, dataIndex: 'tags',
      render: tags => tags.map(tag => <Tag key={tag.label} color={tag.color} style={{ marginBottom: 2 }}>{tag.label}</Tag>),
    },
  ];

  const canRefine = selected.length > 0 && !over && !!data;
  const left = residual?.data || null;
  // 2 % of the strongest net intensity: the markers sit just above the axis, inside the plot area.
  const markerLevel = 0.02 * Math.max(0, ...((data?.profile?.net || []).filter(Number.isFinite)));
  const bounds = data?.profile ? [data.profile.two_theta?.[0], data.profile.two_theta?.slice(-1)[0]] : null;
  // The background view (when the server sends the curve) shows the measured pattern with the
  // background the ranking removed; the sticks belong to the net scale and stay in the other views.
  const withBackground = pointTraces || (backgroundPreview ? backgroundTraces(backgroundPreview) : showBackground ? backgroundTraces(data?.background) : null);
  // The residual view shows what is left: the selected phases' sticks would cover it; the preview stays.
  const traces = withBackground || !data ? withBackground || [] : [...overlayTraces(showResidual && left ? withResidualProfile(data, left) : data, showResidual && left ? [] : selected, colors, focused),
    ...(left?.unexplained_peaks?.length ? [unexplainedMarkers(left.unexplained_peaks, markerLevel)] : [])];
  // A click on an unexplained-peak marker zooms there. The hovered point can be the pattern's (closest
  // in 2-D), so a click within 0.3° of a marker, by the 2θ under the mouse, counts as well.
  const onPlotClick = event => {
    if (withBackground) return; // no unexplained-peak markers in the background view
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
      <Button type="primary" onClick={() => quick.close()}>Back to setup</Button>{' '}
      <Button onClick={() => quick.start()}>Retry</Button>
    </div>;
  } else if (state.status === 'idle') {
    body = <div className="dara-empty">
      <h3>Search-match cancelled</h3>
      <p>Run it again to rank candidate structures for this pattern.</p>
      <Button type="primary" onClick={() => quick.start()}>Run search-match</Button>
    </div>;
  } else {
    // The wavelength mismatch leads the workspace; its message is not repeated among the notes.
    const caveats = rankingNotes(data).filter(text => text !== data.wavelength_mismatch?.message);
    body = <div className={`dara-qr-body${rows.length ? '' : ' single'}`}>
        <div>
          <section className="dara-qr-panel dara-qr-plot" aria-label="Measured pattern and reference sticks">
            <div className="dara-qr-plot-head">
              <span>{focusedRow
                ? <><span className="dara-qr-preview-mark" />Preview <b><ChemicalFormula formula={focusedRow.formula} /></b>
                  {` · ${focusedRow.sourceLabel}`}{selected.includes(focusedRow.key) ? ' (selected)' : ''}</>
                : 'Click a row to preview its sticks'}</span>
              <span className="dara-qr-plot-tools">
                {backgroundTraces(data.background) ? <label><Switch size="small" checked={showBackground || Boolean(backgroundPreview) || Boolean(points)}
                  onChange={value => { setShowBackground(value); if (!value) setPoints(null); }} aria-label="Show the background" />Background</label> : null}
                {showBackground && !points && backgroundTraces(data.background) ? <Tooltip title="Set the background by hand: drag, add and remove base points on the curve (search-match only)">
                  <Button size="small" onClick={startPoints}>Edit points</Button>
                </Tooltip> : null}
                {data.background?.settings && !points ? <Popover trigger="click" placement="bottomRight" title="Determine background" open={backgroundOpen}
                  onOpenChange={value => { setBackgroundOpen(value); if (!value) setBackgroundPreview(null); }} destroyTooltipOnHide
                  content={<BackgroundForm initial={data.background.settings} jobId={data.job_id} onPreview={setBackgroundPreview} onAccept={settings => {
                    setBackgroundOpen(false);
                    setBackgroundPreview(null);
                    setShowBackground(true);
                    quick.rankWithBackground(settings);
                  }} />}>
                  <Button size="small">Background…</Button>
                </Popover> : null}
                <Tooltip title={selected.length ? 'Measured minus the selected phases (the residual net pattern)' : 'Select phases first'}>
                  <label><Switch size="small" checked={showResidual && Boolean(left)} disabled={!left || Boolean(withBackground)} onChange={setShowResidual} aria-label="Show the residual" />Residual</label>
                </Tooltip>
                {zoom.range ? <Button size="small" type="link" onClick={() => setZoom(previous => ({ range: null, revision: previous.revision + 1 }))}>Whole pattern</Button> : null}
              </span>
            </div>
            {/* While the base points are edited their list sits beside the plot, so a drag never moves the plot. */}
            {data.profile ? <div className={points ? 'dara-plot-with-side' : undefined}><div className="dara-plot-main">
            <Plot data={traces} useResizeHandler xBounds={bounds} onClick={onPlotClick} anchorEdit={pointsEdit}
              onDoubleClick={() => setZoom(previous => ({ range: null, revision: previous.revision + 1 }))}
              className="dara-qr-plot-area" style={{ width: '100%' }}
              layout={{
                autosize: true, uirevision: `${state.key}-${zoom.revision}`, showlegend: false, hovermode: 'closest',
                margin: { l: 56, r: 12, t: 8, b: 40 }, font: { size: 12 },
                xaxis: { title: { text: '2θ (°)' }, ...(zoom.range ? { range: [...zoom.range], autorange: false } : {}) },
                yaxis: { title: { text: withBackground ? 'Intensity' : showResidual && left ? 'Residual (net)' : 'Net intensity' }, rangemode: 'tozero',
                  ...(zoom.range && zoom.y ? { range: [...zoom.y], autorange: false } : {}) },
              }}
              config={{ displaylogo: false, responsive: true }} /></div>
            {pointsEdit ? <BackgroundCurvePanel side anchors={points.anchors} selected={points.selected}
              onChange={pointsEdit.onChange} onSelect={pointsEdit.onSelect} onRemove={pointsEdit.onRemove} error={pointsError}
              warnings={[...pointWarnings, ...(shownCurve?.error ? [`Curve preview unavailable: ${shownCurve.error}`] : [])]}
              help="Drag a point · click the curve to add one · Alt+click a point or use the list to remove one. The points change the search-match only; a Rietveld refinement fits its own background."
              submitText="Accept and rank again" resetText="Back to automatic" canReset={Boolean(bgView.settings?.anchors?.length)}
              resetTitle={bgView.settings?.anchors?.length ? 'Rank again with the automatic background' : 'This ranking already uses the automatic background'}
              onSubmit={() => rankWithPoints(points.anchors)} onReset={() => rankWithPoints([])} onCancel={() => setPoints(null)} /> : null}
            </div> : null}
          </section>
          <div className="dara-qr-toolbar">
            <Tooltip title="Include structures computed only (Materials Project), not measured">
              <label><Switch size="small" checked={quick.theoretical} onChange={quick.toggleTheoretical} />Theoretical structures</label>
            </Tooltip>
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
          </div> : !data.profile ? <div className="dara-empty">
            <h3>This pattern cannot be ranked</h3>
            <p>Check the file and the 2θ range in the setup: the search-match needs a measured pattern with peaks.</p>
          </div> : <div className="dara-empty">
            <h3>No candidate could be ranked</h3>
            <p>{caveats.length ? 'See the notes above.' : 'No reference structure matched the elements in this 2θ range.'}
              {' '}Check the elements and databases in the setup{quick.theoretical ? '.' : ', or include theoretical structures.'}</p>
          </div>}
        </div>
        {rows.length ? <aside className="dara-qr-panel dara-qr-selected" aria-label="Selected phases">
          <h2>Selected phases <Tooltip title={`At most ${MAX_REFINE} phases`}><span>{`${selected.length} / ${MAX_REFINE}`}</span></Tooltip></h2>
          {chosen.length ? <ul>
            <li className="dara-qr-list-head"><span /><span>Phase</span><Tooltip title={HELP.explains}><span className="dara-num">Explains</span></Tooltip><span /></li>
            {chosen.map(row => [<li key={row.key}>
            <i className="dara-qr-swatch" style={{ background: colors[row.key] }} aria-hidden="true" />
            <div className="dara-qr-phase">
              <div><ChemicalFormula formula={row.formula} /> <span className="meta"><SpaceGroup row={row} /></span></div>
              <div className="meta">{row.sourceLabel}</div>
            </div>
            <span className="dara-num" aria-label={`Explains ${row.scoreText}`}>{row.scoreText}</span>
            <Button type="text" size="small" icon={<CloseOutlined />} aria-label={`Remove ${row.formula}`}
              onClick={() => setSelected(selected.filter(key => key !== row.key))} />
          </li>, groups[row.key] ? <li key={`${row.key}-alt`} className="dara-qr-alt">
            <Tooltip title="The same strong peaks as the phase above: peak matching cannot tell them apart">
              <span>{'Alternatives: '}</span></Tooltip>
            {groups[row.key].map((other, index) => <React.Fragment key={other.key}>{index ? ', ' : ''}
              <ChemicalFormula formula={other.formula} />{` ${other.sourceLabel}`}</React.Fragment>)}
            <div><Tooltip title="Known-phase Rietveld refinement of the selected phases with these alternatives (minutes): the fit tells them apart">
              <Button size="small" loading={quick.refining} disabled={quick.identifying || selected.length + groups[row.key].length > MAX_REFINE}
                onClick={() => quick.refine(onRefine, groups[row.key].map(other => other.key))}>Tell apart with Rietveld</Button>
            </Tooltip></div>
          </li> : null])}</ul>
            : <p className="dara-qr-empty">No phase selected: tick candidates in the table.</p>}
          {weakLeft.length ? <p className="dara-qr-weak">
            {'Weak, not selected: '}
            {/* One click adds the one phase it names. */}
            {weakLeft.map((item, index) => <React.Fragment key={item.key}>{index ? ' ' : ''}
              <Button type="link" size="small" icon={<PlusOutlined />} disabled={selected.length >= MAX_REFINE}
                aria-label={`Add ${item.formula}`} onClick={() => setSelected([...selected, item.key])}><ChemicalFormula formula={item.formula} /></Button>
            </React.Fragment>)}
          </p> : null}
          {chosen.length ? <ResidualPanel residual={residual} rows={rows} selected={selected} setSelected={setSelected} /> : null}
          {over ? <p className="dara-qr-limit">{`At most ${MAX_REFINE} phases: remove ${selected.length - MAX_REFINE}.`}</p> : null}
          {data.combination?.length ? <div className="dara-qr-suggest">
            <p>Suggested: <Suggestion combination={data.combination} /></p>
            <Button size="small" onClick={() => setSelected(suggestion)} disabled={usingSuggestion}>Use suggestion</Button>
          </div> : null}
        </aside> : null}
    </div>;
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
    {mismatch?.recorded ? <Alert className="dara-qr-mismatch" type="warning" showIcon role="alert"
      message={`The file was recorded with ${mismatch.recorded} radiation; this search-match uses ${mismatch.chosen}.`}
      description="Peak positions scale with the wavelength, so the candidates below are matched at the wrong angles."
      action={onUseRecorded && /^[A-Z][a-z]?$/.test(mismatch.recorded)
        ? <Button type="primary" onClick={() => onUseRecorded(mismatch.recorded)}>{`Use ${mismatch.recorded} and rank again`}</Button> : null} /> : null}
    {alert}
    {body}
  </Workspace>;
}
