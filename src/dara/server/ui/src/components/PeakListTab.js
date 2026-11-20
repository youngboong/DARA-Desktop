import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, InputNumber, Radio, Select, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula from './ChemicalFormula';
import { spacegroupOfPhase } from './versions.mjs';
import { curveRangeText, curveText, instrumentQuery } from './instrumentCurve.mjs';
import { FwhmCurveDialog } from './InstrumentFwhmCurve';
import CellRefinementView from './CellRefinementView';
import { DEFAULT_SHIFT_MODEL, SHIFT_MODELS } from './cellRefinement.mjs';
import useAdvancedTools from './useAdvancedTools';
import { shownPeakView } from './advancedTools.mjs';

const { Text } = Typography;
const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <Spin /> });

const SCHERRER_K = [
  { value: 0.89, label: 'K = 0.89' },
  { value: 0.94, label: 'K = 0.94' },
  { value: 1, label: 'K = 1.0' },
];
const K_HELP = 'Scherrer constant for the FWHM. 0.89: spherical crystallites (the usual value); 0.94: cubic crystallites; 1.0: no crystallite shape assumed.';
const INSTRUMENT_HELP = 'Instrument FWHM removed from each peak before Scherrer and Williamson–Hall: quadratically for Gaussian-like peaks (η < 0.5), linearly otherwise. '
  + 'Standard curve: the Caglioti curve of a line-broadening standard measured on this instrument profile, taken at each peak\'s angle. '
  + 'Constant FWHM: one value for all angles, remembered for this profile.';
const WH_HELP = 'Williamson–Hall: β·cos θ = K·λ/D + ε·4·sin θ, a straight line through the fitted peaks of one phase (β: Kα1 FWHM with the instrument FWHM removed, in radians). Needs 4 or more fitted peaks over at least 30° 2θ.';
const SIZE_HELP = 'Crystallite size and microstrain from the peak widths: a Scherrer size for each observed peak and a Williamson–Hall line per phase.';
const CELL_HELP = 'Unit-cell refinement: least-squares lattice parameters from the Kα1 positions of the indexed observed peaks of each phase, with a zero shift, a specimen displacement or an internal-standard 2θ correction.';
const SHIFT_HELP = 'Instrumental 2θ shift refined with the cell. Zero shift and specimen displacement are not refined together: over a lab scan they are almost fully correlated. An internal standard corrects 2θ with a phase of known cell first.';
const FIT_NOTE_HELP = {
  merged: 'Found closer than one FWHM to a stronger peak: fitted as that peak, which carries the FWHM',
  overlapped: 'Overlaps its neighbours too much for a width of its own',
  'too weak': 'The width is not determined (uncertainty above 30%)',
  'fit failed': 'The profile fit did not converge or the width ran to a limit',
};
const QUIET_NOTE = 'Single-peak estimates. The Rietveld size of each phase (instrument-corrected with the instrument profile) is the reported value.';
const CONSTANT_NOTE = 'The instrument FWHM is one value for all angles, while the instrument broadening varies with angle, so the sizes stay approximate.';

/** Footnote of the instrument correction the server applied (`instrument` block of the response). */
function correctionNote(applied) {
  if (applied?.mode === 'constant') return CONSTANT_NOTE;
  if (applied?.mode !== 'curve') return null;
  const clamped = applied.clamped > 0
    ? ` ${applied.clamped} peak${applied.clamped === 1 ? '' : 's'} outside the standard's range use${applied.clamped === 1 ? 's' : ''} the edge value.` : '';
  return `Instrument broadening removed with the ${applied.standard || 'standard'} curve measured on ${applied.measured_on || 'an unknown date'} (${curveRangeText(applied.two_theta_range)}).${clamped}`;
}

// Negative values take the minus sign (U+2212), as on the plot axes.
const fixed = digits => value => (value == null ? '—' : Number(value).toFixed(digits).replace(/^-/, '\u2212'));

