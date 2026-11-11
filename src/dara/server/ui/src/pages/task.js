import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, Badge, Button, Col, Descriptions, Divider, Dropdown, Grid, List, message, Modal, Row, Select, Space, Spin, Switch, Table, Tabs, Tag, Tooltip, Typography } from 'antd';
import { ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, DownOutlined, DownloadOutlined, EllipsisOutlined, InfoCircleOutlined, LineChartOutlined, SearchOutlined } from '@ant-design/icons';
import Loadable from 'react-loadable'
import parse from 'html-react-parser';
import { gofTone, peakCountTone, toneColor } from '../components/statusTone.mjs';
import { buildRefinementView, exclusionViewRange, isTraceSelected, legendKey } from '../components/refinementPlotState.mjs';
import { viewChangeFromRelayout } from '../components/plotNavigation.mjs';
import { NAVIGATION_CONFIG, NAVIGATION_DRAGMODE } from '../components/usePlotView';
import { formatParameterWithEsd, latticeParameterRows, latticeStateNote } from '../components/latticeParameters.mjs';
import { crystalliteSizeCell, microstrainCell } from '../components/crystalliteSize.mjs';
import { amountTag } from '../components/statusTags.mjs';
import { knownPhaseNotes, mpFallbackNotes, phaseSetUrl } from '../components/knownPhases.mjs';
import StatusTag from '../components/StatusTag';
import { PhaseSelectionButton, ReviewPanel, TaskFailurePanel, useTaskReview } from '../components/ResultWorkflows';
import { TaskProgress } from '../components/PracticalControls';
import ChemicalFormula from '../components/ChemicalFormula';
import FitCautions from '../components/FitCautionsPanel';
import { cautionSortValue, cautionTagColor, cautionTooltip, countCautions } from '../components/fitCautions.mjs';
import { levelRank, missingCell, unexplainedSummary, unexplainedTooltipLines, reasonsText } from '../components/confidence.mjs';
import { ConfidenceBanner, DisplacementTag, DownloadMenu, OverallTag, ScreeningTags, StateTag, VerdictTag } from '../components/ConfidenceViews';
import VersionsPanel, { useVersions } from '../components/VersionsPanel';
import QuickRefineProgress, { API, NOT_AVAILABLE, postJson, requestJson, useQuickRefine } from '../components/QuickRefineProgress';
import PeakCandidatesDrawer, { DRAWER_WIDTH } from '../components/PeakCandidatesDrawer';
import FitCompare from '../components/FitCompare';
import StructureRefineDrawer, { StructureResults } from '../components/StructureRefineDrawer';
import { structurePhases } from '../components/structureModel.mjs';
import PeakListTab from '../components/PeakListTab';
import InternalStandardCard from '../components/InternalStandardCard';
import BackgroundEditor from '../components/BackgroundEditor';
import useKeyboardShortcuts, { FIT_MODAL_CLASS, HELP_MODAL_CLASS } from '../components/useKeyboardShortcuts';
import { MOUSE_HELP, SHORTCUT_HELP } from '../components/keyboardShortcuts.mjs';
import {
  collectHints, hintsPending, observedPeak, peakFromPlotClick, peakShapes, phaseGroupNames, previewTrace, significantPeaks, updatePeakSelection,
} from '../components/peakSelection.mjs';
import { noFitHints, searchOptionsText } from '../components/resultsList.mjs';
import { colorPhaseTraces, compositionSegments, fillIndex, tidyFormula, trayLine } from '../components/composition.mjs';
import {
  BASELINE_LABEL, QUICK_CHECK_HINT, REFINE_ALL_FIRST, addedLabel, alternativeLink, alternativeOfVerdict, availableComparisonBase, baselineRequest, comparisonBase,
  deltaBaseText, deltaStrip, deltaText, displayedCandidateOf, fitFromSearch, phaseSymbolLookup, searchWithFit,
  formulaOfPhase, isQuickCheck, knownDisplayFormula, needsBaseline, parseSelection, plotQuery, refineAllRequest, relabelPhaseTraces,
  rememberDisplayFormulas, removeRequest,
  referenceVariants, replacementOf, runText, selectOptionGroups, selectionLabel, spacegroupOfPhase, versionGone, versionPhaseOptions, versionReadyText,
  weightSpread, NEAR_RWP_RATIO, phaseRecordLine } from '../components/versions.mjs';

const { Text } = Typography;
const HINT_POLL_MS = 3000;
const HINT_POLL_MAX = 60; // 3 min: right after a server start the COD pool took ~2 min

const URL = API;

async function downloadResultArchive(taskId, resultIndex) {
  const query = resultIndex === undefined ? '' : `?idx=${resultIndex}`;
  const response = await fetch(`${URL}/task/${taskId}/download${query}`);
  if (!response.ok) {
    let detail = `Download failed (HTTP ${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail);
  }

  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const filename = filenameMatch?.[1] || `dara_task_${taskId}.zip`;
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function DownloadButton({ taskId, resultIndex, children, ...buttonProps }) {
  const [loading, setLoading] = React.useState(false);
  const download = async () => {
    setLoading(true);
    try {
      await downloadResultArchive(taskId, resultIndex);
      message.success('Result ZIP downloaded.');
    } catch (error) {
      message.error(error.message || 'Could not download the result.');
    } finally {
      setLoading(false);
    }
  };
  return <Button {...buttonProps} loading={loading} onClick={download}>{children}</Button>;
}

const Container = styled.div`
& code {
  background-color: rgba(0, 0, 0, 0.1);
  border-radius: var(--dara-radius);
  padding: .2em .4em;
  font-size: 85%;
  text-align: center;
}

.no-highlight {
  color: var(--dara-text-secondary);
  background-color: rgba(0, 0, 0, 0.03);
}

.highlight {
  color: black;
}

.representative-phase {
  font-weight: bold;
  color: black;
  font-size: 1em;
  border-radius: var(--dara-radius);
  border: 1px solid var(--dara-text-secondary);
  padding: .2em .4em;
}

.phase-identity {
  display: inline-flex;
  flex-direction: column;
  line-height: 1.35;
}

.phase-identity-primary {
  color: var(--dara-text);
  font-weight: 600;
}

.phase-identity-secondary {
  color: var(--dara-text-secondary);
  font-size: 0.86em;
  font-weight: normal;
}

.phases-container {
  line-height: 2;
}

.error-code {
  background-color: var(--dara-surface-sunken);
  padding: 10px;
  border-radius: var(--dara-radius);
  border: 1px solid var(--dara-border-strong);
  color: var(--dara-text);
  overflow-x: scroll;
}
`

const SpinContainer = styled.div`
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  width: 100%;
`

const TitleContriner = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
`

const ResultTitleContainer = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
`

const Title = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
`


const Plotly = Loadable({
  loader: () => import('../components/RefinementPlotly'),
  loading: ({ timedOut }) =>
    timedOut ? (
      <blockquote>Error: Loading Plotly timed out.</blockquote>
    ) : (
      <Spin />
    ),
  timeout: 10000,
})

const plotRequests = new Map();

function plotUrl(taskId, plotId) {
  return `${URL}/task/${taskId}/plot${plotQuery(plotId === null ? 'best' : plotId)}`;
}

function loadPlot(taskId, plotId) {
  const requestUrl = plotUrl(taskId, plotId);
  if (!plotRequests.has(requestUrl)) {
    const request = fetch(requestUrl)
      .then(response => {
        if (!response.ok) throw new Error(`Could not load plot (HTTP ${response.status}).`);
        return response.json();
      })
      // Older servers returned a JSON string. Keep compatibility while the
      // direct JSON response rolls out.
      .then(response => typeof response === 'string' ? JSON.parse(response) : response)
      .catch(error => {
        plotRequests.delete(requestUrl);
        throw error;
      });
    // Bound retained plot data when an operator reviews many tasks in one tab.
    if (plotRequests.size >= 24) plotRequests.delete(plotRequests.keys().next().value);
    plotRequests.set(requestUrl, request);
  }
  return plotRequests.get(requestUrl);
}

function preloadPlot(taskId, plotId) {
  Plotly.preload();
  if (taskId !== undefined && plotId !== undefined) loadPlot(taskId, plotId).catch(() => {});
}


const RefinementPlot = React.forwardRef(function RefinementPlot({
  task_id, plot_id, excludedRanges = [], peaks = [], onPeakClick, previewSticks = null, onGroups, title = null, phaseColor = null, phaseLegend = null,
  // Leaves the Phases / Peaks / Structure tabs in view under the workspace plot on a 900 px screen.
  height = 'clamp(440px, 58vh, 1000px)', compact = false,
}, ref) {
  const [plot, setPlot] = React.useState(undefined);
  const [error, setError] = React.useState('');
  const [scale, setScale] = React.useState('linear');
  const [showDiagnostics, setShowDiagnostics] = React.useState(true);
  const [showPhaseProfiles, setShowPhaseProfiles] = React.useState(false);
  const [showReflections, setShowReflections] = React.useState(true);
  const [resetKey, setResetKey] = React.useState(0);
  const [selection, setSelection] = React.useState({});
  const [yRange, setYRange] = React.useState(null);
  const [xRange, setXRange] = React.useState(null);
  const [fitY, setFitY] = React.useState(true);
  const lastLegendKey = React.useRef(null);
  const sameLegendClicked = React.useRef(false);
  // Cursor readout written straight to the DOM, so hovering never re-renders the plot.
  const readoutRef = React.useRef(null);
  // Width of the plot: below COMPACT_LEGEND_WIDTH the legend column is made narrower.
  const [plotWidth, setPlotWidth] = React.useState(null);
  const containerRef = React.useRef(null);
  React.useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const measure = () => setPlotWidth(Math.round(element.getBoundingClientRect().width) || null);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [plot]);
  const resetView = () => { setXRange(null); setYRange(null); setResetKey(value => value + 1); };
  // Keyboard shortcuts act through this handle; it always reads the latest state.
  const latest = React.useRef({});
  latest.current = { plot, xRange };
  React.useImperativeHandle(ref, () => ({
    reset: () => { setXRange(null); setYRange(null); setResetKey(value => value + 1); },
    toggleFitY: () => { setFitY(value => !value); setYRange(null); },
    togglePhase: name => {
      const current = latest.current.plot;
      const index = (current?.data || []).findIndex(trace => trace.legendgroup === name && trace.meta === 'phase-profile');
      if (index < 0) return;
      const trace = current.data[index];
      const key = legendKey(trace, index);
      setSelection(previous => ({ ...previous, [key]: !isTraceSelected(trace, index, previous) }));
      setYRange(null);
    },
    // Only a real zoom: the unzoomed range would exclude the whole pattern.
    getViewRange: () => exclusionViewRange(latest.current.xRange, latest.current.plot?.layout?.xaxis?.range),
  }), []);
  React.useEffect(() => {
    setPlot(undefined);
    setError('');
    setSelection({});
    setYRange(null);
    setXRange(null);
    if (onGroups) onGroups([]);
    if (task_id === undefined || plot_id === undefined) return;
    let active = true;
    loadPlot(task_id, plot_id)
      .then(response => { if (active) { setPlot(response); if (onGroups) onGroups(phaseGroupNames(response)); } })
      .catch(error => { if (active) setError(error.message); });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task_id, plot_id]);
  if (error) return <Alert type="error" showIcon message={error} />;
  if (!plot) return <Spin />;
  // Legend formulas from the CIFs (NH4H2PO4, not the file stem's PH6NO4); trace order is unchanged.
  const view = buildRefinementView({ ...plot, data: (phaseLegend || (d => d))(colorPhaseTraces(relabelPhaseTraces(plot.data), phaseColor)) }, {
    selection, showDiagnostics, showPhaseProfiles, showReflections, scale, yRange, xRange, fitY, plotWidth, title,
  });
  const toggleLegend = event => {
    const index = event.curveNumber;
    const trace = plot.data[index];
    const key = legendKey(trace, index);
    sameLegendClicked.current = lastLegendKey.current === key;
    lastLegendKey.current = key;
    setSelection(previous => ({ ...previous,
      [legendKey(trace, index)]: !isTraceSelected(trace, index, previous) }));
    setYRange(null);
    return false;
  };
  const isolateLegend = event => {
    // Plotly can count quick clicks on different legend items as a double
    // click. Only isolate when both clicks really targeted the same item.
    if (!sameLegendClicked.current) return false;
    const selectedKey = legendKey(plot.data[event.curveNumber], event.curveNumber);
    setSelection(Object.fromEntries(plot.data.map((trace, index) => [legendKey(trace, index),
      trace.legendgroup ? legendKey(trace, index) === selectedKey : isTraceSelected(trace, index, selection)])));
    setYRange(null);
    return false;
  };
  const handleClick = event => {
    if (!onPeakClick) return;
    const peak = peakFromPlotClick(plot, event);
    if (peak !== null) onPeakClick(peak, Boolean(event.event?.shiftKey));
  };
  const wavelength = Number(plot.layout?.meta?.wavelength_angstrom);
  const showReadout = event => {
    const point = event?.points?.[0];
    const element = readoutRef.current;
    if (!point || !element || !Number.isFinite(Number(point.x))) return;
    const twoTheta = Number(point.x);
    const d = wavelength > 0 ? wavelength / (2 * Math.sin((twoTheta * Math.PI) / 360)) : null;
    const set = (key, text) => { const cell = element.querySelector(`[data-k="${key}"]`); if (cell) cell.textContent = text; };
    element.classList.add('dara-readout-live');
    set('x', twoTheta.toFixed(3));
    set('d', Number.isFinite(d) ? d.toFixed(4) : '–');
    set('n', point.data?.name ? `${point.data.name}` : 'I');
    set('i', Number.isFinite(Number(point.y)) ? Math.round(Number(point.y)).toLocaleString('en-US') : '–');
  };
  const data = previewSticks?.length
    ? [...view.data, previewTrace(previewSticks, observedPeak(plot) * 0.8, { floor: scale === 'log' ? 1 : 0 })]
    : view.data;
  return <>
    {/* The page preview draws the fit only; the workspace has the controls. */}
    {compact ? null : <Space wrap style={{ marginBottom: 8 }}>
      <Text>Scale</Text>
      <Select value={scale} onChange={value => { setScale(value); setYRange(null); }} style={{ width: 90 }}
        options={[{ value: 'linear', label: 'Linear' }, { value: 'log', label: 'Log' }]} />
      <Tooltip title="Phase profiles"><Text>Profiles</Text></Tooltip><Switch aria-label="Phase profiles" checked={showPhaseProfiles} onChange={setShowPhaseProfiles} />
      <Tooltip title="Bragg positions"><Text>Bragg</Text></Tooltip><Switch aria-label="Bragg positions" checked={showReflections} onChange={value => { setShowReflections(value); setYRange(null); }} />
      <Tooltip title="Unexplained-peak and diagnostic markers"><Text>Markers</Text></Tooltip><Switch aria-label="Peak markers" checked={showDiagnostics} onChange={setShowDiagnostics} />
      <Tooltip title="Fit the intensity axis to the visible 2θ range (F)"><Text>Auto-Y</Text></Tooltip><Switch aria-label="Auto-Y" checked={fitY} onChange={value => { setFitY(value); setYRange(null); }} />
      <Button onClick={resetView}>Reset zoom</Button>
      <Tooltip title={`Left-drag to zoom into a box (2θ only while Auto-Y is on), Shift- or right-drag to pan 2θ, mouse wheel to zoom 2θ at the cursor, double-click or Reset zoom to show the whole pattern. Zoom and pan stop at the measured range. Click a phase in the legend to show its profile and Bragg positions; hover for 2θ, d-spacing and hkl. The lower panel shows observed − calculated.${onPeakClick ? ' Click a missing-peak marker or an observed point to search references for that peak (Shift+click to select several).' : ''}`}>
        <InfoCircleOutlined aria-label="Plot navigation help" style={{ color: 'var(--dara-text-hint)' }} />
      </Tooltip>
    </Space>}
    <div ref={readoutRef} className="dara-readout" aria-label="Cursor position">
      <span className="dara-readout-hint">Hover the plot to read 2θ, d and intensity</span>
      <span className="dara-readout-value">2θ <b data-k="x">–</b>°</span>
      {wavelength > 0 ? <span className="dara-readout-value">d <b data-k="d">–</b> Å</span> : null}
      <span className="dara-readout-value"><span data-k="n">I</span> <b data-k="i">–</b></span>
    </div>
    <div ref={containerRef} style={{ width: '100%' }}>
    <Plotly key={`${task_id}-${plot_id}`} data={data} xBounds={view.fullXRange}
      layout={{ ...view.layout, autosize: true,
        shapes: [...(view.layout.shapes || []), ...excludedRanges.flatMap(([x0, x1]) => [
          { type: 'rect', xref: 'x', yref: 'y domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(130,130,130,0.15)', line: { width: 0 }, layer: 'below' },
          { type: 'rect', xref: 'x2', yref: 'y2 domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(130,130,130,0.15)', line: { width: 0 }, layer: 'below' },
        ]), ...peakShapes(peaks)],
        dragmode: NAVIGATION_DRAGMODE,
        uirevision: `${task_id}-${plot_id}-${resetKey}` }}
      onLegendClick={toggleLegend}
      onLegendDoubleClick={isolateLegend}
      onDoubleClick={resetView}
      onClick={handleClick}
      onHover={showReadout}
      onRelayout={event => {
        const change = viewChangeFromRelayout(event, { full: view.fullXRange });
        if ('xRange' in change) setXRange(change.xRange);
        // A y box (Auto-Y off) becomes the data window; the bands are drawn below it.
        if ('yRange' in change) setYRange(change.yRange);
      }}
      // Ranges are React state: Plotly's own reset/autoscale buttons would only
      // restore the state they were last given, so Reset zoom replaces them.
      config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG,
        toImageButtonOptions: { format: 'svg', filename: `dara_${task_id}_${String(plot_id ?? 'best').replace(':', '_')}`, width: 1600, height: 900 } }}
      useResizeHandler style={{ height, width: '100%' }} />
    </div>
  </>;
});

