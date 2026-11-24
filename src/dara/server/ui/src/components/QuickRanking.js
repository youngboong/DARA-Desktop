import React, { useEffect, useRef, useState } from 'react';
import Loadable from 'react-loadable';
import styled from 'styled-components';
import { Alert, Button, message, Popover, Progress, Radio, Slider, Spin, Switch, Table, Tag, Tooltip } from 'antd';
import { ArrowLeftOutlined, CloseOutlined, DownOutlined, PlusOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons';
import ChemicalFormula from './ChemicalFormula';
import { progressPercent } from './structureLibrary.mjs';
import {
  MAX_REFINE, SORTERS, assignColors, backgroundLabel, backgroundSettings as automaticBackground, backgroundSettings, backgroundTraces, combinationText, taskRankingBody, defaultSelection, nextMatches, overlayTraces, pollDelay, poolText,
  progressText, rankingNotes, rankingRows, refineSelection, requestKey, rowKeyAction, sourceInfo, suggestedKeys, toggleKey,
  mismatchView, poolSources, startingPoints, unexplainedMarkers, unexplainedStatus, weakNotSelected, windowRange, withPoints, withResidualProfile, zoomWindow,
  alternativeGroups, alternativeOf, alternativeRecords, unexplainedRows, peakTitle, peakCandidateRows, withPreviewRow, zoomAfterRelayout,
} from './quickRanking.mjs';
import { viewChangeFromRelayout } from './plotNavigation.mjs';
import { addAnchor, anchorError, curveWarnings, pchip, removeAnchor } from './backgroundAnchors.mjs';
import { BackgroundCurvePanel } from './BackgroundEditor';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <div className="dara-qr-plot-area" /> });
const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const DOUBLE_CLICK_MS = 300; // Plotly's default doubleClickDelay

// A cancelled job stops on the server; older servers have no DELETE, so its errors are ignored.
// Each job is cancelled once (a Cancel and the poll loop noticing it would send two DELETEs).
const cancelled = new Set();
const cancelJob = (id, keepalive = false) => {
  if (!id || cancelled.has(id)) return;
  cancelled.add(id);
  fetch(`${URL}/quick-ranking/${id}`, { method: 'DELETE', keepalive }).catch(() => {});
};
// The pattern a request ranks: the uploaded file, or the stored pattern of a task.
const patternOf = body => {
  const file = body.get('pattern_file');
  return file && typeof file === 'object' ? `${file.name}|${file.size}|${file.lastModified}` : `task:${body.get('source_task_id')}`;
};

/**
 * State of the quick ranking (search-match) of the submission page; it lives with the page, so the
 * last ranking survives "Back to setup". `buildRequest()` returns the multipart body (pattern file,
 * elements, formulas, wavelength, range) or throws an Error with a user message; a body with
 * `source_task_id` and no file ranks the stored pattern of that task (a rerun).
 * Opening the workspace pushes a history entry, so the browser's Back returns to the setup.
 */
