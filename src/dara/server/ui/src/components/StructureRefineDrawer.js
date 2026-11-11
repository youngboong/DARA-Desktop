import React from 'react';
import { Alert, Button, Checkbox, Drawer, Input, Radio, Select, Space, Spin, Table, Tooltip, Typography } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { requestJson } from './QuickRefineProgress';
import { DRAWER_WIDTH } from './PeakCandidatesDrawer';
import StatusTag from './StatusTag';
import ChemicalFormula from './ChemicalFormula';
import { displayFormula } from './versions.mjs';
import {
  constraintComplete, countParameters, isEmptyDraft, parameterLabel, parameterText, refusalLine, structurePhases, structureRequest, structureResultRows,
} from './structureModel.mjs';

const { Text } = Typography;
const EMPTY = { positions: [], thermal: 'fixed', occupancy: [] };
const KIND_OPTIONS = [
  { value: 'exchange', label: 'Exchange (two sites swap elements)' },
  { value: 'substitution', label: 'Substitution (dopant on a site)' },
  { value: 'vacancy', label: 'Vacancy (empty part of a site)' },
];
const NO_FREE = 'This site is on a special position: all its coordinates are fixed by symmetry.';

function elementsOf(site) {
  return (site?.species || []).map(item => ({ value: item.element, label: item.element }));
}

/** One occupancy constraint: kind, then the site and element fields that kind needs. */
function ConstraintRow({ item, sites, onChange, onRemove }) {
  const siteOptions = sites.map(site => ({ value: site.index, label: `${site.label} (${site.wyckoff})` }));
  const siteOf = index => sites.find(site => site.index === index);
  const set = patch => onChange({ ...item, ...patch });
  const dopantInvalid = item.dopant && !/^[A-Z][a-z]?$/.test(item.dopant);
  const [first, second] = item.sites || [];
  const [elementA, elementB] = item.elements || [];
  const pick = (placeholder, value, options, onPick, label) => (
    <Select size="small" aria-label={label} placeholder={placeholder} value={value} options={options} onChange={onPick} style={{ minWidth: 104 }} />
  );
  return <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
    <Space wrap size={[8, 4]} style={{ flex: 1, minWidth: 0 }}>
      <Select size="small" aria-label="Constraint kind" value={item.kind} options={KIND_OPTIONS} style={{ width: 210 }}
        onChange={kind => onChange({ kind })} />
      {item.kind === 'exchange' ? <>
        {pick('Site A', first, siteOptions, value => set({ sites: [value, second], elements: [undefined, elementB] }), 'First site')}
        {pick('Element', elementA, elementsOf(siteOf(first)), value => set({ elements: [value, elementB] }), 'Element on the first site')}
        <Text type="secondary">with</Text>
        {pick('Site B', second, siteOptions, value => set({ sites: [first, value], elements: [elementA, undefined] }), 'Second site')}
        {pick('Element', elementB, elementsOf(siteOf(second)), value => set({ elements: [elementA, value] }), 'Element on the second site')}
      </> : null}
      {item.kind === 'substitution' ? <>
        {pick('Site', item.site, siteOptions, value => set({ site: value, host: undefined }), 'Site')}
        {pick('Host', item.host, elementsOf(siteOf(item.site)), value => set({ host: value }), 'Host element')}
        <Text type="secondary">replaced by</Text>
        <Input size="small" aria-label="Dopant element" placeholder="e.g. Mn" maxLength={2} style={{ width: 72 }}
          status={dopantInvalid ? 'error' : undefined} value={item.dopant || ''}
          onChange={event => set({ dopant: event.target.value.replace(/^./, c => c.toUpperCase()) })} />
      </> : null}
      {item.kind === 'vacancy' ? <>
        {pick('Site', item.site, siteOptions, value => set({ site: value, element: undefined }), 'Site')}
        {pick('Element', item.element, elementsOf(siteOf(item.site)), value => set({ element: value }), 'Element that can be missing')}
      </> : null}
    </Space>
    <Tooltip title="Remove constraint">
      <Button size="small" type="text" aria-label="Remove constraint" icon={<DeleteOutlined />} onClick={onRemove} />
    </Tooltip>
  </div>;
}