const COMMON_PHASES = {
  'Al2O3:167': { name: 'Corundum', structure: 'Trigonal' },
  'CaCO3:62': { name: 'Aragonite', structure: 'Orthorhombic' },
  'CaCO3:167': { name: 'Calcite', structure: 'Trigonal' },
  'CaF2:225': { name: 'Fluorite', structure: 'Cubic' },
  'Cr2O3:167': { name: 'Eskolaite', structure: 'Trigonal' },
  'Fe2O3:167': { name: 'Hematite', structure: 'Trigonal' },
  'Fe3O4:227': { name: 'Magnetite', structure: 'Cubic' },
  'Fe:225': { name: 'Austenite (γ-Fe)', structure: 'FCC' },
  'Fe:229': { name: 'Ferrite (α-Fe)', structure: 'BCC' },
  'In2O3:206': { name: 'Bixbyite-type', structure: 'Cubic' },
  'Mg(OH)2:164': { name: 'Brucite', structure: 'Trigonal' },
  'TiO2:61': { name: 'Brookite', structure: 'Orthorhombic' },
  'TiO2:136': { name: 'Rutile', structure: 'Tetragonal' },
  'TiO2:141': { name: 'Anatase', structure: 'Tetragonal' },
  'ZnO:186': { name: 'Zincite', structure: 'Hexagonal' },
};

function crystalSystem(spaceGroup) {
  if (!spaceGroup) return undefined;
  if (spaceGroup <= 2) return 'Triclinic';
  if (spaceGroup <= 15) return 'Monoclinic';
  if (spaceGroup <= 74) return 'Orthorhombic';
  if (spaceGroup <= 142) return 'Tetragonal';
  if (spaceGroup <= 167) return 'Trigonal';
  if (spaceGroup <= 194) return 'Hexagonal';
  return 'Cubic';
}

function parsePhaseReference(reference = '') {
  const match = reference.match(/^(.+?)_(\d+)_\(([^)]+)\)(?:-(.*))?$/);
  if (!match) return { formula: reference };
  const [, formula, spaceGroupText, databaseId] = match;
  const spaceGroup = Number(spaceGroupText);
  return {
    formula,
    spaceGroup,
    databaseId,
    crystalSystem: crystalSystem(spaceGroup),
    common: COMMON_PHASES[`${formula}:${spaceGroup}`],
  };
}

function SpaceGroupSymbol({ symbol }) {
  // Monoclinic full symbols read better in short form: C12/c1 -> C2/c, P12_1/c1 -> P2_1/c.
  const parts = String(symbol || '').replace(/^([A-Z])1(.+)1$/, '$1$2').split(/(_\d)/g).filter(Boolean);
  return <>{parts.map((part, index) => part.startsWith('_') ? <sub key={index}>{part.slice(1)}</sub> : <React.Fragment key={index}>{part}</React.Fragment>)}</>;
}

/** "COD 9007583" for `cod_9007583`; other database ids are shown as stored. */
function databaseLabel(databaseId) {
  const cod = /^cod_(\d+)$/i.exec(databaseId || '');
  return cod ? `COD ${cod[1]}` : databaseId;
}

function PhaseIdentity({ reference, formulaHtml, metadata, compact = false, showDatabaseId = false }) {
  const parsedPhase = parsePhaseReference(reference);
  // A phase refined in a subgroup setting is named by its database space group (design 2.1 F).
  const refinedGroup = metadata?.spacegroup_number || parsedPhase.spaceGroup;
  const spaceGroup = metadata?.database_spacegroup_number || refinedGroup;
  const subgroup = metadata?.database_spacegroup_symbol && metadata?.spacegroup_symbol
    && metadata.database_spacegroup_number !== metadata.spacegroup_number ? metadata : null;
  const phase = {
    ...parsedPhase,
    spaceGroup,
    crystalSystem: crystalSystem(spaceGroup),
    common: COMMON_PHASES[`${parsedPhase.formula}:${spaceGroup}`],
  };
  // The CIF's conventional formula (display only) wins over the name's and the group head's.
  const conventional = knownDisplayFormula(reference);
  const formula = conventional ? <ChemicalFormula formula={conventional} />
    : formulaHtml ? parse(formulaHtml) : <ChemicalFormula formula={phase.formula} />;
  // Compact labels (trays, lists) name the formula and space group; the mineral name is in the tables and tooltips.
  const primary = phase.common && !compact ? <>{phase.common.name} · {formula}</> : formula;
  const structure = phase.common?.structure || phase.crystalSystem;
  const spaceGroupLabel = subgroup
    ? <Tooltip title={subgroup.setting_note}><span><SpaceGroupSymbol symbol={subgroup.database_spacegroup_symbol} /> {`(No. ${phase.spaceGroup})`} (refined as <SpaceGroupSymbol symbol={subgroup.spacegroup_symbol} />)</span></Tooltip>
    : metadata?.spacegroup_symbol
    ? <><SpaceGroupSymbol symbol={metadata.spacegroup_symbol} /> {`(No. ${phase.spaceGroup})`}</>
    : phase.spaceGroup ? `Space group No. ${phase.spaceGroup}` : null;
  const secondary = [structure, spaceGroupLabel, showDatabaseId ? databaseLabel(parsedPhase.databaseId) : null]
    .filter(Boolean)
    .map((item, index) => <React.Fragment key={index}>{index ? ' · ' : ''}{item}</React.Fragment>);

  return (
    <span className="phase-identity" style={{ display: 'inline-flex', flexDirection: 'column', lineHeight: 1.35 }}>
      <span className="phase-identity-primary" style={{ fontWeight: 600 }}>{primary}
        {/* Compact rows still name the space group: candidates often differ only by polymorph. */}
        {compact && phase.spaceGroup ? <span className="phase-identity-sg" style={{ fontWeight: 400, color: 'var(--dara-text-secondary)' }}>{' · '}{metadata?.spacegroup_symbol ? <SpaceGroupSymbol symbol={metadata.database_spacegroup_symbol || metadata.spacegroup_symbol} /> : `SG ${phase.spaceGroup}`}</span> : null}</span>
      {!compact && secondary ? <span className="phase-identity-secondary" style={{ fontSize: '0.86em', color: 'var(--dara-text-secondary)' }}>{secondary}</span> : null}
    </span>
  );
}

