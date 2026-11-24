import React from 'react';
import { Alert, Button, InputNumber, message, Modal, Popover, Radio, Space, Table, Tooltip, Typography } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { backgroundRequest } from './versions.mjs';
import { MIN_ANCHORS, intervalProblem } from './backgroundAnchors.mjs';

const { Text } = Typography;

export { backgroundRequest };

/**
 * One Background control (the search-match uses the same words): Automatic, Polynomial terms or
 * Manual points (drawn on the plot), plus the 2θ intervals excluded from the fit.
 */
function EditorForm({ busy, getView, onSubmit, baseDegree, manual, onEditCurve, twoThetaRange = null }) {
  // A fit with an explicit degree keeps it unless the user switches to Automatic (which resets it).
  const hasBase = baseDegree != null && Number.isFinite(Number(baseDegree));
  const [method, setMethod] = React.useState(manual ? 'manual' : hasBase ? 'terms' : 'auto');
  const auto = method !== 'terms';
  const [degree, setDegree] = React.useState(hasBase ? Number(baseDegree) : 8);
  const [ranges, setRanges] = React.useState([]);
  const setRange = (index, position, value) => setRanges(previous => previous.map((range, i) => (i === index
    ? (position === 0 ? [value, range[1]] : [range[0], value]) : range)));
  const useCurrentView = () => {
    // `getView` returns null unless the plot is zoomed into part of the pattern.
    const view = getView?.();
    if (!view) {
      message.info('Zoom into the hump first');
      return;
    }
    const [lo, hi] = [Math.min(...view), Math.max(...view)];
    setRanges(previous => [...previous, [Number(lo.toFixed(2)), Number(hi.toFixed(2))]]);
  };
  const base = { degree, ranges, baseDegree: hasBase ? Number(baseDegree) : null };
  // Automatic over a manual curve resets it (as "Back to automatic" of the curve panel does).
  const kept = backgroundRequest({ ...base, auto: true, manual: true }); // the manual curve with new intervals
  const request = method === 'manual' ? (manual ? kept : null)
    : manual && method === 'auto' ? { background: { degree: null, excluded_ranges: kept?.background.excluded_ranges || [] },
      label: ['Background: automatic', ...(kept?.label ? [kept.label] : [])].join('; ') }
      : backgroundRequest({ ...base, auto, manual: false });
  const problems = ranges.map(range => intervalProblem(range, twoThetaRange));
  const invalid = problems.some(Boolean);
  return <div style={{ width: 360 }}>
    <Space direction="vertical" size="small" style={{ width: '100%' }}>
      <div>
        <Radio.Group size="small" optionType="button" value={method} onChange={event => setMethod(event.target.value)} aria-label="Background method"
          options={[{ value: 'auto', label: 'Automatic' }, { value: 'terms', label: 'Polynomial terms' },
            ...(onEditCurve || manual ? [{ value: 'manual', label: 'Manual points' }] : [])]} />
        {method === 'terms' ? <div style={{ marginTop: 6 }}>
          <InputNumber id="bg-terms" aria-label="Background polynomial terms" min={2} max={20} precision={0}
            value={degree} onChange={value => setDegree(value ?? 8)} addonAfter="terms" />
        </div> : null}
      </div>
      <div>
        <strong>Excluded 2θ intervals</strong>
        {ranges.map((range, index) => <Space key={index} style={{ marginTop: 6 }}>
          <InputNumber aria-label={`Excluded interval ${index + 1} from`} value={range[0]} step={0.5} onChange={value => setRange(index, 0, value)} />
          <span>–</span>
          <InputNumber aria-label={`Excluded interval ${index + 1} to`} value={range[1]} step={0.5} onChange={value => setRange(index, 1, value)} />
          <Button size="small" icon={<DeleteOutlined />} aria-label={`Remove interval ${index + 1}`}
            onClick={() => setRanges(previous => previous.filter((_, i) => i !== index))} />
          {/* said, not only a disabled button: an unusable interval would otherwise fail silently */}
          {problems[index] && (range[0] != null || range[1] != null) ? <Text type="danger">{problems[index]}</Text> : null}
        </Space>)}
        <div style={{ marginTop: 6 }}>
          <Space wrap>
            <Button size="small" icon={<PlusOutlined />} onClick={() => setRanges(previous => [...previous, [null, null]])}>Add interval</Button>
            <Tooltip title="Adds the zoomed 2θ window; zoom into the hump first">
              <Button size="small" onClick={useCurrentView} disabled={!getView}>Use current view</Button>
            </Tooltip>
          </Space>
        </div>
      </div>
      <Space wrap>
        {method === 'manual' && onEditCurve ? <Button type="primary" disabled={busy} onClick={onEditCurve}>Edit points on the plot</Button> : null}
        {method !== 'manual' || request ? <Button type={method === 'manual' ? 'default' : 'primary'} disabled={!request || invalid || busy} loading={busy}
          onClick={() => onSubmit(request.background, request.label)}>Refine as new version</Button> : null}
      </Space>
    </Space>
  </div>;
}

/**
 * "Background…" editor of the fit view. `onSubmit(background, label)` starts a quick re-refinement.
 * Without `open` it is a button with a popover; with `open`/`onOpenChange` it is a modal opened
 * from elsewhere (the Advanced menu).
 */