/**
 * Right-hand drawer of "Structure…": choose a phase, the sites to free, the thermal model
 * and occupancy constraints, then start a version (origin "structure"). Eligibility, sites and the
 * parameter limit come from GET /task/{id}/structure-sites; the server checks every rule again on submit.
 */
export default function StructureRefineDrawer({ taskId, sel, phases, renderPhase, open, onClose, busy, onSubmit }) {
  const [info, setInfo] = React.useState({});
  const [loading, setLoading] = React.useState(false);
  const [phase, setPhase] = React.useState(null);
  const [draft, setDraft] = React.useState(EMPTY);
  const phasesKey = phases.join('|');

  React.useEffect(() => {
    if (!open) return undefined;
    let active = true;
    setLoading(true);
    Promise.all(phases.map(name => requestJson(`/task/${taskId}/structure-sites?sel=${encodeURIComponent(sel)}&phase=${encodeURIComponent(name)}`)
      .catch(error => ({ phase: name, eligible: false, sites: [], refusals: [{ code: 'unsupported_structure', message: error.message }], warnings: [] }))))
      .then(list => {
        if (!active) return;
        setInfo(Object.fromEntries(list.map(item => [item.phase, item])));
        setLoading(false);
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, taskId, sel, phasesKey]);

  // The first eligible phase is preselected; a phase that is gone from the list is dropped.
  React.useEffect(() => {
    if (loading) return;
    if (!phase || !phases.includes(phase)) {
      setPhase(phases.find(name => info[name]?.eligible) || null);
      setDraft(EMPTY);
    }
  }, [loading, info, phase, phasesKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = phase ? info[phase] : null;
  const sites = current?.sites || [];
  const maxParameters = current?.max_parameters;
  const count = countParameters(draft, sites);
  const tooMany = Number.isFinite(maxParameters) && count > maxParameters;
  const incomplete = draft.occupancy.some(item => !constraintComplete(item));
  const tooManyRefusal = tooMany ? { code: 'too_many_parameters', message: `Too many structure parameters for the data: ${count} requested, at most ${maxParameters} allowed.` } : null;
  const blocked = !current ? 'Choose a phase.'
    : !current.eligible ? refusalLine(current.refusals?.[0])
      : isEmptyDraft(draft) ? 'Choose positions, a thermal model or an occupancy constraint to refine.'
        : incomplete ? 'Complete or remove the occupancy constraint rows.'
          : tooMany ? refusalLine(tooManyRefusal)
            : busy ? 'A re-refinement is already running.' : null;

  const setPositions = (index, checked) => setDraft(previous => ({
    ...previous, positions: checked ? [...previous.positions, index] : previous.positions.filter(item => item !== index),
  }));
  const run = () => {
    onSubmit(structureRequest(sel, phase, draft));
    onClose();
  };

  const siteColumns = [
    { title: 'Refine', key: 'refine', width: 64, render: (_, site) => site.free?.length
      ? <Checkbox aria-label={`Refine coordinates of ${site.label}`} checked={draft.positions.includes(site.index)} onChange={event => setPositions(site.index, event.target.checked)} />
      : <Tooltip title={NO_FREE}><span><Checkbox disabled aria-label={`${site.label} has no free coordinate`} /></span></Tooltip> },
    { title: 'Site', render: (_, site) => `${site.label} · ${site.multiplicity}${String(site.wyckoff).replace(/^\d+/, '')}` },
    { title: 'Element', render: (_, site) => site.species.map(item => (item.occupancy < 1 ? `${item.element} ${item.occupancy.toFixed(2)}` : item.element)).join(', ') },
    ...['x', 'y', 'z'].map((axis, index) => ({
      title: axis, key: axis, className: 'dara-num', width: 64,
      render: (_, site) => Number(site.xyz[index]).toFixed(4),
    })),
    { title: <Tooltip title="Coordinates the Wyckoff position leaves free; the others follow from symmetry."><span>Free</span></Tooltip>, key: 'free',
      render: (_, site) => (site.free?.length ? site.free.join(', ') : 'none') },
  ];

  return <Drawer title="Refine structure" placement="right" width={DRAWER_WIDTH} open={open} onClose={onClose}
    keyboard={false} mask={false} destroyOnClose={false} className="dara-peak-drawer">
    <Alert type="info" showIcon style={{ marginBottom: 12 }}
      message="Structure results are indicative: laboratory X-ray data fix atomic positions and occupancies only roughly." />
    <Typography.Title level={5} style={{ marginTop: 0 }}>Phase</Typography.Title>
    {loading ? <Spin size="small" /> : <Select aria-label="Phase to refine" style={{ width: '100%', marginBottom: 12 }} value={phase} placeholder="Choose a phase"
      onChange={value => { setPhase(value); setDraft(EMPTY); }}
      options={phases.map(name => {
        const refusal = info[name]?.eligible ? null : info[name]?.refusals?.[0];
        return {
          value: name, disabled: Boolean(refusal),
          label: <Tooltip title={refusal ? refusalLine(refusal) : null} placement="left">
            <span><ChemicalFormula formula={displayFormula(name)} />{refusal ? <Text type="secondary"> (not available)</Text> : null}</span>
          </Tooltip>,
        };
      })} />}
    {!loading && !phases.some(name => info[name]?.eligible) ? <Alert type="warning" showIcon style={{ marginBottom: 12 }}
      message="No phase of this fit can be refined structurally."
      description={<Space direction="vertical" size={2}>{phases.map(name => <span key={name}>
        {renderPhase ? renderPhase(name) : name}: {refusalLine(info[name]?.refusals?.[0])}</span>)}</Space>} /> : null}
    {current?.eligible ? <>
      <Typography.Title level={5} style={{ marginBottom: 0 }}>Sites</Typography.Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>Starting values come from the database CIF.</Text>
      <Table size="small" pagination={false} rowKey="index" dataSource={sites} columns={siteColumns} scroll={{ x: 'max-content' }} style={{ marginBottom: 16 }} />
      <Typography.Title level={5}>Thermal displacement (B)</Typography.Title>
      <Radio.Group value={draft.thermal} onChange={event => setDraft(previous => ({ ...previous, thermal: event.target.value }))} style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={4}>
          <Radio value="fixed">Fixed at 1.0 Å²</Radio>
          <Radio value="overall">One B for the phase</Radio>
          <Radio value="per_element">One B per element</Radio>
        </Space>
      </Radio.Group>
      <Typography.Title level={5}>Occupancy</Typography.Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>Occupancies on a site always sum to 1.</Text>
      {draft.occupancy.map((item, index) => <ConstraintRow key={index} item={item} sites={sites}
        onChange={next => setDraft(previous => ({ ...previous, occupancy: previous.occupancy.map((old, i) => (i === index ? next : old)) }))}
        onRemove={() => setDraft(previous => ({ ...previous, occupancy: previous.occupancy.filter((_, i) => i !== index) }))} />)}
      <Button size="small" icon={<PlusOutlined />} disabled={draft.occupancy.length >= 6}
        onClick={() => setDraft(previous => ({ ...previous, occupancy: [...previous.occupancy, { kind: 'exchange' }] }))}>Add constraint</Button>
      <div style={{ marginTop: 20, paddingTop: 12, borderTop: '1px solid var(--dara-border)' }}>
        <div style={{ marginBottom: 8 }}>
          <Text strong className="dara-num" style={{ color: tooMany ? 'var(--dara-failed)' : undefined }}>
            {`Parameters: ${parameterText(count, maxParameters)}`}</Text>
          {current.n_reflections != null ? <Text type="secondary">{`  (${current.n_reflections} reflections)`}</Text> : null}
        </div>
        {tooManyRefusal ? <Alert type="error" showIcon style={{ marginBottom: 8 }} message={refusalLine(tooManyRefusal)} /> : null}
        {(current.warnings || []).map(text => <Alert key={text} type="warning" showIcon style={{ marginBottom: 8 }} message={text} />)}
        <Space align="center" wrap>
          <Tooltip title={blocked}>
            <span><Button type="primary" disabled={Boolean(blocked)} onClick={run}>Refine as new version</Button></span>
          </Tooltip>
          <StatusTag kind="indicative" tooltip="Atomic positions, occupancies and B from laboratory X-ray data are indicative only." style={{ marginInlineStart: 0 }} />
        </Space>
        {blocked && !isEmptyDraft(draft) ? <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{blocked}</Text> : null}
      </div>
    </> : null}
  </Drawer>;
}

const VALUE_TIPS = {
  at_limit: 'The value reached the edge of its refinement window and has no e.s.d.',
  fixed: 'Held at the database value (fixed by symmetry or not selected); not refined.',
};

function ValueCell({ item, hideLimit = false }) {
  const tag = VALUE_TIPS[item.state] ? <StatusTag kind={item.state === 'at_limit' ? 'at_limit' : 'fixed'} tooltip={VALUE_TIPS[item.state]} /> : null;
  // Fixed values are greyed, so the refined ones (with e.s.d.) stand out without a tag on every cell.
  if (item.state === 'fixed') return <Tooltip title={VALUE_TIPS.fixed}><span className="dara-num" style={{ color: 'var(--dara-text-secondary)' }}>{item.text}</span></Tooltip>;
  return <span className="dara-num">{item.text}{item.state === 'at_limit' && !hideLimit ? tag : null}</span>;
}

const B_FLOOR = 0.1; // Å², the lower limit of the refinement window
/** True when every site's B sits at the lower limit: then the data do not determine B at all. */
const allBAtFloor = result => (result.sites || []).length > 0 && result.sites.every(site => site.b_iso
  && site.b_iso.state === 'at_limit' && Number(site.b_iso.value) <= B_FLOOR + 0.01);

/** "Structure" tab of the fit view: refined positions, occupancies and B per phase, always labelled indicative. */
export function StructureResults({ details, renderPhase }) {
  const phases = structurePhases(details);
  return <div>
    <Alert type="info" showIcon style={{ marginBottom: 12 }}
      message="Structure results are indicative: laboratory X-ray data fix atomic positions and occupancies only roughly; do not report them as a structure determination." />
    {phases.map(([phase, result]) => {
      const bFloor = allBAtFloor(result);
      // Every value tagged "at limit" in the table, plus the occupancy parameters (same list as the caution).
      const limited = [
        ...structureResultRows(result).flatMap(row => row.xyz.map((item, index) => (item.state === 'at_limit' ? { label: `${row.label} ${'xyz'[index]}` } : null)).filter(Boolean)),
        ...(result.parameters || []).filter(item => item.state === 'at_limit' && !(bFloor && /^B/.test(item.name))),
      ];
      return <div key={phase} style={{ marginBottom: 16 }}>
      <Typography.Title level={5}>{renderPhase ? renderPhase(phase) : phase}</Typography.Title>
      <Table size="small" pagination={false} rowKey="key" scroll={{ x: 'max-content' }} dataSource={structureResultRows(result)}
        columns={[
          { title: 'Site', dataIndex: 'label' },
          ...['x', 'y', 'z'].map((axis, index) => ({ title: axis, key: axis, render: (_, row) => <ValueCell item={row.xyz[index] || { text: '—' }} /> })),
          { title: 'Occupancy', key: 'occupancy', render: (_, row) => <Space direction="vertical" size={0}>{row.species.map(item =>
            <span key={item.element} className="dara-num">{`${item.element} ${item.text}`}</span>)}</Space> },
          { title: 'B (Å²)', key: 'b', render: (_, row) => (row.b ? <ValueCell item={row.b} hideLimit={bFloor} /> : '—') },
        ]} />
      {bFloor ? <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>B reached the lower limit (0.1 Å²) on all sites; thermal parameters are not determined by these data.</Text> : null}
      {limited.length
        ? <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
          {`At limit: ${limited.map(item => item.label || parameterLabel(item.name)).join(', ')}. These values hit the edge of their window; treat them as unresolved.`}</Text> : null}
    </div>;
    })}
  </div>;
}