function PeakDiagnosticTags({ diagnostics }) {
  if (!diagnostics) return <Text type="secondary">Diagnostics unavailable</Text>;
  const counts = diagnostics?.counts || {};
  const unexplained = unexplainedSummary(diagnostics);
  return (
    <Space size="small" wrap>
      {unexplained ? (
        <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{unexplainedTooltipLines(diagnostics).join('\n') || 'No unexplained peaks.'}</div>}>
          <Tag color={unexplained.color}>{unexplained.text}</Tag>
        </Tooltip>
      ) : (
        <Tag color={toneColor(peakCountTone(counts.missing))}>{`Unexplained ${counts.missing || 0}`}</Tag>
      )}
      <Tag>{`Extra ${counts.extra || 0}`}</Tag>
      <Tag>{`Intensity mismatch ${counts.intensity_mismatch || 0}`}</Tag>
    </Space>
  );
}

function formatMetric(value, digits = 2) {
  return value === null || value === undefined || Number.isNaN(Number(value))
    ? '—' : Number(value).toFixed(digits);
}

/** Rwp, GOF and DW; the other statistics are in the Rwp tooltip. `dwOnly`: beside a delta row that already has Rwp and GOF. */
function FitQualitySummary({ quality, dwOnly = false }) {
  if (!quality) return <Text type="secondary">Fit statistics unavailable</Text>;
  const details = [
    `Rp ${formatMetric(quality.rp)}%`, `Rexp ${formatMetric(quality.rexp)}%`, `χ² ${formatMetric(quality.chi_squared)}`,
    `2θ ${formatMetric(quality.two_theta_min)}–${formatMetric(quality.two_theta_max)}°`,
    `${quality.data_points ?? '—'} points, ${quality.iterations ?? '—'} iterations`,
  ];
  if (dwOnly) return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{['Durbin–Watson statistic for residual correlation', ...details].join('\n')}</div>}>
    <Tag>DW {formatMetric(quality.durbin_watson)}</Tag></Tooltip>;
  return <Space size="small" wrap>
    <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{details.join('\n')}</div>}><Tag>Rwp {formatMetric(quality.rwp)} %</Tag></Tooltip>
    <Tooltip title="Goodness of fit = Rwp / Rexp"><Tag color={toneColor(gofTone(quality.gof))}>GOF {formatMetric(quality.gof)}</Tag></Tooltip>
    <Tooltip title="Durbin–Watson statistic for residual correlation (near 2 is ideal; below 1 means strongly correlated residuals)"><Tag color={toneColor(Number(quality.durbin_watson) < 1 ? 'review' : 'neutral')}>DW {formatMetric(quality.durbin_watson)}</Tag></Tooltip>
  </Space>;
}

const STOP_STAGE_LABEL = { screening: 'reference screening', search: 'phase search', final_refinement: 'final refinement' };

const PRUNE_REASON = { failed: 'refinement failed', negligible: 'below 0.5 wt%', duplicate: 'same phase as a heavier one' };

function SearchSummary({ summary = {} }) {
  if (!Object.keys(summary).length) return <Text type="secondary">Detailed search counts were not stored for this older result.</Text>;
  return <Space size="small" wrap>
    <Tag>{`${summary.screening_results_retained ?? 0} retained tree result(s)`}</Tag>
    <Tag>{`${summary.combinations_evaluated ?? 0} combinations evaluated`}</Tag>
    <Tag>{`${summary.structurally_distinct_considered ?? 0} distinct combinations considered`}</Tag>
    <Tag>{`${summary.equivalent_combinations_merged ?? 0} equivalent combinations merged`}</Tag>
    <Tag>{`${summary.displayed_hypotheses ?? 0} displayed`}</Tag>
    {summary.final_refinement_failed ? <Tag color="red">{`${summary.final_refinement_failed} final refinement failed`}</Tag> : null}
    {summary.stopped_early ? <Tag color="gold">{`Stopped at time limit (${STOP_STAGE_LABEL[summary.stopped_at_stage] || 'search'})`}</Tag> : null}
    {summary.final_refinement_skipped ? <Tag color="orange">{`${summary.final_refinement_skipped} final refinement(s) skipped (time limit)`}</Tag> : null}
    {/* Phases that collapsed or repeated another one were dropped and the fit refined again. */}
    {summary.pruned_phases?.length ? <Tooltip title={<div>{summary.pruned_phases.map(item =>
      <div key={`${item.candidate}-${item.phase}`}>{`${item.phase} (${PRUNE_REASON[item.reason] || item.reason})`}</div>)}</div>}>
      <Tag>{`${summary.pruned_phases.length} phase(s) dropped after final refinement`}</Tag>
    </Tooltip> : null}
  </Space>;
}

/** Known-phase tasks: listed phases that are not in the automatic fit, and formulas taken from
 * Materials Project for lack of a COD structure (one line each, under the Phases table). */
function KnownPhaseNotes({ data, style }) {
  const notes = [
    ...knownPhaseNotes(data?.search_summary?.known_phases),
    ...mpFallbackNotes(data?.candidate_summary?.mp_fallback_formulas),
  ];
  if (!notes.length) return null;
  return <div style={{ margin: '8px 0', ...style }}>
    {notes.map(note => <div key={note}><Text type="secondary">{note}</Text></div>)}
  </div>;
}

function parameterText(name, parameter) {
  const value = formatParameterWithEsd(parameter);
  return value ? `${name} = ${value}` : null;
}

function PhaseRefinementTable({ phases = [], metadata = {}, details = {}, confidence = null, rowActions = null, spread = null }) {
  const dataSource = phases.map(phase => ({ phase, ...(details?.[phase] || {}) }));
  // Rows with the same formula and space group show their database id to tell them apart.
  const identityKey = phase => `${formulaOfPhase(phase)}_${spacegroupOfPhase(phase)}`;
  const repeated = new Set(phases.map(identityKey).filter((key, index, keys) => keys.indexOf(key) !== index));
  const verdictColumn = confidence?.phases ? [{
    title: <Tooltip title="Evidence-based assessment of whether each phase is supported by the data. Hover a tag for the reasons."><span>Verdict</span></Tooltip>,
    key: 'verdict', width: 120,
    sorter: (a, b) => levelRank(confidence.phases[a.phase]?.level) - levelRank(confidence.phases[b.phase]?.level),
    render: (_, row) => <VerdictTag verdict={confidence.phases[row.phase]} /> }] : [];
  return <Table size="small" pagination={false} rowKey="phase" scroll={{ x: 'max-content' }} tableLayout="auto" dataSource={dataSource}
    columns={[
      { title: 'Phase', dataIndex: 'phase', width: 220,
        render: phase => <PhaseIdentity reference={phase} metadata={metadata?.[phase]} showDatabaseId={repeated.has(identityKey(phase))} /> },
      ...verdictColumn,
      { title: <Tooltip title="Normalized over modeled crystalline phases; amorphous and unidentified material are excluded. ± is the BGMN estimated standard deviation, propagated after normalization."><span>Quantity</span></Tooltip>,
        className: 'dara-num',
        render: (_, row) => {
          if (row.weight_percent == null) return '—';
          const tag = amountTag(row, confidence?.phases?.[row.phase]?.weight_flag);
          const range = spread?.[formulaOfPhase(row.phase)];
          return <span>{Number(row.weight_percent).toFixed(2)} wt%{row.weight_esd_percent == null ? '' : ` ± ${Number(row.weight_esd_percent).toFixed(2)}`}
            {/* On its own line, so the right-aligned amounts keep one decimal column. */}
            {tag ? <div><StatusTag text={tag.text} tone={tag.tone} tooltip={tag.tooltip} /></div> : null}
            {/* Model dependence of the amount: the ± above is for this one fit only. */}
            {range ? <Tooltip title={`wt% of this compound in the ${range.n} search candidates within ${Math.round((NEAR_RWP_RATIO - 1) * 100)} % of the best Rwp. The ± of one fit does not include the choice of phase model.`}>
              <div><Text type="secondary" style={{ fontSize: 'var(--dara-font-12)' }}>{`${range.min.toFixed(1)}–${range.max.toFixed(1)} wt% across ${range.n} candidates`}</Text></div>
            </Tooltip> : null}</span>;
        } },
      { title: <Tooltip title="± values are BGMN estimated standard deviations. Equalities and fixed angles are imposed by the reported space group, not independently refined."><span>Lattice parameters</span></Tooltip>,
        render: (_, row) => {
          const spaceGroup = metadata?.[row.phase]?.spacegroup_number;
          const values = latticeParameterRows(row.lattice_parameters || {}, spaceGroup);
          const refined = metadata?.[row.phase];
          // A phase refined in a subgroup setting is shown first in its database setting (as on cards).
          const database = row.database_lattice ? latticeParameterRows(row.database_lattice, row.database_lattice.spacegroup_number) : [];
          return values.length ? <Space direction="vertical" size={1}>
            {database.length ? <>
              <Tooltip title="Converted from the refined cell. The e.s.d. are propagated without correlations, so they are approximate.">
                <Text type="secondary" style={{ fontSize: 'var(--dara-font-12)' }}>
                  In the <SpaceGroupSymbol symbol={refined?.database_spacegroup_symbol || ''} /> setting</Text>
              </Tooltip>
              {database.map((value, index) => <span key={`db-${value.label}-${index}`} style={{ whiteSpace: 'nowrap' }}>{parameterText(value.label, value.parameter)}</span>)}
            </> : null}
            {refined?.setting_note && refined.spacegroup_symbol ? <Text type="secondary" style={{ fontSize: 'var(--dara-font-12)', marginTop: database.length ? 4 : 0 }}>
              {database.length ? 'Refined in ' : 'Lattice in '}<SpaceGroupSymbol symbol={refined.spacegroup_symbol} />{database.length ? '' : ' setting'}</Text> : null}
            {/* One line per parameter: value, unit and (symmetry) never split. */}
            {values.map((value, index) => <span key={`${value.label}-${index}`} style={{ whiteSpace: 'nowrap' }}>
              {parameterText(value.label, value.parameter)}
              {latticeStateNote(value) ? <StatusTag kind={value.state === 'at_limit' ? 'at_limit' : 'fixed'}
                tooltip={value.state === 'at_limit' ? 'The value reached the edge of its refinement window and has no e.s.d.' : 'Held at the database cell; the lattice was not refined for this phase.'} /> : null}
              {value.constrained ? <Tooltip title={value.fixed
                ? 'Fixed by space-group symmetry; this was not an independently refined parameter.'
                : 'The additional axis value is constrained equal by space-group symmetry.'}>
                <Text type="secondary" style={{ marginLeft: 6, fontSize: 'var(--dara-font-12)', whiteSpace: 'nowrap' }}>(symmetry)</Text>
              </Tooltip> : null}
            </span>)}
          </Space> : '—';
        } },
      { title: 'Density', className: 'dara-num', render: (_, row) => row.xray_density == null ? '—' : `${Number(row.xray_density).toFixed(3)} g/cm³` },
      { title: <Tooltip title="R factor of this phase's own reflections (BGMN Rphase); compares phases within one fit."><span>Rphase</span></Tooltip>, className: 'dara-num', render: (_, row) => row.rphase == null ? '—' : `${Number(row.rphase).toFixed(2)}%` },
      { title: 'Crystallite size', className: 'dara-num', render: (_, row) => {
        const cell = crystalliteSizeCell(row);
        return cell.tooltip ? <Tooltip title={cell.tooltip}><span>{cell.text}</span></Tooltip> : cell.text;
      } },
      { title: <Tooltip title="Microstrain = sqrt(k2) from BGMN, in percent."><span>Microstrain (%)</span></Tooltip>, className: 'dara-num', render: (_, row) => {
        const cell = microstrainCell(row);
        const text = cell.tooltip ? <Tooltip title={cell.tooltip}><span>{cell.text}</span></Tooltip> : cell.text;
        return cell.note ? <span>{text} <Tooltip title={cell.note}><InfoCircleOutlined style={{ color: 'var(--dara-text-secondary)' }} /></Tooltip></span> : text;
      } },
      ...(rowActions ? [{ title: <span className="dara-sr-only">Actions</span>, key: 'row-actions', width: 48, render: (_, row) => rowActions(row) }] : []),
    ]} />;
}


function normalizePlotId(value) {
  if (value === undefined) return undefined;
  if (value === null || value === 'best') return null;
  const parsed = parseSelection(value);
  if (parsed === null) return undefined;
  if (parsed === 'best') return null;
  return typeof parsed === 'number' ? parsed : `version:${parsed.version}`;
}

const phaseSetKey = phases => [...new Set((phases || []).map(name => `${formulaOfPhase(name)}_${spacegroupOfPhase(name)}`))].sort().join('|');

