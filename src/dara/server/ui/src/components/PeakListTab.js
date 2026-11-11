import React from 'react';
import { Alert, Button, Select, Space, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula from './ChemicalFormula';
import { spacegroupOfPhase } from './versions.mjs';

const { Text } = Typography;

const fixed = digits => value => (value == null ? '—' : Number(value).toFixed(digits));

/**
 * "Peaks" tab of the fit view: the reflections of the displayed fit (hkl, 2θ, d, intensity, FWHM) from
 * GET /task/{id}/peaks, filterable by phase, with the CSV from /peaks.csv. Loads when the tab is shown.
 */
export default function PeakListTab({ taskId, sel, active }) {
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
  // Formula with the space group, so polymorphs of one formula (R-3c and R-3m Fe2O3) stay apart.
  const phaseText = row => { const label = row.phase_label || row.phase; const sg = spacegroupOfPhase(row.phase); return sg && !String(label).includes('·') ? `${label} (SG ${sg})` : label; };
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
