import React from 'react';
import { Alert, Button, Checkbox, InputNumber, message, Modal, Popover, Space, Table, Tooltip, Typography } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { backgroundRequest } from './versions.mjs';
import { MIN_ANCHORS } from './backgroundAnchors.mjs';

const { Text } = Typography;

export { backgroundRequest };

function EditorForm({ busy, getView, onSubmit, baseDegree, manual, onEditCurve }) {
  // A fit with an explicit degree keeps it unless the user switches to Auto (which resets it).
  const hasBase = baseDegree != null && Number.isFinite(Number(baseDegree));
  const [auto, setAuto] = React.useState(!hasBase);
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
  const request = backgroundRequest({ auto, degree, ranges, baseDegree: hasBase ? Number(baseDegree) : null, manual });
  const invalid = ranges.some(([lo, hi]) => !(Number.isFinite(lo) && Number.isFinite(hi) && lo < hi));
  return <div style={{ width: 360 }}>
    <Space direction="vertical" size="small" style={{ width: '100%' }}>
      <div>
        <label htmlFor="bg-terms"><strong>Background polynomial terms</strong></label>
        <div style={{ marginTop: 4 }}>
          <Space>
            <Checkbox checked={auto} onChange={event => setAuto(event.target.checked)}>Auto</Checkbox>
            <InputNumber id="bg-terms" aria-label="Background polynomial terms" min={2} max={20} precision={0}
              disabled={auto} value={degree} onChange={value => setDegree(value ?? 8)} />
          </Space>
        </div>
      </div>
      <div>
        <strong>Exclude a hump</strong>
        <div><Text type="secondary">2θ intervals excluded from the fit</Text></div>
        {ranges.map((range, index) => <Space key={index} style={{ marginTop: 6 }}>
          <InputNumber aria-label={`Excluded interval ${index + 1} from`} value={range[0]} step={0.5} onChange={value => setRange(index, 0, value)} />
          <span>–</span>
          <InputNumber aria-label={`Excluded interval ${index + 1} to`} value={range[1]} step={0.5} onChange={value => setRange(index, 1, value)} />
          <Button size="small" icon={<DeleteOutlined />} aria-label={`Remove interval ${index + 1}`}
            onClick={() => setRanges(previous => previous.filter((_, i) => i !== index))} />
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
        <Button type="primary" disabled={!request || invalid || busy} loading={busy}
          onClick={() => onSubmit(request.background, request.label)}>Refine as new version</Button>
        {onEditCurve ? <Tooltip title="Draw the background yourself: drag anchor points of the curve on the plot">
          <Button disabled={busy} onClick={onEditCurve}>Edit curve</Button>
        </Tooltip> : null}
      </Space>
    </Space>
  </div>;
}

/**
 * "Background…" editor of the fit view. `onSubmit(background, label)` starts a quick re-refinement.
 * Without `open` it is a button with a popover; with `open`/`onOpenChange` it is a modal opened
 * from elsewhere (the Advanced menu).
 */
export default function BackgroundEditor({ busy, disabled, getView, onSubmit, baseDegree = null, manual = false, open: controlledOpen, onOpenChange, onEditCurve = null }) {
  const [localOpen, setLocalOpen] = React.useState(false);
  if (controlledOpen !== undefined) {
    return <Modal title="Background" open={controlledOpen} footer={null} width={420} destroyOnClose
      onCancel={() => onOpenChange?.(false)}>
      <EditorForm busy={busy} getView={getView} baseDegree={baseDegree} manual={manual}
        onEditCurve={onEditCurve ? () => { onOpenChange?.(false); onEditCurve(); } : null}
        onSubmit={(background, label) => { onOpenChange?.(false); onSubmit(background, label); }} />
    </Modal>;
  }
  return <Popover trigger="click" open={localOpen} onOpenChange={setLocalOpen} placement="bottomLeft" title="Background"
    content={<EditorForm busy={busy} getView={getView} baseDegree={baseDegree} manual={manual}
      onEditCurve={onEditCurve ? () => { setLocalOpen(false); onEditCurve(); } : null}
      onSubmit={(background, label) => { setLocalOpen(false); onSubmit(background, label); }} />}>
    <Button disabled={disabled}>Background…</Button>
  </Popover>;
}

/**
 * Panel of the background curve editor (shown above the plot while the curve is edited on it).
 * `anchors` are `[[2θ, y], ...]`; `onChange(anchors)` replaces them; `selected` is the anchor
 * index Delete removes. `warnings` never block; `error` (anchors the server would reject) does.
 */
export function BackgroundCurvePanel({
  anchors, selected = null, onChange, onSelect, onRemove, warnings = [], error = null, busy = false,
  canReset = false, onSubmit, onReset, onCancel,
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
  return <div role="region" aria-label="Background curve editor" style={{ marginBottom: 8, padding: '8px 12px',
    border: '1px solid var(--dara-border, #d9d9d9)', borderRadius: 6, background: 'var(--dara-surface-sunken, #fafafa)' }}>
    <Space align="start" wrap size="large" style={{ width: '100%' }}>
      <div style={{ maxWidth: 420 }}>
        <Text strong>Edit background curve</Text> <Text type="secondary">({anchors.length} points, PCHIP)</Text>
        <div><Text type="secondary">Drag a point up or down · click the curve to add a point · Alt+click a point or press Delete to remove the selected one</Text></div>
        {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} /> : null}
        {warnings.map(text => <Alert key={text} type="warning" showIcon message={text} style={{ marginTop: 8 }} />)}
        <Space wrap style={{ marginTop: 8 }}>
          <Button type="primary" disabled={busy || Boolean(error)} loading={busy} onClick={onSubmit}>Refine as new version</Button>
          <Tooltip title={canReset ? "Refine with BGMN's automatic background polynomial" : 'This fit already uses the automatic background'}>
            <Button disabled={busy || !canReset} onClick={onReset}>Reset to automatic</Button>
          </Tooltip>
          <Button onClick={onCancel}>Cancel</Button>
        </Space>
      </div>
      <Table size="small" columns={columns} dataSource={rows} pagination={false} scroll={{ y: 150 }} style={{ width: 280 }}
        rowClassName={row => (row.index === selected ? 'ant-table-row-selected' : '')}
        onRow={row => ({ onClick: () => onSelect(row.index) })} />
    </Space>
  </div>;
}