/** Tooltip of a candidate card that only has a screening fit. */
function screeningCardTooltip(result, finalResult) {
  const quality = result.fit_quality || {};
  const range = quality.two_theta_min != null && quality.two_theta_max != null
    ? ` (2θ ${formatMetric(quality.two_theta_min, 0)}–${formatMetric(quality.two_theta_max, 0)}°)` : '';
  const sameAsBest = finalResult?.phases?.length && phaseSetKey(result.highlighted_phases) === phaseSetKey(finalResult.phases);
  return `Fast restricted refinement${range} used to rank candidate phase sets; its Rwp and wt% are not final.${sameAsBest
    ? ` The best fit is the final refinement of these phases (Rwp ${formatMetric(finalResult.fit_quality?.rwp ?? finalResult.rwp)} %).` : ''}`;
}

function DeltaStrip({ rows, lead = 'vs v1:', extra = null }) {
  const shown = rows.filter(row => row.a !== null || row.b !== null);
  if (!shown.length) return null;
  // A change that rounds to zero is not colored as better or worse.
  const visible = row => row.delta !== null && Number(Math.abs(row.delta).toFixed(row.digits ?? 2)) !== 0;
  return <div style={{ marginBottom: 8 }}>
    <Space wrap size={[4, 4]}>
      <Text type="secondary">{lead}</Text>
      {/* Chips stay neutral (a better GOF can still be a poor one); only the arrow says which way it moved. */}
      {shown.map(row => {
        const tone = !visible(row) || row.key.startsWith('wt:') ? null : row.better ? 'var(--dara-ok)' : row.delta > 0 ? 'var(--dara-review)' : null;
        return <Tag key={row.key}>
          {row.key.startsWith('wt:') ? <ChemicalFormula formula={row.label} /> : row.label} {deltaText(row)}
          {tone ? <span aria-label={row.better ? 'improved' : 'worse'} style={{ color: tone, marginLeft: 4 }}>{row.better ? <ArrowDownOutlined /> : <ArrowUpOutlined />}</span> : null}
        </Tag>;
      })}
      {extra}
    </Space>
  </div>;
}

function ShortcutHelp({ open, onClose }) {
  return <Modal title="Keyboard shortcuts" open={open} onCancel={onClose} footer={null} keyboard={false}
    wrapClassName={HELP_MODAL_CLASS} width={420}>
    <Table size="small" pagination={false} rowKey="keys" dataSource={SHORTCUT_HELP}
      columns={[{ title: 'Keys', dataIndex: 'keys', width: 110, render: keys => <code>{keys}</code> }, { title: 'Action', dataIndex: 'action' }]} />
    <Table size="small" pagination={false} rowKey="keys" dataSource={MOUSE_HELP} style={{ marginTop: 12 }}
      columns={[{ title: 'Mouse (plot)', dataIndex: 'keys', width: 110, render: keys => <code>{keys}</code> }, { title: 'Action', dataIndex: 'action' }]} />
  </Modal>;
}

const ANALYSIS_MODE = {
  known_mixture: 'Known-phase refinement',
  elemental: 'Phase identification',
  reaction: 'Reaction check',
  selected_phases: 'User-selected phase refinement',
};

const DESCRIPTION_COLUMNS = { xs: 1, sm: 1, md: 1, lg: 2, xl: 2, xxl: 3 };
const BREAKPOINTS = ['xxl', 'xl', 'lg', 'md', 'sm', 'xs'];

/** Column count for the current screen (antd's rule); a full-row span must match it. */
function useDescriptionColumns() {
  const screens = Grid.useBreakpoint();
  const hit = BREAKPOINTS.find(key => screens[key]);
  return hit ? DESCRIPTION_COLUMNS[hit] : DESCRIPTION_COLUMNS.xxl;
}

function InstrumentText({ details }) {
  if (!details) return null;
  const extra = [details.geometry, details.radiation, details.notes].filter(Boolean).join(' · ');
  return <Tooltip title={extra || null}>
    <span>{details.label} <Tag color={details.verified ? 'green' : 'orange'}>{details.verified ? 'operator-verified' : 'unverified'}</Tag></span>
  </Tooltip>;
}