// Formula with the space group, so polymorphs of one formula (R-3c and R-3m Fe2O3) stay apart.
// The page's name for each phase of the fit (`name -> label | null`): formula, space group and, where
// those repeat, the database record. Without it the server's label and the space-group number.
const PhaseLabels = React.createContext(null);
const phaseText = (row, labelOf = null) => {
  const own = labelOf?.(row.phase);
  if (own) return own;
  const label = row.phase_label || row.phase; const sg = spacegroupOfPhase(row.phase);
  return sg && !String(label).includes('·') ? `${label} (SG ${sg})` : label;
};

// The instrument FWHM is remembered per instrument profile; the page works without storage.
const fwhmKey = profile => `dara-instrument-fwhm:${profile}`;
function storedFwhm(profile) {
  try { const value = Number(window.localStorage.getItem(fwhmKey(profile))); return value > 0 ? value : null; } catch (e) { return null; }
}
function storeFwhm(profile, value) {
  try { if (value) window.localStorage.setItem(fwhmKey(profile), String(value)); else window.localStorage.removeItem(fwhmKey(profile)); } catch (e) { /* private mode */ }
}

/**
 * "Peaks" tab of the fit view: the calculated reflections of the displayed fit and the peaks found in
 * the measured pattern; with the advanced analysis tools also crystallite size / microstrain
 * (Scherrer per peak, Williamson–Hall per phase) and the unit cell from the peak positions.
 * Loads when the tab is shown.
 */
