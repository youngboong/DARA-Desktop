import React, { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, AutoComplete, Button, Form, Input, InputNumber, message, Modal, Radio, Select, Space, Switch, Table, Tag, Typography, Upload } from 'antd';
import { FileOutlined, MinusCircleOutlined, PlusOutlined, RightOutlined, UploadOutlined } from '@ant-design/icons';
import { CalibrateProfile, DEFAULT_CONTROLS, InstrumentManager, RefinementControlsEditor } from '../components/PracticalControls';
import { detailMessage, qualityAlertItems, qualityStatText, wavelengthMismatch } from '../components/dataQuality.mjs';
import { MpKeyStatus, ReferenceLibraryField, useReferenceLibrary } from '../components/ReferenceLibrary';
import { MAX_SERIES_FILES, seriesPreviewRows } from '../components/seriesTrends.mjs';
import { COMMON_STANDARDS } from '../components/InternalStandardCard';
import { FwhmCurveButton } from '../components/InstrumentFwhmCurve';
import { curveText } from '../components/instrumentCurve.mjs';
import useAdvancedTools from '../components/useAdvancedTools';
import {
  advancedChanges, checkElements, forgetRemembered, limiter, previewShape, readRecentElements, readRemembered, rememberElements,
  runAfterSuccess, unrecognisedTokens, writeRemembered,
} from '../components/submitHelpers.mjs';
import { phaseSetIdFromSearch, phaseSetLabel, withoutPhaseSet } from '../components/knownPhases.mjs';
// explicit .js: on case-insensitive disks webpack would resolve quickRanking.mjs / structureLibrary.mjs first
import QuickRankingWorkspace, { QuickRankingSummary, useQuickRanking } from '../components/QuickRanking.js';
import StructureLibrary from '../components/StructureLibrary.js';
import { setLeaveGuard } from '../components/leaveGuard.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const { confirm } = Modal;
const { Dragger } = Upload;

// Layout: cards with labels above the inputs. From WIDE_LAYOUT px the form splits into a main
// column (sample, chemistry, instrument) and a sticky side column (run card, advanced options);
// below it the cards stack in the order sample, chemistry, instrument, advanced, run.
const PAGE_MAX_WIDTH = 1440;
const SINGLE_COLUMN_MAX_WIDTH = 960;
const WIDE_LAYOUT = 1200;

const Container = styled.div`
  font-size: var(--dara-font-14);
  width: 100%;
  max-width: ${SINGLE_COLUMN_MAX_WIDTH}px;
  margin: 0 auto;
  .dara-submit-layout { display: flex; flex-direction: column; gap: 16px; margin-bottom: 24px; }
  .dara-submit-main, .dara-submit-side { display: contents; }
  .dara-card-sample { order: 1; }
  .dara-card-chemistry { order: 2; }
  .dara-card-instrument { order: 3; }
  .dara-card-advanced { order: 4; }
  .dara-card-run { order: 5; }
  .dara-card {
    min-width: 0;
    background: var(--dara-surface);
    border: 1px solid var(--dara-border);
    border-radius: var(--dara-radius);
    padding: 16px 24px 0;
  }
  .dara-card-title {
    margin: 0 0 16px;
    font-size: var(--dara-font-14);
    font-weight: 600;
    color: var(--dara-text);
  }
  .dara-subhead { font-weight: 500; margin-bottom: 8px; }
  .ant-form-item { margin-bottom: 16px; }
  .ant-form-vertical .ant-form-item-label { padding-bottom: 4px; }
  .ant-form-item-extra { font-size: var(--dara-font-13); }
  /* Notes inside a card fill the card instead of shrinking to their text. */
  .ant-alert, .dara-note-group { width: auto; max-width: none; }
  .dara-card .ant-alert { margin-bottom: 16px; }
  .dara-note-group > .ant-alert { margin-bottom: 4px; padding: 4px 12px; }
  .dara-field-row {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr));
    gap: 0 16px;
  }
  .dara-field-row.wide-cells { grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); }
  .dara-field-row > label { display: block; margin-bottom: 16px; }
  .dara-inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .dara-inline > .grow { flex: 1 1 240px; min-width: 0; }
  .dara-run-summary { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 16px; margin: 0 0 16px; }
  .dara-run-summary dt { color: var(--dara-text-secondary); }
  .dara-run-summary dd { margin: 0; overflow-wrap: anywhere; }
  .dara-run-summary .missing { color: var(--dara-text-hint); }
  .dara-run-hint { margin: 0 0 16px; color: var(--dara-text-hint); font-size: var(--dara-font-13); }
  .dara-run-actions { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; padding-bottom: 16px; }
  .dara-run-actions.single { grid-template-columns: minmax(0, 1fr); }
  /* The time an action costs sits in its label: "Search-match · seconds", "Run analysis · about 16 min". */
  .dara-run-actions .ant-btn .cost { margin-left: 6px; font-size: var(--dara-font-13); opacity: 0.85; }
  .dara-run-action-note { margin: 4px 0 0; color: var(--dara-text-hint); font-size: var(--dara-font-12); }
  .dara-run-summary .invalid { color: var(--dara-fail); }
  /* antd's warning text (#faad14) is too light on white: the review tone carries it. */
  .ant-form-item-explain-warning { color: var(--dara-review); }
  .dara-run-submitted { margin: 0; padding: 0 0 16px; list-style: none; font-size: var(--dara-font-13); color: var(--dara-text-secondary); }
  .dara-run-submitted li + li { margin-top: 2px; }
  .dara-file-row {
    display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 4px 8px 4px 12px;
    background: var(--dara-surface-sunken); border: 1px solid var(--dara-border); border-radius: var(--dara-radius);
  }
  .dara-file-row .name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dara-file-row .meta { color: var(--dara-text-secondary); font-size: var(--dara-font-12); white-space: nowrap; }
  .dara-pattern-preview { display: block; width: 100%; height: 80px; margin-top: 8px; border: 1px solid var(--dara-border); border-radius: var(--dara-radius); background: var(--dara-surface); }
  .dara-pattern-preview path { fill: none; stroke: var(--dara-text-secondary); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .dara-pattern-preview rect { fill: var(--dara-surface-sunken); }
  .dara-pattern-preview-axis { display: flex; justify-content: space-between; color: var(--dara-text-hint); font-size: var(--dara-font-12); }
  .dara-recent { display: flex; flex-wrap: wrap; align-items: center; gap: 0 4px; }
  .dara-recent .ant-btn-link { height: auto; padding: 0 4px; }
  .dara-qr-summary {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
    margin: 0 0 16px; padding: 8px 12px;
    background: var(--dara-surface-sunken); border: 1px solid var(--dara-border); border-radius: var(--dara-radius);
  }
  .dara-qr-summary-meta { color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-advanced-toggle {
    display: flex; align-items: center; gap: 8px; width: 100%;
    margin: 0; padding: 0 0 16px; border: 0; background: none; cursor: pointer; text-align: left;
  }
  .dara-advanced-toggle .anticon { transition: transform 0.2s; color: var(--dara-text-hint); font-size: var(--dara-font-12); }
  .dara-advanced-toggle[aria-expanded='true'] .anticon { transform: rotate(90deg); }
  .dara-advanced-toggle .dara-card-title { margin: 0; }
  .dara-advanced-toggle .note { margin-left: auto; color: var(--dara-text-hint); font-size: var(--dara-font-13); }
  .dara-mode-radio { display: flex; }
  .dara-mode-radio .ant-radio-button-wrapper { flex: 1; text-align: center; }
  .dara-advanced-body[hidden] { display: none; }
  @media (min-width: ${WIDE_LAYOUT}px) {
    max-width: ${PAGE_MAX_WIDTH}px;
    .dara-submit-layout {
      display: grid;
      grid-template-columns: minmax(0, 3fr) minmax(380px, 2fr);
      gap: 24px;
      align-items: start;
    }
    .dara-submit-main, .dara-submit-side { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    /* The run card stays in view while the main column scrolls; a side column taller than the
       window scrolls with the page (no nested scroll area). */
    .dara-submit-side { position: sticky; top: 16px; }
    .dara-card-run { order: 0; }
  }
`;

const MODE_LABELS = { elemental: 'Phase search + Rietveld', known_mixture: 'Known-phase refinement', reaction: 'Reaction check' };
// One name per field; the formula list changes its name with the mode.
const FORMULA_LABELS = { elemental: 'Expected formulas', known_mixture: 'Phases to refine', reaction: 'Precursors' };
const PATTERN_ACCEPT = '.xy,.xye,.xrdml,.raw,.rasx,.txt,.scn,.ras,.brml,.csv,.dat,.asc';

function Card({ name, title, children }) {
  return <section className={`dara-card dara-card-${name}`} aria-label={title}>
    <h2 className="dara-card-title">{title}</h2>
    {children}
  </section>;
}

const INSTRUMENT_NAMES = [
  'synchrotron',
  'Aeris-fds-Pixcel1d-Medipix3',
  'LBL-d8-LynxEyeXE',
  'd8-solxe-fds-0600',
  'siemens-d5000-fds1mm',
  'RMS-D8-ADS-15-LynxEyeXE',
  'Rigaku-Miniflex-600-DTEXultra2-fds',
  'Rigaku-SmartLab-CBO-BB-FDS05deg',
  'PW3040-FDS-ADS-Xcelerator',
  'xpert-pixcel-0500',
];

const WAVELENGTHS = [
  ['Cu', 1.540598], ['Co', 1.789010], ['Cr', 2.289760], ['Fe', 1.936042], ['Mo', 0.709300],
];

const commaList = value => JSON.stringify(
  (value || '').split(',').map(item => item.trim()).filter(Boolean)
);
const uploadFile = value => value?.originFileObj || value;
const splitList = value => (value || '').split(',').map(item => item.trim()).filter(Boolean);
const elementList = value => JSON.stringify(checkElements(value).symbols);
const fileKey = file => (file ? `${file.uid}:${file.name}` : null);

// 'm min' below one hour, 'h:mm h' from one hour on.
const formatDuration = seconds => {
  if (!Number.isFinite(seconds)) return '';
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')} h`;
};
const ESTIMATE_ALERT_TYPE = { ok: 'info', long: 'warning', very_long: 'error', unknown: 'info' };