export function useQuickRanking(buildRequest) {
  const [shown, setShown] = useState(false);
  const shownRef = useRef(shown);
  shownRef.current = shown;
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
  const backgroundFor = useRef(null); // the pattern they were set on: another pattern ranks with the automatic background
  const preselect = useRef(null); // reference ids to select when the next ranking finishes (a reopened identification)

  const setSelected = keys => {
    setSelectedKeys(keys);
    setColors(previous => assignColors(previous, keys));
  };
  const request = includeTheoretical => {
    const body = buildRequest();
    body.set('include_theoretical', String(includeTheoretical));
    const settings = backgroundOf(body);
    if (settings) body.set('background', JSON.stringify(settings));
    return body;
  };
  // The accepted background settings when they were set on the pattern of `body`, else none.
  const backgroundOf = body => (background.current && backgroundFor.current === patternOf(body) ? background.current : null);
  const setBackground = settings => {
    background.current = settings;
    try { backgroundFor.current = patternOf(buildRequest()); } catch (error) { backgroundFor.current = null; }
  };
  let currentKey = null;
  try { currentKey = requestKey(request(theoretical).entries()); } catch (error) { /* no pattern yet */ }

  /**
   * Ranks with the setup's inputs. `keepSelection` (Run again, Theoretical structures, a new
   * background) ticks the selected structures again where the new ranking has them; otherwise the
   * new ranking starts from its suggestion (a ticked row of an earlier ranking is not refined with it).
   */
  const start = async (includeTheoretical = theoretical, { keepSelection = false } = {}) => {
    let body;
    try {
      body = request(includeTheoretical);
    } catch (error) {
      setState({ status: 'error', error: String(error.message || error) });
      return;
    }
    const sequence = ++run.current;
    cancelJob(job.current);
    job.current = null;
    if (keepSelection && selected.length && !preselect.current) preselect.current = [...selected];
    setSelected([]);
    setFocused(null);
    const key = requestKey(body.entries());
    setState({ status: 'pending', key, progress: { phase: 'listing' } });
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
      const asked = preselect.current || [];
      const wanted = asked.filter(source => (data.ranking || []).some(row => row.source === source));
      preselect.current = null;
      if (asked.length > wanted.length) {
        const missing = asked.length - wanted.length;
        message.warning(`${missing} of the ${asked.length} selected phases ${missing === 1 ? 'is' : 'are'} not in this ranking${
          wanted.length ? '' : ': the suggestion is selected instead'}.`, 8);
      }
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

  useEffect(() => {
    // A reload or a closed tab stops a running ranking too: it would hold the server's one ranking slot.
    const stop = () => cancelJob(job.current, true);
    window.addEventListener('pagehide', stop);
    return () => { window.removeEventListener('pagehide', stop); run.current += 1; cancelJob(job.current); };
  }, []);

  const closeView = restoreScroll => {
    if (status.current === 'pending') cancel();
    setShown(false);
    if (restoreScroll) requestAnimationFrame(() => window.scrollTo(0, scroll.current));
  };
  // Browser Back from the workspace: back to the setup, as "Back to setup"; Forward shows it again.
  useEffect(() => {
    const onPop = event => {
      if (ownBack.current) { ownBack.current = false; return; }
      if (!event.state?.daraQuick) closeView(true);
      else if (!shownRef.current) { scroll.current = window.scrollY; setShown(true); window.scrollTo(0, 0); }
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
        setBackground(stored.background || null);
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
      start(checked, { keepSelection: true });
    },
    /** Ranks again with HighScore-style background settings ({method, granularity, bending_factor, anchors}). */
    rankWithBackground(settings) {
      setBackground(settings);
      start(theoretical, { keepSelection: true });
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
        // In ranking order, as a refinement takes them; the theoretical switch is stored so a reopened
        // identification ranks the same pool (an MP-only phase stays selectable).
        let settings = null;
        try { settings = backgroundOf(buildRequest()); } catch (error) { /* no pattern: none */ }
        await onIdentify({ referenceIds: refineSelection(state.data, selected).referenceIds, background: settings,
          alternatives: alternativeRecords(state.data, selected), theoretical });
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
 * The one Background control of the search-match (HighScore's "Determine Background"; the fit view's
 * Background uses the same words): show the curve on the plot, and the method: Automatic, Sonneveld &
 * Visser with HighScore's granularity and bending factor, or Manual points dragged on the plot. While
 * the sliders move, the curve is drawn on the pattern (`onPreview(view)`, debounced, from the finished
 * job's pattern); "Accept and rank again" ranks with it; Manual points opens the point editor (`onManual`).
 */
function BackgroundForm({ initial, jobId, open, onAccept, onPreview, shown, onShow, onManual, onClose }) {
  const points = initial?.anchors?.length || 0;
  // Kept while the popover is closed (it is not destroyed): sliders moved and not accepted come back as left.
  const [settings, setSettings] = useState(() => ({ ...backgroundSettings(initial), ...(points ? { method: 'manual' } : {}) }));
  const [previewError, setPreviewError] = useState('');
  const set = (key, value) => {
    setSettings(previous => ({ ...previous, [key]: value }));
    if (key !== 'method' && onShow) onShow(true); // a moved slider shows its curve
  };
  useEffect(() => {
    if (!open) return undefined;
    const escape = event => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [open, onClose]);
  const manual = settings.method === 'manual';
  // One automatic method (Sonneveld & Visser); default values are sent as "auto".
  const tuned = !manual && (settings.granularity !== 20 || settings.bending_factor !== 0);
  const chosen = tuned ? { ...settings, method: 'sonneveld_visser' } : { method: 'auto' };
  const chosenKey = JSON.stringify(chosen);
  useEffect(() => {
    if (!jobId || manual || !open) return undefined;
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
  }, [jobId, chosenKey, manual, open]);
  return <div className="dara-qr-bg-form">
    {onShow ? <label className="dara-qr-bg-show"><Switch size="small" checked={shown} onChange={onShow} />Show on plot</label> : null}
    <Radio.Group size="small" value={manual ? 'manual' : 'auto'} optionType="button" onChange={event => set('method', event.target.value)} aria-label="Background method"
      options={[{ value: 'auto', label: 'Automatic' }, ...(onManual ? [{ value: 'manual', label: 'Manual points' }] : [])]} />
    {!manual ? <>
      <label htmlFor="dara-qr-granularity">Granularity <span className="dara-num">{settings.granularity}</span></label>
      <Slider id="dara-qr-granularity" min={2} max={200} value={settings.granularity}
        onChange={value => set('granularity', value)} tooltip={{ formatter: null }} />
      <label htmlFor="dara-qr-bending">Bending factor <span className="dara-num">{settings.bending_factor}</span></label>
      <Slider id="dara-qr-bending" min={0} max={50} step={0.5} value={settings.bending_factor}
        onChange={value => set('bending_factor', value)} tooltip={{ formatter: null }} />
      {tuned ? <Button type="link" size="small" onClick={() => setSettings(previous => ({ ...previous, granularity: 20, bending_factor: 0 }))}>Default (20, 0)</Button> : null}
    </> : null}
    {previewError && !manual ? <p className="dara-qr-bg-error" role="alert">{`Curve preview unavailable: ${previewError}`}</p> : null}
    {points && !manual ? <p className="dara-qr-bg-note">{`Replaces the ${points} manual points.`}</p> : null}
    <p className="dara-qr-bg-note">Search-match only; a Rietveld refinement fits its own background.</p>
    {manual ? <Button type="primary" size="small" onClick={onManual}>Edit points on the plot</Button>
      : <Button type="primary" size="small" onClick={() => onAccept(chosen)}>Accept and rank again</Button>}
  </div>;
}

// Column definitions, shared by the candidate table and the side lists (one term per thing).
const HELP = {
  explains: 'Share of the measured net intensity this structure explains on its own (lattice-penalised). Not an amount: refine for wt%.',
  rest: 'Share of the intensity the selected phases leave unexplained that this structure explains',
  peaks: 'Strong calculated peaks found in the pattern / strong peaks in range',
  peaksRest: 'Strong calculated peaks found in what the selected phases leave / strong peaks in range (peaks they cover do not count)',
  lattice: 'Lattice change that fits best (d scale − 1)',
  line: 'Relative intensity of this structure\'s line at the peak (% of its strongest line)',
  height: 'Net intensity left at the peak after the selected phases (counts)',
  flags: 'picked: in the suggestion · weak: small share of the pattern · polymorph: other structures of the formula ranked · theoretical: computed only (MP) · variant of: same structure as a higher row',
};

/** "Ia-3 (206)" with the symbol set as a space group; a bare number when there is no symbol. */
const SpaceGroup = ({ row }) => (row.spacegroup_symbol
  ? <><ChemicalFormula formula={row.spacegroup_symbol} />{` (${row.spacegroup})`}</> : String(row.spacegroup ?? '—'));

/**
 * The pool's candidates with a strong line at one unexplained peak (the d-spacing lookup of a classical
 * search-match), inline under the peak's row so the plot and the previewed sticks stay in view.
 * Hovering or focusing a row previews its sticks; "Add" selects it. Esc, a click outside or the close
 * button dismisses it with nothing changed.
 */
function PeakCandidates({ peak, lookup, rows, selected, onAdd, onPreview, onClose }) {
  const panel = useRef(null);
  useEffect(() => {
    const outside = event => {
      // a peak row toggles the panel itself; a marker click on the plot selects another peak
      if (panel.current && !panel.current.contains(event.target) && !event.target.closest?.('.dara-qr-peak-row')) onClose(false);
    };
    const escape = event => { if (event.key === 'Escape') onClose(true); };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape); };
  }, [onClose]);
  // Bring the panel into view: the side column scrolls first, the page only as far as needed.
  useEffect(() => { panel.current?.scrollIntoView({ block: 'nearest' }); }, [lookup]);
  const found = lookup?.data ? peakCandidateRows(lookup.data, rows) : [];
  const full = selected.length >= MAX_REFINE;
  let content;
  if (!lookup) content = <p className="dara-qr-empty is-stale">Looking up candidates…</p>;
  else if (lookup.error) content = <p className="dara-qr-empty" role="alert">{lookup.error}</p>;
  else if (!found.length) {
    content = <><p className="dara-qr-peak-none">No candidate in the pool has a line here</p>
      <p className="dara-qr-empty">It may be a phase outside the entered elements or missing from COD / MP, a Kβ or tungsten line, or the sample holder.</p></>;
  } else {
    content = <ul onMouseLeave={() => onPreview(null)}>
      <li className="dara-qr-list-head"><span>Phase</span>
        <Tooltip title={HELP.line}><span className="dara-num">Line</span></Tooltip>
        <Tooltip title={HELP.peaksRest}><span className="dara-num">Peaks</span></Tooltip>
        <Tooltip title={HELP.rest}><span className="dara-num">Of rest</span></Tooltip><span /></li>
      {found.map(row => <li key={row.key} onMouseEnter={() => onPreview(row)} onFocus={() => onPreview(row)}>
        <div className="dara-qr-phase">
          <div><ChemicalFormula formula={row.formula} /></div>
          <div className="meta"><span className="dara-nowrap"><SpaceGroup row={row} /></span>{' · '}<span className="dara-nowrap">{row.sourceLabel}</span></div>
        </div>
        <span className="dara-num" aria-label={`Line ${row.lineText} at ${row.line?.two_theta}°`}>{row.lineText}</span>
        <span className="dara-num" aria-label={`Peaks ${row.peaksText}`}>{row.peaksText}</span>
        <span className="dara-num" aria-label={`Explains ${row.scoreText} of the rest`}>{row.scoreText}</span>
        <Tooltip title={!row.inRanking ? 'Not in the ranked list' : full ? `At most ${MAX_REFINE} phases` : null}>
          <Button size="small" icon={<PlusOutlined />} disabled={!row.inRanking || full}
            aria-label={`Add ${row.formula} ${row.sourceLabel}`} onClick={() => onAdd(row.key)}>Add</Button>
        </Tooltip>
      </li>)}
    </ul>;
  }
  return <div className="dara-qr-peak-panel" ref={panel} id={`dara-qr-peak-${peak.key}`} role="region" aria-label={peakTitle(peak)}
    aria-busy={!lookup || undefined}>
    <div className="dara-qr-peak-title"><b>{peakTitle(peak)}</b>
      <Button type="text" size="small" icon={<CloseOutlined />} aria-label="Close" onClick={() => onClose(true)} /></div>
    {content}
  </div>;
}

/**
 * What the selected phases leave: the number of unexplained peaks (the selection's status), the
 * peaks themselves (a click looks up the candidates with a line there) and the best matches for the
 * remaining peaks, each with "Add".
 */
function ResidualPanel({ residual, rows, selected, setSelected, peak, onPeak, peakLookup }) {
  if (!residual) return <section className="dara-qr-left" aria-label="What is left"><p className="dara-qr-left-status is-stale">Checking what is left…</p></section>;
  if (residual.error) return <section className="dara-qr-left" aria-label="What is left"><p className="dara-qr-empty">{residual.error}</p></section>;
  const left = residual.data;
  const peaks = unexplainedRows(left);
  const open = peaks.length > 0;
  const next = nextMatches(left, rows);
  return <section className="dara-qr-left" aria-label="What is left" aria-busy={residual.stale || undefined}>
    <p className={`dara-qr-left-status${open ? ' is-open' : ''}${residual.stale ? ' is-stale' : ''}`} role="status">{unexplainedStatus(left)}</p>
    {open ? <ul className="dara-qr-peaks">
      <li className="dara-qr-list-head"><span className="dara-num">2θ (°)</span><span className="dara-num">d (Å)</span>
        <Tooltip title={HELP.height}><span className="dara-num">Height</span></Tooltip></li>
      {peaks.map(item => {
        const shown = peak?.key === item.key;
        return <li key={item.key}>
          <button type="button" className="dara-qr-peak-row" aria-expanded={shown} aria-controls={shown ? `dara-qr-peak-${item.key}` : undefined}
            data-peak={item.key} title="Candidates with a line at this peak" onClick={() => onPeak(shown ? null : item)}>
            <span className="dara-num">{item.x.toFixed(2)}</span>
            <span className="dara-num">{Number.isFinite(item.d) ? item.d.toFixed(3) : '—'}</span>
            <span className="dara-num">{Math.round(item.height).toLocaleString('en-US')}</span>
          </button>
          {shown ? <PeakCandidates peak={item} lookup={peakLookup.state} rows={rows} selected={selected}
            onAdd={peakLookup.add} onPreview={peakLookup.preview} onClose={peakLookup.close} /> : null}
        </li>;
      })}
    </ul> : null}
    {open && next.length ? <>
      <ul className="dara-qr-next">
        <li className="dara-qr-list-head"><span>Next best match</span><Tooltip title={HELP.peaksRest}><span className="dara-num">Peaks</span></Tooltip>
          <Tooltip title={HELP.rest}><span className="dara-num">Of rest</span></Tooltip><span /></li>
        {next.map(row => <li key={row.key}>
        <div className="dara-qr-phase">
          <div><ChemicalFormula formula={row.formula} /> <span className="meta"><SpaceGroup row={row} /></span></div>
          <div className="meta">{row.sourceLabel}</div>
        </div>
        <span className="dara-num" aria-label={`Peaks ${row.peaksText}`}>{row.peaksText}</span>
        <span className="dara-num" aria-label={`Explains ${row.scoreText} of the rest`}>{row.scoreText}</span>
        <Tooltip title={row.inRanking ? null : 'Not in the ranked list'}>
          <Button size="small" icon={<PlusOutlined />} disabled={!row.inRanking || selected.includes(row.key) || selected.length >= MAX_REFINE}
            aria-label={`Add ${row.formula} ${row.sourceLabel}`} onClick={() => setSelected([...selected, row.key])}>Add</Button>
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
        {count ? `${count} phase${count === 1 ? '' : 's'} ranked · ${quick.selected.length} selected` : 'no candidate ranked'}</span></div>
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
  /* Sticky beside the table, and scrollable itself so an open peak lookup is never cut off. */
  .dara-qr-body > aside { position: sticky; top: 16px; max-height: calc(100vh - 32px); overflow: auto; }
  .dara-nowrap { white-space: nowrap; }
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
  /* Unexplained peaks: one button per peak; its candidates open inline under it. */
  .dara-qr-selected .dara-qr-peaks { margin: 0 0 8px; }
  .dara-qr-selected .dara-qr-peaks > li { display: block; padding: 0; }
  .dara-qr-selected .dara-qr-peaks > li.dara-qr-list-head, .dara-qr-peak-row {
    display: grid; grid-template-columns: 64px 64px minmax(0, 1fr); gap: 0 8px; align-items: center;
  }
  .dara-qr-selected .dara-qr-peaks > li.dara-qr-list-head { padding: 0 8px 2px 0; }
  .dara-qr-peak-row {
    width: 100%; padding: 3px 8px 3px 0; border: 0; border-radius: var(--dara-radius); background: none;
    font: inherit; color: inherit; text-align: left; cursor: pointer;
  }
  .dara-qr-peak-row:hover { background: var(--dara-surface-sunken); }
  .dara-qr-peak-row[aria-expanded="true"] { background: var(--dara-accent-bg); color: var(--dara-accent-hover); font-weight: 600; }
  .dara-qr-peak-row:focus-visible { outline: 2px solid var(--dara-accent); outline-offset: -2px; }
  .dara-qr-peak-panel { margin: 2px 0 6px; padding: 6px 8px 8px; background: var(--dara-surface-sunken); border-radius: var(--dara-radius); }
  .dara-qr-peak-title { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 4px; font-size: var(--dara-font-12); }
  .dara-qr-peak-title b { font-weight: 600; }
  .dara-qr-selected .dara-qr-peak-panel li { grid-template-columns: minmax(0, 1fr) 36px 40px 40px auto; gap: 0 6px; }
  .dara-qr-selected .dara-qr-peak-panel li:hover { background: var(--dara-surface); }
  .dara-qr-peak-panel .dara-qr-empty { font-size: var(--dara-font-12); }
  .dara-qr-peak-none { margin: 0 0 2px; font-weight: 600; }
  .dara-qr-empty.is-stale { opacity: 0.6; }
  .dara-qr-plot-tools { display: inline-flex; align-items: center; gap: 12px; }
  .dara-qr-plot-tools label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .dara-qr-body.single { grid-template-columns: minmax(0, 1fr); }
  @media (max-width: 1079px) {
    .dara-qr-body { grid-template-columns: minmax(0, 1fr); }
    .dara-qr-body > aside { position: static; max-height: none; }
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
  // A new ranking or an empty selection starts from the pattern view again.
  useEffect(() => { setShowResidual(false); }, [state.data?.job_id, selected.length === 0]);
  const [showBackground, setShowBackground] = useState(false); // the measured pattern with its background
  const [backgroundOpen, setBackgroundOpen] = useState(false);
  const [backgroundPreview, setBackgroundPreview] = useState(null); // live curve while the Background popover is open
  // The plot view: `range` the 2θ window (null: whole pattern), `y` null (autorange), 'fit' (follows the
  // window: a click on an unexplained-peak marker zooms there) or a zoom box's [lo, hi] (zoomAfterRelayout).
  const [zoom, setZoom] = useState({ range: null, y: null, revision: 0 });
  const pendingClick = useRef(null); // a marker click waiting out the double-click delay
  useEffect(() => () => clearTimeout(pendingClick.current), []);
  const [points, setPoints] = useState(null); // {anchors, selected} while the base points are edited
  const [pointsPreview, setPointsPreview] = useState(null); // {key, view} | {key, error}: the server's curve through them
  const [peak, setPeak] = useState(null); // the unexplained peak whose candidates are shown ({x, d, height, key})
  const [peakLookup, setPeakLookup] = useState(null); // {key, data} | {key, error} of `peak`
  const [peakPreview, setPeakPreview] = useState(null); // a peak candidate row previewed on the plot

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
  const closeBackground = React.useCallback(() => { setBackgroundOpen(false); setBackgroundPreview(null); }, []);
  // "Back to automatic": the automatic curve with the default settings (no base points, no tuned granularity).
  const automatic = backgroundLabel(bgView?.settings) === backgroundLabel(null);
  const backToAutomatic = () => {
    setPoints(null);
    setShowBackground(true);
    quick.rankWithBackground(automaticBackground(null));
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
    if (!next?.classList.contains('ant-table-row')) return;
    // Only the table's list scrolls to the row; the page stays where it is.
    next.focus({ preventScroll: true });
    next.scrollIntoView({ block: 'nearest' });
  };

  const columns = [
    { title: 'Rank', dataIndex: 'rank', width: 64, className: 'dara-num', sorter: SORTERS.rank, defaultSortOrder: 'ascend',
      render: (rank, row) => (altOf[row.key] ? <Tooltip title={`Alternative of ${altOf[row.key].map(other => `${other.formula} (rank ${other.rank})`).join(', ')}: the same strong peaks, so peak matching cannot tell them apart`}>
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
      render: tags => tags.map(tag => <Tag key={tag.label} color={tag.color} style={{ marginBottom: 2 }}>
        {tag.label.startsWith('variant of ') ? <>variant of <ChemicalFormula formula={tag.label.slice(11)} /></> : tag.label}</Tag>),
    },
  ];

  const canRefine = selected.length > 0 && !over && !!data;
  const left = residual?.data || null;
  const bounds = data?.profile ? [data.profile.two_theta?.[0], data.profile.two_theta?.slice(-1)[0]] : null;
  const shownData = showResidual && left ? withResidualProfile(data, left) : data;

  // The candidates with a line at one unexplained peak: looked up when a peak is chosen (a list row or
  // a marker), from the finished job's pool scored for this selection. A new selection closes it.
  const jobId = data?.job_id;
  const selectionKey = selected.join('|');
  useEffect(() => { setPeak(null); setPeakPreview(null); }, [jobId, selectionKey]);
  useEffect(() => {
    if (!peak || !jobId) { setPeakLookup(null); return undefined; }
    let current = true;
    setPeakLookup(null);
    (async () => {
      try {
        const response = await fetch(`${URL}/quick-ranking/${jobId}/peak-candidates`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ two_theta: peak.x, selected }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(response.status === 404 ? 'This search-match has expired: run it again.' : body.detail || `HTTP ${response.status}`);
        if (current) setPeakLookup({ key: peak.key, data: body });
      } catch (error) {
        if (current) setPeakLookup({ key: peak.key, error: String(error.message || error) });
      }
    })();
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peak?.key, jobId]);
  // The intensity axis follows the window ('fit' zoom), so a small peak fills the plot.
  const zoomTo = x => setZoom(previous => ({ range: zoomWindow(x, bounds), y: 'fit', revision: previous.revision }));
  // Back to the whole pattern on both axes; the chosen peak (marker and candidates) goes with the zoom.
  const wholePattern = () => {
    setPeak(null);
    setPeakPreview(null);
    setZoom(previous => ({ range: null, y: null, revision: previous.revision + 1 }));
  };
  // Plotly's own changes (wheel, pan, zoom box, axis reset) keep the zoom state in step with the plot.
  const onRelayout = event => {
    const next = zoomAfterRelayout(zoom, viewChangeFromRelayout(event || {}, { full: bounds }), bounds);
    if (!next) return;
    if (next.whole) wholePattern();
    else setZoom(previous => ({ range: next.range, y: next.y, revision: previous.revision }));
  };
  const choosePeak = item => {
    setPeakPreview(null);
    setPeak(item);
    if (item) zoomTo(item.x);
  };
  const lookup = {
    state: peakLookup && peak && peakLookup.key === peak.key ? peakLookup : null,
    preview: setPeakPreview,
    add: key => { setPeak(null); setPeakPreview(null); setSelected([...selected, key]); },
    close: React.useCallback(restoreFocus => {
      const key = peak?.key;
      setPeak(null);
      setPeakPreview(null);
      if (restoreFocus && key) requestAnimationFrame(() => document.querySelector(`.dara-qr-peak-row[data-peak="${key}"]`)?.focus());
    }, [peak?.key]),
  };
  // The background view (when the server sends the curve) shows the measured pattern with the
  // background the ranking removed; the sticks belong to the net scale and stay in the other views.
  const withBackground = pointTraces || (!showBackground ? null : backgroundPreview ? backgroundTraces(backgroundPreview) : backgroundTraces(data?.background));
  // The residual view shows what is left: the selected phases' sticks would cover it; the preview stays.
  // A peak candidate under the mouse previews its sticks (fitted to what is left) in place of the table's preview.
  // The curve the plot leads with: the measured pattern in the background view, else the net (or residual) pattern.
  const backgroundShown = !pointTraces && withBackground ? (backgroundPreview || bgView) : null;
  const curve = backgroundShown ? { two_theta: backgroundShown.two_theta, net: backgroundShown.observed } : shownData?.profile;
  // The unexplained peaks are marked in every view but the base-point editor (a click there adds a point).
  const markers = !pointTraces && left?.unexplained_peaks?.length ? unexplainedMarkers(left.unexplained_peaks, curve, peak?.x) : null;
  const traces = withBackground || !data ? [...(withBackground || []), ...(markers ? [markers] : [])]
    : [...overlayTraces(withPreviewRow(shownData, peakPreview), showResidual && left ? [] : selected, colors, peakPreview?.key ?? focused),
      ...(markers ? [markers] : [])];
  // 'fit' zoom: the shown curve's range in the window, with the unexplained-peak markers above the data in view.
  const fitY = range => {
    const y = windowRange(curve, range);
    const mark = Math.max(0, ...(markers?.y || []).filter((_, i) => markers.x[i] >= range[0] && markers.x[i] <= range[1]));
    return y && mark ? [0, Math.max(y[1], mark * 1.06)] : y;
  };
  // The residual view is scaled by what is left: a previewed candidate's sticks (fitted to the whole
  // pattern) would otherwise flatten it.
  const residualY = showResidual && left && !withBackground && bounds ? fitY(bounds) : null;
  const shownY = zoom.y === 'fit' ? (zoom.range ? fitY(zoom.range) : null) : zoom.y ?? residualY;
  // A click on an unexplained-peak marker zooms there and lists its candidates. The hovered point can be
  // the pattern's (closest in 2-D), so a click within 0.3° of a marker, by the 2θ under the mouse, counts as well.
  const onPlotClick = event => {
    if (!markers) return;
    const point = event?.points?.find(item => item.data?.meta === 'unexplained');
    const near = unexplainedRows(left)
      .filter(item => Number.isFinite(event?.cursorX) && Math.abs(item.x - event.cursorX) <= 0.3)
      .sort((a, b) => Math.abs(a.x - event.cursorX) - Math.abs(b.x - event.cursorX))[0];
    const item = point ? unexplainedRows(left).find(row => Math.abs(row.x - Number(point.x)) < 1e-6) : near;
    if (!item) return;
    // Applied after Plotly's double-click delay: the first click of a double-click (back to the whole
    // pattern) must not zoom in, and redrawing the plot between its clicks would hide the double-click.
    clearTimeout(pendingClick.current);
    pendingClick.current = setTimeout(() => {
      choosePeak(item);
      requestAnimationFrame(() => document.querySelector(`.dara-qr-peak-row[data-peak="${item.key}"]`)?.scrollIntoView({ block: 'nearest' }));
    }, DOUBLE_CLICK_MS);
  };
  const onDoubleClick = () => {
    clearTimeout(pendingClick.current);
    wholePattern();
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
              <span>{peakPreview || focusedRow
                ? <><span className="dara-qr-preview-mark" />Preview <b><ChemicalFormula formula={(peakPreview || focusedRow).formula} /></b>
                  {` · ${(peakPreview || focusedRow).sourceLabel}`}{!peakPreview && selected.includes(focusedRow.key) ? ' (selected)' : ''}</>
                : null}</span>
              <span className="dara-qr-plot-tools">
                {/* What the plot shows: the net pattern or what the selected phases leave (one choice). */}
                <Tooltip title={withBackground ? 'The background is shown: turn it off under Background' : null}>
                  <Radio.Group size="small" optionType="button" aria-label="Plot view" disabled={Boolean(withBackground)}
                    value={showResidual && left ? 'residual' : 'net'} onChange={event => setShowResidual(event.target.value === 'residual')}
                    options={[{ value: 'net', label: 'Pattern' },
                      { value: 'residual', disabled: !left, label: <Tooltip title={left ? 'Measured minus the selected phases' : 'Select phases first'}>Residual</Tooltip> }]} />
                </Tooltip>
                {/* One Background control: show the curve, the method (automatic, granularity / bending, manual points), rank again. */}
                {data.background?.settings ? <Popover trigger="click" placement="bottomRight" title="Background" open={backgroundOpen && !points}
                  onOpenChange={value => { setBackgroundOpen(value); if (!value) setBackgroundPreview(null); }}
                  content={<BackgroundForm key={data.job_id} initial={data.background.settings} jobId={data.job_id} onPreview={setBackgroundPreview}
                    open={backgroundOpen && !points} onClose={closeBackground}
                    shown={showBackground} onShow={backgroundTraces(data.background) ? setShowBackground : null}
                    onManual={backgroundTraces(data.background) ? () => { setBackgroundOpen(false); setBackgroundPreview(null); startPoints(); } : null}
                    onAccept={settings => {
                      setBackgroundOpen(false);
                      setBackgroundPreview(null);
                      setShowBackground(true);
                      quick.rankWithBackground(settings);
                    }} />}>
                  <Button size="small" disabled={Boolean(points)} aria-haspopup="dialog">
                    {points ? 'Background: editing points' : backgroundLabel(data.background.settings)} <DownOutlined /></Button>
                </Popover> : null}
                {zoom.range || Array.isArray(zoom.y) ? <Button size="small" type="link" onClick={wholePattern}>Whole pattern</Button> : null}
              </span>
            </div>
            {/* While the base points are edited their list sits beside the plot, so a drag never moves the plot. */}
            {data.profile ? <div className={points ? 'dara-plot-with-side' : undefined}><div className="dara-plot-main">
            <Plot data={traces} useResizeHandler xBounds={bounds} onClick={onPlotClick} anchorEdit={pointsEdit}
              onDoubleClick={onDoubleClick} onRelayout={onRelayout}
              className="dara-qr-plot-area" style={{ width: '100%' }}
              layout={{
                autosize: true, uirevision: `${state.key}-${zoom.revision}`, showlegend: false, hovermode: 'closest',
                margin: { l: 56, r: 12, t: 8, b: 40 }, font: { size: 12 },
                xaxis: { title: { text: '2θ (°)' }, ...(zoom.range ? { range: [...zoom.range], autorange: false } : {}) },
                yaxis: { title: { text: withBackground ? 'Intensity' : showResidual && left ? 'Residual (net)' : 'Net intensity' }, rangemode: 'tozero',
                  ...(shownY ? { range: [...shownY], autorange: false } : {}) },
              }}
              config={{ displaylogo: false, responsive: true }} /></div>
            {pointsEdit ? <BackgroundCurvePanel side anchors={points.anchors} selected={points.selected}
              onChange={pointsEdit.onChange} onSelect={pointsEdit.onSelect} onRemove={pointsEdit.onRemove} error={pointsError}
              warnings={[...pointWarnings, ...(shownCurve?.error ? [`Curve preview unavailable: ${shownCurve.error}`] : [])]}
              help="Drag a point · click the curve to add one · Alt+click a point or use the list to remove one. Search-match only."
              submitText="Accept and rank again" resetText="Back to automatic" canReset={!automatic}
              resetTitle={!automatic ? 'Rank again with the automatic background (default settings)' : 'This ranking already uses the automatic background'}
              onSubmit={() => rankWithPoints(points.anchors)} onReset={backToAutomatic} onCancel={() => setPoints(null)} /> : null}
            </div> : null}
          </section>
          <div className="dara-qr-toolbar">
            <Tooltip title="Include structures computed only (Materials Project), not measured">
              <label><Switch size="small" checked={quick.theoretical} onChange={quick.toggleTheoretical} />Theoretical structures</label>
            </Tooltip>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => quick.start(undefined, { keepSelection: true })}>Run again</Button>
            <Tooltip title={[poolSources(data), `${rows.length} rows: one per formula and space group`].filter(Boolean).join(' · ')}>
              <span className="dara-qr-pool">{poolText(data)}</span></Tooltip>
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
            <div><Tooltip title={selected.length + groups[row.key].length > MAX_REFINE
              ? `${selected.length} selected + ${groups[row.key].length} alternatives exceed ${MAX_REFINE} phases: remove ${selected.length + groups[row.key].length - MAX_REFINE} selected phase${selected.length + groups[row.key].length - MAX_REFINE === 1 ? '' : 's'} first`
              : 'Known-phase Rietveld refinement of the selected phases with these alternatives (minutes): the fit tells them apart'}>
              <Button size="small" loading={quick.refining} disabled={quick.identifying || selected.length + groups[row.key].length > MAX_REFINE}
                onClick={() => quick.refine(onRefine, groups[row.key].map(other => other.key))}>Tell apart with Rietveld</Button>
            </Tooltip></div>
          </li> : null])}</ul>
            : <p className="dara-qr-empty">No phase selected</p>}
          {weakLeft.length ? <p className="dara-qr-weak">
            {'Weak, not selected: '}
            {/* One click adds the one phase it names. */}
            {weakLeft.map((item, index) => <React.Fragment key={item.key}>{index ? ' ' : ''}
              <Button type="link" size="small" icon={<PlusOutlined />} disabled={selected.length >= MAX_REFINE}
                aria-label={`Add ${item.formula}`} onClick={() => setSelected([...selected, item.key])}><ChemicalFormula formula={item.formula} /></Button>
            </React.Fragment>)}
          </p> : null}
          {chosen.length ? <ResidualPanel residual={residual} rows={rows} selected={selected} setSelected={setSelected}
            peak={peak} onPeak={choosePeak} peakLookup={lookup} /> : null}
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