export default function PeakListTab({ taskId, sel, active, phaseLabel = null, identification = false }) {
  // A search-match result has no fitted profile: its own peaks come first.
  const [view, setView] = React.useState(identification ? 'observed' : 'calculated');
  const [advanced] = useAdvancedTools();
  // A remembered advanced view shows "Observed peaks" while the tools are off (and fetches nothing).
  const shown = shownPeakView(view, advanced);
  const [k, setK] = React.useState(0.89);
  const [instrument, setInstrument] = React.useState(null);
  const [profile, setProfile] = React.useState(null);
  // null: the server's default (the profile's curve, else the constant, else none); or 'curve' | 'constant' | 'none'.
  const [mode, setMode] = React.useState(null);
  const [applied, setApplied] = React.useState(null); // `instrument` block of the last response
  const [curve, setCurve] = React.useState(null); // the profile's curve, once a response used it
  const [curveOpen, setCurveOpen] = React.useState(false);
  const [reload, setReload] = React.useState(0);
  const [shiftModel, setShiftModel] = React.useState(DEFAULT_SHIFT_MODEL);
  const [cellPhases, setCellPhases] = React.useState([]); // [{phase, label}] of the fit, for the internal-standard choices
  const onCellPhases = React.useCallback((phases, correction) => {
    const found = [...(correction ? [{ phase: correction.standard, label: correction.phase_label || correction.standard }] : []),
      ...phases.map(entry => ({ phase: entry.phase, label: entry.phase_label || entry.phase }))];
    setCellPhases(previous => [...previous, ...found.filter(entry => !previous.some(known => known.phase === entry.phase))]);
  }, []);
  React.useEffect(() => { if (profile !== null) setInstrument(storedFwhm(profile)); }, [profile]);
  // Another fit or version may have other phases: its standards are listed afresh.
  React.useEffect(() => {
    setCellPhases([]);
    setShiftModel(model => (model.startsWith('standard:') ? DEFAULT_SHIFT_MODEL : model));
  }, [sel]);
  const changeInstrument = value => {
    const next = Number.isFinite(value) && value > 0 ? value : null;
    setInstrument(next);
    if (profile !== null) storeFwhm(profile, next);
  };
  const onResponse = React.useCallback(data => {
    setProfile(data.instrument_profile ?? '');
    setApplied(data.instrument || null);
    if (data.instrument?.mode === 'curve') setCurve(data.instrument);
  }, []);
  const curveChanged = stored => {
    setCurve(null);
    setMode(previous => (stored || previous === 'curve' ? null : previous));
    setReload(value => value + 1);
  };
  const shownMode = mode ?? applied?.mode ?? (instrument ? 'constant' : 'none');
  const query = `sel=${encodeURIComponent(sel)}&${instrumentQuery({ k, mode, constant: instrument })}`;
  const plainQuery = `sel=${encodeURIComponent(sel)}`;
  // Profiles without a file of their own (synchrotron) cannot carry a curve.
  const measurable = Boolean(profile) && profile !== 'synchrotron';
  return <PhaseLabels.Provider value={phaseLabel}>
    <div className="dara-toolbar">
      <div className="dara-toolbar-group">
        <Radio.Group size="small" optionType="button" value={shown} onChange={event => setView(event.target.value)}
          aria-label="Peak list" options={[{ value: 'calculated', label: 'Calculated reflections' }, { value: 'observed', label: 'Observed peaks' },
            ...(advanced ? [{ value: 'size', label: <Tooltip title={SIZE_HELP}><span>Size / strain</span></Tooltip> },
              { value: 'cell', label: <Tooltip title={CELL_HELP}><span>Unit cell</span></Tooltip> }] : [])]} />
      </div>
      {shown === 'cell' ? <div className="dara-toolbar-group" aria-label="Unit-cell settings">
        <label className="dara-toolbar-item">
          <Tooltip title={SHIFT_HELP}><span>Shift model</span></Tooltip>
          <Select size="small" aria-label="Shift model" value={shiftModel} onChange={setShiftModel} style={{ width: 240 }} dropdownMatchSelectWidth={false}
            options={[...SHIFT_MODELS, ...cellPhases.map(entry => ({ value: `standard:${entry.phase}`,
              label: <>Internal standard: <ChemicalFormula formula={entry.label} /></> }))]} />
        </label>
      </div> : null}
      {shown !== 'size' ? null : <div className="dara-toolbar-group" aria-label="Size estimate settings">
        <Tooltip title={K_HELP}>
          <Select size="small" aria-label="Scherrer constant" value={k} onChange={setK} options={SCHERRER_K} style={{ width: 96 }} />
        </Tooltip>
        <label className="dara-toolbar-item">
          <Tooltip title={INSTRUMENT_HELP}><span>Instrument broadening</span></Tooltip>
          <Select size="small" aria-label="Instrument broadening" value={shownMode} onChange={setMode} style={{ width: curve ? 290 : 140 }}
            options={[...(curve ? [{ value: 'curve', label: `Standard curve (${curveText(curve)})` }] : []),
              { value: 'constant', label: 'Constant FWHM' }, { value: 'none', label: 'None' }]} />
        </label>
        {shownMode === 'constant' ? <InputNumber size="small" aria-label="Instrument FWHM in degrees" min={0} max={0.5} step={0.005} placeholder="0"
          value={instrument} onChange={changeInstrument} addonAfter="°" style={{ width: 110 }} /> : null}
        {measurable && applied && !curve ? <Button type="link" size="small" onClick={() => setCurveOpen(true)}>Measure with a standard…</Button> : null}
      </div>}
    </div>
    {shown === 'observed' ? <ObservedPeaks taskId={taskId} query={plainQuery} onResponse={onResponse} active={active} />
      : shown === 'size' ? <SizeStrain taskId={taskId} query={query} onResponse={onResponse} active={active} reload={reload} />
        : shown === 'cell' ? <CellRefinementView taskId={taskId} sel={sel} model={shiftModel} active={active} onPhases={onCellPhases} />
          : <CalculatedReflections taskId={taskId} sel={sel} active={active} widths={!identification} />}
    {measurable && advanced ? <FwhmCurveDialog profile={profile} open={curveOpen} onClose={() => setCurveOpen(false)} onChange={curveChanged} /> : null}
    {/* An identification shows the RIR amounts and Scherrer sizes in its Phases tab already. */}
    {advanced ? null : <Text className="dara-footnote" style={{ marginTop: 12 }}>
      {identification ? 'Size / strain per peak and the unit cell from the peak positions: turn on Advanced analysis tools in the Help menu.'
        : 'Crystallite size, unit cell, RIR and Le Bail: turn on Advanced analysis tools in the Help menu.'}</Text>}
  </PhaseLabels.Provider>;
}

