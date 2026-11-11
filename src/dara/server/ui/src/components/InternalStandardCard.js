import React from 'react';
import { Alert, AutoComplete, Button, InputNumber, message, Modal, Select, Space, Table, Tooltip, Typography } from 'antd';
import { requestJson } from './QuickRefineProgress';

const { Text } = Typography;

export const COMMON_STANDARDS = ['Si', 'Al2O3', 'ZnO', 'CeO2', 'CaF2', 'LaB6'];
const WARNING_STATUS = new Set(['standard_not_found', 'standard_unreliable', 'negative_amorphous']);

const amount = (value, esd) => {
  if (value == null) return '—';
  const sigma = esd == null ? '' : ` ± ${Number(esd).toFixed(Math.abs(esd) < 0.1 && esd !== 0 ? 2 : 1)}`;
  return `${Number(value).toFixed(1)}${sigma}`;
};

/** Editor of the standard: formula, added wt%, and (when several phases match) the standard phase. */
function StandardEditor({ taskId, spec, candidates, open, onClose, onSaved }) {
  const [formula, setFormula] = React.useState(spec?.formula || '');
  const [weight, setWeight] = React.useState(spec?.wt_percent ?? null);
  const [phase, setPhase] = React.useState(spec?.phase || undefined);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    if (!open) return;
    setFormula(spec?.formula || '');
    setWeight(spec?.wt_percent ?? null);
    setPhase(spec?.phase || undefined);
    setError('');
  }, [open, spec]);

  const save = async body => {
    setSaving(true);
    setError('');
    try {
      const response = await requestJson(`/task/${taskId}/internal-standard`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      onSaved(response.internal_standard ?? null);
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  };
  const valid = formula.trim() && Number.isFinite(weight) && weight > 0 && weight < 100;
  return <Modal title="Internal standard" open={open} onCancel={onClose} width={420} destroyOnClose
    footer={<Space>
      {spec ? <Button danger disabled={saving} onClick={() => save(null)}>Remove standard</Button> : null}
      <Button onClick={onClose}>Cancel</Button>
      <Button type="primary" loading={saving} disabled={!valid} onClick={() => save({ formula: formula.trim(), wt_percent: weight, phase: phase || null })}>Save</Button>
    </Space>}>
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <div>
        <label htmlFor="dara-standard-formula" style={{ display: 'block', marginBottom: 4 }}>Formula of the standard</label>
        <AutoComplete id="dara-standard-formula" style={{ width: '100%' }} placeholder="Si" value={formula} onChange={setFormula}
          options={COMMON_STANDARDS.map(value => ({ value }))} />
      </div>
      <div>
        <label htmlFor="dara-standard-wt" style={{ display: 'block', marginBottom: 4 }}>Added amount (wt% of the mixture)</label>
        <InputNumber id="dara-standard-wt" min={0} max={100} step={0.5} style={{ width: 160 }} value={weight} onChange={setWeight} />
      </div>
      {candidates?.length ? <div>
        <label htmlFor="dara-standard-phase" style={{ display: 'block', marginBottom: 4 }}>Standard phase</label>
        <Select id="dara-standard-phase" allowClear style={{ width: '100%' }} placeholder="Choose the fitted phase" value={phase} onChange={setPhase}
          options={candidates.map(value => ({ value, label: value }))} />
      </div> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
    </Space>
  </Modal>;
}

/**
 * Absolute quantification from an internal standard (design 2.4). `block` is the `internal_standard`
 * JSON of the displayed summary, `null` when none is set; `onSaved(block)` receives the answer of
 * PUT /task/{id}/internal-standard.
 */
export default function InternalStandardCard({ taskId, block, renderPhase, onSaved }) {
  const [editing, setEditing] = React.useState(false);
  const spec = block?.spec || null;
  const handleSaved = next => {
    message.success(next ? 'Internal standard saved.' : 'Internal standard removed.');
    onSaved?.(next);
  };
  const editor = <StandardEditor taskId={taskId} spec={spec} candidates={block?.candidates} open={editing}
    onClose={() => setEditing(false)} onSaved={handleSaved} />;
  if (block === undefined) return null; // an older server or a version still loading
  if (!block) {
    return <div style={{ margin: '12px 0' }}>
      <Button size="small" onClick={() => setEditing(true)}>Add internal standard…</Button>
      {editor}
    </div>;
  }

  const amorphous = block.amorphous;
  const rows = [
    ...(block.phases || []).map(row => ({ key: row.name, name: renderPhase ? renderPhase(row.name) : row.name,
      mixture: amount(row.absolute_wt_percent, row.absolute_esd_percent), sample: amount(row.sample_wt_percent, row.sample_esd_percent) })),
    ...(amorphous ? [{
      key: '__amorphous', name: 'Amorphous and unidentified',
      mixture: amorphous.not_detected
        ? <Tooltip title={`Fitted value ${amount(amorphous.mixture_wt_percent, amorphous.esd_percent)}, within twice its uncertainty of zero`}><span>0 (not detected)</span></Tooltip>
        : amount(amorphous.mixture_wt_percent, amorphous.esd_percent),
      sample: amorphous.not_detected ? '0 (not detected)' : amount(amorphous.sample_wt_percent, amorphous.esd_percent),
    }] : []),
  ];
  const title = `Absolute quantification (internal standard ${spec?.formula}, ${spec?.wt_percent} wt% added)`;
  return <div style={{ margin: '12px 0' }}>
    <Space align="baseline" style={{ marginBottom: 8 }}>
      <h3 style={{ margin: 0, fontSize: 'var(--dara-font-16)' }}>{title}</h3>
      <Button size="small" onClick={() => setEditing(true)}>Edit</Button>
    </Space>
    {block.message ? <Alert type={WARNING_STATUS.has(block.status) || block.status === 'ambiguous_standard' ? 'warning' : 'info'} showIcon
      style={{ marginBottom: 8 }} message={block.message}
      action={block.status === 'ambiguous_standard' ? <Button size="small" onClick={() => setEditing(true)}>Choose phase</Button> : null} /> : null}
    {rows.length ? <Table size="small" pagination={false} rowKey="key" dataSource={rows} scroll={{ x: 'max-content' }}
      columns={[
        { title: 'Phase', dataIndex: 'name' },
        { title: <Tooltip title="Amount in the weighed mixture, including the added standard; ± is propagated without correlations."><span>Of mixture (wt%)</span></Tooltip>, dataIndex: 'mixture', className: 'dara-num' },
        { title: <Tooltip title="Amount in the sample without the standard"><span>Of sample (wt%)</span></Tooltip>, dataIndex: 'sample', className: 'dara-num' },
      ]} /> : null}
    {block.standard_phase ? <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
      {`Standard fitted at ${amount(block.fitted_wt_percent, block.fitted_esd_percent)} wt% against ${spec.wt_percent} wt% added (scale ${block.scale}).`}</Text> : null}
    {editor}
  </div>;
}
