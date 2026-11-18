import React from 'react';
import { Alert, Button, Collapse, Spin, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula, { FormulaText } from './ChemicalFormula';
import { spacegroupOfPhase } from './versions.mjs';
import { lineText, missingPhases, rirRows, signedText } from './rirView.mjs';

const { Text } = Typography;
const percent = value => (value == null ? '—' : Number(value).toFixed(1));
const phaseText = row => { const label = row.phase_label || row.phase; const sg = spacegroupOfPhase(row.phase); return sg ? `${label} (SG ${sg})` : label; };

/** The RIR table of `sel` (GET /task/{id}/rir), loaded when mounted. */
function RirTable({ taskId, sel }) {
  const [body, setBody] = React.useState(null);
  const [error, setError] = React.useState('');
  const query = `sel=${encodeURIComponent(sel)}`;
  React.useEffect(() => {
    let current = true;
    setBody(null); setError('');
    requestJson(`/task/${taskId}/rir?${query}`)
      .then(data => { if (current) setBody(data); })
      .catch(failure => { if (current) setError(failure.missing ? NOT_AVAILABLE : failure.message); });
    return () => { current = false; };
  }, [taskId, query]);
  if (error) return <Alert type="error" showIcon message="The RIR estimate could not be calculated." description={error} />;
  if (!body) return <Spin size="small" />;
  const missing = missingPhases(body);
  const columns = [
    { title: 'Phase', key: 'phase', render: (_, row) => <ChemicalFormula formula={phaseText(row)} /> },
    { title: <Tooltip title="Reference intensity ratio: intensity of the strongest line relative to corundum 113 in a 50:50 mixture by weight"><span>I/Ic</span></Tooltip>,
      dataIndex: 'rir', className: 'dara-num', render: (value, row) => (value == null ? '—' : <Tooltip title={row.rir_source}>{Number(value).toFixed(2)}</Tooltip>) },
    { title: 'Line used', key: 'line', render: (_, row) => {
      const text = lineText(row);
      if (!text) return <Text type="secondary">{row.note || 'no usable line'}</Text>;
      return <span style={{ whiteSpace: 'nowrap' }}>{text}{row.scaled_from_strongest
        ? <Tooltip title={row.note || 'The strongest line is not clean: scaled from this line by the calculated relative intensities'}>
          <Text type="secondary"> · scaled</Text></Tooltip> : null}</span>;
    } },
    { title: 'RIR wt%', dataIndex: 'weight_percent_rir', className: 'dara-num', render: percent },
    { title: <Tooltip title="Weight fraction of the Rietveld fit (the reported result)"><span>Rietveld wt%</span></Tooltip>,
      dataIndex: 'weight_percent_rietveld', className: 'dara-num', render: percent },
    { title: <Tooltip title="RIR − Rietveld, in wt%"><span>Δ</span></Tooltip>, dataIndex: 'delta', className: 'dara-num', render: value => signedText(value) },
  ];
  return <>
    {body.complete === false ? <Alert type="warning" showIcon style={{ marginBottom: 8 }}
      message={<FormulaText text={`Incomplete: no RIR fraction for ${missing.join(', ') || 'one phase'}. The other phases are normalised among themselves.`} />} /> : null}
    <div className="dara-fit-table">
      <Table size="small" rowKey="key" dataSource={rirRows(body)} columns={columns} pagination={false} scroll={{ x: 'max-content' }} />
    </div>
    <div className="dara-toolbar" style={{ marginTop: 8, alignItems: 'flex-start' }}>
      <Button size="small" icon={<DownloadOutlined />} href={`${API}/task/${taskId}/rir.csv?${query}`}>Download CSV</Button>
      {(body.assumptions || []).length ? <details className="dara-footnote">
        <summary>Assumptions of the RIR method ({body.assumptions.length})</summary>
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{body.assumptions.map(text => <li key={text}>{text}</li>)}</ul>
      </details> : null}
    </div>
  </>;
}

/**
 * Semi-quantitative RIR (I/Ic) check under the phase table, collapsed: the Rietveld fractions stay
 * the reported result; this is the classical cross-check. Loads when opened.
 */
export default function RirSemiQuant({ taskId, sel }) {
  return <Collapse ghost destroyInactivePanel className="dara-rir">
    <Collapse.Panel key="rir" header={<span>Semi-quantitative check (RIR, I/Ic) <Text type="secondary" className="dara-footnote" style={{ display: 'inline' }}>
      beside the Rietveld weight fractions</Text></span>}>
      <RirTable taskId={taskId} sel={sel} />
    </Collapse.Panel>
  </Collapse>;
}