/** GET `path` while `active` (again when `reload` changes); hands each response to `onResponse`. */
function useSizeRequest(path, active, onResponse, reload) {
  const [body, setBody] = React.useState(null);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    setBody(null);
    setError('');
    if (!active) return undefined;
    let current = true;
    requestJson(path)
      .then(data => { if (current) { setBody(data); onResponse(data); } })
      .catch(failure => { if (current) setError(failure.missing ? NOT_AVAILABLE : failure.message); });
    return () => { current = false; };
  }, [path, active, onResponse, reload]);
  return [body, error];
}

const withEsd = (value, esd, digits) => (value == null ? '—'
  : `${Number(value).toFixed(digits)}${esd != null && Number.isFinite(esd) ? ` ± ${Number(esd).toFixed(digits)}` : ''}`);

/** Peaks of the measured pattern (GET /task/{id}/observed-peaks): position, intensity, fitted FWHM and the assignment to the reflections of the fit. */
function ObservedPeaks({ taskId, query, onResponse, active }) {
  const labelOf = React.useContext(PhaseLabels);
  const [filter, setFilter] = React.useState('all');
  const [body, error] = useSizeRequest(`/task/${taskId}/observed-peaks?${query}`, active, onResponse, 0);

  if (error) return <Alert type="error" showIcon message="The observed peaks could not be loaded." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Fitting the peaks of the measured pattern" /></div>;

  const rows = (body.rows || []).map((row, index) => ({ ...row, key: index, number: index + 1 }));
  const unassigned = rows.filter(row => !row.phase).length;
  const shown = filter === 'all' ? rows : rows.filter(row => (filter === 'assigned') === Boolean(row.phase));
  const columns = [
    { title: '#', dataIndex: 'number', className: 'dara-num', sorter: byNumber('number') },
    { title: '2θ (°)', dataIndex: 'two_theta_deg', className: 'dara-num', sorter: byNumber('two_theta_deg'), render: fixed(3) },
    { title: <Tooltip title={body.wavelength ? `Bragg's law, Kα1 of ${body.wavelength}` : null}><span>d (Å)</span></Tooltip>, dataIndex: 'd_angstrom', className: 'dara-num', sorter: byNumber('d_angstrom'), render: fixed(4) },
    { title: <Tooltip title="Integrated intensity of the peak fitted by EFLECH (BGMN)"><span>Intensity</span></Tooltip>, dataIndex: 'intensity', className: 'dara-num', sorter: byNumber('intensity'), render: fixed(1) },
    { title: <Tooltip title="Relative to the strongest observed peak"><span>Rel. I (%)</span></Tooltip>, dataIndex: 'intensity_rel_percent', className: 'dara-num', sorter: byNumber('intensity_rel_percent'), render: fixed(1) },
    { title: <Tooltip title="Kα1 full width at half maximum from a pseudo-Voigt fit of the measured peak (instrument + sample)"><span>FWHM (°)</span></Tooltip>, dataIndex: 'fwhm_deg',
      className: 'dara-num', sorter: byNumber('fwhm_deg'),
      render: fwhmCell },
    { title: 'Phase', key: 'phase', sorter: (a, b) => Number(Boolean(a.phase)) - Number(Boolean(b.phase)) || String(a.phase_label).localeCompare(String(b.phase_label)),
      render: (_, row) => (row.phase ? <ChemicalFormula formula={phaseText(row, labelOf)} /> : <span style={{ color: 'var(--dara-review)' }}>Unassigned</span>) },
    { title: <Tooltip title="Indices of the nearest reflection, in the database setting"><span>hkl</span></Tooltip>, dataIndex: 'hkl',
      render: value => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{value ?? <Text type="secondary">—</Text>}</span> },
    { title: <Tooltip title="Observed − calculated 2θ of the assigned reflection"><span>Δ2θ (°)</span></Tooltip>, dataIndex: 'delta_two_theta_deg', className: 'dara-num',
      sorter: (a, b) => Math.abs(a.delta_two_theta_deg ?? 1e9) - Math.abs(b.delta_two_theta_deg ?? 1e9), render: fixed(3) },
  ];
  return <>
    <div className="dara-toolbar">
      <Radio.Group size="small" optionType="button" value={filter} onChange={event => setFilter(event.target.value)} aria-label="Filter by assignment"
        options={[{ value: 'all', label: 'All' }, { value: 'assigned', label: 'Assigned' }, { value: 'unassigned', label: 'Unassigned' }]} />
      <Text type="secondary" className="dara-num">{`${rows.length} peak${rows.length === 1 ? '' : 's'}, ${unassigned} unassigned`}</Text>
      <Button size="small" icon={<DownloadOutlined />} href={`${API}/task/${taskId}/observed-peaks.csv?${query}`}>Download CSV</Button>
    </div>
    <div className="dara-fit-table">
      <Table size="small" rowKey="key" dataSource={shown} columns={columns} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }} />
    </div>
    <Text className="dara-footnote">Each peak is assigned to the nearest reflection within max(0.1°, its calculated FWHM).</Text>
  </>;
}