export default function BackgroundEditor({ busy, disabled, getView, onSubmit, baseDegree = null, manual = false, open: controlledOpen, onOpenChange, onEditCurve = null, twoThetaRange = null }) {
  const [localOpen, setLocalOpen] = React.useState(false);
  if (controlledOpen !== undefined) {
    return <Modal title="Background" open={controlledOpen} footer={null} width={420} destroyOnClose
      onCancel={() => onOpenChange?.(false)}>
      <EditorForm busy={busy} getView={getView} baseDegree={baseDegree} manual={manual} twoThetaRange={twoThetaRange}
        onEditCurve={onEditCurve ? () => { onOpenChange?.(false); onEditCurve(); } : null}
        onSubmit={(background, label) => { onOpenChange?.(false); onSubmit(background, label); }} />
    </Modal>;
  }
  return <Popover trigger="click" open={localOpen} onOpenChange={setLocalOpen} placement="bottomLeft" title="Background"
    content={<EditorForm busy={busy} getView={getView} baseDegree={baseDegree} manual={manual} twoThetaRange={twoThetaRange}
      onEditCurve={onEditCurve ? () => { setLocalOpen(false); onEditCurve(); } : null}
      onSubmit={(background, label) => { setLocalOpen(false); onSubmit(background, label); }} />}>
    <Button disabled={disabled}>Background…</Button>
  </Popover>;
}

/**
 * Panel of the background curve editor, beside the plot while the curve is edited on it (`side`),
 * so dragging an anchor never moves the plot; above it otherwise.
 * `anchors` are `[[2θ, y], ...]`; `onChange(anchors)` replaces them; `selected` is the anchor
 * index Delete removes. `warnings` never block; `error` (anchors the server would reject) does.
 * The texts default to the fit view's; the search-match passes its own (`help`, `submitText`, ...).
 */
export function BackgroundCurvePanel({
  anchors, selected = null, onChange, onSelect, onRemove, warnings = [], error = null, busy = false,
  canReset = false, onSubmit, onReset, onCancel, side = false,
  help = 'Drag a point up or down · click the curve to add a point · Alt+click a point or press Delete to remove the selected one',
  submitText = 'Refine as new version', resetText = 'Back to automatic',
  resetTitle = canReset ? "Refine with BGMN's automatic background polynomial" : 'This fit already uses the automatic background',
}) {
  const setY = (index, value) => {
    if (!Number.isFinite(value)) return;
    onChange(anchors.map((point, i) => (i === index ? [point[0], Math.max(value, 0)] : point)));
  };
  const columns = [
    { title: '2θ (°)', dataIndex: 'x', width: 90, render: value => value.toFixed(3) },
    { title: 'Intensity', dataIndex: 'y', render: (value, row) => <InputNumber size="small" min={0} step={10} value={value}
      aria-label={`Intensity of anchor ${row.index + 1} at ${row.x.toFixed(3)}°`} style={{ width: 110 }}
      onFocus={() => onSelect(row.index)} onChange={next => { if (next !== null) setY(row.index, Number(next)); }} /> },
    { title: '', dataIndex: 'index', width: 40, render: index => <Button size="small" type="text" icon={<DeleteOutlined />}
      aria-label={`Remove anchor ${index + 1}`} disabled={anchors.length <= MIN_ANCHORS} onClick={event => { event.stopPropagation(); onRemove(index); }} /> },
  ];
  const rows = anchors.map(([x, y], index) => ({ key: index, index, x, y }));
  return <div role="region" aria-label="Background curve editor" className={side ? 'dara-curve-side' : undefined} style={{ marginBottom: side ? 0 : 8, padding: '8px 12px',
    border: '1px solid var(--dara-border)', borderRadius: 'var(--dara-radius)', background: 'var(--dara-surface-sunken)' }}>
    <Space align="start" wrap size={side ? 'middle' : 'large'} direction={side ? 'vertical' : 'horizontal'} style={{ width: '100%' }}>
      <div style={{ maxWidth: 420 }}>
        <Text strong>Background: manual points</Text> <Text type="secondary">({anchors.length}, PCHIP)</Text>
        <div><Text type="secondary">{help}</Text></div>
        {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} /> : null}
        {warnings.map(text => <Alert key={text} type="warning" showIcon message={text} style={{ marginTop: 8 }} />)}
        <Space wrap style={{ marginTop: 8 }}>
          <Button type="primary" disabled={busy || Boolean(error)} loading={busy} onClick={onSubmit}>{submitText}</Button>
          <Tooltip title={resetTitle}>
            <Button disabled={busy || !canReset} onClick={onReset}>{resetText}</Button>
          </Tooltip>
          <Button onClick={onCancel}>Cancel</Button>
        </Space>
      </div>
      <Table size="small" columns={columns} dataSource={rows} pagination={false} scroll={{ y: side ? 260 : 150 }} style={{ width: 280 }}
        rowClassName={row => (row.index === selected ? 'ant-table-row-selected' : '')}
        onRow={row => ({ onClick: () => onSelect(row.index) })} />
    </Space>
  </div>;
}
