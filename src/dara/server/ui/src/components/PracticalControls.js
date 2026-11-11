import React from 'react';
import { runAfterSuccess } from './submitHelpers.mjs';
import { stateLabel } from './resultsList.mjs';
import { Alert, Button, Checkbox, Form, Input, InputNumber, message, Modal, Progress, Radio, Select, Space, Tag, Tooltip } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';
import { API } from './QuickRefineProgress';
export const DEFAULT_CONTROLS = { lattice: 'inherit', lattice_percent: 2, orientation: 'inherit', peak_width: 'inherit' };

export function RefinementControlsEditor({ value = DEFAULT_CONTROLS, onChange, bare = false }) {
  const update = patch => onChange({ ...value, ...patch });
  // ``bare``: no frame, selects fill a responsive grid (used inside a card of the submit form).
  const width = fallback => (bare ? '100%' : fallback);
  const Fields = bare ? 'div' : Space;
  const fieldsProps = bare ? { className: 'dara-field-row' } : { wrap: true, align: 'start' };
  return <section aria-label="Final refinement controls" style={bare ? undefined : { padding: 12, border: '1px solid var(--dara-border)', borderRadius: 'var(--dara-radius)', marginBottom: 16 }}>
    {bare ? <div className="dara-subhead">Final refinement controls</div> : <h4>Final refinement controls</h4>}
    <Fields {...fieldsProps}>
      <label>Lattice parameters<br /><Select aria-label="Lattice refinement" value={value.lattice} style={{ width: width(200) }} onChange={lattice => update({ lattice })}
        options={[{value:'inherit',label:'Keep saved/default'}, {value:'fixed',label:'Fixed to reference CIF'}, {value:'refine',label:'Refine within bounds'}]} /></label>
      {value.lattice === 'refine' ? <label>Allowed lattice change (±%)<br /><InputNumber aria-label="Lattice bound percent" min={0.01} max={10} step={0.5} value={value.lattice_percent} onChange={lattice_percent => update({ lattice_percent })} style={bare ? { width: '100%' } : undefined} /></label> : null}
      <label>Preferred orientation <Tooltip title="More flexible models can lower Rwp without proving the phase assignment."><QuestionCircleOutlined style={{ color: 'var(--dara-text-hint)' }} /></Tooltip><br /><Select aria-label="Preferred orientation" value={value.orientation} style={{ width: width(220) }} onChange={orientation => update({ orientation })}
        options={[{value:'inherit',label:'Keep saved/default'}, {value:'isotropic',label:'None (random powder)'}, {value:'SPHAR2',label:'Spherical harmonics: order 2'}, {value:'SPHAR4',label:'Spherical harmonics: order 4'}]} /></label>
      <label>Sample peak broadening <Tooltip title="Instrument-only broadening is appropriate only when sample broadening is negligible."><QuestionCircleOutlined style={{ color: 'var(--dara-text-hint)' }} /></Tooltip><br /><Select aria-label="Peak broadening" value={value.peak_width} style={{ width: width(220) }} onChange={peak_width => update({ peak_width })}
        options={[{value:'inherit',label:'Keep saved/default'}, {value:'refine',label:'Refine K1, K2 and B1'}, {value:'instrument_only',label:'Fixed K1 = K2 = B1 = 0'}]} /></label>
    </Fields>
  </section>;
}

