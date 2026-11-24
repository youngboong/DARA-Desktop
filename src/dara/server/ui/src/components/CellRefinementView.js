import React from 'react';
import { Alert, Button, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula, { FormulaText } from './ChemicalFormula';
import { spacegroupOfPhase } from './versions.mjs';
import { cellQuery, cellText, parameterRows, peakCounts, shiftText, sigmaText } from './cellRefinement.mjs';

const { Text } = Typography;
// The page's label of the phase (`labelOf`, as in the legend and the Peaks tab) first; the server's otherwise.
const phaseText = (row, labelOf = null) => { const own = labelOf?.(row.phase); if (own) return own; const label = row.phase_label || row.phase; const sg = spacegroupOfPhase(row.phase); return sg ? `${label} (SG ${sg})` : label; };
const angle = (value, digits = 4) => (value == null ? '—' : Number(value).toFixed(digits).replace(/^-(?=0\.?0*$)/, '').replace(/^-/, '−'));
const signed = (value, digits) => `${value < 0 ? '−' : '+'}${Math.abs(value).toFixed(digits)}`;
const esd = (item, digits, unit) => (item?.value == null ? null : `${signed(Number(item.value), digits)}${item.esd != null ? ` ± ${Number(item.esd).toFixed(digits)}` : ''} ${unit}`);
// A difference of 3 combined e.s.d. or more is more than counting statistics: it stands out.
const SIGNIFICANT = 3;

const PARAMETER_COLUMNS = [
  { title: 'Parameter', dataIndex: 'label', render: (label, row) => (row.unit === 'Å³' ? <>V (Å<sup>3</sup>)</> : `${label} (${row.unit === 'deg' ? '°' : 'Å'})`) },
  // The unit is in the parameter column: the values go without it.
  { title: 'From peaks', key: 'peaks', className: 'dara-num', render: (_, row) => cellText(row.peaks, row.unit) },
  { title: <Tooltip title="Lattice parameters of the Rietveld fit (the whole pattern)"><span>Rietveld</span></Tooltip>, key: 'rietveld', className: 'dara-num',
    render: (_, row) => cellText(row.rietveld, row.unit) },
  { title: <Tooltip title="(From peaks − Rietveld) in combined e.s.d.; 3σ or more is more than counting statistics"><span>Δ</span></Tooltip>, key: 'z', className: 'dara-num',
    render: (_, row) => <Tooltip title={row.delta == null ? null : `${signed(row.delta, row.unit === 'Å³' ? 3 : 5)} ${row.unit === 'deg' ? '°' : row.unit}`}>
      <span style={Math.abs(row.z) >= SIGNIFICANT ? { color: 'var(--dara-review)' } : undefined}>{sigmaText(row.z)}</span></Tooltip> },
];

const PEAK_COLUMNS = [
  { title: 'hkl', dataIndex: 'hkl', render: (value, row) => <span style={{ whiteSpace: 'nowrap' }}>{row.hkl_database_setting || value}</span> },
  { title: '2θ obs (°)', dataIndex: 'two_theta_obs', className: 'dara-num', render: value => angle(value) },
  { title: '2θ calc (°)', dataIndex: 'two_theta_calc', className: 'dara-num', render: value => angle(value) },
  { title: 'Δ (°)', dataIndex: 'delta', className: 'dara-num', render: value => angle(value) },
  { title: 'Used', key: 'used', render: (_, row) => (row.used ? 'yes' : <Text type="secondary">{row.reason || 'no'}</Text>) },
];

/** One phase: the refined cell beside the Rietveld cell, the shift, r.m.s. Δ2θ and the per-peak table. */
function PhaseCell({ phase, labelOf = null }) {
  const counts = peakCounts(phase);
  const shift = shiftText(phase);
  const facts = [phase.crystal_system, `${counts.used} of ${counts.total} peaks used`,
    phase.rms_deg != null ? `r.m.s. Δ2θ ${Number(phase.rms_deg).toFixed(4)}°` : null, shift].filter(Boolean);
  return <section className="dara-cell-phase" aria-label={`Unit cell of ${phase.phase_label || phase.phase}`}>
    <div className="dara-cell-head">
      <strong><ChemicalFormula formula={phaseText(phase, labelOf)} /></strong>
      <span className="dara-footnote" style={{ display: 'inline' }}>{facts.join(' · ')}</span>
    </div>
    {phase.parameters ? <Table size="small" rowKey="key" pagination={false} columns={PARAMETER_COLUMNS} dataSource={parameterRows(phase)} />
      : <Text type="secondary"><FormulaText text={phase.note || 'The cell could not be refined from the peaks of this phase.'} /></Text>}
    {phase.parameters && phase.note ? <div className="dara-footnote" style={{ marginTop: 4 }}><FormulaText text={phase.note} /></div> : null}
    {counts.total ? <details className="dara-footnote" style={{ marginTop: 6 }}>
      <summary>{`Peaks (${counts.total})`}</summary>
      <Table size="small" rowKey={(row, index) => `${row.hkl}-${index}`} pagination={false} columns={PEAK_COLUMNS} dataSource={phase.peaks} style={{ marginTop: 4 }} />
    </details> : null}
  </section>;
}

/**
 * "Unit cell" view of the Peaks tab: lattice parameters refined by least squares on the Kα1
 * positions of the observed peaks (GET /task/{id}/cell-refinement) with the shift `model`.
 * `onPhases(list)` hands the fit's phases to the shift-model select (internal-standard choices).
 */
export default function CellRefinementView({ taskId, sel, model, active, onPhases, phaseLabel = null }) {
  const [body, setBody] = React.useState(null);
  const [error, setError] = React.useState('');
  const query = `sel=${encodeURIComponent(sel)}&${cellQuery(model)}`;
  React.useEffect(() => {
    setBody(null); setError('');
    if (!active) return undefined;
    let current = true;
    requestJson(`/task/${taskId}/cell-refinement?${query}`)
      .then(data => { if (current) { setBody(data); onPhases?.(data.phases || [], data.correction); } })
      .catch(failure => { if (current) setError(failure.missing ? NOT_AVAILABLE : failure.message); });
    return () => { current = false; };
  }, [taskId, query, active, onPhases]);
  if (error) return <Alert type="error" showIcon message="The unit cell could not be refined." description={error} />;
  if (!body) return <div style={{ minHeight: 480, paddingTop: 48, textAlign: 'center' }}><Spin tip="Fitting the peak positions" /></div>;
  const rietveld = body.rietveld_shift || {};
  const rietveldShift = [esd(rietveld.displacement_mm, 3, 'mm') && `specimen displacement ${esd(rietveld.displacement_mm, 3, 'mm')}`,
    rietveld.zero_deg?.value ? `zero ${esd(rietveld.zero_deg, 3, '°')}` : null].filter(Boolean).join(', ');
  const correction = body.correction;
  const notCertified = /not a certified/i.test(correction?.certified_cell_source || '');
  return <>
    <div className="dara-toolbar">
      <Button size="small" icon={<DownloadOutlined />} href={`${API}/task/${taskId}/cell-refinement.csv?${query}`}>Download CSV</Button>
      {rietveldShift ? <Text type="secondary" className="dara-num" style={{ fontSize: 12 }}>{`Rietveld fit: ${rietveldShift}`}</Text> : null}
    </div>
    {correction ? <div className="dara-footnote" style={{ marginBottom: 8 }}>
      {'2θ corrected with the internal standard '}<ChemicalFormula formula={correction.phase_label || correction.standard} />
      {': '}{[esd(correction.model?.zero_deg, 3, '°') && `zero ${esd(correction.model.zero_deg, 3, '°')}`,
        esd(correction.model?.displacement_mm, 3, 'mm') && `displacement ${esd(correction.model.displacement_mm, 3, 'mm')}`,
        correction.rms_deg != null ? `r.m.s. ${Number(correction.rms_deg).toFixed(4)}°` : null].filter(Boolean).join(', ')}
      {'. Cell: '}<span style={notCertified ? { color: 'var(--dara-review)' } : undefined}>{correction.certified_cell_source}</span>.
      {correction.note ? ` ${correction.note}` : ''}
    </div> : null}
    {(body.phases || []).length ? <div className="dara-cell-grid">{body.phases.map(phase => <PhaseCell key={phase.phase} phase={phase} labelOf={phaseLabel} />)}</div>
      : <Alert type="info" showIcon message="No phase of this fit has assigned peaks to refine." />}
    <Text className="dara-footnote">From peak positions of the profile fits; the Rietveld cell uses the whole pattern.</Text>
  </>;
}