const byNumber = key => (a, b) => (a[key] ?? -Infinity) - (b[key] ?? -Infinity);
const phaseCell = labelOf => (_, row) => (row.phase ? <ChemicalFormula formula={phaseText(row, labelOf)} /> : <span style={{ color: 'var(--dara-review)' }}>Unassigned</span>);
// The fitted Kα1 FWHM ± e.s.d., or why the peak has none (merged, overlapped, too weak).
function fwhmCell(value, row) {
  return value == null ? <Tooltip title={FIT_NOTE_HELP[row.fit_note] || null}><Text type="secondary">{row.fit_note || '—'}</Text></Tooltip>
    : <Tooltip title={row.eta == null ? null : `η = ${Number(row.eta).toFixed(2)} (0 Gaussian, 1 Lorentzian)`}>{withEsd(value, row.fwhm_esd_deg, 3)}</Tooltip>;
}

/** "Size / strain" view: the Scherrer size of each observed peak, then the Williamson–Hall fit per phase. */
function SizeStrain({ taskId, query, onResponse, active, reload }) {
  const labelOf = React.useContext(PhaseLabels);
  const [body, error] = useSizeRequest(`/task/${taskId}/observed-peaks?${query}`, active, onResponse, reload);
  if (error) return <Alert type="error" showIcon message="The observed peaks could not be loaded." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Fitting the peaks of the measured pattern" /></div>;
  const rows = (body.rows || []).map((row, index) => ({ ...row, key: index }));
  const corrected = (body.instrument?.mode ?? 'none') !== 'none';
  const sizeTitle = corrected
    ? <Tooltip title="Scherrer estimate from this peak alone: K·λ / (β·cos θ), β the FWHM with the instrument FWHM at this angle removed"><span>Size (nm)</span></Tooltip>
    : <Tooltip title="No instrument broadening removed: a lower bound. Choose a standard curve or a constant instrument FWHM to correct it."><span>Size (nm), uncorrected</span></Tooltip>;
  const columns = [
    { title: 'Phase', key: 'phase', render: phaseCell(labelOf),
      sorter: (a, b) => Number(Boolean(a.phase)) - Number(Boolean(b.phase)) || String(a.phase_label).localeCompare(String(b.phase_label)) },
    { title: <Tooltip title="Indices of the nearest reflection, in the database setting"><span>hkl</span></Tooltip>, dataIndex: 'hkl',
      render: value => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{value ?? <Text type="secondary">—</Text>}</span> },
    { title: '2θ (°)', dataIndex: 'two_theta_deg', className: 'dara-num', sorter: byNumber('two_theta_deg'), render: fixed(3) },
    { title: <Tooltip title="Kα1 full width at half maximum from a pseudo-Voigt fit of the measured peak (instrument + sample)"><span>FWHM (°)</span></Tooltip>,
      dataIndex: 'fwhm_deg', className: 'dara-num', sorter: byNumber('fwhm_deg'), render: fwhmCell },
    ...(corrected ? [{ title: <Tooltip title="Instrument FWHM removed from this peak: the standard curve at its angle, or the constant"><span>Instr. (°)</span></Tooltip>,
      dataIndex: 'instrument_fwhm_deg', className: 'dara-num', render: fixed(3) }] : []),
    { title: sizeTitle, dataIndex: 'size_nm', className: 'dara-num', sorter: byNumber('size_nm'),
      render: (value, row) => (value != null ? fixed(0)(value)
        : <Text type="secondary">{row.size_note === 'below instrument resolution' ? '> resolution limit' : '—'}</Text>) },
  ];
  return <>
    <h4 className="dara-section-label">Scherrer size per peak</h4>
    <div className="dara-toolbar">
      <Text type="secondary" className="dara-num">{`${rows.length} peak${rows.length === 1 ? '' : 's'}`}</Text>
      <Button size="small" icon={<DownloadOutlined />} href={`${API}/task/${taskId}/observed-peaks.csv?${query}`}>Download CSV</Button>
    </div>
    <div className="dara-fit-table" style={{ marginBottom: 16 }}>
      <Table size="small" rowKey="key" dataSource={rows} columns={columns} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }} />
    </div>
    <h4 className="dara-section-label"><Tooltip title={WH_HELP}><span>Williamson–Hall per phase</span></Tooltip></h4>
    <WilliamsonHall taskId={taskId} query={query} onResponse={onResponse} active={active} reload={reload} />
    <Text className="dara-footnote">{QUIET_NOTE} {correctionNote(body.instrument)}</Text>
  </>;
}