function TaskDetail({ data, task_id, onDelete }) {
  // The open fit is mirrored in the address (`?id=9&fit=version:12`): a reload or a new tab
  // opens the same fit.
  const [plot_id, setPlotIdRaw] = React.useState(() => fitFromSearch(typeof window === 'undefined' ? '' : window.location.search));
  const setPlotId = React.useCallback(value => setPlotIdRaw(normalizePlotId(value)), []);
  React.useEffect(() => {
    const next = searchWithFit(window.location.search, plot_id);
    if (next !== window.location.search) {
      window.history.replaceState(window.history.state, '', `${window.location.pathname}${next}${window.location.hash}`);
    }
  }, [plot_id]);
  const [previewSel, setPreviewSel] = React.useState(undefined); // fit drawn on the page (undefined: the adopted one)
  const [phaseSignal, setPhaseSignal] = React.useState(0); // opens Select phases… from the Refine menu
  const store = useVersions(task_id);
  const columns = useDescriptionColumns();
  const savedReview = useTaskReview(task_id);
  const summaryShown = React.useRef(false); // later reloads (after an edit) keep the page in place
  // Quick checks are approximate fits: every adoption path (review select, versions table,
  // compare window) goes through this one guard.
  const review = {
    ...savedReview,
    save: async (choice = savedReview.selection) => {
      if (isQuickCheck(store.versions, choice)) {
        message.warning(`${QUICK_CHECK_HINT} ${REFINE_ALL_FIRST}.`);
        return false;
      }
      return savedReview.save(choice);
    },
  };
  const [versionDetails, setVersionDetails] = React.useState({});
  const [versionError, setVersionError] = React.useState('');
  const [compare, setCompare] = React.useState(null);
  const [peaks, setPeaks] = React.useState([]);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [structureOpen, setStructureOpen] = React.useState(false);
  const [fitTab, setFitTab] = React.useState('phases');
  // Internal standard of the best fit after an edit (undefined: the task summary's own block).
  const [bestStandard, setBestStandard] = React.useState(undefined);
  const [preview, setPreview] = React.useState(null);
  const [helpOpen, setHelpOpen] = React.useState(false);
  const [phaseGroups, setPhaseGroups] = React.useState([]);
  const plotRef = React.useRef(null);
  const plotIdRef = React.useRef(plot_id);
  plotIdRef.current = plot_id;
  const peaksRef = React.useRef(peaks);
  peaksRef.current = peaks;
  const refreshVersions = store.refresh;

  const handleVersion = React.useCallback(async (n, job, meta = {}) => {
    const list = await refreshVersions();
    const item = list.find(entry => entry.version === n);
    const text = versionReadyText(n, job, item?.rwp);
    const sel = n === 1 ? null : `version:${n}`;
    if (meta.afterDone === 'compare') {
      // A duplicate request compares the existing version it matched.
      if (job?.reused) message.info(text);
      else message.success(text);
      setCompare({ a: availableComparisonBase(meta.base ?? 'best', list, n), b: sel ?? 'best' });
    } else if (job?.reused) {
      // A duplicate request: nothing new was refined, the existing version is opened instead.
      message.info(text);
      setPlotId(sel);
    } else if (plotIdRef.current !== undefined) {
      message.success(text);
      setPlotId(sel);
    } else {
      message.success(<span>{`${text} `}<a onClick={() => setPlotId(sel)}>View</a></span>);
    }
  }, [refreshVersions, setPlotId]);
  const quick = useQuickRefine(task_id, { onVersion: handleVersion });
  const quickShown = quick.available !== false && store.available !== false;

  // A version can disappear (deleted, or a quick check replaced by its full fit): move the fit
  // view and the compare window to its replacement instead of showing a missing version.
  React.useEffect(() => {
    if (store.loading) return;
    if (versionGone(store.versions, plot_id)) {
      setPlotId(replacementOf(store.versions, parseSelection(plot_id).version));
    }
    setCompare(previous => {
      if (!previous) return previous;
      const fix = sel => (versionGone(store.versions, sel) ? replacementOf(store.versions, parseSelection(sel).version) : sel);
      const next = { a: fix(previous.a), b: fix(previous.b) };
      return next.a === previous.a && next.b === previous.b ? previous : next;
    });
  }, [store.versions, store.loading, plot_id, setPlotId]);

  const firstPlotId = data.all_results[0]?.source_index ?? (data.all_results.length ? 0 : null);
  React.useEffect(() => {
    const warm = () => preloadPlot(task_id, firstPlotId);
    if (window.requestIdleCallback) {
      const handle = window.requestIdleCallback(warm, { timeout: 1500 });
      return () => window.cancelIdleCallback(handle);
    }
    const handle = window.setTimeout(warm, 250);
    return () => window.clearTimeout(handle);
  }, [task_id, firstPlotId]);
  const referenceMetadata = Object.assign({}, data.final_result.phase_metadata,
    ...data.all_results.map(result => result.phase_metadata));
  const renderReference = reference => <PhaseIdentity reference={reference} metadata={referenceMetadata[reference]} />;
  // Space-group symbols for labels that repeat a formula: task references, opened version fits
  // and the symbols the versions list carries for every version phase.
  const symbolOf = phaseSymbolLookup({
    metadata: [referenceMetadata, ...Object.values(versionDetails).map(body => body?.final_result?.phase_metadata)],
    symbols: store.versions.map(item => item.phase_symbols),
  });

  const currentSel = plot_id === null || plot_id === undefined ? 'best' : plot_id;
  const versionNumber = typeof plot_id === 'string' ? (parseSelection(plot_id)?.version ?? null) : null;
  // A version opened from the address is only loaded once the versions list says it exists:
  // a deleted one is replaced (effect above) without a 404 from /plot or /versions/N first.
  const versionPending = versionNumber !== null && (store.loading || versionGone(store.versions, plot_id));
  const versionData = versionNumber ? versionDetails[versionNumber] : null;
  // Deltas against 'best' use the baseline version (same code as the new version) when it exists;
  // a base that is gone (replaced quick check, deleted version) falls back to the best fit.
  const effectiveBase = versionData ? availableComparisonBase(versionData.origin?.base ?? 'best', store.versions, versionNumber) : null;
  const baseParsed = effectiveBase !== null ? parseSelection(effectiveBase) : null;
  const baseVersionNumber = baseParsed && typeof baseParsed === 'object' ? baseParsed.version : null;
  // The task summary shows the adopted fit once one is saved (a version or a candidate).
  const adoptedSel = savedReview.saved?.selection ?? null;
  const adoptedParsed = adoptedSel === null ? null : parseSelection(adoptedSel);
  const adoptedVersionNumber = adoptedParsed && typeof adoptedParsed === 'object' && !versionGone(store.versions, adoptedSel)
    ? adoptedParsed.version : null;
  // A version picked in the versions table is described on the page too, so its result is loaded.
  const previewParsed = typeof previewSel === 'string' ? parseSelection(previewSel) : null;
  const previewVersionNumber = previewParsed && typeof previewParsed === 'object' && !versionGone(store.versions, previewSel)
    ? previewParsed.version : null;
  React.useEffect(() => {
    if (versionPending) return undefined;
    // A failed load is stored as null, so it is not requested again in a loop.
    const wanted = [versionNumber, baseVersionNumber, adoptedVersionNumber, previewVersionNumber].filter(n => n && !(n in versionDetails));
    if (!wanted.length) return undefined;
    let active = true;
    setVersionError('');
    wanted.forEach(n => {
      requestJson(`/task/${task_id}/versions/${n}`)
        .then(body => { if (active) setVersionDetails(previous => ({ ...previous, [n]: body })); })
        .catch(error => {
          if (active) setVersionDetails(previous => ({ ...previous, [n]: null }));
          // Only the displayed version is worth an error; a missing base just leaves out the deltas.
          if (active && n === versionNumber) setVersionError(error.missing ? NOT_AVAILABLE : error.message);
        });
    });
    return () => { active = false; };
  }, [task_id, versionNumber, baseVersionNumber, versionDetails, versionPending, adoptedVersionNumber, previewVersionNumber]);
  // "Find phases for the unexplained peaks" opens the fit and the drawer together.
  const openDrawerWithFit = React.useRef(false);
  React.useEffect(() => {
    // A different fit has different peaks: drop the selection, any tick preview and an old error.
    setPeaks([]);
    setPreview(null);
    setVersionError('');
    setDrawerOpen(openDrawerWithFit.current && plot_id !== undefined);
    setStructureOpen(false);
    setFitTab('phases');
    openDrawerWithFit.current = false;
  }, [plot_id]);

  // A merged result (other database record) shows its own Rwp, weights and fit statistics.
  const candidateSummary = index => {
    const found = displayedCandidateOf(data.all_results, index);
    return found ? (found.member || found.result) : undefined;
  };
  const displayedResult = plot_id === undefined ? undefined
    : plot_id === null ? data.final_result
      : versionNumber ? versionData?.final_result
        : candidateSummary(plot_id);
  const findSummary = sel => {
    const parsed = parseSelection(sel);
    if (parsed === 'best' || parsed === null) return data.final_result;
    if (typeof parsed === 'number') return candidateSummary(parsed);
    return versionDetails[parsed.version]?.final_result;
  };
  const baseSummary = versionData ? findSummary(effectiveBase) : null;
  const adoptedSummary = adoptedSel === null || adoptedParsed === 'best' ? null : findSummary(adoptedSel);
  const deltaRows = versionData?.final_result && baseSummary ? deltaStrip(baseSummary, versionData.final_result, symbolOf) : [];
  const displayedPhases = displayedResult ? (displayedResult.highlighted_phases || displayedResult.phases || []) : [];
  // Explicit background polynomial degree of the displayed fit (null: BGMN chooses it).
  const baseDegree = versionNumber ? (versionData?.background?.degree ?? null)
    : (data.final_refinement_params?.background_degree ?? null);

  // Set only while the baseline check below is in flight, so a failed baseline is retried later.
  const baselineChecking = React.useRef(false);
  const runQuick = async (body, meta = {}) => {
    if (quick.busy || baselineChecking.current) {
      message.info('A re-refinement is already running for this task.');
      return;
    }
    const request = { base: currentSel, ...body };
    if (store.baselineStale && parseSelection(request.base) === 'best') {
      // The stored result predates the current refinement code: queue a baseline with today's code
      // first (silently; the server runs jobs in order) so comparisons use the same code. The
      // current versions and queue decide, so a baseline that failed is queued again.
      baselineChecking.current = true;
      try {
        const [list, jobs] = await Promise.all([
          refreshVersions({ silent: true }),
          requestJson(`/task/${task_id}/quick-jobs`).catch(() => null),
        ]);
        const queue = Array.isArray(jobs) ? jobs : (jobs?.jobs || jobs?.active || []);
        const baselineQueued = queue.some(job => ['queued', 'running'].includes(job.status) && job.label === BASELINE_LABEL);
        if (!baselineQueued && needsBaseline(true, list.length ? list : store.versions, request)) {
          await postJson(`/task/${task_id}/quick-refine`, baselineRequest());
        }
      } catch (_) {
        // Not queued; the user's own job still runs and the next one tries again.
      } finally {
        baselineChecking.current = false;
      }
    }
    quick.start(request, { base: currentSel, afterDone: 'view', ...meta });
  };
  // Fast Add-phase versions keep the base phases fixed; this makes the full fit (which replaces the
  // quick check) and compares it with the quick check's own base.
  const refineAll = n => runQuick(refineAllRequest(n, store.versions));
  const quickCheck = versionData?.refine_mode === 'fast';
  const addHint = (hint, target) => runQuick({
    phases: [target], origin: 'add_hint',
    label: addedLabel(hint.formula || formulaOfPhase(hint.phase), { symbol: hint.spacegroup_symbol ?? symbolOf(hint.phase), number: hint.spacegroup_number, ref: hint.phase },
      { displayedPhases, versions: store.versions }),
  });
  // The API nests the alternatives under `verdict.evidence`; an alternative is a stored candidate.
  const alternativeOf = phase => alternativeOfVerdict(displayedResult?.confidence?.phases?.[phase]);
  // Phase row actions sit in one menu, so the phases table stays within the workspace width.
  const rowActions = row => {
    const link = alternativeLink(alternativeOf(row.phase), data.all_results, plot_id);
    const items = [
      ...(link ? [{ key: 'view', label: link.text, title: link.title }] : []),
      ...(displayedPhases.length >= 2 ? [{ key: 'refit', label: 'Refit without this phase…', disabled: quick.busy }] : []),
    ];
    if (!items.length) return null;
    const onClick = ({ key }) => {
      if (key === 'view') setPlotId(link.sel);
      if (key === 'refit') Modal.confirm({ title: 'Refine the other phases without this one?', content: 'Creates a new version; this fit is kept.',
        okText: 'Refine as new version', cancelText: 'Cancel', onOk: () => runQuick(removeRequest(row.phase)) });
    };
    return <Dropdown trigger={['click']} menu={{ items, onClick }}>
      <Button size="small" icon={<EllipsisOutlined />} aria-label="Phase actions" />
    </Dropdown>;
  };

  // A click only changes the selection, never the layout: opening or closing the drawer here
  // would resize the plot under the cursor between the click and the next Shift+click. The
  // drawer opens with "Add phase for these peaks" (and an open drawer follows the selection).
  const onPeakClick = (peak, shift) => {
    const next = updatePeakSelection(peaksRef.current, peak, { shift });
    setPeaks(next);
    if (!next.length) setPreview(null);
  };
  const [suggestionCount, setSuggestionCount] = React.useState(0);
  const clearPeaks = () => { setPeaks([]); setPreview(null); setDrawerOpen(false); };
  const addCandidate = candidate => {
    setDrawerOpen(false);
    setPreview(null);
    runQuick({
      phases: [{ ref: candidate.ref, source: candidate.source }], origin: 'add_peak_search',
      label: addedLabel(candidate.formula || formulaOfPhase(candidate.ref),
        { symbol: candidate.spacegroup_symbol ?? symbolOf(candidate.ref), number: candidate.spacegroup_number, ref: candidate.ref },
        { displayedPhases, versions: store.versions }),
    });
  };
  // A quick check is changed only through "Refine all phases"; the drawer stays closed on it.
  const drawerShown = drawerOpen && plot_id !== undefined && !quickCheck;
  const structureShown = structureOpen && plot_id !== undefined && !quickCheck;
  const sideShown = drawerShown || structureShown;
  const peakClicksOn = quickShown && !quickCheck;

  const options = [
    'best',
    ...data.all_results.flatMap((result, index) => [result.source_index ?? index, ...referenceVariants(result).map(m => m.source_index)]),
    ...store.versions.filter(item => item.version > 1).map(item => `version:${item.version}`),
  ];
  const keyContext = React.useRef({});
  keyContext.current = { options, current: currentSel, phaseGroups, peakSelectionCount: peaks.length, helpOpen, drawerOpen: sideShown };
  useKeyboardShortcuts(plot_id !== undefined && !compare, keyContext, {
    select: action => setPlotId(action.value === 'best' ? null : action.value),
    'toggle-phase': action => plotRef.current?.togglePhase(action.name),
    reset: () => plotRef.current?.reset(),
    'fit-y': () => plotRef.current?.toggleFitY(),
    help: () => setHelpOpen(true),
    'close-help': () => setHelpOpen(false),
    'close-drawer': () => { setDrawerOpen(false); setStructureOpen(false); },
    'clear-peaks': clearPeaks,
    close: () => setPlotId(undefined),
  });
  const selectOptions = selectOptionGroups(data.all_results, store.versions);
  const [backgroundOpen, setBackgroundOpen] = React.useState(false);
  // Nothing adopted yet and a full version fits better than v1: point at it (the choice stays the user's).
  const betterVersion = adoptedSel !== null ? null : store.versions.filter(item => item.version > 1 && item.refine_mode !== 'fast'
    && Number(item.rwp) < Number(store.versions[0]?.rwp)).sort((x, y) => x.rwp - y.rwp)[0] || null;
  // Candidate rows numbered by search rank; the best one is the automatic fit (selection null = v1).
  const v1Index = data.all_results.findIndex(other => other.rwp === data.final_result?.rwp);
  const candidateRows = data.all_results.map((item, index) => ({ ...item, candidate_number: index + 1,
    source_index: item.source_index ?? index, sel: index === v1Index ? null : (item.source_index ?? index) }));
  // A phase (formula and space group) keeps one compartment colour in every tray.
  const trayKey = phase => { const parsed = parsePhaseReference(phase); return `${tidyFormula(parsed.formula)}:${parsed.spaceGroup}`; };
  const trayFills = fillIndex(candidateRows.flatMap(row => (row.highlighted_phases || []).map(trayKey)));
  // The plot draws each phase in its tray colour (darker line of the same hue).
  const phaseColor = phase => trayLine(trayFills.get(trayKey(phase)));
  const mineralOf = phase => COMMON_PHASES[trayKey(phase)]?.name || null;
  // Legend entries read like the trays: "Fe2O3 · R-3c (35.89 wt%)" instead of "Hematite · Fe2O3 (…)".
  const phaseLegend = traces => (traces || []).map(trace => {
    const mineral = trace?.legendgroup ? mineralOf(trace.legendgroup) : null;
    if (!mineral || typeof trace.name !== 'string' || !trace.name.startsWith(`${mineral} · `)) return trace;
    const [, formula, weight = ''] = /^(.*?)( \([^()]*wt%\))?$/.exec(trace.name.slice(mineral.length + 3));
    // The database space group, as on the trays (a subgroup refinement setting stays in the tables).
    const symbol = referenceMetadata[trace.legendgroup]?.database_spacegroup_symbol || symbolOf(trace.legendgroup);
    const group = symbol ? String(symbol).replace(/^([A-Z])1(.+)1$/, '$1$2').replace(/_(\d)/g, '<sub>$1</sub>')
      : `SG ${parsePhaseReference(trace.legendgroup).spaceGroup}`;
    return { ...trace, name: `${formula} · ${group}${weight}` };
  });
  const isAdoptedRow = row => adoptedSel !== null && (row.sel === adoptedSel || (row.sel === null && adoptedSel === 'best'));
  const defaultSel = adoptedSummary ? adoptedSel : null;
  const shownSel = previewSel !== undefined ? previewSel : defaultSel;
  // Everything under the trays (verdict, phases, cautions, details) describes the fit picked
  // above, the adopted one by default; adopting stays a separate, explicit step.
  const summary = findSummary(shownSel ?? 'best') || adoptedSummary || data.final_result;
  const shownLabel = selectionLabel(shownSel ?? 'best', { versions: store.versions, all_results: data.all_results }).split(' — ')[0];
  const shownIsAdopted = adoptedSel !== null && (shownSel === adoptedSel || (shownSel === null && adoptedSel === 'best'));
  const summaryHeading = `Phases (${shownLabel}${shownIsAdopted ? ', adopted' : ''})`;
  const shownRwp = shownSel === null ? data.final_result?.rwp
    : typeof shownSel === 'number' ? candidateRows.find(row => row.sel === shownSel)?.rwp
      : store.versions.find(item => `version:${item.version}` === shownSel)?.rwp;
  const firstOtherCandidate = data.all_results.length
    ? (data.all_results[1] ?? data.all_results[0]).source_index ?? (data.all_results.length > 1 ? 1 : 0) : null;
  // "Compare…" in the fit view: a version against the fit its deltas use, a candidate against
  // the best fit, the best fit against the next candidate. Overlays the fit view.
  const compareFromFit = plot_id === undefined ? null
    : versionNumber ? { a: effectiveBase ?? comparisonBase('best', store.versions, versionNumber), b: currentSel }
      : plot_id === null ? (firstOtherCandidate === null ? null : { a: 'best', b: firstOtherCandidate })
        : { a: 'best', b: currentSel };
  const reportBlocked = review.loading || review.error ? 'unavailable' : review.dirty ? 'save the review first' : null;
  const downloadZip = () => downloadResultArchive(task_id)
    .then(() => message.success('Result ZIP downloaded.'))
    .catch(error => message.error(error.message || 'Could not download the result.'));
  const searchSummary = data.search_summary || {};
  const excludedRanges = data.additional_search_options?.refinement_params?.excluded_ranges || [];
  const hasBanner = Boolean(summary?.confidence?.overall?.level);
  const addPhaseTip = quickCheck ? REFINE_ALL_FIRST
    : suggestionCount ? `${suggestionCount} possible or strong match${suggestionCount === 1 ? '' : 'es'} for the significant unexplained peaks` : null;

  // Until the adopted fit is known and loaded, the summary would show the best fit and then jump.
  const summaryPending = !summaryShown.current
    && (savedReview.loading || (adoptedVersionNumber && !(adoptedVersionNumber in versionDetails)));
  if (!summaryPending) summaryShown.current = true;
  else return <SpinContainer><Spin size='large' tip='Loading results…' /></SpinContainer>;

  return (
    <>
      <TitleContriner>
        <h2 style={{ margin: 0, fontSize: 'var(--dara-font-20)', overflowWrap: 'anywhere' }}><a href="/results" style={{ fontWeight: 400 }}>Results</a>{' › '}{`Task ${task_id} · ${data.sample || data.task_label}`}</h2>
        <Space wrap align="center" style={{ justifyContent: 'flex-end' }}>
        <Tooltip title="Downloads use the adopted fit (or the best fit)" placement="left">
          <span><DownloadMenu taskId={task_id} reportBlocked={reportBlocked} extraItems={[
            { key: 'csv', label: <a href={`${URL}/task/${task_id}/table.csv`}>Table (CSV)</a> },
            { key: 'zip', label: 'All results (ZIP)', onClick: downloadZip },
          ]} /></span>
        </Tooltip>
        <Button href={`/?clone_task=${task_id}`}>Rerun</Button>
        <TaskMoreMenu onDelete={onDelete} taskId={task_id} />
        </Space>
      </TitleContriner>
      <Divider style={{ margin: '12px 0' }} />
      {/* Confidence banner and review side by side on wide screens, stacked below 1200 px. */}
      <Row gutter={[16, 12]} className="dara-summary-row" style={{ marginBottom: 12 }}>
        {hasBanner ? <Col xs={24}>
          <ConfidenceBanner confidence={summary?.confidence} style={{ marginBottom: 0 }}
            action={significantPeaks(summary?.peak_diagnostics).length
              ? <Button size="small" icon={<SearchOutlined />} onClick={() => { openDrawerWithFit.current = true; setPlotId(shownSel); }}>
                Find phases for the unexplained peaks
              </Button> : null} />
        </Col> : null}
      </Row>
      {/* The candidate phase sets are the main result: listed first; a row click shows its fit in the plot below. */}
      <ResultTitleContainer>
        <Space wrap align="baseline">
          <h3 style={{ marginTop: 8, marginBottom: 0 }}>{`Candidates (${candidateRows.length})`}</h3>
          {data.duplicate_results_removed > 0 ? <Tooltip title="Results with the same phases (same formula and space group) reached in another order or with other database records. Only the best-Rwp copy is listed; polymorphs stay separate.">
            <Text type="secondary">{`· ${data.duplicate_results_removed} equivalent result${data.duplicate_results_removed === 1 ? '' : 's'} merged`}</Text>
          </Tooltip> : null}
        </Space>
        <Button disabled={firstOtherCandidate === null}
          // A is the adopted fit (or v1); B is the fit shown in the plot, else the next candidate.
          onClick={() => { const base = adoptedSel ?? 'best'; const shown = shownSel ?? 'best';
            setCompare({ a: base, b: shown !== base ? shown : firstOtherCandidate }); }}>Compare fits</Button>
      </ResultTitleContainer>
      <Table size="small" pagination={false} scroll={{ x: 'max-content' }} tableLayout="auto" style={{ marginTop: 4, marginBottom: 12 }}
        rowKey="source_index"
        dataSource={candidateRows}
        // A row click shows that candidate's fit in the plot above (buttons keep their own action).
        onRow={row => ({ onClick: event => { if (!event.target.closest('button, a')) setPreviewSel(row.sel); },
          onKeyDown: event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) { event.preventDefault(); setPreviewSel(row.sel); } },
          tabIndex: 0, 'aria-selected': row.sel === shownSel, style: { cursor: 'pointer' }, title: 'Show this fit in the plot' })}
        rowClassName={row => (row.sel === shownSel ? 'dara-row-shown' : '')}
        columns={[
          { title: 'Tray', dataIndex: 'candidate_number', className: 'dara-tray-code', render: (n, row) => <Space size={4} direction="vertical">
            <Tooltip title={`Candidate ${n}${row.sel === null ? ' — the automatic fit, v1' : ''}`}><span className="dara-code">{`C${n}`}{row.sel === null ? <span className="dara-code-sub">v1</span> : null}</span></Tooltip>
            {isAdoptedRow(row) ? <span className="dara-adopted-mark">Adopted</span> : null}
          </Space> },
          // The tray: compartments sized by wt% (always 100 % wide), labelled inside when there is
          // room; the database record and the other matching records are in each one's tooltip.
          { title: 'Composition (wt%)', render: (_, row) => {
            const segments = compositionSegments(row.highlighted_phases || [], row.weight_percent);
            const narrow = segments.filter(seg => seg.share < 16);
            const tipOf = (phase, slot) => {
              const others = [...new Set((row.phases?.[slot] || []).filter(other => other !== phase))];
              return [...(mineralOf(phase) ? [mineralOf(phase)] : []), phaseRecordLine(phase, symbolOf(phase)),
                ...(others.length ? [`${others.length} other matching record${others.length === 1 ? '' : 's'}:`, ...others.slice(0, 8).map(other => phaseRecordLine(other, symbolOf(other)))] : [])];
            };
            const wtText = seg => (seg.wt === null ? 'wt% n/a' : Number(seg.wt).toFixed(1));
            return <div className="dara-tray">
              <div className="dara-tray-bar">
                {segments.map((seg, slot) => <Tooltip key={seg.phase} placement="topLeft" mouseEnterDelay={0.2}
                  title={<div>{[`${wtText(seg)}${seg.wt === null ? '' : ' wt%'}`, ...tipOf(seg.phase, slot)].map(line => <div key={line}>{line}</div>)}</div>}>
                  <div className="dara-tray-cell" style={{ width: `${seg.share}%`, background: trayFills.get(trayKey(seg.phase)) }}>
                    {seg.share >= 16 ? <>
                      <span className="dara-tray-name"><PhaseIdentity reference={seg.phase} metadata={row.phase_metadata?.[seg.phase]} compact /></span>
                      <span className="dara-num">{wtText(seg)}</span>
                    </> : null}
                  </div>
                </Tooltip>)}
              </div>
              {narrow.length ? <div className="dara-tray-rest">{narrow.map(seg => <span key={seg.phase}>
                <i style={{ background: trayFills.get(trayKey(seg.phase)) }} />
                <PhaseIdentity reference={seg.phase} metadata={row.phase_metadata?.[seg.phase]} compact />
                <span className="dara-num">{wtText(seg)}</span>
              </span>)}</div> : null}
            </div>;
          } },
          { title: <Tooltip title="Weighted-profile R factor: misfit between observed and calculated pattern (lower is better)."><span>Rwp (%)</span></Tooltip>, dataIndex: 'rwp', className: 'dara-num', sorter: (a,b) => a.rwp-b.rwp, defaultSortOrder: 'ascend', render: (value,row) => <>{formatMetric(value)}
            {/* Only the exception is marked: a screening fit (restricted range, fast) is not a final number. */}
            {row.is_final_refined ? null : <Tooltip title={screeningCardTooltip(row, data.final_result)}><Tag style={{ marginLeft: 6 }}>Screening</Tag></Tooltip>}</> },
          { title: <Tooltip title="Goodness of fit = Rwp / Rexp; near 1 is ideal for counting statistics."><span>GOF</span></Tooltip>, className: 'dara-num', sorter: (a,b) => (a.fit_quality?.gof ?? Infinity)-(b.fit_quality?.gof ?? Infinity), render: (_,row) => formatMetric(row.fit_quality?.gof) },
          { title: 'Unexplained', className: 'dara-num', sorter: (a,b) => missingCell(a.peak_diagnostics).sortValue-missingCell(b.peak_diagnostics).sortValue, render: (_,row) => missingCell(row.peak_diagnostics).text },
          { title: <Tooltip title="Automated review flags; hover a count to read them.">Cautions</Tooltip>,
            sorter: (a,b) => cautionSortValue(a.cautions)-cautionSortValue(b.cautions),
            render: (_,row) => {
              const counts = countCautions(row.cautions);
              if (!counts) return '—';
              return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{counts.total ? cautionTooltip(row.cautions) : 'No automated cautions.'}</div>}>
                <Tag color={cautionTagColor(row.cautions)}>{counts.total}</Tag>
              </Tooltip>;
            } },
          // Shown only when the candidates differ; the same level everywhere is the banner's verdict.
          ...(new Set(candidateRows.map(row => row.confidence?.overall?.level)).size > 1 ? [{ title: <Tooltip title="Hover for the reasons.">Confidence</Tooltip>,
            sorter: (a,b) => levelRank(a.confidence?.overall?.level)-levelRank(b.confidence?.overall?.level),
            render: (_,row) => <OverallTag confidence={row.confidence} short /> }] : []),
          { title: 'Actions', render: (_,row) => <Space size="small">
            <Button size="small" icon={<LineChartOutlined />} onMouseEnter={() => preloadPlot(task_id, row.sel)} onFocus={() => preloadPlot(task_id, row.sel)} onClick={() => setPlotId(row.sel)}>Open</Button>
            <Tooltip title="Adopt this fit for the report and downloads (saves the notes too)">
              <span><Button size="small" disabled={review.loading || !!review.error || review.saving}
                onClick={() => review.save(row.sel === null ? 'best' : row.sel)}>{isAdoptedRow(row) ? 'Adopted' : 'Adopt'}</Button></span>
            </Tooltip>
            <Tooltip title="Download (ZIP)"><DownloadButton taskId={task_id} resultIndex={row.source_index} size="small" icon={<DownloadOutlined />} aria-label={`Download candidate ${row.candidate_number}`} /></Tooltip>
          </Space> },
        ]} />
      {/* The fit of the tray picked above, drawn on the page; the workspace adds the editing tools. */}
      {plot_id === undefined ? <section aria-label="Fit plot" className="dara-inline-fit">
        <div className="dara-inline-fit-head">
          <Space size="small" wrap>
            <Text strong>{selectionLabel(shownSel ?? 'best', { versions: store.versions, all_results: data.all_results }).split(' — Rwp')[0]}</Text>
            {Number.isFinite(Number(shownRwp)) ? <Text type="secondary" className="dara-num">{`Rwp ${formatMetric(shownRwp)} %`}</Text> : null}
            {shownIsAdopted ? <span className="dara-adopted-mark">Adopted</span> : null}
            {/* Viewing another fit: the whole page follows it; one click returns to the default. */}
            {shownSel !== defaultSel ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setPreviewSel(undefined)}>
              {adoptedSummary ? 'Show the adopted fit' : 'Show v1'}</Button> : null}
          </Space>
          {betterVersion ? <Text type="secondary">
            {/* Rwp alone favours more parameters: GOF and the caution count go with it. */}
            {`v${betterVersion.version}: Rwp ${formatMetric(betterVersion.rwp)} % vs ${formatMetric(store.versions[0]?.rwp)} %, GOF ${formatMetric(betterVersion.gof)} vs ${formatMetric(store.versions[0]?.gof)}${betterVersion.cautions_count ? `, ${betterVersion.cautions_count} caution${betterVersion.cautions_count === 1 ? '' : 's'}` : ''}. `}
            <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setPreviewSel(`version:${betterVersion.version}`)}>Show</Button>
          </Text> : null}
          <Button type="primary" icon={<LineChartOutlined />} onClick={() => setPlotId(shownSel)}>Open fit workspace</Button>
        </div>
        <RefinementPlot task_id={task_id} plot_id={shownSel} excludedRanges={excludedRanges} height={420} compact phaseColor={phaseColor} phaseLegend={phaseLegend} />
      </section> : null}
      {/* Choosing and annotating the fit sits with the versions it chooses from. */}
      <ReviewPanel data={data} review={review} versions={store.versions} style={{ marginTop: 16, marginBottom: 0 }} />
      <VersionsPanel taskId={task_id} store={store} review={review}
        onView={setPlotId} onPreview={setPreviewSel} shownSel={shownSel} onCompare={(a, b) => setCompare({ a, b })} symbolOf={symbolOf} onRefineAll={quickShown ? refineAll : null} refineBusy={quick.busy}
        extra={quickShown ? <QuickRefineProgress job={quick.job} onCancel={quick.cancel} onDismiss={quick.dismiss} /> : null} />
      <h3 style={{ marginTop: 16 }}>{summaryHeading}</h3>
      <PhaseRefinementTable phases={summary.highlighted_phases || summary.phases} metadata={summary.phase_metadata} details={summary.phase_details} confidence={summary.confidence} spread={weightSpread(data.all_results)} />
      {/* The known-phase outcomes describe the automatic fit, not an adopted re-refinement. */}
      <KnownPhaseNotes data={data} style={adoptedVersionNumber ? { display: 'none' } : undefined} />
      <InternalStandardCard taskId={task_id} renderPhase={renderReference}
        block={adoptedVersionNumber ? versionDetails[adoptedVersionNumber]?.internal_standard : (bestStandard !== undefined ? bestStandard : data.internal_standard)}
        onSaved={next => { setBestStandard(next); setVersionDetails({}); }} />
      <FitCautions cautions={summary.cautions} besideBanner={hasBanner} bannerReasons={reasonsText(summary?.confidence?.overall)} showHidden />
      <Descriptions bordered column={columns} size='small'>
        <Descriptions.Item label="Pattern file">{data.task_label}</Descriptions.Item>
        <Descriptions.Item label="Analysis mode">{ANALYSIS_MODE[data.analysis_mode] || data.analysis_mode || (data.use_rxn_predictor ? "Reaction check" : "Phase identification")}</Descriptions.Item>
        <Descriptions.Item label="Run">
          {runText(data.start_time, data.runtime)}
          {searchSummary.stopped_early ? <Tag color="gold" style={{ marginLeft: 8 }}>{`Stopped at time limit (${STOP_STAGE_LABEL[searchSummary.stopped_at_stage] || 'search'})`}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="Fit quality">
          <Space size="small" wrap>
            <FitQualitySummary quality={summary.fit_quality} />
            <DisplacementTag confidence={summary.confidence} />
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="Peak diagnostics">
          <PeakDiagnosticTags diagnostics={summary.peak_diagnostics} />
        </Descriptions.Item>
      </Descriptions>
      <details style={{ marginTop: 8 }}>
        <summary>Analysis settings</summary>
        <Descriptions bordered column={columns} size='small' style={{ marginTop: 8 }}>
          <Descriptions.Item label="Temperature">{data.temperature || "None"}</Descriptions.Item>
          <Descriptions.Item label="Reaction network">{data.use_rxn_predictor ? "Yes" : "No"}</Descriptions.Item>
          <Descriptions.Item label="Included elements">{(data.elements || []).join(', ') || "None"}</Descriptions.Item>
          <Descriptions.Item label="Reference databases">{(data.candidate_sources || []).map(source => source.toUpperCase()).join(' + ') || "Uploaded CIFs"}</Descriptions.Item>
          <Descriptions.Item label="Candidate CIFs">{data.candidate_summary?.after ?? "Unknown"} retained ({data.candidate_summary?.removed_duplicates ?? 0} duplicates removed)
            <ScreeningTags candidateSummary={data.candidate_summary} />
          </Descriptions.Item>
          <Descriptions.Item label="Precursors">{(data.precursors || []).join(', ') || "None"}</Descriptions.Item>
          <Descriptions.Item label="Search options">{searchOptionsText(data.additional_search_options)}</Descriptions.Item>
          {excludedRanges.length ? <Descriptions.Item label="Excluded 2θ">
            <Tooltip title="Shaded in the fit plot; omitted from fitting">
              <span>{excludedRanges.map(([lo, hi]) => `${lo}–${hi}°`).join(', ')}</span>
            </Tooltip>
          </Descriptions.Item> : null}
          {data.instrument_profile_details ? <Descriptions.Item label="Instrument">
            <InstrumentText details={data.instrument_profile_details} />
          </Descriptions.Item> : null}
          {searchSummary.parent_task_id ? <Descriptions.Item label="Derived from">
            <a href={`/task/?id=${searchSummary.parent_task_id}`}>Task {searchSummary.parent_task_id}</a>
          </Descriptions.Item> : null}
          <Descriptions.Item label="Hypothesis search" span={columns}><SearchSummary summary={searchSummary} /></Descriptions.Item>
        </Descriptions>
      </details>
      {/* The Add phase drawer sits beside the fit instead of covering the plot. */}
      {/* The fit selector names the fit; the title only names the workspace. */}
      <Modal title="Fit workspace" open={plot_id !== undefined} onCancel={() => setPlotId(undefined)} footer={null}
        width={sideShown ? `calc(100vw - ${DRAWER_WIDTH} - 32px)` : 'calc(100vw - 32px)'}
        style={{ top: 16, ...(sideShown ? { marginRight: `calc(${DRAWER_WIDTH} + 8px)`, marginLeft: 'auto' } : {}) }}
        bodyStyle={{ padding: '12px 16px' }}
        keyboard={false} wrapClassName={FIT_MODAL_CLASS}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Select aria-label="Displayed fit" style={{ minWidth: 360, maxWidth: '40vw' }} value={currentSel}
            onChange={value => setPlotId(value === 'best' ? null : value)} options={selectOptions} />
          {quickShown && displayedResult ? <>
            <Tooltip title={addPhaseTip}>
              <Badge count={quickCheck ? 0 : suggestionCount} size="small" offset={[-4, 2]} color="var(--dara-info)" title={`${suggestionCount} suggested phases`}>
                <span><Button type={!quickCheck && (suggestionCount || collectHints(displayedResult?.peak_diagnostics).length) ? 'primary' : 'default'}
                  disabled={quickCheck} onClick={() => { setStructureOpen(false); setDrawerOpen(true); }}>Add phase…</Button></span>
              </Badge>
            </Tooltip>
            {/* The other edits that make a new version share one menu. */}
            <Dropdown trigger={['click']} disabled={quick.busy || quickCheck}
              menu={{ items: [
                { key: 'phases', label: 'Select phases…', title: 'Choose the phases to refine as a new version' },
                { key: 'background', label: 'Background…', title: 'Background polynomial and excluded 2θ intervals' },
                { key: 'structure', label: 'Structure…', title: 'Free atomic positions, B or occupancies of one phase (indicative)' },
              ], onClick: ({ key }) => {
                if (key === 'phases') setPhaseSignal(n => n + 1);
                if (key === 'background') setBackgroundOpen(true);
                if (key === 'structure') { setDrawerOpen(false); setStructureOpen(true); }
              } }}>
              <Tooltip title={quickCheck ? REFINE_ALL_FIRST : quick.busy ? 'A refinement is running' : null}>
                <Button disabled={quick.busy || quickCheck}>Refine <DownOutlined /></Button>
              </Tooltip>
            </Dropdown>
            <PhaseSelectionButton taskId={task_id} phases={displayedPhases} renderPhase={renderReference} busy={quick.busy}
              extraPhases={versionPhaseOptions(displayedPhases, store.versions)} symbolOf={symbolOf} hideButton openSignal={phaseSignal}
              onSubmit={request => runQuick(request)} />
            <BackgroundEditor open={backgroundOpen} onOpenChange={setBackgroundOpen} busy={quick.busy} disabled={quick.busy} baseDegree={baseDegree}
              getView={() => plotRef.current?.getViewRange?.() || null}
              onSubmit={(background, label) => runQuick({ phases: [], background, origin: 'background', label })} />
          </> : null}
          {displayedResult ? <DownloadMenu taskId={task_id} selection={currentSel} label="Download this fit"
            blocked={quickCheck ? REFINE_ALL_FIRST : null} reportBlocked={reportBlocked} /> : null}
          {compareFromFit ? <Tooltip title="Compare this fit with its base; A and B can be changed in the comparison">
            <Button onClick={() => setCompare(compareFromFit)}>Compare…</Button>
          </Tooltip> : null}
        </Space>
        {quickShown ? <QuickRefineProgress job={quick.job} onCancel={quick.cancel} onDismiss={quick.dismiss} /> : null}
        {versionError ? <Alert type="error" showIcon message={versionError} style={{ marginBottom: 12 }} /> : null}
        {versionNumber && !versionData && !versionError ? <Spin /> : null}
        {displayedResult ? <>
          {quickCheck ? <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={QUICK_CHECK_HINT}
            action={quickShown ? <Button size="small" type="primary" disabled={quick.busy} onClick={() => refineAll(versionNumber)}>Refine all phases</Button> : null} /> : null}
          {/* A version shows its metrics once, as changes against its base, in one row. */}
          {versionNumber && deltaRows.length ? <DeltaStrip rows={deltaRows} lead={deltaBaseText(effectiveBase, data.all_results)} extra={<>
            <FitQualitySummary quality={displayedResult.fit_quality} dwOnly />
            <OverallTag confidence={displayedResult.confidence} />
            <DisplacementTag confidence={displayedResult.confidence} />
          </>} /> : <Space size="small" wrap style={{ marginBottom: 12 }}>
            <FitQualitySummary quality={displayedResult.fit_quality} />
            <OverallTag confidence={displayedResult.confidence} />
            <DisplacementTag confidence={displayedResult.confidence} />
          </Space>}
        </> : null}
        {/* One fixed-height row, so selecting a peak never moves the plot. */}
        {peakClicksOn ? <div style={{ height: 32, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8, overflowX: 'auto', whiteSpace: 'nowrap' }}>
          {peaks.length ? <>
            <Text type="secondary">Selected peaks:</Text>
            {peaks.map(peak => <Tag key={peak} closable color="volcano" style={{ marginInlineEnd: 0 }}
              onClose={event => { event.preventDefault(); onPeakClick(peak, true); }}>{`${peak.toFixed(2)}°`}</Tag>)}
            <Button size="small" type={drawerShown ? 'default' : 'primary'} onClick={() => setDrawerOpen(true)}>Add phase for these peaks</Button>
            <Button size="small" onClick={clearPeaks}>Clear</Button>
          </> : <Text type="secondary">Click a peak to search references for it (Shift+click adds more) · press ? for shortcuts</Text>}
        </div> : null}
        <RefinementPlot ref={plotRef} task_id={task_id} plot_id={versionPending ? undefined : plot_id} title={data.task_label}
          excludedRanges={[...excludedRanges, ...(versionData?.background?.excluded_ranges || [])]}
          peaks={peaks} previewSticks={preview?.sticks || null} onGroups={setPhaseGroups} phaseColor={phaseColor} phaseLegend={phaseLegend}
          onPeakClick={peakClicksOn ? onPeakClick : undefined} />
        {displayedResult ? <>
          <Tabs activeKey={fitTab === 'structure' && !structurePhases(displayedResult.phase_details).length ? 'phases' : fitTab} onChange={setFitTab} destroyInactiveTabPane
            items={[
              { key: 'phases', label: 'Phases', children: <>
                <PhaseRefinementTable phases={displayedPhases}
                  metadata={displayedResult.phase_metadata} details={displayedResult.phase_details} confidence={displayedResult.confidence}
                  rowActions={quickShown && !quickCheck ? rowActions : null} />
                {currentSel === 'best' ? <KnownPhaseNotes data={data} /> : null}
                <FitCautions cautions={displayedResult.cautions} style={{ margin: '12px 0' }} />
              </> },
              { key: 'peaks', label: 'Peaks', children: <PeakListTab taskId={task_id} sel={currentSel} active={fitTab === 'peaks' && !versionPending} /> },
              ...(structurePhases(displayedResult.phase_details).length ? [{ key: 'structure', label: 'Structure',
                children: <StructureResults details={displayedResult.phase_details} renderPhase={renderReference} /> }] : []),
            ]} />
        </> : null}
      </Modal>
      <StructureRefineDrawer taskId={task_id} sel={currentSel} phases={displayedPhases} renderPhase={renderReference} open={structureShown}
        busy={quick.busy} onClose={() => setStructureOpen(false)} onSubmit={request => runQuick(request)} />
      <PeakCandidatesDrawer taskId={task_id} sel={currentSel} peaks={peaks} open={drawerShown}
        diagnostics={displayedResult?.peak_diagnostics} onAddHint={(hint, target) => { setDrawerOpen(false); addHint(hint, target); }}
        active={plot_id !== undefined && !versionPending && !quickCheck} onSuggestions={setSuggestionCount}
        displayedPhases={displayedPhases} versions={store.versions} onOpenVersion={n => setPlotId(n === 1 ? null : `version:${n}`)}
        onClose={() => setDrawerOpen(false)} onPreview={setPreview} previewRef={preview?.key} onAdd={addCandidate} busy={quick.busy} />
      <FitCompare taskId={task_id} a={compare?.a} b={compare?.b} open={Boolean(compare)} onClose={() => setCompare(null)}
        adopting={review.saving} allResults={data.all_results} versions={store.versions} onSelect={(a, b) => setCompare({ a, b })}
        onAdoptB={async sel => { if (await review.save(sel)) setCompare(null); }}
        onViewB={sel => { setCompare(null); setPlotId(sel); }} />
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  )
}