function EstimateAlert({ estimate, loading, prefilter }) {
  if (!estimate) return loading ? <Alert type="info" showIcon message="Estimating candidates and run time..." style={{ marginBottom: 16 }} /> : null;
  const { candidates = {}, runtime_s: runtime, workers, suggestions = [], level } = estimate;
  let message = null;
  if (candidates.expected_after_dedup != null) {
    const parts = [];
    if (candidates.cod != null) parts.push(`${candidates.cod} COD`);
    if (candidates.mp != null) parts.push(`${candidates.mp} MP`);
    message = `Estimated candidates: about ${candidates.expected_after_dedup}`
      + (parts.length ? ` (${parts.join(' + ')}${candidates.mp_capped ? ', MP limit reached' : ''})` : '')
      + (prefilter && candidates.prefilter_expected_after != null ? `, about ${candidates.prefilter_expected_after} after pre-filter` : '');
  } else {
    message = candidates.note || 'Estimated candidates unavailable';
  }
  return (
    <Alert
      style={{ marginBottom: 16, opacity: loading ? 0.6 : 1 }}
      showIcon
      type={ESTIMATE_ALERT_TYPE[level] || 'info'}
      message={message}
      description={(
        <div>
          {candidates.expected_after_dedup != null && candidates.note ? <div>{candidates.note}</div> : null}
          {candidates.no_cod_formulas?.length ? <div>{`${candidates.no_cod_formulas.join(', ')}: no COD structure; ${
            candidates.no_cod_source === 'mp' ? 'taken from Materials Project.' : 'no structure available (add a CIF or set an MP API key).'}`}</div> : null}
          {runtime ? (
            <div>Estimated run time {formatDuration(runtime.low)}–{formatDuration(runtime.high)} (typically {formatDuration(runtime.expected)}) on {workers} workers.</div>
          ) : null}
          {suggestions.length ? <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>{suggestions.map(item => <li key={item}>{item}</li>)}</ul> : null}
        </div>
      )}
    />
  );
}

/** The scan of the chosen file, as HighScore, Jade and Match! show it on opening; ranges left out are shaded. */
function PatternPreview({ points, start, end, excluded }) {
  const shape = previewShape(points, { width: 600, height: 80, start, end, excluded });
  if (!shape) return null;
  return <div>
    <svg className="dara-pattern-preview" viewBox="0 0 600 80" preserveAspectRatio="none" role="img"
      aria-label={`Measured pattern from ${shape.lo.toFixed(1)} to ${shape.hi.toFixed(1)} degrees 2θ`}>
      {shape.shades.map(item => <rect key={item.x} x={item.x} y={0} width={item.width} height={80} />)}
      <path d={shape.d} />
    </svg>
    <div className="dara-pattern-preview-axis"><span>{`${shape.lo.toFixed(1)}°`}</span><span>2θ</span><span>{`${shape.hi.toFixed(1)}°`}</span></div>
  </div>;
}