const clock = seconds => {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, '0');
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${String(minutes).padStart(2, '0')}:${rest}`;
};
// The stage can hold a raw job state (e.g. FIZZLED) when no progress file was written; never show the code.
const stageText = data => {
  if (data.status === 'READY') return 'Waiting for the analyses ahead to finish';
  const stage = stateLabel(data.stage);
  return stage === stateLabel(data.status) ? '' : stage;
};
const etaText = seconds => (seconds < 60 ? ', < 1 min left in this stage' : `, about ${Math.round(seconds / 60)} min left in this stage`);

export function TaskProgress({ taskId, onStatusChange }) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    let active = true; let timer;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const response = await fetch(`${API}/task/${taskId}/progress`, { signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || 'Progress unavailable');
        if (!active) return;
        setData(body); setError(''); onStatusChange?.(body.status);
        if (['READY','RUNNING','CANCELLING'].includes(body.status)) timer = setTimeout(poll, 3000);
      } catch (e) { if (active) { setError(e.message); timer = setTimeout(poll, 5000); } }
    };
    poll(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [taskId, onStatusChange]);
  const cancel = () => Modal.confirm({ title: 'Stop this analysis?',
    content: 'The active calculation will be terminated. Fully refined candidate checkpoints remain downloadable; the interrupted candidate is not a valid result.',
    okText: 'Stop analysis', okButtonProps: { danger: true }, async onOk() {
      setBusy(true);
      try {
        const response = await fetch(`${API}/task/${taskId}/cancel`, { method: 'POST' });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail);
        setData(previous => ({...previous, status: body.status})); onStatusChange?.(body.status);
      } catch (e) { message.error(e.message); }
      finally { setBusy(false); }
    }});
  if (error) return <Alert type="warning" message={error} />;
  if (!data || data.status === 'COMPLETED') return null;
  // A failed task without stage, attempts or checkpoints would be a card that only repeats "Failed": the failure panel says it.
  if (data.status === 'FIZZLED' && !stageText(data) && !(data.total > 0) && !(data.completed_candidates > 0)) return null;
  return <section aria-label="Analysis progress" className="dara-panel" style={{ padding: 16, border: '1px solid var(--dara-border)', borderRadius: 'var(--dara-radius)', marginBottom: 16 }}>
    <Space wrap><Tag>{stateLabel(data.status)}</Tag>{stageText(data) ? <strong>{stageText(data)}</strong> : null}
      {['READY','RUNNING','CANCELLING'].includes(data.status) ? <Button danger loading={busy} disabled={data.status === 'CANCELLING'} onClick={cancel}>{data.status === 'CANCELLING' ? 'Stopping…' : 'Stop analysis'}</Button> : null}
      {data.completed_candidates > 0 ? <Button href={`${API}/task/${taskId}/checkpoints`}>Download completed candidates ({data.completed_candidates})</Button> : null}
    </Space>
    {Number.isFinite(data.elapsed_s) ? <p style={{ marginTop: 8, marginBottom: 0 }}>Elapsed {clock(data.elapsed_s)}{Number.isFinite(data.budget_s) ? ` / limit ${clock(data.budget_s)}` : ''}</p> : null}
    {data.total > 0 && ['FIZZLED','CANCELLED'].includes(data.status) ? <p>{`${data.status === 'CANCELLED' ? 'Cancelled' : 'Failed'} after ${data.completed} of ${data.total} candidate attempts.`}</p> : null}
    {data.total > 0 && !['FIZZLED','CANCELLED'].includes(data.status) ? <><Progress percent={Math.round(100 * data.completed / data.total)} status={data.status === 'RUNNING' ? 'active' : 'normal'} /><p>{data.stage === 'Screening reference phases' ? `${data.completed} / ${data.total} references screened` : `${data.completed} / ${data.total} candidate attempts finished (not overall progress)`}{Number.isFinite(data.eta_s) ? etaText(data.eta_s) : ''}.</p></> : null}
    {data.completed_candidates > 0 ? <p>Checkpoints contain completed refinements only, numbered by execution order; final ranking may change.</p> : null}
  </section>;
}

const SETUP_DEFAULTS = { verified:false, radiation:'Cu', radius:240, focus_length:12, focus_width:0.04, divergence:'fixed', divergence_angle:0.5,
  irradiated_length:10, divergence_distance:170, soller_primary:2.5, soller_secondary:2.5, bruker_soller:false, sample_diameter:25,
  detector:'linear', detector_length:14.4, strip_width:0.075, detector_height:16, monochromator:'none' };

const mm = { addonAfter:'mm' }, deg = { addonAfter:'°' };
function Num({ name, label, unit, required = true, help }) {
  return <Form.Item name={name} label={label} help={help} rules={required ? [{required:true}] : []} style={{marginBottom:12}}>
    <InputNumber min={0} style={{width:'100%'}} {...unit} /></Form.Item>;
}
const SECTION = { gridColumn:'1 / -1', margin:'4px 0 8px', fontWeight:600, borderBottom:'1px solid var(--dara-border)' };

// Bragg-Brentano setup in datasheet units; the server writes the BGMN device file from these values.
function InstrumentSetupFields({ form }) {
  const divergence = Form.useWatch('divergence', form);
  const detector = Form.useWatch('detector', form);
  return <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(180px, 1fr))', columnGap:16}}>
    <div style={SECTION}>Goniometer and tube</div>
    <Num name="radius" label="Goniometer radius" unit={mm} />
    <Num name="focus_length" label="Tube focus length" unit={mm} />
    <Num name="focus_width" label="Focus width (optical)" unit={mm} help="Line focus: 1/10 of nominal" />
    <div style={SECTION}>Incident beam</div>
    <Form.Item name="divergence" label="Divergence slit" style={{marginBottom:12}}>
      <Select options={[{label:'Fixed', value:'fixed'}, {label:'Automatic', value:'automatic'}]} /></Form.Item>
    {divergence === 'automatic'
      ? <Num name="irradiated_length" label="Irradiated length" unit={mm} />
      : <Num name="divergence_angle" label="Opening angle" unit={deg} />}
    <Num name="divergence_distance" label="Slit to sample" unit={mm} />
    <Num name="soller_primary" label="Primary Soller" unit={deg} />
    <Num name="beam_mask" label="Beam mask" unit={mm} required={false} />
    <Num name="beam_mask_distance" label="Mask to sample" unit={mm} required={false} />
    <div style={SECTION}>Sample and diffracted beam</div>
    <Num name="sample_diameter" label="Sample diameter" unit={mm} />
    <Num name="soller_secondary" label="Secondary Soller" unit={deg} />
    <Form.Item name="bruker_soller" valuePropName="checked" style={{marginBottom:12, alignSelf:'end'}}><Checkbox>Bruker Soller slits</Checkbox></Form.Item>
    <Num name="antiscatter_width" label="Anti-scatter slit" unit={mm} required={false} />
    <Num name="antiscatter_distance" label="Anti-scatter to sample" unit={mm} required={false} />
    <Form.Item name="monochromator" label="Monochromator" style={{marginBottom:12}}>
      <Select options={[{label:'None (filter)', value:'none'}, {label:'Graphite, secondary', value:'graphite'}]} /></Form.Item>
    <div style={SECTION}>Detector</div>
    <Form.Item name="detector" label="Type" style={{marginBottom:12}}>
      <Select options={[{label:'1D strip', value:'linear'}, {label:'Point', value:'point'}]} /></Form.Item>
    {detector === 'point' ? null : <Num name="detector_length" label="Active length" unit={mm} />}
    <Num name="strip_width" label={detector === 'point' ? 'Receiving slit' : 'Strip width'} unit={mm} />
    <Num name="detector_height" label="Axial height" unit={mm} required={false} />
  </div>;
}

export function InstrumentManager({ onRegistered, buttonLabel = 'Register instrument profile', buttonSize = 'small' }) {
  const [open, setOpen] = React.useState(false);
  const [form] = Form.useForm();
  const [files, setFiles] = React.useState({});
  const [busy, setBusy] = React.useState(false);
  // 'build': BGMN compiles a profile from the instrument's datasheet values; 'upload': a ready .geq.
  const [mode, setMode] = React.useState('build');
  const submit = async () => {
    try {
      const values = await form.validateFields();
      let response;
      if (mode === 'build') {
        setBusy(true);
        const { verified, verified_by, geometry, ...setup } = values;
        response = await fetch(`${API}/instrument-profiles/build`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(setup)});
      } else {
        if (!files.geq) throw new Error('Select a GEQ profile file.');
        const data = new FormData();
        Object.entries(values).forEach(([key,value]) => data.append(key, value ?? ''));
        Object.entries(files).forEach(([key,value]) => { if (value) data.append(key, value); });
        setBusy(true);
        response = await fetch(`${API}/instrument-profiles`, {method:'POST',body:data});
      }
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check required profile fields.');
      // The profile is already registered on the server; UI follow-up problems must not look like a failed registration.
      runAfterSuccess([
        () => onRegistered(result), () => setOpen(false), () => form.resetFields(), () => setFiles({}),
        () => message.success('Profile registered and selected.'),
      ]);
    } catch (e) { if (e.message) message.error(e.message); }
    finally { setBusy(false); }
  };
  return <><Button size={buttonSize} onClick={() => setOpen(true)}>{buttonLabel}</Button>
    <Modal title="Register an instrument profile" open={open} width={640} onCancel={() => !busy && setOpen(false)} onOk={submit}
      okText={mode === 'build' ? (busy ? 'Compiling (up to about 10 min)…' : 'Build profile') : 'Register'} confirmLoading={busy} destroyOnClose>
      <Radio.Group value={mode} onChange={e => setMode(e.target.value)} optionType="button" disabled={busy} style={{marginBottom:16}}
        options={[{label:'Instrument setup', value:'build'}, {label:'Upload GEQ file', value:'upload'}]} />
      <Alert showIcon type="warning" message="A profile is not a calibration." style={{marginBottom:16}}
        description={mode === 'build'
          ? 'Enter the values from the instrument datasheet. BGMN computes the profile from them. Check it by fitting a standard (Si, LaB6) before trusting sizes or strain.'
          : 'Use a BGMN GEQ profile generated for the actual optics and detector. Supply its matching GER when available. An XRDML instrument name alone cannot verify the optics.'} />
      <Form form={form} layout="vertical" initialValues={SETUP_DEFAULTS}>
        <Form.Item name="label" label="Instrument / profile name" rules={[{required:true}]}><Input maxLength={120} /></Form.Item>
        <Form.Item name="radiation" label="Radiation (e.g. Cu Kα)" rules={[{required:true}]}><Input maxLength={60} /></Form.Item>
        {mode === 'build' ? <InstrumentSetupFields form={form} /> : <>
          <Form.Item name="geometry" label="Geometry, slits and detector" rules={[{required:true}]}><Input maxLength={250} /></Form.Item>
          <p><label>Compiled GEQ (required)<input aria-label="GEQ profile file" type="file" accept=".geq" onChange={e => setFiles({...files,geq:e.target.files[0]})} /></label></p>
          <p><label>Matching GER (optional)<input aria-label="GER profile file" type="file" accept=".ger" onChange={e => setFiles({...files,ger:e.target.files[0]})} /></label></p>
          <Form.Item name="verified" valuePropName="checked"><Checkbox>Verified against a standard by an operator</Checkbox></Form.Item>
          <Form.Item name="verified_by" label="Verified by"><Input maxLength={120} /></Form.Item>
        </>}
        <Form.Item name="notes" label={mode === 'build' ? 'Notes' : 'Standard, calibration date, validated 2θ range and notes'}><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
      </Form>
    </Modal></>;
}