/** "2026-10-30 17:07" from "2026-10-30 17:07:02…": minutes are enough to place a run. */
const toMinute = value => String(value ?? '').replace(/^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}):\d{2}.*$/, '$1');

function TaskDetailedUnfinished({ data, taskId }) {
  const { status, submitted_on, start_time, end_time, error_tb, error_summary } = data;
  return (<>
    {status === 'FIZZLED' ? <TaskFailurePanel taskId={taskId} errorSummary={error_summary} errorTb={error_tb} /> : null}
    <Descriptions bordered column={1} size='small' labelStyle={{ width: 160 }}>
    {/* A failed run names itself in the title and the failure panel above. */}
    {status !== 'FIZZLED' ? <Descriptions.Item label="Name" span={1}>{data.task_label}</Descriptions.Item> : null}
    {status !== 'FIZZLED' ? <Descriptions.Item label="Status" span={1}><StateTag state={status} /></Descriptions.Item> : null}
    <Descriptions.Item label="Submitted on" span={1}>{toMinute(submitted_on)}</Descriptions.Item>
    {start_time ? <Descriptions.Item label="Start time" span={1}>{toMinute(start_time)}</Descriptions.Item> : <></>}
    {end_time ? <Descriptions.Item label="End time" span={1}>{toMinute(end_time)}</Descriptions.Item> : <></>}
    {error_tb && status !== 'FIZZLED' ? <Descriptions.Item label="Error details" span={1}>
      <details>
        <summary>Technical details (traceback)</summary>
        <div className="error-code"><pre style={{ whiteSpace: "break-spaces", fontSize: "0.9em" }}>{error_tb}</pre></div>
      </details>
    </Descriptions.Item> : <></>}
  </Descriptions>
  </>)
}