function Submission() {
  const [form] = Form.useForm();
  const [analysisMode, setAnalysisMode] = useState('elemental');
  const [synchrotron, setSynchrotron] = useState(false);
  const [previousInstrument, setPreviousInstrument] = useState('Aeris-fds-Pixcel1d-Medipix3');
  const [instrumentNames, setInstrumentNames] = useState(INSTRUMENT_NAMES);
  const [instrumentDetails, setInstrumentDetails] = useState({});
  const [calibratable, setCalibratable] = useState([]);
  const [fwhmCurves, setFwhmCurves] = useState({}); // {profile: summary} of the stored instrument FWHM curves
  const [advanced] = useAdvancedTools(); // the FWHM curve is an advanced analysis tool
  const [refinementControls, setRefinementControls] = useState(DEFAULT_CONTROLS);
  const selectedInstrument = Form.useWatch('instrument_profile', form);
  const [patternInspection, setPatternInspection] = useState(null);
  const [inspectingPattern, setInspectingPattern] = useState(false);
  const inspectionSequence = useRef(0);
  const autoElements = useRef(null);
  const autoFormulas = useRef(null); // formulas filled from the file name, cleared with the file
  const [msg, setMsg] = useState(null);
  const [spinning, setSpinning] = useState(false);
  const submitting = useRef(false); // a second click while the first submission is on its way does nothing
  const leaving = useRef(false); // set before the page opens a task, so the leave warning stays quiet
  const leave = url => { leaving.current = true; window.location.href = url; };
  const [submittedTasks, setSubmittedTasks] = useState([]); // [{id, name}] of this page, newest first
  const [recentElements, setRecentElements] = useState([]);
  const [cloneSource, setCloneSource] = useState(null);
  const [phaseSet, setPhaseSet] = useState(null); // {id, phases: [{formula, spacegroup}] | null, error?}
  const [usingRemembered, setUsingRemembered] = useState(false);
  const browserStorage = () => (typeof window === 'undefined' ? null : window.localStorage);
  // Settings from the last submission in this browser (user, modes, databases, limits, instrument).
  const applyRemembered = saved => {
    if (!saved) return false;
    form.setFieldsValue(saved.fields);
    setAnalysisMode(saved.fields.analysis_mode ?? 'elemental');
    setSynchrotron(Boolean(saved.fields.synchrotron));
    if (saved.controls) setRefinementControls(saved.controls);
    setUsingRemembered(true);
    return true;
  };
  const resetToDefaults = () => {
    forgetRemembered(browserStorage());
    form.resetFields();
    setAnalysisMode('elemental');
    setSynchrotron(false);
    setRefinementControls(DEFAULT_CONTROLS);
    setUsingRemembered(false);
  };
  const [loadingClone, setLoadingClone] = useState(false);
  // A stored search-match to reopen once the clone's settings are in the form: /?clone_task=ID&search=1,
  // or any identification task ({selected, background, theoretical}).
  const [reopenSearch, setReopenSearch] = useState(null);
  const [firstRun, setFirstRun] = useState(false); // an empty database: the Tutorial is offered
  const [seriesMode, setSeriesMode] = useState(false);
  // "Submit a series" from the Series page opens the form in series mode.
  useEffect(() => { if (new URLSearchParams(window.location.search).get('series')) setSeriesMode(true); }, []);
  const [seriesPreview, setSeriesPreview] = useState(null);
  const seriesFiles = Form.useWatch('series_files', form);
  const firstSeriesFile = useRef(null);
  const [seriesInspections, setSeriesInspections] = useState({}); // file key -> inspection | {error}
  const inspections = useRef({}); // file key -> promise of its inspection (each file once)
  const inspectQueue = useRef(limiter(3));
  const library = useReferenceLibrary();
  const [estimate, setEstimate] = useState(null);
  const [estimating, setEstimating] = useState(false);
  const [estimateFailed, setEstimateFailed] = useState(false);
  const referenceIds = useRef([]); // structures picked in the quick ranking, sent once by onSubmit
  const watchedMode = Form.useWatch('analysis_mode', form);
  const watchedElements = Form.useWatch('elements', form);
  const watchedPrecursors = Form.useWatch('precursor_formulas', form);
  const watchedUseCod = Form.useWatch('use_cod', form);
  const watchedUseMp = Form.useWatch('use_mp', form);
  const watchedEHull = Form.useWatch('mp_e_hull_cutoff', form);
  const watchedMaxMp = Form.useWatch('max_mp_structures', form);
  const watchedPrefilter = Form.useWatch('candidate_prefilter', form);
  const watchedMaxMinutes = Form.useWatch('max_analysis_minutes', form);
  const watchedWavelengthLab = Form.useWatch('wavelength_lab', form);
  // The synchrotron wavelength is entered in Å (as everywhere in the app); the server takes nm.
  const watchedWavelengthSync = Form.useWatch('wavelength_sync_a', form);
  const syncNm = value => (value === undefined || value === null || value === '' ? '' : String(Number((Number(value) / 10).toPrecision(10))));
  const watchedThetaMax = Form.useWatch('two_theta_max', form);
  const watchedPattern = Form.useWatch('pattern_file', form);
  const watchedLibraryCifs = Form.useWatch('library_cif_ids', form);
  const watchedAdditional = Form.useWatch('additional_phases', form);
  const watchedReuseCifs = Form.useWatch('reuse_additional_cifs', form);
  const watchedThetaMin = Form.useWatch('two_theta_min', form);
  const watchedScreening = Form.useWatch('screening_timeout_s', form);
  const watchedExcluded = Form.useWatch('excluded_ranges', form);
  const elementCheck = checkElements(watchedElements);
  useEffect(() => { setRecentElements(readRecentElements(browserStorage())); }, []);

  // Advanced options are collapsed unless one of them differs from its default (remembered
  // settings, a cloned task, or a validation error there); the card never closes by itself.
  const changedAdvanced = advancedChanges({
    use_cod: watchedUseCod, use_mp: watchedUseMp, mp_e_hull_cutoff: watchedEHull, max_mp_structures: watchedMaxMp,
    candidate_prefilter: watchedPrefilter, screening_timeout_s: watchedScreening,
    max_analysis_minutes: watchedMaxMinutes, library_cif_ids: watchedLibraryCifs, additional_phases: watchedAdditional,
    reuse_additional_cifs: watchedReuseCifs,
  }, refinementControls, DEFAULT_CONTROLS);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [standardOpen, setStandardOpen] = useState(false);
  const hasAdvancedChanges = changedAdvanced.length > 0;
  useEffect(() => { if (hasAdvancedChanges) setAdvancedOpen(true); }, [hasAdvancedChanges]);

  useEffect(() => {
    fetch(`${URL}/instrument-profiles`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error('Profile list unavailable')))
      .then(data => {
        if (Array.isArray(data.profiles) && data.profiles.length) setInstrumentNames(data.profiles);
        setInstrumentDetails(data.details || {});
        setCalibratable(data.calibratable || []);
        setFwhmCurves(data.fwhm_curves || {});
      })
      .catch(() => {});
  }, []);

  // Candidate/run-time estimate: debounced, and the previous request is aborted.
  const inspectedThetaMax = patternInspection?.two_theta_max;
  const inspectedPoints = patternInspection?.data_points;
  useEffect(() => {
    const mode = watchedMode || 'elemental';
    const { symbols: elementSymbols, error: elementError } = checkElements(watchedElements);
    const precursorList = splitList(watchedPrecursors);
    setEstimateFailed(false);
    if (elementError || (mode === 'elemental' && !elementSymbols.length && !precursorList.length) || (mode !== 'elemental' && !precursorList.length)) {
      setEstimate(null);
      setEstimating(false);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setEstimating(true);
      const thetaMax = Number.isFinite(watchedThetaMax) ? watchedThetaMax : inspectedThetaMax;
      fetch(`${URL}/submission/estimate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({
          analysis_mode: mode,
          elements: elementSymbols,
          precursor_formulas: precursorList,
          use_cod: watchedUseCod !== false,
          use_mp: watchedUseMp !== false,
          mp_e_hull_cutoff: watchedEHull ?? 0.05,
          max_mp_structures: watchedMaxMp ?? 100,
          candidate_prefilter: watchedPrefilter !== false,
          wavelength: synchrotron ? (syncNm(watchedWavelengthSync) || 'Cu') : (watchedWavelengthLab || 'Cu'),
          two_theta_max: Number.isFinite(thetaMax) ? thetaMax : null,
          data_points: Number.isFinite(inspectedPoints) ? inspectedPoints : null,
          max_analysis_minutes: watchedMaxMinutes ?? null,
        }),
      })
        .then(response => response.ok ? response.json() : Promise.reject(new Error('Estimate unavailable')))
        .then(data => { setEstimate(data); setEstimating(false); })
        .catch(error => { if (error.name !== 'AbortError') { setEstimate(null); setEstimating(false); setEstimateFailed(true); } });
    }, 600);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [watchedMode, watchedElements, watchedPrecursors, watchedUseCod, watchedUseMp, watchedEHull, watchedMaxMp,
    watchedPrefilter, watchedMaxMinutes, watchedWavelengthLab, watchedWavelengthSync, watchedThetaMax,
    synchrotron, inspectedThetaMax, inspectedPoints]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('clone_task')) return;
    applyRemembered(readRemembered(browserStorage()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // "Use these phases for other files" opens /?phase_set=<task id>: Known-phase refinement with the
  // exact structures of that task's adopted result. (After the remembered-settings effect, so the
  // locked mode wins.)
  useEffect(() => {
    const id = phaseSetIdFromSearch(window.location.search);
    if (id === null) return undefined;
    let active = true;
    setPhaseSet({ id, phases: null });
    form.setFieldsValue({ analysis_mode: 'known_mixture' });
    setAnalysisMode('known_mixture');
    fetch(`${URL}/task/${id}/phase-set`)
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'The phases of this task could not be loaded.');
        return data;
      })
      .then(data => { if (active) setPhaseSet({ id, phases: data.phases }); })
      .catch(error => { if (active) setPhaseSet({ id, phases: null, error: String(error.message || error) }); });
    return () => { active = false; };
  }, [form]);
  const clearPhaseSet = () => {
    setPhaseSet(null);
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${withoutPhaseSet(window.location.search)}${window.location.hash}`);
  };

  useEffect(() => {
    const sourceId = new URLSearchParams(window.location.search).get('clone_task');
    if (!sourceId || !/^\d+$/.test(sourceId)) return;
    let active = true;
    setLoadingClone(true);
    fetch(`${URL}/task/${sourceId}/rerun-settings`)
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Saved input could not be loaded.');
        return data;
      })
      .then(data => {
        if (!active) return;
        const settings = data.settings;
        // A rerun of a task that reused a phase set refines the same stored structures, not the formulas.
        if (data.phase_set?.length) setPhaseSet({ id: settings.phase_set_from ?? data.source_task_id, phases: data.phase_set, stored: true });
        const isSync = typeof settings.wavelength === 'number';
        form.setFieldsValue({
          ...settings,
          elements: (settings.elements || []).join(', '),
          precursor_formulas: (settings.precursor_formulas || []).join(', '),
          synchrotron: isSync,
          wavelength_sync_a: isSync ? String(Number((Number(settings.wavelength) * 10).toPrecision(8))) : undefined,
          wavelength_lab: isSync ? 'Cu' : settings.wavelength,
          excluded_ranges: settings.excluded_ranges || [],
          reuse_additional_cifs: true,
          candidate_prefilter: settings.candidate_prefilter === true,
          screening_timeout_s: settings.screening_timeout_s ?? null,
          max_analysis_minutes: settings.max_analysis_minutes ?? null,
          standard_formula: settings.internal_standard?.formula,
          standard_wt: settings.internal_standard?.wt_percent,
        });
        if (settings.internal_standard) setStandardOpen(true);
        // An identification is a search-match result, not a form mode: the form keeps a search mode.
        const identification = settings.analysis_mode === 'identification';
        const mode = identification ? 'elemental' : settings.analysis_mode;
        if (identification) form.setFieldsValue({ analysis_mode: mode });
        setAnalysisMode(mode);
        if (identification || new URLSearchParams(window.location.search).get('search') === '1') {
          setReopenSearch({ selected: settings.reference_ids || [], background: settings.background || null, theoretical: settings.include_theoretical });
        }
        setSynchrotron(isSync);
        setPreviousInstrument(settings.instrument_profile);
        setCloneSource(data);
      })
      .catch(error => { if (active) setMsg(String(error.message || error)); })
      .finally(() => { if (active) setLoadingClone(false); });
    return () => { active = false; };
  }, [form]);

  useEffect(() => {
    const names = (seriesFiles || []).map(item => item.name);
    if (!seriesMode || names.length < 2) {
      setSeriesPreview(null);
      return undefined;
    }
    const controller = new AbortController();
    fetch(`${URL}/series/preview`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames: names }), signal: controller.signal,
    })
      .then(response => response.ok ? response.json() : Promise.reject(new Error('Preview unavailable')))
      .then(setSeriesPreview)
      .catch(error => { if (error.name !== 'AbortError') setSeriesPreview({ error: String(error.message || error) }); });
    return () => controller.abort();
  }, [seriesMode, seriesFiles]);

  // Every series file is inspected once (at most three at a time) for the per-file table.
  const seriesInspection = item => {
    const key = fileKey(item);
    if (!inspections.current[key]) {
      inspections.current[key] = inspectQueue.current(() => fetchInspection(uploadFile(item)));
      inspections.current[key]
        .then(data => setSeriesInspections(previous => ({ ...previous, [key]: data })),
          error => setSeriesInspections(previous => ({ ...previous, [key]: { error: String(error.message || error) } })));
    }
    return inspections.current[key];
  };
  useEffect(() => { if (seriesMode) (seriesFiles || []).forEach(seriesInspection); }, [seriesMode, seriesFiles]); // eslint-disable-line react-hooks/exhaustive-deps

  const onSeriesFilesChange = info => {
    // The first file's inspection sets the instrument, wavelength and elements; re-apply when another file becomes first.
    const first = info?.fileList?.[0];
    const key = fileKey(first);
    if (key === firstSeriesFile.current) return;
    firstSeriesFile.current = key;
    inspectFile(first ? uploadFile(first) : null, first ? seriesInspection(first) : null);
  };

  const toggleSeriesMode = checked => {
    setSeriesMode(checked);
    firstSeriesFile.current = null;
    form.setFieldsValue({ pattern_file: undefined, series_files: [] });
    setSeriesPreview(null);
    inspectFile(null);
  };

  const inspectPattern = info => {
    const fileValue = info?.fileList?.length ? info.file : null;
    form.setFieldValue('pattern_file', fileValue);
    return inspectFile(uploadFile(fileValue));
  };
  const removePattern = () => {
    form.setFieldValue('pattern_file', null);
    inspectFile(null);
  };

  const fetchInspection = async file => {
    const body = new FormData();
    body.append('pattern_file', file);
    const response = await fetch(`${URL}/pattern/inspect`, { method: 'POST', body });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not inspect the pattern.');
    return data;
  };

  /** Inspects `file` (or awaits `pending`, its inspection already on its way) and applies the suggestions. */
  const inspectFile = async (file, pending = null) => {
    const sequence = ++inspectionSequence.current;
    if (autoElements.current !== null && form.getFieldValue('elements') === autoElements.current) {
      form.setFieldValue('elements', '');
    }
    if (autoFormulas.current !== null && form.getFieldValue('precursor_formulas') === autoFormulas.current) {
      form.setFieldValue('precursor_formulas', '');
    }
    autoElements.current = null;
    autoFormulas.current = null;
    setPatternInspection(null);
    if (!file) {
      setInspectingPattern(false);
      return;
    }

    setInspectingPattern(true);
    try {
      const data = await (pending || fetchInspection(file));
      if (sequence !== inspectionSequence.current) return;

      const suggestion = data.profile_suggestion;
      const updates = {};
      const inferredElements = data.filename_chemistry?.elements || [];
      const applyElements = inferredElements.length > 0 && !(form.getFieldValue('elements') || '').trim();
      if (applyElements) {
        updates.elements = inferredElements.join(', ');
        autoElements.current = updates.elements;
      }
      // The file name's formulas fill the expected formulas or precursors when the list is empty;
      // typed entries are never replaced. In Known-phase refinement they are only offered: a file
      // name often names the precursors of a reaction product, not the phases in it.
      const inferredFormulas = data.filename_chemistry?.formulas || [];
      const applyFormulas = inferredFormulas.length > 0 && form.getFieldValue('analysis_mode') !== 'known_mixture'
        && !(form.getFieldValue('precursor_formulas') || '').trim();
      if (applyFormulas) {
        updates.precursor_formulas = inferredFormulas.join(', ');
        autoFormulas.current = updates.precursor_formulas;
      }
      if (suggestion?.profile) {
        updates.instrument_profile = suggestion.profile;
        setPreviousInstrument(suggestion.profile);
        setSynchrotron(false);
        updates.synchrotron = false;
      }
      if (data.recommended_wavelength && WAVELENGTHS.some(item => item[0] === data.recommended_wavelength)) {
        updates.wavelength_lab = data.recommended_wavelength;
      }
      form.setFieldsValue(updates);
      setPatternInspection({ ...data, elementsAutoApplied: applyElements, formulasAutoApplied: applyFormulas });
    } catch (error) {
      if (sequence === inspectionSequence.current) {
        setPatternInspection({ error: String(error.message || error) });
      }
    } finally {
      if (sequence === inspectionSequence.current) setInspectingPattern(false);
    }
  };

  // Quick ranking: the current pattern and chemistry, without validating the whole form.
  const quickRankingRequest = () => {
    const values = form.getFieldsValue(true);
    if (!values.pattern_file && !cloneSource) throw new Error('Upload a pattern first.');
    const body = new FormData();
    // A rerun without a replacement file ranks the source task's stored pattern.
    if (values.pattern_file) body.append('pattern_file', uploadFile(values.pattern_file));
    else body.append('source_task_id', cloneSource.source_task_id);
    body.append('elements', elementList(values.elements));
    body.append('precursor_formulas', commaList(values.precursor_formulas));
    body.append('wavelength', synchrotron ? Number((Number(values.wavelength_sync_a) / 10).toPrecision(10)) : (values.wavelength_lab || 'Cu'));
    if (values.two_theta_min != null) body.append('two_theta_min', values.two_theta_min);
    if (values.two_theta_max != null) body.append('two_theta_max', values.two_theta_max);
    body.append('excluded_ranges', JSON.stringify((values.excluded_ranges || []).filter(item => item?.[0] != null && item?.[1] != null)));
    body.append('use_cod', values.use_cod !== false);
    body.append('use_mp', values.use_mp !== false);
    body.append('mp_e_hull_cutoff', values.mp_e_hull_cutoff ?? 0.05);
    body.append('max_mp_structures', values.max_mp_structures ?? 100);
    body.append('library_cif_ids', JSON.stringify(values.library_cif_ids || []));
    return body;
  };
  // "Refine selected": a Known-phase refinement of exactly the ranked structures. Resolves to
  // whether it was submitted; otherwise (invalid form, cancelled, server error) the form is restored
  // so a later Submit never runs a formula search without the chosen structures.
  const refineRanked = async ({ referenceIds: ids, formulas }) => {
    const previous = form.getFieldsValue(['analysis_mode', 'precursor_formulas']);
    const previousMode = analysisMode;
    form.setFieldsValue({ analysis_mode: 'known_mixture', precursor_formulas: formulas.join(', ') });
    setAnalysisMode('known_mixture');
    referenceIds.current = ids;
    let submitted = false;
    try {
      // An explicit action on the chosen phases: no confirmation; on success the new task opens.
      // Known-phase mode was for the ranked structures only: the next analysis keeps the user's mode.
      submitted = await onSubmit({ openTask: true, rememberedMode: previous.analysis_mode, confirmed: true });
      // The workspace hides the form: name the first error and go back to the setup, where it is
      // marked; the ranking stays open behind the Run card's "Open search-match".
      const invalid = submitted ? [] : form.getFieldsError().filter(item => item.errors.length);
      if (invalid.length) {
        message.error(`Not submitted: ${invalid[0].errors[0]}`);
        showField(invalid[0].name);
      }
    } finally {
      referenceIds.current = [];
      if (!submitted || !leaving.current) {
        form.setFieldsValue(previous);
        setAnalysisMode(previousMode);
      }
    }
    if (submitted && !leaving.current) quick.close(); // an older server without the task id: back to the setup
    return submitted;
  };
  const quick = useQuickRanking(quickRankingRequest);
  // Back to the setup with `name` marked, in view and focused.
  const showField = name => {
    quick.close(false);
    setTimeout(() => {
      form.scrollToField(name, { block: 'center' });
      form.getFieldInstance(name)?.focus?.({ preventScroll: true });
    }, 0);
  };
  // Opened in the render where the clone is in place, so the ranking request carries its stored pattern.
  useEffect(() => {
    if (!reopenSearch || !cloneSource) return;
    setReopenSearch(null);
    quick.open(reopenSearch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reopenSearch, cloneSource]);
  useEffect(() => {
    let active = true;
    fetch(`${URL}/tasks?page=1&limit=1&user=`).then(response => (response.ok ? response.json() : null))
      .then(body => { if (active && body?.total_tasks === 0) setFirstRun(true); }).catch(() => {});
    return () => { active = false; };
  }, []);
  const openSearchMatch = () => {
    setRecentElements(rememberElements(browserStorage(), checkElements(form.getFieldValue('elements')).symbols));
    quick.open();
  };
  // Leaving with a pattern or a ranking on the page asks first (refresh, closing the tab, another site).
  const unsaved = Boolean(watchedPattern || (seriesFiles || []).length || quick.ranked || quick.state.status === 'pending');
  useEffect(() => {
    if (!unsaved) return undefined;
    const warn = event => { if (!leaving.current) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    // The header's links change the route without unloading: they ask through the leave guard.
    const unguard = setLeaveGuard(() => (leaving.current ? null
      : 'The loaded pattern, its settings and the search-match ranking on this page are not kept.'));
    return () => { window.removeEventListener('beforeunload', warn); unguard(); };
  }, [unsaved]);

  // "Save identification": the selected phases as the result, without refinement. Uses the ranking's
  // pattern and chemistry with the setup's user, sample and instrument; a field the setup has not
  // filled in yet takes the user back to it, like "Refine with Rietveld".
  const saveIdentification = async ({ referenceIds: ids }) => {
    let values;
    try {
      values = await form.validateFields(['user', 'instrument_profile', ...(synchrotron ? ['wavelength_sync_a'] : ['wavelength_lab'])]);
    } catch (error) {
      const first = (error?.errorFields || [])[0];
      if (first) {
        message.error(`Not saved: ${first.errors[0]}`);
        showField(first.name);
      }
      return false;
    }
    let body;
    try {
      // A rerun without a new file sends `source_task_id`: the server reuses the stored pattern.
      body = quickRankingRequest();
    } catch (error) {
      setMsg(String(error.message || error));
      return false;
    }
    body.append('user', values.user);
    const sample = form.getFieldValue('sample');
    if (sample?.trim()) body.append('sample', sample.trim());
    body.append('instrument_profile', values.instrument_profile);
    body.append('reference_ids', JSON.stringify(ids));
    setMsg(null);
    const send = async confirmed => {
      if (confirmed) body.set('confirm_wavelength', 'true');
      const response = await fetch(`${URL}/identification`, { method: 'POST', body });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.wf_id != null) {
        runAfterSuccess([
          () => writeRemembered(browserStorage(), form.getFieldsValue(true), refinementControls),
          () => leave(`/task/?id=${data.wf_id}`),
        ]);
        return true;
      }
      const mismatch = wavelengthMismatch(data.detail);
      if (mismatch && !confirmed) {
        return new Promise(resolve => Modal.confirm({
          title: 'The selected wavelength differs from the file',
          content: `${mismatch.filename ? `${mismatch.filename}: ` : ''}${mismatch.message} Save with the selected wavelength anyway?`,
          okText: 'Save anyway', cancelText: "Don't save",
          onOk: () => send(true).then(resolve, failure => { setMsg(String(failure.message || failure)); resolve(false); }),
          onCancel: () => resolve(false),
        }));
      }
      setMsg(detailMessage(data.detail, `The identification could not be saved (HTTP ${response.status}).`));
      return false;
    };
    try {
      return await send(false);
    } catch (error) {
      setMsg(String(error.message || error));
      return false;
    }
  };

  /**
   * Validates and submits the form; resolves to whether the server accepted it. `openTask` opens the
   * new task page; `rememberedMode` is the mode remembered for the next analysis; `confirmed` skips
   * the confirmation of a long run or a series.
   */
  const onSubmit = async ({ openTask = false, rememberedMode = null, confirmed = false } = {}) => {
    if (submitting.current) return false;
    let values;
    try {
      values = await form.validateFields();
    } catch (error) {
      const advancedNames = ['use_cod', 'use_mp', 'mp_e_hull_cutoff', 'max_mp_structures', 'candidate_prefilter',
        'screening_timeout_s', 'max_analysis_minutes', 'library_cif_ids', 'additional_phases', 'reuse_additional_cifs'];
      if ((error?.errorFields || []).some(item => advancedNames.includes(item.name?.[0]))) setAdvancedOpen(true);
      if ((error?.errorFields || []).some(item => String(item.name?.[0]).startsWith('standard_'))) setStandardOpen(true);
      return false;
    }

    const formData = new FormData();
    formData.append('user', values.user);
    if (values.sample?.trim()) formData.append('sample', values.sample.trim());
    formData.append('analysis_mode', values.analysis_mode);
    formData.append('elements', elementList(values.elements));
    formData.append('precursor_formulas', commaList(values.precursor_formulas));
    if (phaseSet && !phaseSet.stored) formData.append('phase_set_task_id', phaseSet.id);
    if (cloneSource?.phase_set?.length && !phaseSet) formData.append('reuse_phase_set', 'false');
    formData.append('temperature', values.analysis_mode === 'reaction' ? values.temperature : -1);
    formData.append('atmosphere', values.atmosphere);
    formData.append('instrument_profile', values.instrument_profile);
    formData.append('refinement_controls', JSON.stringify(refinementControls));
    formData.append('wavelength', synchrotron ? Number((Number(values.wavelength_sync_a) / 10).toPrecision(10)) : values.wavelength_lab);
    formData.append('use_cod', values.use_cod);
    formData.append('use_mp', values.use_mp);
    formData.append('mp_e_hull_cutoff', values.mp_e_hull_cutoff);
    formData.append('max_mp_structures', values.max_mp_structures);
    if (values.standard_formula?.trim()) {
      formData.append('internal_standard', JSON.stringify({ formula: values.standard_formula.trim(), wt_percent: values.standard_wt }));
    }
    formData.append('library_cif_ids', JSON.stringify(values.library_cif_ids || []));
    if (referenceIds.current.length) formData.append('reference_ids', JSON.stringify(referenceIds.current));
    formData.append('candidate_prefilter', String(values.candidate_prefilter !== false));
    if (values.screening_timeout_s != null) formData.append('screening_timeout_s', values.screening_timeout_s);
    if (values.max_analysis_minutes != null) formData.append('max_analysis_minutes', values.max_analysis_minutes);
    if (seriesMode) {
      formData.append('series_name', (values.series_name || '').trim());
      (values.series_files || []).forEach(file => formData.append('pattern_files', uploadFile(file)));
    } else {
      if (values.pattern_file) formData.append('pattern_file', uploadFile(values.pattern_file));
      if (cloneSource) formData.append('source_task_id', cloneSource.source_task_id);
      formData.append('reuse_additional_cifs', values.reuse_additional_cifs !== false);
    }
    if (values.two_theta_min != null) formData.append('two_theta_min', values.two_theta_min);
    if (values.two_theta_max != null) formData.append('two_theta_max', values.two_theta_max);
    formData.append('excluded_ranges', JSON.stringify(values.excluded_ranges || []));
    (values.additional_phases || []).forEach(file => formData.append('additional_phases', uploadFile(file)));

    const abortControl = new AbortController();
    setMsg(null);
    setSpinning(true);
    submitting.current = true;
    // The returned promise resolves to whether the server accepted the task, once every dialog is closed.
    let submitted = false;
    let finish;
    const done = new Promise(resolve => { finish = () => { submitting.current = false; resolve(submitted); }; });
    const patternName = seriesMode ? values.series_name : (uploadFile(values.pattern_file)?.name || cloneSource?.filename);
    const remembered = { ...form.getFieldsValue(true), ...(rememberedMode ? { analysis_mode: rememberedMode } : {}) };
    const send = confirmedWavelength => {
      if (confirmedWavelength) formData.set('confirm_wavelength', 'true');
      return fetch(`${URL}${seriesMode ? '/submit-series' : '/submit'}`, { method: 'POST', body: formData, signal: abortControl.signal })
        .then(response => response.json())
        .then(data => {
          // Once the server confirms the submission, UI follow-up failures must never be reported as a failed submission.
          if (data.message === 'submitted' && data.series_id) {
            submitted = true;
            const seriesUrl = `/series/?id=${encodeURIComponent(data.series_id)}`;
            runAfterSuccess([
              () => writeRemembered(browserStorage(), remembered, refinementControls),
              () => rememberElements(browserStorage(), checkElements(values.elements).symbols),
              () => message.success(`The series was submitted (${(data.task_ids || []).length} tasks).`),
              () => leave(seriesUrl),
            ]);
            return false;
          }
          if (data.message === 'submitted') {
            submitted = true;
            const taskId = data.wf_id;
            if (openTask && taskId != null) {
              runAfterSuccess([
                () => writeRemembered(browserStorage(), remembered, refinementControls),
                () => rememberElements(browserStorage(), checkElements(values.elements).symbols),
                () => leave(`/task/?id=${taskId}`),
              ]);
              return false;
            }
            runAfterSuccess([
              () => writeRemembered(browserStorage(), remembered, refinementControls),
              () => setRecentElements(rememberElements(browserStorage(), checkElements(values.elements).symbols)),
              () => form.resetFields(),
              () => { autoElements.current = null; autoFormulas.current = null; },
              () => setCloneSource(null),
              () => setPatternInspection(null),
              () => setAnalysisMode('elemental'),
              () => setSynchrotron(false),
              () => applyRemembered(readRemembered(browserStorage())),
              () => {
                if (phaseSet?.stored) setPhaseSet(null);
                else if (phaseSet) { form.setFieldsValue({ analysis_mode: 'known_mixture' }); setAnalysisMode('known_mixture'); }
              },
              () => (data.warnings || []).forEach(item => message.warning(item.message, 10)),
              () => { if (taskId != null) setSubmittedTasks(list => [{ id: taskId, name: patternName }, ...list].slice(0, 3)); },
              () => message.success(taskId != null ? `Analysis submitted — Task ${taskId}` : 'Analysis submitted'),
            ]);
            return false;
          }
          const mismatch = wavelengthMismatch(data.detail);
          if (mismatch && !confirmedWavelength) {
            // The recorded and selected wavelengths differ: ask, never silently change either.
            Modal.confirm({
              title: 'The selected wavelength differs from the file',
              content: `${mismatch.filename ? `${mismatch.filename}: ` : ''}${mismatch.message} Submit with the selected wavelength anyway?`,
              okText: 'Submit anyway',
              cancelText: "Don't submit",
              onOk() {
                setSpinning(true);
                return send(true)
                  .catch(error => { if (error.name !== 'AbortError') setMsg(String(error)); })
                  .finally(() => { setSpinning(false); finish(); });
              },
              onCancel: () => finish(),
            });
            return true;
          }
          setMsg(detailMessage(data.detail, JSON.stringify(data)));
          return false;
        });
    };
    const go = () => send(false)
      .then(waiting => { if (!waiting) finish(); }) // true: the wavelength dialog finishes it
      .catch(error => { if (error.name !== 'AbortError') setMsg(String(error)); finish(); })
      .finally(() => setSpinning(false));
    // A routine run starts at once (the Run card already shows what will run); a series or a long
    // estimate first says what will run and for how long.
    const runtime = estimate?.runtime_s;
    const long = ['long', 'very_long'].includes(estimate?.level);
    if (confirmed || (!seriesMode && !long)) {
      go();
      return done;
    }
    const fileCount = (values.series_files || []).length;
    confirm({
      title: seriesMode ? `Run a series of ${fileCount} patterns?` : `Run a long analysis${runtime ? ` (typically ${formatDuration(runtime.expected)})` : ''}?`,
      width: 520,
      content: <dl className="dara-confirm-summary">
        <dt>Mode</dt><dd>{MODE_LABELS[values.analysis_mode]}</dd>
        <dt>{seriesMode ? 'Series' : 'Pattern'}</dt><dd>{seriesMode ? `${values.series_name} · ${fileCount} files` : patternName}</dd>
        {checkElements(values.elements).symbols.length ? <><dt>Elements</dt><dd>{checkElements(values.elements).symbols.join(', ')}</dd></> : null}
        {splitList(values.precursor_formulas).length ? <><dt>{FORMULA_LABELS[values.analysis_mode]}</dt><dd>{splitList(values.precursor_formulas).join(', ')}</dd></> : null}
        {estimate?.candidates?.expected_after_dedup != null ? <><dt>Candidates</dt><dd>{`about ${estimate.candidates.expected_after_dedup}`}</dd></> : null}
        {runtime ? <><dt>Run time</dt><dd>{`${formatDuration(runtime.low)}–${formatDuration(runtime.high)}, typically ${formatDuration(runtime.expected)}${seriesMode ? ' per file' : ''}`}</dd></> : null}
        {estimate?.suggestions?.length ? <><dt>To shorten</dt><dd>{estimate.suggestions[0]}</dd></> : null}
      </dl>,
      okText: seriesMode ? `Run series (${fileCount} files)` : 'Run analysis',
      cancelText: 'Not now',
      onOk: go,
      onCancel() { abortControl.abort(); setSpinning(false); finish(); },
    });
    return done;
  };

  const modeMessage = analysisMode === 'reaction'
    ? 'Predict products from precursors, temperature, and atmosphere, then fit the XRD pattern.'
    : analysisMode === 'known_mixture'
      ? 'Every listed phase is refined together; DARA picks the best database structure for each. Unlisted impurities or products are not searched.'
      : 'Search plausible phases in the selected element system, then refine them with Rietveld; expected formulas are prioritized.';

  const instrumentLabel = name => (instrumentDetails[name]
    ? `${instrumentDetails[name].label} — ${instrumentDetails[name].verified ? 'operator verified' : 'unverified'}`
    : name);
  const missing = text => <span className="missing">{text}</span>;
  const seriesCount = (seriesFiles || []).length;
  const patternSummary = seriesMode
    ? (seriesCount ? `${seriesCount} files (series)` : missing('No files selected'))
    : watchedPattern?.name
      || (cloneSource ? `${cloneSource.filename} (from Task ${cloneSource.source_task_id})` : missing('No file selected'));
  const elementSummary = elementCheck.symbols.join(', ');
  const formulaSummary = splitList(watchedPrecursors).join(', ');
  // The file was recorded with another anode than the one selected (from the inspection; a lab anode only).
  const recordedRadiation = !seriesMode && !synchrotron ? patternInspection?.recommended_wavelength : null;
  const wavelengthWarning = recordedRadiation && watchedWavelengthLab && recordedRadiation !== watchedWavelengthLab
    ? { recorded: recordedRadiation, chosen: watchedWavelengthLab } : null;
  const useRecorded = recorded => {
    if (!WAVELENGTHS.some(item => item[0] === recorded)) return;
    form.setFieldsValue({ wavelength_lab: recorded });
    quick.start();
  };
  const fileChemistry = patternInspection?.filename_chemistry;
  const unrecognised = fileChemistry ? unrecognisedTokens(patternInspection.filename, fileChemistry) : [];
  const offeredFormulas = analysisMode === 'known_mixture' && !phaseSet && fileChemistry?.formulas?.length
    && splitList(watchedPrecursors).join(', ') !== fileChemistry.formulas.join(', ') ? fileChemistry.formulas : null;
  // Series: the files whose names point to other elements than the ones every file runs with.
  const seriesItems = seriesMode ? (seriesFiles || []) : [];
  const seriesOrder = Object.fromEntries((seriesPreview?.items?.length ? seriesPreviewRows(seriesPreview) : []).map(row => [row.filename, row]));
  const seriesRows = seriesItems.map(item => {
    const data = seriesInspections[fileKey(item)];
    const order = seriesOrder[item.name];
    const notes = data?.error ? [data.error] : data ? [
      ...qualityAlertItems(data).filter(note => note.type !== 'info').map(note => note.message),
      ...(!synchrotron && data.recommended_wavelength && watchedWavelengthLab && data.recommended_wavelength !== watchedWavelengthLab
        ? [`Recorded with ${data.recommended_wavelength}`] : []),
    ] : [];
    return {
      key: fileKey(item), filename: item.name, order: order?.order ?? null, variableText: order?.variableText ?? '—',
      pending: !data, elements: data?.filename_chemistry?.elements || [],
      instrument: data ? (data.profile_suggestion?.profile || data.metadata?.instrument_system || '—') : '…',
      range: Number.isFinite(data?.two_theta_min) && Number.isFinite(data?.two_theta_max) ? `${data.two_theta_min.toFixed(1)}–${data.two_theta_max.toFixed(1)}°` : '—',
      notes,
    };
  }).sort((a, b) => (a.order ?? Infinity) - (b.order ?? Infinity));
  const usedElements = elementCheck.symbols.length ? elementCheck.symbols
    : [...new Set(splitList(watchedPrecursors).flatMap(formula => formula.match(/[A-Z][a-z]?/g) || []))];
  const otherChemistry = seriesRows.filter(row => row.elements.some(symbol => !usedElements.includes(symbol)));
  const wavelengthSummary = synchrotron
    ? (watchedWavelengthSync ? `${watchedWavelengthSync} Å (synchrotron)` : missing('wavelength not set'))
    : (watchedWavelengthLab || 'Cu');
  const rangeSummary = watchedThetaMin != null || watchedThetaMax != null
    ? `2θ ${watchedThetaMin ?? 'start'}–${watchedThetaMax ?? 'end'}°`
    : 'measured 2θ range';
  const selected = instrumentDetails[selectedInstrument];
  const hasChemistry = elementCheck.symbols.length > 0 || splitList(watchedPrecursors).length > 0;
  const unreadable = Boolean(!seriesMode && watchedPattern && patternInspection?.error);
  const quickReady = Boolean(watchedPattern || cloneSource) && !unreadable && !elementCheck.error && hasChemistry;
  const quickNote = !watchedPattern && !cloneSource ? 'Needs a pattern'
    : unreadable ? 'The pattern could not be read'
    : elementCheck.error ? `Fix the elements: ${elementCheck.error}`
      : !hasChemistry ? 'Needs the elements or expected formulas'
        : quick.ranked ? 'Ranked: open to pick the phases' : 'Ranks reference structures; you pick the phases';
  const runtimeText = estimate?.runtime_s?.expected ? `about ${formatDuration(estimate.runtime_s.expected)}${seriesMode ? ' per file' : ''}` : null;
  const msgAlert = msg ? <Alert message={msg} type="error" showIcon closable onClose={() => setMsg(null)} /> : null;

  return (
    <Layout hasSider={false} title="Submit">
      {/* The workspace replaces the setup on screen; the form stays mounted with its file and values. */}
      {quick.shown ? <QuickRankingWorkspace quick={quick} onRefine={refineRanked} onIdentify={saveIdentification} alert={msgAlert}
        mismatch={wavelengthWarning} onUseRecorded={useRecorded}
        context={[watchedPattern?.name || (cloneSource ? `${cloneSource.filename} (stored with Task ${cloneSource.source_task_id})` : null),
          elementSummary && `Elements ${elementSummary}`, formulaSummary && `${FORMULA_LABELS[analysisMode]} ${formulaSummary}`,
          <>Radiation {wavelengthSummary}</>, rangeSummary]} /> : null}
      <Container hidden={quick.shown}>
        <h1 className="dara-sr-only">New analysis</h1>
        {firstRun ? <p className="dara-first-run">No analyses yet. The <a href="/tutorial/">tutorial</a> walks through a first one, from the pattern to the adopted fit.</p> : null}
        <Form className="dara-submit-layout" layout="vertical" form={form}
          initialValues={{
            analysis_mode: 'elemental', temperature: 800, atmosphere: 'air', use_cod: true,
            use_mp: true, mp_e_hull_cutoff: 0.05, max_mp_structures: 100,
            wavelength_lab: 'Cu', instrument_profile: 'Aeris-fds-Pixcel1d-Medipix3', synchrotron: false,
            excluded_ranges: [], reuse_additional_cifs: true,
            candidate_prefilter: true, screening_timeout_s: 300, max_analysis_minutes: null,
          }}
          onValuesChange={(changed, values) => {
            setAnalysisMode(values.analysis_mode);
            if (Object.prototype.hasOwnProperty.call(changed, 'elements')) autoElements.current = null;
            if (Object.prototype.hasOwnProperty.call(changed, 'precursor_formulas')) autoFormulas.current = null;
            if (Object.prototype.hasOwnProperty.call(changed, 'analysis_mode')) {
              // File-name formulas are filled in only as expected formulas or precursors; in
              // Known-phase refinement they are offered instead (often the precursors of a product).
              const named = (patternInspection?.filename_chemistry?.formulas || []).join(', ');
              const current = (values.precursor_formulas || '').trim();
              if (changed.analysis_mode === 'known_mixture' && autoFormulas.current !== null && current === autoFormulas.current) {
                form.setFieldValue('precursor_formulas', '');
                autoFormulas.current = null;
              } else if (changed.analysis_mode !== 'known_mixture' && named && !current) {
                form.setFieldValue('precursor_formulas', named);
                autoFormulas.current = named;
              }
            }
          }}
        >
          <div className="dara-submit-main">
            <Card name="sample" title="Sample & pattern">
              <div className="dara-field-row">
                <Form.Item label="User" name="user" rules={[{ required: true, message: 'Please input your username.' }]}
                  extra={usingRemembered && !cloneSource ? <span>
                    Filled in from your last submission in this browser.{' '}
                    <a onClick={resetToDefaults}>Reset to defaults</a>
                  </span> : null}>
                  <Input placeholder="Username" />
                </Form.Item>
                {/* Not remembered between submissions: each pattern is its own sample. */}
                <Form.Item label="Sample name" name="sample" tooltip="Shown in Results and searchable; the file name is kept as well.">
                  <Input placeholder="e.g. NCM811 calcined 800 °C, batch 3 (optional)" maxLength={120} />
                </Form.Item>
                {!cloneSource && !loadingClone ? (
                  <Form.Item label="Submit as a series" tooltip="Analyse several patterns (for example a temperature or time series) with identical settings.">
                    <Switch checked={seriesMode} onChange={toggleSeriesMode} aria-label="Submit as a series" />
                  </Form.Item>
                ) : null}
              </div>
              {loadingClone ? <Alert type="info" showIcon message="Loading saved pattern and conditions..." /> : null}
              {cloneSource ? (
                <Alert type="info" showIcon
                  message={`Copied from Task ${cloneSource.source_task_id}: ${cloneSource.filename}`}
                  description={`The original pattern (${cloneSource.pattern.data_points.toLocaleString()} points) is reused unless you upload a replacement. Edit any conditions; the source task is unchanged.${
                    cloneSource.settings?.phase_set_from ? ` The phases are the exact structures of Task ${cloneSource.settings.phase_set_from}, as stored with the source task.` : ''}`}
                />
              ) : null}
              {seriesMode ? (
                <>
                  <Form.Item label="Series name" name="series_name" rules={[{ required: true, whitespace: true, message: 'Please enter a series name.' }, { max: 120 }]}>
                    <Input placeholder="e.g. ZnO anneal series" maxLength={120} />
                  </Form.Item>
                  <Form.Item label="XRD patterns" name="series_files" valuePropName="fileList" getValueFromEvent={event => event?.fileList}
                    rules={[{ required: true, type: 'array', min: 2, message: 'Please add at least two XRD patterns.' }]}
                    tooltip="XRDML is recommended: its acquisition metadata can select the closest instrument profile and wavelength automatically.">
                    <Dragger multiple maxCount={MAX_SERIES_FILES} beforeUpload={() => false} onChange={onSeriesFilesChange}
                      showUploadList={!seriesRows.length} accept={PATTERN_ACCEPT}>
                      <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                      <p className="ant-upload-text">Select all measured XRD files of the series</p>
                      <p className="ant-upload-hint">2 to {MAX_SERIES_FILES} files with the same format and acquisition settings</p>
                    </Dragger>
                  </Form.Item>
                  {seriesPreview?.error ? <Alert type="warning" showIcon message="Series order preview unavailable" description={seriesPreview.error} /> : null}
                  {otherChemistry.length ? <Alert type="warning" showIcon
                    message={`${otherChemistry.length} of ${seriesRows.length} file names point to other elements than ${usedElements.join(', ') || 'the ones entered'}`}
                    description={`${otherChemistry.map(row => `${row.filename} (${row.elements.filter(symbol => !usedElements.includes(symbol)).join(', ')})`).join('; ')}. All files run with the listed elements; add the missing elements, or submit the files of the other chemical system as their own series.`} /> : null}
                  {seriesRows.length ? (
                    <div style={{ marginBottom: 16 }}>
                      <div className="dara-subhead">Files</div>
                      <Table size="small" pagination={false} rowKey="key" dataSource={seriesRows} scroll={{ x: 'max-content' }}
                        columns={[
                          ...(seriesPreview?.items?.length ? [{ title: 'Order', dataIndex: 'order', width: 64, className: 'dara-num' }] : []),
                          { title: 'File', dataIndex: 'filename', render: (name, row) => <span style={{ whiteSpace: 'nowrap' }}>{name}{row.pending ? <span className="dara-dim"> · reading…</span> : null}</span> },
                          ...(seriesPreview?.items?.length ? [{ title: seriesPreview.unit ? `Variable (${seriesPreview.unit})` : 'Variable', dataIndex: 'variableText' }] : []),
                          { title: 'Elements in name', dataIndex: 'elements', render: list => (list.length ? list.join(', ') : '—') },
                          { title: 'Instrument', dataIndex: 'instrument' },
                          { title: '2θ', dataIndex: 'range', className: 'dara-num' },
                          { title: 'Notes', dataIndex: 'notes', width: 220, render: notes => (notes.length ? <Typography.Text type="warning" title={notes.join('\n')}>{notes[0]}{notes.length > 1 ? ` (+${notes.length - 1})` : ''}</Typography.Text> : '—') },
                        ]} />
                      <Typography.Text type="secondary">
                        Files are analysed independently with identical settings and the listed elements; the variable is parsed from the file names for plotting only.
                      </Typography.Text>
                    </div>
                  ) : null}
                </>
              ) : (
                <Form.Item label={cloneSource ? 'Replacement pattern (optional)' : 'XRD pattern'} name="pattern_file" rules={[{ required: !cloneSource, message: 'Please upload an XRD pattern.' }]}
                  tooltip="XRDML is recommended: its acquisition metadata can select the closest instrument profile and wavelength automatically.">
                  {watchedPattern ? (
                    // After a file is chosen the drop zone gives way to the file and its scan.
                    <div className="dara-file-row">
                      <FileOutlined aria-hidden="true" />
                      <span className="name" title={watchedPattern.name}>{watchedPattern.name}</span>
                      {Number.isFinite(uploadFile(watchedPattern)?.size) ? <span className="meta">{`${Math.max(1, Math.round(uploadFile(watchedPattern).size / 1024)).toLocaleString()} KB`}</span> : null}
                      <Upload showUploadList={false} beforeUpload={() => false} onChange={inspectPattern} accept={PATTERN_ACCEPT}>
                        <Button size="small">Change…</Button>
                      </Upload>
                      <Button size="small" onClick={removePattern}>Remove</Button>
                    </div>
                  ) : (
                    <Dragger maxCount={1} beforeUpload={() => false} onChange={inspectPattern} accept={PATTERN_ACCEPT}>
                      <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                      <p className="ant-upload-text">{cloneSource ? 'Select a replacement file (optional)' : 'Start by selecting the measured XRD file'}</p>
                      <p className="ant-upload-hint">xrdml (metadata-aware) / xy(e) / txt / csv / dat / asc / ras / rasx / brml / raw</p>
                    </Dragger>
                  )}
                </Form.Item>
              )}
              {!seriesMode && watchedPattern && patternInspection?.preview ? <div style={{ margin: '-8px 0 16px' }}>
                <PatternPreview points={patternInspection.preview} start={watchedThetaMin} end={watchedThetaMax} excluded={watchedExcluded} />
              </div> : null}
              {inspectingPattern ? (
                <Alert type="info" showIcon message="Reading pattern and acquisition metadata..." />
              ) : patternInspection?.error ? (
                <Alert type="error" showIcon message={seriesMode ? 'First file could not be read' : 'Pattern could not be read'} description={patternInspection.error} />
              ) : patternInspection ? (
                <Alert
                  type={patternInspection.profile_suggestion ? 'success' : 'warning'}
                  showIcon
                  message={(seriesMode ? 'Inspection of the first file: ' : '') + (patternInspection.profile_suggestion
                    ? `Instrument settings applied: ${patternInspection.profile_suggestion.profile}`
                    : 'Pattern loaded; select the instrument profile manually')}
                  description={[
                    patternInspection.metadata?.instrument_system || patternInspection.metadata?.instrument_control_software,
                    patternInspection.metadata?.detector,
                    patternInspection.recommended_wavelength ? `${patternInspection.recommended_wavelength} radiation` : null,
                    Number.isFinite(patternInspection.two_theta_min) && Number.isFinite(patternInspection.two_theta_max)
                      ? `2θ ${patternInspection.two_theta_min.toFixed(2)}–${patternInspection.two_theta_max.toFixed(2)}°`
                      : null,
                    patternInspection.data_points ? `${patternInspection.data_points.toLocaleString()} points` : null,
                    qualityStatText(patternInspection.quality_stats),
                    patternInspection.profile_suggestion?.reason,
                    patternInspection.profile_suggestion ? 'Confirm the selection if the actual slit or optical setup was different.' : null,
                  ].filter(Boolean).join(' · ')}
                />
              ) : null}
              {patternInspection && !patternInspection.error && qualityAlertItems(patternInspection).length ? (
                <div className="dara-note-group" style={{ marginBottom: 16 }}>
                  <div className="dara-subhead">Data quality pre-check (advisory)</div>
                  {qualityAlertItems(patternInspection).map((item, index) => (
                    <Alert key={index} type={item.type} showIcon message={item.message} />
                  ))}
                </div>
              ) : null}
              {fileChemistry && !seriesMode ? (
                <Alert type="info" showIcon
                  message={fileChemistry.elements.length
                    ? `Elements recognised from the file name: ${fileChemistry.elements.join(', ')}${unrecognised.length ? ` · not recognised: ${unrecognised.join(', ')}` : ''}`
                    : `No unambiguous chemical formula in the file name${unrecognised.length ? ` (not recognised: ${unrecognised.join(', ')})` : ''}. Enter the elements.`}
                  description={fileChemistry.elements.length
                    ? `Formulas: ${fileChemistry.formulas.join(', ')}. ${patternInspection.elementsAutoApplied ? 'Added to Elements.' : 'Existing element entries were kept.'} ${
                      patternInspection.formulasAutoApplied ? `Added to ${FORMULA_LABELS[analysisMode] || 'the formulas'}.` : 'The formula list was kept.'} ${
                      unrecognised.length ? `${unrecognised.join(', ')} ${unrecognised.length === 1 ? 'was' : 'were'} not read as chemistry: add any element they stand for. ` : ''}A suggestion from the file name only; compare it with the actual sample. Temperatures, times and single-letter symbols are never filled in.`
                    : 'Example: BiFeO3_800C_60min.xrdml → Bi, Fe, O. Check single-letter symbols such as C or S and ambiguous names before entering them.'}
                />
              ) : null}
            </Card>

            <Card name="chemistry" title="Chemistry">
              <Form.Item label="Analysis mode" name="analysis_mode">
                <Radio.Group className="dara-mode-radio" optionType="button" buttonStyle="solid" disabled={Boolean(phaseSet)}
                  options={Object.entries(MODE_LABELS).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
              <Alert type={analysisMode === 'known_mixture' ? 'warning' : 'info'} showIcon message={modeMessage} />
              <div className="dara-field-row wide-cells">
                <Form.Item label="Elements" name="elements" dependencies={['precursor_formulas']}
                  rules={[
                    { validator: (_, value) => (checkElements(value).error ? Promise.reject(new Error(checkElements(value).error)) : Promise.resolve()) },
                    // Search-match and Phase search + Rietveld take the same input: elements, or formulas to derive them from.
                    ({ getFieldValue }) => ({ validator: (_, value) => (getFieldValue('analysis_mode') === 'elemental' && !checkElements(value).symbols.length
                      && !splitList(getFieldValue('precursor_formulas')).length ? Promise.reject(new Error('Enter the elements, or the expected formulas.')) : Promise.resolve()) }),
                  ]}
                  tooltip="Symbols separated by commas or spaces, e.g. In, La, Ti, O; case is corrected where the symbol is unambiguous. Left empty, they are derived from the formulas."
                  extra={recentElements.length ? <span className="dara-recent">Recent:{recentElements.slice(0, 5).map(set => (
                    <Button key={set.join()} type="link" size="small" onClick={() => {
                      form.setFieldsValue({ elements: set.join(', ') });
                      autoElements.current = null;
                      form.validateFields(['elements']).catch(() => {});
                    }}>{set.join(', ')}</Button>))}</span> : null}>
                  <Input placeholder="In, La, Ti, O" onBlur={() => {
                    const value = form.getFieldValue('elements') || '';
                    const { text } = checkElements(value);
                    if (value.trim() && text !== value) form.setFieldValue('elements', text);
                  }} />
                </Form.Item>
                {phaseSet ? (
                  <Form.Item label="Phases to refine">
                    {phaseSet.error
                      ? <Alert type="error" showIcon message={phaseSet.error} />
                      : <Alert type="info" showIcon
                        message={phaseSet.phases ? phaseSetLabel(phaseSet.id, phaseSet.phases) : `Loading the phases of Task ${phaseSet.id}...`}
                        description={phaseSet.stored
                          ? 'Exactly the structures stored with this task are refined, with no new search of the databases.'
                          : 'Exactly these database structures are refined in every file, with no new search of the databases.'} />}
                    <a onClick={clearPhaseSet}>Enter formulas instead</a>
                  </Form.Item>
                ) : (
                  <Form.Item label={FORMULA_LABELS[analysisMode] || 'Expected formulas'}
                    name="precursor_formulas" rules={[{ required: analysisMode !== 'elemental', message: 'Please input chemical formulas.' }]}
                    tooltip="In Phase search + Rietveld this is optional, but listed phases are guaranteed candidates. Optional amounts enable the mass-balance check: a molar coefficient before the formula (Fe2O3, 2 TiO2) or a weighed mass after a colon (In2O3:0.3022g, TiO2:0.2004g)."
                    extra={offeredFormulas ? <span className="dara-recent">From the file name:
                      <Button type="link" size="small" onClick={() => {
                        form.setFieldsValue({ precursor_formulas: offeredFormulas.join(', ') });
                        form.validateFields(['precursor_formulas', 'elements']).catch(() => {});
                      }}>{`Use ${offeredFormulas.join(', ')}`}</Button></span> : null}>
                    <Input placeholder="In2O3, La(OH)3, TiO2" />
                  </Form.Item>
                )}
              </div>
              <div className="dara-field-row wide-cells">
                <Form.Item label="Temperature (°C)" name="temperature" hidden={analysisMode !== 'reaction'}
                  rules={[{ required: analysisMode === 'reaction' }]}>
                  <InputNumber min={27} max={1727} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item label="Atmosphere" name="atmosphere" hidden={analysisMode !== 'reaction'}>
                  <Select options={[
                    { value: 'air', label: 'Air (open O₂, pO₂ = 0.21 atm)' },
                    { value: 'oxygen', label: 'Oxygen (open O₂, standard state)' },
                    { value: 'inert', label: 'Inert / closed system' },
                    { value: 'vacuum', label: 'Vacuum / closed system' },
                    { value: 'reducing', label: 'Reducing / closed system' },
                  ]} />
                </Form.Item>
              </div>
              <button type="button" className="dara-advanced-toggle" style={{ paddingBottom: 8 }} aria-expanded={standardOpen} aria-controls="dara-standard-body"
                onClick={() => setStandardOpen(open => !open)}>
                <RightOutlined />
                <span>Internal standard (optional)</span>
                <span className="note">For absolute phase amounts</span>
              </button>
              <div id="dara-standard-body" hidden={!standardOpen}>
                <div className="dara-field-row wide-cells">
                  <Form.Item label="Standard formula" name="standard_formula"
                    tooltip="A crystalline phase you mixed into the sample, for example Si. Its elements are added to Elements."
                    rules={[({ getFieldValue }) => ({ validator: (_, value) => (getFieldValue('standard_wt') != null && !(value || '').trim()
                      ? Promise.reject(new Error('Enter the formula of the standard.')) : Promise.resolve()) })]}>
                    <AutoComplete placeholder="Si" allowClear options={COMMON_STANDARDS.map(value => ({ value }))} />
                  </Form.Item>
                  <Form.Item label="Added amount (wt% of the mixture)" name="standard_wt" dependencies={['standard_formula']}
                    rules={[({ getFieldValue }) => ({ validator: (_, value) => (!(getFieldValue('standard_formula') || '').trim() ? Promise.resolve()
                      : Number.isFinite(value) && value > 0 && value < 100 ? Promise.resolve()
                        : Promise.reject(new Error('Enter an amount between 0 and 100 wt%.'))) })]}>
                    <InputNumber min={0} max={100} step={0.5} style={{ width: '100%' }} placeholder="10" />
                  </Form.Item>
                </div>
              </div>
            </Card>

            <Card name="instrument" title="Instrument & range">
              <Form.Item label="Instrument profile"
                tooltip="For XRDML, DARA applies the closest bundled profile from the recorded instrument and detector. Always confirm unusual slit or optic setups.">
                <div className="dara-inline">
                  <div className="grow">
                    <Form.Item name="instrument_profile" noStyle>
                      <Select showSearch optionFilterProp="search" aria-label="Instrument profile" style={{ width: '100%' }}
                        options={instrumentNames.map(name => ({ value: name, search: instrumentLabel(name),
                          label: advanced && fwhmCurves[name] ? <>{instrumentLabel(name)} <span className="dara-dim">· FWHM curve {curveText(fwhmCurves[name])}</span></> : instrumentLabel(name) }))} />
                    </Form.Item>
                  </div>
                  <InstrumentManager buttonLabel="Register profile…" buttonSize="middle" onRegistered={profile => {
                    setInstrumentNames(previous => [...previous, profile.id]);
                    setInstrumentDetails(previous => ({ ...previous, [profile.id]: profile }));
                    if (profile.has_sav) setCalibratable(previous => [...previous, profile.id]);
                    form.setFieldsValue({ instrument_profile: profile.id });
                  }} />
                  <CalibrateProfile profile={selectedInstrument} enabled={calibratable.includes(selectedInstrument)}
                    wavelength={watchedWavelengthLab || 'Cu'} onCalibrated={(profile, curve) => {
                      if (curve?.usable) setFwhmCurves(previous => ({ ...previous, [profile.id]: curve }));
                      setInstrumentNames(previous => [...previous, profile.id]);
                      setInstrumentDetails(previous => ({ ...previous, [profile.id]: profile }));
                      setCalibratable(previous => [...previous, profile.id]);
                      form.setFieldsValue({ instrument_profile: profile.id });
                    }} />
                  {advanced && selectedInstrument && selectedInstrument !== 'synchrotron'
                    ? <FwhmCurveButton profile={selectedInstrument} profileLabel={instrumentLabel(selectedInstrument)}
                      wavelength={watchedWavelengthLab || 'Cu'}
                      onChange={curve => setFwhmCurves(previous => {
                        const { [selectedInstrument]: _, ...rest } = previous;
                        return curve ? { ...rest, [selectedInstrument]: curve } : rest;
                      })} /> : null}
                </div>
              </Form.Item>
              {selected ? <Alert type={selected.verified ? 'info' : 'warning'} showIcon
                message={`${selected.label} — ${selected.verified ? 'verification declared by ' + selected.verified_by
                  : selected.calibration ? `DetW fitted to a ${selected.calibration.standard} standard` : 'not verified against a standard'}`}
                description={<>{selected.geometry} · {selected.radiation}<br />{selected.notes}</>} /> : null}
              <div className="dara-field-row">
                {!synchrotron ? (
                  <Form.Item label="Radiation" name="wavelength_lab" rules={[{ required: true }]}
                    validateStatus={wavelengthWarning ? 'warning' : undefined}
                    help={wavelengthWarning ? `The file was recorded with ${wavelengthWarning.recorded}; you selected ${wavelengthWarning.chosen}.` : undefined}>
                    <Select options={WAVELENGTHS.map(item => ({ value: item[0], label: `${item[0]} Kα1 (${item[1].toFixed(4)} Å)` }))} />
                  </Form.Item>
                ) : (
                  <Form.Item label="Wavelength (Å)" name="wavelength_sync_a" rules={[{ required: true, pattern: /^\d+(\.\d+)?$/, message: 'Wavelength in Å, e.g. 0.7293' }]}>
                    <Input />
                  </Form.Item>
                )}
                <Form.Item label="Synchrotron" name="synchrotron" valuePropName="checked">
                  <Switch onChange={checked => {
                    setSynchrotron(checked);
                    if (checked) {
                      setPreviousInstrument(form.getFieldValue('instrument_profile'));
                      form.setFieldsValue({ instrument_profile: 'synchrotron' });
                    } else {
                      form.setFieldsValue({ instrument_profile: previousInstrument });
                    }
                  }} />
                </Form.Item>
              </div>
              <div className="dara-field-row wide-cells">
                <Form.Item label="Analysis range · 2θ (°)" tooltip="Leave blank to use the measured range. Explicit limits apply to search and final refinement alike.">
                  <Space>
                    <Form.Item name="two_theta_min" noStyle><InputNumber min={0} max={180} placeholder="Start" aria-label="Analysis start angle" style={{ width: 96 }} /></Form.Item>
                    <span>to</span>
                    <Form.Item name="two_theta_max" noStyle><InputNumber min={0} max={180} placeholder="End" aria-label="Analysis end angle" style={{ width: 96 }} /></Form.Item>
                  </Space>
                </Form.Item>
                <Form.Item label="Excluded intervals · 2θ (°)" tooltip="Exclude confirmed holder/artifact regions only. These intervals are omitted from fitting and peak diagnostics; measured intensities are preserved.">
                  <Form.List name="excluded_ranges">
                    {(fields, { add, remove }) => (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        {fields.map(field => (
                          <Space key={field.key} align="start">
                            <Form.Item name={[field.name, 0]} rules={[{ required: true, message: 'Start angle required' }]} style={{ marginBottom: 0 }}>
                              <InputNumber min={0} max={180} placeholder="Start" aria-label="Excluded interval start" style={{ width: 96 }} />
                            </Form.Item>
                            <span style={{ lineHeight: '32px' }}>to</span>
                            <Form.Item name={[field.name, 1]} rules={[{ required: true, message: 'End angle required' }]} style={{ marginBottom: 0 }}>
                              <InputNumber min={0} max={180} placeholder="End" aria-label="Excluded interval end" style={{ width: 96 }} />
                            </Form.Item>
                            <Button type="text" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} aria-label="Remove excluded interval" />
                          </Space>
                        ))}
                        <Button type="dashed" icon={<PlusOutlined />} onClick={() => add([null, null])}>Add excluded interval</Button>
                      </Space>
                    )}
                  </Form.List>
                </Form.Item>
              </div>
            </Card>
          </div>

          <div className="dara-submit-side">
            <Card name="run" title="Run">
              <dl className="dara-run-summary">
                <dt>Pattern</dt><dd>{patternSummary}</dd>
                <dt>Mode</dt><dd>{MODE_LABELS[analysisMode] || analysisMode}</dd>
                <dt>Elements</dt><dd>{elementCheck.error ? <span className="invalid">{elementCheck.error}</span>
                  : elementSummary || missing(formulaSummary || analysisMode !== 'elemental' ? 'From the formulas' : 'Not entered')}</dd>
                {formulaSummary ? <><dt>{FORMULA_LABELS[analysisMode] || 'Formulas'}</dt><dd>{formulaSummary}</dd></> : null}
                <dt>Instrument</dt><dd>{selectedInstrument ? instrumentLabel(selectedInstrument) : missing('Not selected')}</dd>
                <dt>Radiation</dt><dd>{wavelengthSummary} · {rangeSummary}</dd>
              </dl>
              {estimate || estimating
                ? <EstimateAlert estimate={estimate} loading={estimating} prefilter={watchedPrefilter !== false} />
                : <p className="dara-run-hint">{elementCheck.error ? `Fix the elements to see the estimate: ${elementCheck.error}.`
                  : estimateFailed ? 'The candidate and run-time estimate is unavailable.'
                    : `${analysisMode === 'elemental' ? 'Enter elements or formulas' : 'Enter formulas'} to see the candidate and run-time estimate.`}</p>}
              {seriesMode ? null : <QuickRankingSummary quick={quick} />}
              {quick.shown ? null : msgAlert}
              {/* Two ways to run, HighScore's order: search-match first (seconds, the primary action),
                  then the full automatic analysis. Each label carries its time cost. */}
              <div className={`dara-run-actions${seriesMode ? ' single' : ''}`}>
                {seriesMode ? null : <div>
                  <Button type="primary" size="large" block onClick={openSearchMatch} disabled={!quickReady}>
                    {quick.ranked ? 'Open search-match' : <>Search-match<span className="cost">· seconds</span></>}
                  </Button>
                  <p className="dara-run-action-note">{quickNote}</p>
                </div>}
                <div>
                  <Button type={seriesMode ? 'primary' : 'default'} size="large" block onClick={() => onSubmit()} loading={spinning}
                    // Nothing to analyse yet: no pattern (a rerun reuses the original one) and no series files.
                    disabled={spinning || loadingClone || (phaseSet && !phaseSet.phases) || (seriesMode ? !seriesCount : !watchedPattern && !cloneSource)}>
                    {seriesMode ? `Run series${seriesCount ? ` (${seriesCount} files)` : ''}` : 'Run analysis'}
                    {runtimeText ? <span className="cost">{`· ${runtimeText}`}</span> : null}
                  </Button>
                  <p className="dara-run-action-note">{MODE_LABELS[analysisMode]}, automatic</p>
                </div>
              </div>
              {submittedTasks.length ? <ul className="dara-run-submitted" aria-label="Submitted from this page">
                {submittedTasks.map(task => <li key={task.id}>
                  Submitted: <a href={`/task/?id=${task.id}`}>{`Task ${task.id}`}</a>{task.name ? ` · ${task.name}` : ''}
                </li>)}
              </ul> : null}
            </Card>

            <section className="dara-card dara-card-advanced" aria-label="Advanced options">
              <button type="button" className="dara-advanced-toggle" aria-expanded={advancedOpen} aria-controls="dara-advanced-body"
                onClick={() => setAdvancedOpen(open => !open)}>
                <RightOutlined />
                <h2 className="dara-card-title">Advanced</h2>
                {hasAdvancedChanges
                  ? <Tag color="blue" style={{ marginLeft: 'auto', marginRight: 0 }} title={changedAdvanced.join(', ')}>{changedAdvanced.length} changed</Tag>
                  : <span className="note">Databases, limits, refinement, extra CIFs</span>}
              </button>
              <div id="dara-advanced-body" className="dara-advanced-body" hidden={!advancedOpen}>
                <Form.Item label="Reference databases" tooltip="COD: experimental structures. MP: computed structures (Materials Project), used as a supplement.">
                  <Space wrap size={[16, 8]}>
                    <span><Form.Item name="use_cod" valuePropName="checked" noStyle>
                      <Switch checkedChildren="COD" unCheckedChildren="COD" />
                    </Form.Item><span style={{ marginLeft: 8 }}>experimental</span></span>
                    <span><Form.Item name="use_mp" valuePropName="checked" noStyle>
                      <Switch checkedChildren="MP" unCheckedChildren="MP" />
                    </Form.Item><span style={{ marginLeft: 8 }}>computed supplement</span></span>
                    <MpKeyStatus />
                    <StructureLibrary />
                  </Space>
                </Form.Item>
                <div className="dara-field-row">
                  <Form.Item label="MP E-hull cutoff (eV/atom)" name="mp_e_hull_cutoff" tooltip="eV/atom; lower values retain more plausible MP structures.">
                    <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Maximum MP structures" name="max_mp_structures">
                    <InputNumber min={1} max={1000} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Reference screening limit (s)" name="screening_timeout_s"
                    tooltip="Maximum BGMN time per reference during screening; very large structures get twice this (at most 1200 s). Leave blank for the original 1200 s.">
                    <InputNumber min={60} max={1200} step={30} placeholder="1200" style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Maximum analysis time (min)" name="max_analysis_minutes"
                    tooltip="When exceeded, the search stops and returns the best result found so far, marked as incomplete. Leave blank for no limit.">
                    <InputNumber min={5} max={720} placeholder="No limit" style={{ width: '100%' }} />
                  </Form.Item>
                </div>
                <Form.Item label="Candidate pre-filter" name="candidate_prefilter" valuePropName="checked"
                  tooltip="Skip references whose strongest calculated peaks are absent from the pattern. Uploaded, library and precursor phases are never skipped.">
                  <Switch />
                </Form.Item>
                <RefinementControlsEditor bare value={refinementControls} onChange={setRefinementControls} />
                {cloneSource?.additional_cif_count > 0 ? (
                  <Form.Item label={`Reuse ${cloneSource.additional_cif_count} saved reference CIFs`} name="reuse_additional_cifs" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                ) : null}
                <ReferenceLibraryField library={library} />
                <Form.Item label="Additional reference CIFs" name="additional_phases" valuePropName="fileList" getValueFromEvent={event => event?.fileList}>
                  <Upload beforeUpload={() => false} multiple accept=".cif">
                    <Button icon={<UploadOutlined />}>Select CIF files</Button>
                  </Upload>
                </Form.Item>
              </div>
            </section>
          </div>
        </Form>
      </Container>
    </Layout>
  );
}

export default Submission;