// A fitted but unreliable W–H line shows no number; the reason and the number stay in the tooltip (and the API).
const gated = (row, detail, text) => (row.reliable === false && row.intercept != null
  ? <Tooltip title={`${row.note}. ${detail}`}><Text type="secondary">Not reliable</Text></Tooltip> : text);

/** Williamson–Hall fit per phase (GET /task/{id}/williamson-hall) beside the Rietveld size and strain. */
function WilliamsonHall({ taskId, query, onResponse, active, reload }) {
  const labelOf = React.useContext(PhaseLabels);
  const [body, error] = useSizeRequest(`/task/${taskId}/williamson-hall?${query}`, active, onResponse, reload);

  if (error) return <Alert type="error" showIcon message="The size / strain estimates could not be loaded." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Fitting the peaks of the measured pattern" /></div>;

  const phases = (body.phases || []).map(entry => ({ ...entry, key: entry.phase }));
  if (!phases.length) return <Alert type="info" showIcon message="No observed peak is assigned to a phase of this fit." />;
  const columns = [
    { title: 'Phase', key: 'phase', render: (_, row) => <>
      <ChemicalFormula formula={phaseText(row, labelOf)} />
      {row.note ? <div className="dara-cell-note">{row.note}</div> : null}
    </> },
    { title: <Tooltip title="D = K·λ / intercept of the line"><span>W–H size (nm)</span></Tooltip>, key: 'size', className: 'dara-num',
      render: (_, row) => gated(row, `W–H size ${withEsd(row.size_nm, row.size_esd_nm, 0)} nm`, withEsd(row.size_nm, row.size_esd_nm, 0)) },
    { title: <Tooltip title="ε = slope of β·cos θ against 4·sin θ (β = 4ε·tan θ, FWHM-based). Definitions differ: the Rietveld microstrain is BGMN's sqrt(k2)."><span>W–H strain (%)</span></Tooltip>,
      key: 'strain', className: 'dara-num',
      render: (_, row) => gated(row, `W–H strain ${withEsd(row.strain_percent, row.strain_esd_percent, 3)} %`, withEsd(row.strain_percent, row.strain_esd_percent, 3)) },
    { title: 'R²', dataIndex: 'r_squared', className: 'dara-num', render: fixed(3) },
    { title: <Tooltip title="Assigned peaks with a fitted FWHM above the instrument FWHM; the others are left out"><span>Peaks used</span></Tooltip>, key: 'used', className: 'dara-num',
      render: (_, row) => `${row.peaks_used}${row.unresolved ? ` (${row.unresolved} left out)` : ''}` },
    { title: <Tooltip title="Crystallite size of the Rietveld fit (BGMN GrainSize)"><span>Rietveld size (nm)</span></Tooltip>, key: 'rsize', className: 'dara-num',
      render: (_, row) => withEsd(row.rietveld?.size_nm, row.rietveld?.size_esd_nm, 0) },
    { title: <Tooltip title="Microstrain of the Rietveld fit: BGMN's sqrt(k2). Definitions differ from the W–H strain."><span>Rietveld strain (%)</span></Tooltip>, key: 'rstrain', className: 'dara-num',
      render: (_, row) => withEsd(row.rietveld?.strain_percent, row.rietveld?.strain_esd_percent, 3) },
  ];
  const plotted = phases.filter(entry => entry.points.length);
  return <>
    <div className="dara-fit-table" style={{ marginBottom: 12 }}>
      <Table size="small" rowKey="key" dataSource={phases} columns={columns} pagination={false} scroll={{ x: 'max-content' }} />
    </div>
    <div className="dara-wh-grid">
      {plotted.map(entry => <WilliamsonHallPlot key={entry.phase} entry={entry} />)}
    </div>
  </>;
}