/** Delete lives in a "more" menu; it keeps the existing confirm dialog. */
function TaskMoreMenu({ onDelete, taskId = null }) {
  // The adopted result's exact structures can be reused on other files (Submit page, known-phase mode).
  const reuse = taskId == null ? [] : [
    { key: 'reuse', label: 'Use these phases for other files…', title: 'Refine the exact structures of the adopted result in new patterns' },
    { key: 'reuse-series', label: 'Use these phases for a series…', title: 'The same, for a series of patterns' },
  ];
  const onClick = ({ key }) => {
    if (key === 'delete') onDelete();
    else window.location.href = phaseSetUrl(taskId, key === 'reuse-series');
  };
  return <Dropdown trigger={['click']} menu={{ items: [...reuse, { key: 'delete', danger: true, icon: <DeleteOutlined />, label: 'Delete…' }], onClick }}>
    <Button icon={<EllipsisOutlined />} aria-label="More actions" />
  </Dropdown>;
}

/** Shown when the server has no result to display (unknown task, or a run that stored none). */
/** The measured pattern alone, so a run without a fit still shows what was analyzed. */
function MeasuredPattern({ taskId }) {
  const [pattern, setPattern] = React.useState(null);
  React.useEffect(() => {
    const controller = new AbortController();
    fetch(`${URL}/task/${taskId}/pattern`, { signal: controller.signal })
      .then(response => (response.ok ? response.json() : null)).then(setPattern).catch(() => {});
    return () => controller.abort();
  }, [taskId]);
  if (!pattern?.angles?.length) return null;
  return <Plotly
    data={[{ x: pattern.angles, y: pattern.intensities, type: 'scattergl', mode: 'lines', name: 'Observed', line: { color: '#000', width: 1 } }]}
    layout={{ autosize: true, height: 320, margin: { t: 16, l: 70, r: 20, b: 50 }, showlegend: false,
      xaxis: { title: '2θ (°)', showline: true, mirror: true, ticks: 'outside', zeroline: false },
      yaxis: { title: 'Intensity', showline: true, ticks: 'outside', zeroline: false } }}
    config={{ responsive: true, displaylogo: false }} style={{ width: '100%' }} useResizeHandler />;
}

