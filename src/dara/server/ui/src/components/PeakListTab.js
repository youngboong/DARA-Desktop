import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, InputNumber, Radio, Select, Space, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula from './ChemicalFormula';
import { spacegroupOfPhase } from './versions.mjs';

const { Text } = Typography;
const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <Spin /> });

const SCHERRER_K = [
  { value: 0.89, label: 'K = 0.89' },
  { value: 0.94, label: 'K = 0.94' },
  { value: 1, label: 'K = 1.0' },
];
const K_HELP = 'Scherrer constant for the FWHM. 0.89: spherical crystallites (the usual value); 0.94: cubic crystallites; 1.0: no crystallite shape assumed.';
const INSTRUMENT_HELP = 'FWHM of your instrument at these angles (from a standard such as LaB6 or Si), removed before Scherrer and Williamson–Hall: quadratically for Gaussian-like peaks (η < 0.5), linearly otherwise. Remembered for this instrument profile.';
const WH_HELP = 'Williamson–Hall: β·cos θ = K·λ/D + ε·4·sin θ, a straight line through the fitted peaks of one phase (β: Kα1 FWHM with the instrument FWHM removed, in radians). Needs 4 or more fitted peaks over at least 30° 2θ.';
const FIT_NOTE_HELP = {
  merged: 'Found closer than one FWHM to a stronger peak: fitted as that peak, which carries the FWHM',
  overlapped: 'Overlaps its neighbours too much for a width of its own',
  'too weak': 'The width is not determined (uncertainty above 30%)',
  'fit failed': 'The profile fit did not converge or the width ran to a limit',
};
const QUIET_NOTE ='Single-peak estimates. The Rietveld size of each phase (instrument-corrected with the instrument profile) is the reported value.';

const fixed = digits => value => (value == null ? '—' : Number(value).toFixed(digits));

// Formula with the space group, so polymorphs of one formula (R-3c and R-3m Fe2O3) stay apart.
const phaseText = row => { const label = row.phase_label || row.phase; const sg = spacegroupOfPhase(row.phase); return sg && !String(label).includes('·') ? `${label} (SG ${sg})` : label; };

// The instrument FWHM is remembered per instrument profile; the page works without storage.
const fwhmKey = profile => `dara-instrument-fwhm:${profile}`;
function storedFwhm(profile) {
  try { const value = Number(window.localStorage.getItem(fwhmKey(profile))); return value > 0 ? value : null; } catch (e) { return null; }
}
function storeFwhm(profile, value) {
  try { if (value) window.localStorage.setItem(fwhmKey(profile), String(value)); else window.localStorage.removeItem(fwhmKey(profile)); } catch (e) { /* private mode */ }
}

/**
 * "Peaks" tab of the fit view: the calculated reflections of the displayed fit, the peaks found in
 * the measured pattern with their fitted FWHM and Scherrer size, or the Williamson–Hall fit per phase.
 * Loads when the tab is shown.
 */
export default function PeakListTab({ taskId, sel, active }) {
  const [view, setView] = React.useState('calculated');
  const [k, setK] = React.useState(0.89);
  const [instrument, setInstrument] = React.useState(null);
  const [profile, setProfile] = React.useState(null);
  React.useEffect(() => { if (profile !== null) setInstrument(storedFwhm(profile)); }, [profile]);
  const changeInstrument = value => {
    const next = Number.isFinite(value) && value > 0 ? value : null;
    setInstrument(next);
    if (profile !== null) storeFwhm(profile, next);
  };
  const query = `sel=${encodeURIComponent(sel)}&k=${k}${instrument ? `&instrument_fwhm=${instrument}` : ''}`;
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <Radio.Group size="small" optionType="button" value={view} onChange={event => setView(event.target.value)}
        aria-label="Peak list" options={[{ value: 'calculated', label: 'Calculated reflections' }, { value: 'observed', label: 'Observed peaks' },
          { value: 'size', label: <Tooltip title={WH_HELP}><span>Size / strain (Williamson–Hall)</span></Tooltip> }]} />
      {view === 'calculated' ? null : <>
        <Tooltip title={K_HELP}>
          <Select size="small" aria-label="Scherrer constant" value={k} onChange={setK} options={SCHERRER_K} style={{ width: 96 }} />
        </Tooltip>
        <Tooltip title={INSTRUMENT_HELP}><Text>Instrument FWHM (°)</Text></Tooltip>
        <InputNumber size="small" aria-label="Instrument FWHM in degrees" min={0} max={0.5} step={0.005} placeholder="0"
          value={instrument} onChange={changeInstrument} style={{ width: 90 }} />
        <Text type="secondary" style={{ fontSize: 12 }}>Constant value; this instrument's broadening varies with angle, so sizes stay approximate.</Text>
      </>}
    </Space>
    {view === 'observed' ? <ObservedPeaks taskId={taskId} query={query} corrected={Boolean(instrument)} onProfile={setProfile} active={active} />
      : view === 'size' ? <WilliamsonHall taskId={taskId} query={query} onProfile={setProfile} active={active} />
        : <CalculatedReflections taskId={taskId} sel={sel} active={active} />}
  </>;
}