/** β·cos θ against 4·sin θ for one phase, with FWHM error bars and the fitted line. */
function WilliamsonHallPlot({ entry }) {
  const labelOf = React.useContext(PhaseLabels);
  const xs = entry.points.map(point => point.x);
  const top = Math.max(...xs) * 1.05;
  const data = [{ x: xs, y: entry.points.map(point => point.y), text: entry.points.map(point => point.hkl ?? ''), type: 'scatter', mode: 'markers',
    error_y: { type: 'data', array: entry.points.map(point => point.y_err), visible: true, thickness: 1, width: 3, color: '#3f6c8f' },
    marker: { size: 7, color: '#3f6c8f' }, hovertemplate: 'hkl %{text}<br>4·sin θ %{x:.3f}<br>β·cos θ %{y:.5f} rad<extra></extra>' }];
  if (entry.intercept != null) {
    data.push({ x: [0, top], y: [entry.intercept, entry.intercept + entry.slope * top], type: 'scatter', mode: 'lines',
      // An unreliable line is drawn quiet: grey and dashed.
      line: entry.reliable ? { color: '#b5482a', width: 1.5 } : { color: '#8c8c8c', width: 1, dash: 'dash' }, hoverinfo: 'skip' });
  }
  // The title is drawn by the page (ChemicalFormula with the space group, as in the table), not by Plotly.
  const layout = { height: 250, margin: { l: 64, r: 12, t: 8, b: 44 }, showlegend: false,
    xaxis: { title: { text: '4·sin θ' }, rangemode: 'tozero', zeroline: false },
    yaxis: { title: { text: 'β·cos θ (rad)', standoff: 8 }, automargin: true, rangemode: 'tozero', zeroline: false } };
  return <figure style={{ margin: 0 }}>
    <figcaption className="dara-wh-title"><ChemicalFormula formula={phaseText(entry, labelOf)} /></figcaption>
    <Plot data={data} layout={layout} config={{ displaylogo: false, responsive: true }} useResizeHandler style={{ width: '100%', height: 250 }} />
  </figure>;
}