/** What the run searched (its saved settings) and what to widen, for a search that found no fit. */
function SearchSettingsSummary({ data, taskId }) {
  const [settings, setSettings] = React.useState(null);
  React.useEffect(() => {
    const controller = new AbortController();
    fetch(`${URL}/task/${taskId}/rerun-settings`, { signal: controller.signal })
      .then(response => (response.ok ? response.json() : null)).then(body => setSettings(body?.settings || {})).catch(() => {});
    return () => controller.abort();
  }, [taskId]);
  const s = settings || {};
  const databases = [s.use_cod ? 'COD' : null, s.use_mp ? `Materials Project (E_hull ≤ ${s.mp_e_hull_cutoff} eV/atom, ≤ ${s.max_mp_structures})` : null].filter(Boolean).join(' + ');
  const range = s.two_theta_min != null || s.two_theta_max != null ? `${s.two_theta_min ?? 'start'}–${s.two_theta_max ?? 'end'}°` : 'Whole pattern';
  const items = [
    ['Mode', ANALYSIS_MODE[s.analysis_mode] || s.analysis_mode],
    ['Elements', (data.elements || s.elements || []).join(', ')],
    ['Precursors', (data.precursors || s.precursor_formulas || []).join(', ')],
    ['Databases', databases],
    ['Candidate CIFs', data.candidate_summary?.after != null ? `${data.candidate_summary.after} screened` : null],
    ['Candidate pre-filter', settings ? (s.candidate_prefilter ? 'On' : 'Off') : null],
    ['2θ range', settings ? range : null],
    ['Instrument / radiation', settings ? `${s.instrument_profile || '—'} · ${s.wavelength || '—'}` : null],
    ['Run', data.start_time ? runText(data.start_time, data.runtime_s) : null],
  ].filter(([, value]) => value);
  const hints = settings ? noFitHints(s, data) : [];
  return <div style={{ maxWidth: 760, margin: '16px auto 0', textAlign: 'left' }}>
    {hints.length ? <Alert type="info" showIcon style={{ marginBottom: 12 }} message="What to change before rerunning"
      description={<ul style={{ margin: 0, paddingLeft: 18 }}>{hints.map(hint => <li key={hint}>{hint}</li>)}</ul>} /> : null}
    <Descriptions bordered size="small" column={1}>
      {items.map(([label, value]) => <Descriptions.Item key={label} label={label}>{value}</Descriptions.Item>)}
    </Descriptions>
  </div>;
}

function TaskUnavailable({ taskId, reason, data = null }) {
  const missing = /not found/i.test(reason || '');
  if (data?.no_result) return <>
    <div className="dara-empty">
      <h3>No phase combination fits this pattern</h3>
      <p>{`${reason ? `${reason} ` : ''}Check the search settings below, then rerun with wider ones.`}</p>
      <KnownPhaseNotes data={data} style={{ textAlign: 'center' }} />
      <Space>
        <Button type="primary" href={`/?clone_task=${taskId}`}>Rerun with edited settings…</Button>
        <Button href="/results">Back to Results</Button>
      </Space>
      <SearchSettingsSummary data={data} taskId={taskId} />
    </div>
    <MeasuredPattern taskId={taskId} />
  </>;
  return <div className="dara-empty">
    <h3>{missing ? `Task ${taskId} does not exist` : 'This task has no result to show'}</h3>
    <p>{missing ? 'It may have been deleted. Open Results to see the tasks that are stored.'
      : `${reason && !/^No search result/i.test(reason) ? `${reason} ` : 'The analysis ended without a final result, so there is nothing to display. '}Rerun it with the same settings (or wider elements and candidates), or go back to the list.`}</p>
    <Space>
      <Button type={missing ? 'primary' : 'default'} href="/results">Back to Results</Button>
      {missing ? null : <Button type="primary" href={`/?clone_task=${taskId}`}>Rerun</Button>}
    </Space>
  </div>;
}

function TaskPage({ location }) {
  const queryParameters = new URLSearchParams(location.search)  
  const task_id = queryParameters.get('id')
  const [error, setError] = React.useState("");

  const [data, setData] = React.useState(undefined);
  const [statusRevision, setStatusRevision] = React.useState('');
  const handleStatusChange = React.useCallback(status => setStatusRevision(status), []);
  const deleteTask = () => {
    Modal.confirm({
      title: `Delete task ${task_id}?`,
      content: 'This permanently removes the stored result.',
      okText: 'Delete',
      okButtonProps: { danger: true },
      cancelText: 'Cancel',
      async onOk() {
        const response = await fetch(`${URL}/task/${task_id}`, { method: 'DELETE' });
        if (!response.ok) {
          const body = await response.json();
          throw new Error(body.detail || 'Could not delete the result.');
        }
        window.location.href = '/results';
      },
    });
  };
  React.useEffect(() => {
    // handle 404 error
    fetch(`${URL}/task/${task_id}`)
      .then(response => {
        if (response.status === 404 || response.status === 400) {
          return response.json().then(data => setError(data.detail)).then(() => null);
        }
        return response.json();
      })
      .then(data => { rememberDisplayFormulas(data?.display_formulas); setData(data); });
  }, [task_id, statusRevision]);

  // Trace hints are simulated in the background after the first load; poll the task summary
  // (every 3 s, for at most 60 s) until the server reports no pending hints.
  const hintsArePending = Boolean(data && data.status === 'COMPLETED' && hintsPending(data));
  React.useEffect(() => {
    if (!hintsArePending) return undefined;
    let cancelled = false;
    let timer = null;
    let attempts = 0;
    const step = () => {
      timer = setTimeout(async () => {
        attempts += 1;
        let more = true;
        try {
          const response = await fetch(`${URL}/task/${task_id}`);
          if (response.ok) {
            const next = await response.json();
            if (cancelled) return;
            if (next && next.status === 'COMPLETED') { rememberDisplayFormulas(next.display_formulas); setData(next); }
            more = hintsPending(next);
          }
        } catch (_) { /* keep the current data; try again until the time is up */ }
        if (!cancelled && more && attempts < HINT_POLL_MAX) step();
      }, HINT_POLL_MS);
    };
    step();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [task_id, hintsArePending]);

  // A completed run that stored no fit (design J) comes back as 200 with no_result.
  const noResult = Boolean(data?.no_result);
  return (
    <Layout hasSider={false} title="Result">
      <Container>
        {/* A finished task carries its own header with the actions (TaskDetail). */}
        {data && data.status === 'COMPLETED' && !noResult ? null : <Title>
          <h2 style={{ margin: 0, fontSize: 'var(--dara-font-20)', overflowWrap: 'anywhere' }}><a href="/results" style={{ fontWeight: 400 }}>Results</a>{' › '}{task_id ? `Task ${task_id}${data?.task_label ? ` · ${data.task_label}` : ''}` : ''}</h2>
          <Space>
            {data && !noResult && !['FIZZLED', 'READY', 'RUNNING', 'CANCELLING'].includes(data.status) ? <Button href={`/?clone_task=${task_id}`}>Rerun</Button> : null}
            {data && !['READY','RUNNING','CANCELLING'].includes(data.status) ? <TaskMoreMenu onDelete={deleteTask} /> : null}
          </Space>
        </Title>}
        {data && data.status !== 'COMPLETED' ? <TaskProgress taskId={task_id} onStatusChange={handleStatusChange} /> : null}
        {
          data === null || noResult ? <TaskUnavailable taskId={task_id} reason={noResult ? data.message : error} data={noResult ? data : null} /> : <></>
        }
        {
          data === undefined ? <SpinContainer><Spin size='large' tip={task_id ? `Loading task ${task_id}…` : 'Loading…'}></Spin></SpinContainer> : <></> 
        }
        {
          data && data.status === "COMPLETED" && !noResult ? <TaskDetail data={data} task_id={task_id} onDelete={deleteTask} /> : <></> 
        }
        {
          data && data.status !== "COMPLETED" ? <TaskDetailedUnfinished data={data} taskId={task_id} /> : <></>
        }
      </Container>
    </Layout>
  )
}

export default TaskPage;