/** GET `path` while `active`; tells `onProfile` the task's instrument profile. */
function useSizeRequest(path, active, onProfile) {
  const [body, setBody] = React.useState(null);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    setBody(null);
    setError('');
    if (!active) return undefined;
    let current = true;
    requestJson(path)
      .then(data => { if (current) { setBody(data); onProfile(data.instrument_profile ?? ''); } })
      .catch(failure => { if (current) setError(failure.missing ? NOT_AVAILABLE : failure.message); });
    return () => { current = false; };
  }, [path, active, onProfile]);
  return [body, error];
}

const withEsd = (value, esd, digits) => (value == null ? '—'
  : `${Number(value).toFixed(digits)}${esd != null && Number.isFinite(esd) ? ` ± ${Number(esd).toFixed(digits)}` : ''}`);

/** Peaks of the measured pattern (GET /task/{id}/observed-peaks): fitted FWHM, Scherrer size and the assignment to the reflections of the fit. */
function ObservedPeaks({ taskId, query, corrected, onProfile, active }) {
  const [filter, setFilter] = React.useState('all');
  const [body, error] = useSizeRequest(`/task/${taskId}/observed-peaks?${query}`, active, onProfile);

  if (error) return <Alert type="error" showIcon message="The observed peaks could not be loaded." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Fitting the peaks of the measured pattern" /></div>;

  const rows = (body.rows || []).map((row, index) => ({ ...row, key: index, number: index + 1 }));
  const unassigned = rows.filter(row => !row.phase).length;
  const shown = filter === 'all' ? rows : rows.filter(row => (filter === 'assigned') === Boolean(row.phase));
  const byNumber = key => (a, b) => (a[key] ?? -Infinity) - (b[key] ?? -Infinity);
  const sizeTitle = corrected
    ? <Tooltip title="Scherrer estimate from this peak alone: K·λ / (β·cos θ), β the FWHM with the instrument FWHM removed"><span>Size (nm)</span></Tooltip>
    : <Tooltip title="No instrument broadening removed: a lower bound. Enter the FWHM of your instrument (from a standard such as LaB6 or Si) to correct it."><span>Size (nm), uncorrected</span></Tooltip>;
  const columns = [
    { title: '#', dataIndex: 'number', className: 'dara-num', sorter: byNumber('number') },
    { title: '2θ (°)', dataIndex: 'two_theta_deg', className: 'dara-num', sorter: byNumber('two_theta_deg'), render: fixed(3) },
    { title: <Tooltip title={body.wavelength ? `Bragg's law, Kα1 of ${body.wavelength}` : null}><span>d (Å)</span></Tooltip>, dataIndex: 'd_angstrom', className: 'dara-num', sorter: byNumber('d_angstrom'), render: fixed(4) },
    { title: <Tooltip title="Integrated intensity of the peak fitted by EFLECH (BGMN)"><span>Intensity</span></Tooltip>, dataIndex: 'intensity', className: 'dara-num', sorter: byNumber('intensity'), render: fixed(1) },
    { title: <Tooltip title="Relative to the strongest observed peak"><span>Rel. I (%)</span></Tooltip>, dataIndex: 'intensity_rel_percent', className: 'dara-num', sorter: byNumber('intensity_rel_percent'), render: fixed(1) },
    { title: <Tooltip title="Kα1 full width at half maximum from a pseudo-Voigt fit of the measured peak (instrument + sample)"><span>FWHM (°)</span></Tooltip>, dataIndex: 'fwhm_deg',
      className: 'dara-num', sorter: byNumber('fwhm_deg'),
      render: (value, row) => (value == null ? <Tooltip title={FIT_NOTE_HELP[row.fit_note] || null}><Text type="secondary">{row.fit_note || '—'}</Text></Tooltip>
        : <Tooltip title={row.eta == null ? null : `η = ${Number(row.eta).toFixed(2)} (0 Gaussian, 1 Lorentzian)`}>{withEsd(value, row.fwhm_esd_deg, 3)}</Tooltip>) },
    { title: 'Phase', key: 'phase', sorter: (a, b) => Number(Boolean(a.phase)) - Number(Boolean(b.phase)) || String(a.phase_label).localeCompare(String(b.phase_label)),
      render: (_, row) => (row.phase ? <ChemicalFormula formula={phaseText(row)} /> : <span style={{ color: 'var(--dara-review)' }}>Unassigned</span>) },
    { title: <Tooltip title="Indices of the nearest reflection, in the database setting"><span>hkl</span></Tooltip>, dataIndex: 'hkl',
      render: value => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{value ?? <Text type="secondary">—</Text>}</span> },
    { title: <Tooltip title="Observed − calculated 2θ of the assigned reflection"><span>Δ2θ (°)</span></Tooltip>, dataIndex: 'delta_two_theta_deg', className: 'dara-num',
      sorter: (a, b) => Math.abs(a.delta_two_theta_deg ?? 1e9) - Math.abs(b.delta_two_theta_deg ?? 1e9), render: fixed(3) },
    { title: sizeTitle, dataIndex: 'size_nm', className: 'dara-num', sorter: byNumber('size_nm'),
      render: (value, row) => (value != null ? fixed(0)(value)
        : <Text type="secondary">{row.size_note === 'below instrument resolution' ? '> resolution limit' : '—'}</Text>) },
  ];
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <Radio.Group size="small" optionType="button" value={filter} onChange={event => setFilter(event.target.value)} aria-label="Filter by assignment"
        options={[{ value: 'all', label: 'All' }, { value: 'assigned', label: 'Assigned' }, { value: 'unassigned', label: 'Unassigned' }]} />
      <Text type="secondary" className="dara-num">{`${rows.length} peak${rows.length === 1 ? '' : 's'}, ${unassigned} unassigned`}</Text>
      <Button icon={<DownloadOutlined />} href={`${API}/task/${taskId}/observed-peaks.csv?${query}`}>Download CSV</Button>
    </Space>
    <Table size="small" rowKey="key" dataSource={shown} columns={columns} scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }} />
    <Text type="secondary" style={{ fontSize: 12 }}>Peaks found by EFLECH in the analysis range, each assigned to the nearest reflection within max(0.1°, the FWHM of its calculated peak). {QUIET_NOTE}</Text>
  </>;
}

