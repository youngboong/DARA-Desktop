import React from 'react';
import { Alert, Button, Radio, Select, Space, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula from './ChemicalFormula';
import { spacegroupOfPhase } from './versions.mjs';

const { Text } = Typography;

const fixed = digits => value => (value == null ? '—' : Number(value).toFixed(digits));

// Formula with the space group, so polymorphs of one formula (R-3c and R-3m Fe2O3) stay apart.
const phaseText = row => { const label = row.phase_label || row.phase; const sg = spacegroupOfPhase(row.phase); return sg && !String(label).includes('·') ? `${label} (SG ${sg})` : label; };

/**
 * "Peaks" tab of the fit view: the calculated reflections of the displayed fit, or the peaks found in
 * the measured pattern with their assignment to those reflections. Loads when the tab is shown.
 */
export default function PeakListTab({ taskId, sel, active }) {
  const [view, setView] = React.useState('calculated');
  return <>
    <Radio.Group size="small" optionType="button" value={view} onChange={event => setView(event.target.value)} style={{ marginBottom: 8 }}
      aria-label="Peak list" options={[{ value: 'calculated', label: 'Calculated reflections' }, { value: 'observed', label: 'Observed peaks' }]} />
    {view === 'observed'
      ? <ObservedPeaks taskId={taskId} sel={sel} active={active} />
      : <CalculatedReflections taskId={taskId} sel={sel} active={active} />}
  </>;
}

/** Peaks of the measured pattern (GET /task/{id}/observed-peaks), each assigned to the nearest reflection of the fit. */
function ObservedPeaks({ taskId, sel, active }) {
  const [body, setBody] = React.useState(null);
  const [error, setError] = React.useState('');
  const [filter, setFilter] = React.useState('all');
  const query = `sel=${encodeURIComponent(sel)}`;

  React.useEffect(() => {
    setBody(null);
    setError('');
    if (!active) return undefined;
    let current = true;
    requestJson(`/task/${taskId}/observed-peaks?${query}`)
      .then(data => { if (current) setBody(data); })
      .catch(failure => { if (current) setError(failure.missing ? NOT_AVAILABLE : failure.message); });
    return () => { current = false; };
  }, [taskId, query, active]);

  if (error) return <Alert type="error" showIcon message="The observed peaks could not be loaded." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Finding peaks in the measured pattern" /></div>;

  const rows = (body.rows || []).map((row, index) => ({ ...row, key: index, number: index + 1 }));
  const unassigned = rows.filter(row => !row.phase).length;
  const shown = filter === 'all' ? rows : rows.filter(row => (filter === 'assigned') === Boolean(row.phase));
  const byNumber = key => (a, b) => (a[key] ?? -Infinity) - (b[key] ?? -Infinity);
  const columns = [
    { title: '#', dataIndex: 'number', className: 'dara-num', sorter: byNumber('number') },
    { title: '2θ (°)', dataIndex: 'two_theta_deg', className: 'dara-num', sorter: byNumber('two_theta_deg'), render: fixed(3) },
    { title: <Tooltip title={body.wavelength ? `Bragg's law, Kα1 of ${body.wavelength}` : null}><span>d (Å)</span></Tooltip>, dataIndex: 'd_angstrom', className: 'dara-num', sorter: byNumber('d_angstrom'), render: fixed(4) },
    { title: <Tooltip title="Integrated intensity of the peak fitted by EFLECH (BGMN)"><span>Intensity</span></Tooltip>, dataIndex: 'intensity', className: 'dara-num', sorter: byNumber('intensity'), render: fixed(1) },
    { title: <Tooltip title="Relative to the strongest observed peak"><span>Rel. I (%)</span></Tooltip>, dataIndex: 'intensity_rel_percent', className: 'dara-num', sorter: byNumber('intensity_rel_percent'), render: fixed(1) },
    { title: 'Phase', key: 'phase', sorter: (a, b) => Number(Boolean(a.phase)) - Number(Boolean(b.phase)) || String(a.phase_label).localeCompare(String(b.phase_label)),
      render: (_, row) => (row.phase ? <ChemicalFormula formula={phaseText(row)} /> : <span style={{ color: 'var(--dara-review)' }}>Unassigned</span>) },
    { title: <Tooltip title="Indices of the nearest reflection, in the database setting"><span>hkl</span></Tooltip>, dataIndex: 'hkl',
      render: value => <span style={{ fontVariantNumeric: 'tabular-nums' }}>{value ?? <Text type="secondary">—</Text>}</span> },
    { title: <Tooltip title="Observed − calculated 2θ of the assigned reflection"><span>Δ2θ (°)</span></Tooltip>, dataIndex: 'delta_two_theta_deg', className: 'dara-num',
      sorter: (a, b) => Math.abs(a.delta_two_theta_deg ?? 1e9) - Math.abs(b.delta_two_theta_deg ?? 1e9), render: fixed(3) },
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
    <Text type="secondary" style={{ fontSize: 12 }}>Peaks found by EFLECH in the analysis range. Each is assigned to the nearest reflection within max(0.1°, the FWHM of its calculated peak).</Text>
  </>;
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
    { title: <Tooltip title="“overlapped”: the reflection merges with a neighbour, so its width was not measured. “—”: no width available."><span>FWHM (°)</span></Tooltip>, dataIndex: 'fwhm_deg', className: 'dara-num',
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