/** Reflections of the displayed fit (hkl, 2θ, d, intensity, FWHM) from GET /task/{id}/peaks, filterable by phase, with the CSV from /peaks.csv. */
function CalculatedReflections({ taskId, sel, active, widths = true }) {
  const labelOf = React.useContext(PhaseLabels);
  const [body, setBody] = React.useState(null);
  const [error, setError] = React.useState('');
  const [phase, setPhase] = React.useState(undefined);
  const query = `sel=${encodeURIComponent(sel)}`;

  React.useEffect(() => {
    setPhase(undefined);
    setBody(null);
    setError('');
    if (!active) return undefined;
    let current = true;
    requestJson(`/task/${taskId}/peaks?${query}`)
      .then(data => { if (current) setBody(data); })
      .catch(failure => { if (current) setError(failure.missing ? NOT_AVAILABLE : failure.message); });
    return () => { current = false; };
  }, [taskId, query, active]);

  if (error) return <Alert type="error" showIcon message="The peak list could not be loaded." description={error} />;
  // Keeps the workspace height while loading, so switching tabs does not scroll the view back up.
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin /></div>;

  const rows = (body.rows || []).map((row, index) => ({ ...row, key: index }));
  const phases = [...new Map(rows.map(row => [row.phase, phaseText(row, labelOf)])).entries()];
  const shown = phase ? rows.filter(row => row.phase === phase) : rows;
  const columns = [
    { title: 'Phase', key: 'phase', render: (_, row) => <ChemicalFormula formula={phaseText(row, labelOf)} /> },
    { title: <Tooltip title="Indices in the database setting (as on cards). For a phase refined in a subgroup setting, the refined-setting indices follow in brackets."><span>hkl</span></Tooltip>, key: 'hkl',
      render: (_, row) => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{row.hkl_database_setting ? <>{row.hkl_database_setting} <Text type="secondary">{`[${row.h} ${row.k} ${row.l}]`}</Text></> : `${row.h} ${row.k} ${row.l}`}</span> },
    { title: '2θ (°)', dataIndex: 'two_theta_deg', className: 'dara-num', sorter: (a, b) => a.two_theta_deg - b.two_theta_deg, render: fixed(3) },
    { title: <Tooltip title={body.wavelength ? `Wavelength: ${body.wavelength}` : null}><span>d (Å)</span></Tooltip>, dataIndex: 'd_angstrom', className: 'dara-num', render: fixed(4) },
    { title: 'Intensity', dataIndex: 'intensity', className: 'dara-num', sorter: (a, b) => a.intensity - b.intensity, render: fixed(1) },
    { title: <Tooltip title="Relative to the strongest reflection of the same phase"><span>Rel. in phase (%)</span></Tooltip>, dataIndex: 'intensity_rel_phase_percent', className: 'dara-num', render: fixed(1) },
    { title: <Tooltip title="Relative to the strongest reflection of all phases"><span>Rel. overall (%)</span></Tooltip>, dataIndex: 'intensity_rel_all_percent', className: 'dara-num', render: fixed(1) },
    { title: <Tooltip title="Kα1 FWHM of the calculated profile. “overlapped”: the reflection merges with a neighbour, so its width was not measured. “—”: no width available."><span>FWHM (°)</span></Tooltip>, dataIndex: 'fwhm_deg', className: 'dara-num',
      render: (value, row) => (value == null
        ? <Tooltip title={row.fwhm_note || null}><Text type="secondary">{row.fwhm_note === 'overlapped' ? 'overlapped' : '—'}</Text></Tooltip>
        : fixed(3)(value)) },
  ].filter(column => widths || column.dataIndex !== 'fwhm_deg'); // reference sticks have no width
  return <>
    <div className="dara-toolbar">
      <Select size="small" allowClear aria-label="Filter by phase" placeholder="All phases" style={{ minWidth: 240 }} value={phase} onChange={setPhase}
        options={phases.map(([value, label]) => ({ value, label }))} />
      <Text type="secondary" className="dara-num">{`${shown.length} reflection${shown.length === 1 ? '' : 's'}`}</Text>
      <Button size="small" icon={<DownloadOutlined />} href={`${API}/task/${taskId}/peaks.csv?${query}`}>Download CSV</Button>
    </div>
    <div className="dara-fit-table">
      <Table size="small" rowKey="key" dataSource={shown} columns={columns} scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }} />
    </div>
  </>;
}