// A fitted but unreliable W–H line shows no number; the reason and the number stay in the tooltip (and the API).
const gated = (row, detail, text) => (row.reliable === false && row.intercept != null
  ? <Tooltip title={`${row.note}. ${detail}`}><Text type="secondary">Not reliable</Text></Tooltip> : text);

/** Williamson–Hall fit per phase (GET /task/{id}/williamson-hall) beside the Rietveld size and strain. */
function WilliamsonHall({ taskId, query, onProfile, active }) {
  const [body, error] = useSizeRequest(`/task/${taskId}/williamson-hall?${query}`, active, onProfile);

  if (error) return <Alert type="error" showIcon message="The size / strain estimates could not be loaded." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Fitting the peaks of the measured pattern" /></div>;

  const phases = (body.phases || []).map(entry => ({ ...entry, key: entry.phase }));
  if (!phases.length) return <Alert type="info" showIcon message="No observed peak is assigned to a phase of this fit." />;
  const columns = [
    { title: 'Phase', key: 'phase', render: (_, row) => <>
      <ChemicalFormula formula={phaseText(row)} />
      {row.note ? <div><Text type="secondary" style={{ fontSize: 12 }}>{row.note}</Text></div> : null}
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
    <Table size="small" rowKey="key" dataSource={phases} columns={columns} pagination={false} scroll={{ x: 'max-content' }} style={{ marginBottom: 12 }} />
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
      {plotted.map(entry => <WilliamsonHallPlot key={entry.phase} entry={entry} />)}
    </div>
    <Text type="secondary" style={{ fontSize: 12 }}>{QUIET_NOTE}</Text>
  </>;
}

/** β·cos θ against 4·sin θ for one phase, with FWHM error bars and the fitted line. */
function WilliamsonHallPlot({ entry }) {
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
  const layout = { height: 260, margin: { l: 70, r: 12, t: 32, b: 44 }, showlegend: false, title: { text: entry.phase_label || entry.phase, font: { size: 13 } },
    xaxis: { title: { text: '4·sin θ' }, rangemode: 'tozero', zeroline: false }, yaxis: { title: { text: 'β·cos θ (rad)' }, rangemode: 'tozero', zeroline: false } };
  return <div style={{ flex: '1 1 340px', maxWidth: 520 }}>
    <Plot data={data} layout={layout} config={{ displaylogo: false, responsive: true }} useResizeHandler style={{ width: '100%', height: 260 }} />
  </div>;
}

/** Reflections of the displayed fit (hkl, 2θ, d, intensity, FWHM) from GET /task/{id}/peaks, filterable by phase, with the CSV from /peaks.csv. */
function CalculatedReflections({ taskId, sel, active }) {
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
  const phases = [...new Map(rows.map(row => [row.phase, phaseText(row)])).entries()];
  const shown = phase ? rows.filter(row => row.phase === phase) : rows;
  const columns = [
    { title: 'Phase', key: 'phase', render: (_, row) => <ChemicalFormula formula={phaseText(row)} /> },
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
  ];
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <Select allowClear aria-label="Filter by phase" placeholder="All phases" style={{ minWidth: 240 }} value={phase} onChange={setPhase}
        options={phases.map(([value, label]) => ({ value, label }))} />
      <Text type="secondary" className="dara-num">{`${shown.length} reflection${shown.length === 1 ? '' : 's'}`}</Text>
      <Button icon={<DownloadOutlined />} href={`${API}/task/${taskId}/peaks.csv?${query}`}>Download CSV</Button>
    </Space>
    <Table size="small" rowKey="key" dataSource={shown} columns={columns} scroll={{ x: 'max-content' }}
      pagination={{ pageSize: 50, showSizeChanger: false, hideOnSinglePage: true }} />
  </>;
}
