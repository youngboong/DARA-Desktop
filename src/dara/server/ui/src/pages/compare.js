import React from 'react';
import { Alert, Button, Checkbox, InputNumber, Radio, Select, Space, Spin, Switch, Table, Tooltip, Typography } from 'antd';
import { DownloadOutlined, InfoCircleOutlined } from '@ant-design/icons';
import Loadable from 'react-loadable';
import Layout from '../components/Layout';
import {
  comparisonTraces, differenceCsv, differenceCurves, mapMatrix, measuredAngularBounds, parseComparisonTaskIds, parseSeriesValues,
} from '../components/patternComparison.mjs';
import { TRAY_LINES } from '../components/composition.mjs';
import { viewChangeFromRelayout } from '../components/plotNavigation.mjs';
import { NAVIGATION_CONFIG, NAVIGATION_DRAGMODE, NAVIGATION_HELP } from '../components/usePlotView';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const Plotly = Loadable({
  // RefinementPlotly registers scatter only; the map adds the heatmap trace to the same Plotly core here.
  loader: () => Promise.all([import('../components/RefinementPlotly'), import('plotly.js/lib/core'), import('plotly.js/lib/heatmap')])
    .then(([component, core, heatmap]) => { (core.default || core).register([heatmap.default || heatmap]); return component; }),
  loading: ({ timedOut }) => timedOut ? <Alert type="error" message="Could not load the plot. Refresh to retry." /> : <Spin />,
  timeout: 20000,
});

// Sequential, one hue (data blue of the theme), light to dark.
const MAP_COLORS = [[0, '#f7f8f5'], [0.25, '#c5d6e3'], [0.55, '#7ea3c0'], [0.8, '#3f6c8f'], [1, '#1b3348']];
const AXIS = { showline: true, mirror: true, ticks: 'outside', zeroline: false };
const VIEWS = [
  { value: 'stack', label: 'Overlay / stack' },
  { value: 'map', label: <Tooltip title="2θ against pattern with the intensity as colour, as for in-situ and series measurements"><span>Map</span></Tooltip> },
  { value: 'difference', label: <Tooltip title="A − k·B: what one pattern has that the other has not"><span>Difference</span></Tooltip> },
];
const shortName = pattern => `#${pattern.task_id} ${pattern.name.length > 40 ? `${pattern.name.slice(0, 38)}…` : pattern.name}`;

function saveText(text, filename) {
  const link = document.createElement('a');
  link.href = window.URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
  link.download = filename;
  link.click();
  window.URL.revokeObjectURL(link.href);
}

function MeasuredComparePage({ location }) {
  const params = new URLSearchParams(location.search);
  const taskQuery = params.get('tasks') || '';
  // A series passes its variable (temperature, time) per task: the map uses it as its y axis.
  const valuesQuery = params.get('values') || '';
  const unit = params.get('unit') || '';
  const [patterns, setPatterns] = React.useState([]);
  const [errors, setErrors] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [view, setView] = React.useState('stack');
  const [normalize, setNormalize] = React.useState(true);
  const [sqrt, setSqrt] = React.useState(false);
  const [offset, setOffset] = React.useState(110);
  const [minimum, setMinimum] = React.useState(null);
  const [maximum, setMaximum] = React.useState(null);
  const [range, setRange] = React.useState(undefined);
  const [viewRevision, setViewRevision] = React.useState(0);
  const [pair, setPair] = React.useState([null, null]); // task ids of A and B
  const [scale, setScale] = React.useState('auto'); // 'auto' or the factor k of B

  React.useEffect(() => {
    const controller = new AbortController();
    const ids = parseComparisonTaskIds(taskQuery);
    setLoading(true);
    setErrors([]);
    setPatterns([]);
    setRange(undefined);
    Promise.all(ids.map(async taskId => {
      try {
        const response = await fetch(`${URL}/task/${taskId}/pattern`, { signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
        return { pattern: body };
      } catch (error) {
        return { error: `Task ${taskId}: ${error.message}` };
      }
    })).then(outcomes => {
      if (controller.signal.aborted) return;
      const loaded = outcomes.filter(item => item.pattern).map(item => item.pattern);
      setPatterns(loaded);
      setErrors(outcomes.filter(item => item.error).map(item => item.error));
      setPair([loaded[0]?.task_id ?? null, loaded[1]?.task_id ?? null]);
      const bounds = measuredAngularBounds(loaded);
      setMinimum(bounds ? bounds[0] : null);
      setMaximum(bounds ? bounds[1] : null);
      setLoading(false);
    });
    return () => controller.abort();
  }, [taskQuery]);

  const traces = React.useMemo(() => comparisonTraces(patterns, { normalize, offset, sqrt }), [patterns, normalize, offset, sqrt]);
  const seriesValues = React.useMemo(() => {
    const ids = parseComparisonTaskIds(taskQuery);
    const values = parseSeriesValues(valuesQuery, ids.length);
    if (!values) return null;
    const byId = new Map(ids.map((id, index) => [id, values[index]]));
    return patterns.map(pattern => byId.get(pattern.task_id));
  }, [taskQuery, valuesQuery, patterns]);
  const map = React.useMemo(() => (view === 'map' && patterns.length > 1 ? mapMatrix(patterns, { normalize, sqrt, values: seriesValues }) : null),
    [view, patterns, normalize, sqrt, seriesValues]);
  const patternA = patterns.find(pattern => pattern.task_id === pair[0]);
  const patternB = patterns.find(pattern => pattern.task_id === pair[1]);
  const difference = React.useMemo(() => (view === 'difference' && patternA && patternB && patternA !== patternB
    ? differenceCurves(patternA, patternB, scale) : null), [view, patternA, patternB, scale]);
  const wavelengths = new Set(patterns.map(pattern => pattern.wavelength).filter(Boolean));
  const missingRadiation = patterns.some(pattern => !pattern.wavelength);
  // Zoom and pan stay inside the measured 2θ range; the view starts there (no autorange padding).
  const bounds = React.useMemo(() => measuredAngularBounds(patterns), [patterns]);
  const validRange = minimum !== null && maximum !== null && Number.isFinite(minimum) && Number.isFinite(maximum) && minimum < maximum;
  const changeNormalization = value => {
    setNormalize(value);
    const largest = patterns.reduce((size, pattern) => pattern.intensities.reduce((maximumValue, intensity) => Math.max(maximumValue, Math.abs(intensity)), size), 0);
    setOffset(value ? 110 : Math.ceil(largest * 1.1));
    setViewRevision(previous => previous + 1);
  };
  const resetView = () => {
    const bounds = measuredAngularBounds(patterns);
    setRange(undefined);
    setMinimum(bounds ? bounds[0] : null);
    setMaximum(bounds ? bounds[1] : null);
    setViewRevision(previous => previous + 1);
  };
  const xRange = (range || bounds) ? [...(range || bounds)] : undefined;
  const intensityTitle = `${normalize ? 'Normalized' : 'Measured'} ${sqrt ? '√intensity' : 'intensity'}`;

  let plot = null;
  if (view === 'stack') {
    plot = { data: traces, layout: {
      height: 530 + patterns.length * 23,
      margin: { t: 25, l: 85, r: 25, b: 80 + patterns.length * 23 },
      yaxis: { title: `${intensityTitle} + offset`, showline: true, ticks: 'outside', zeroline: false },
      legend: { orientation: 'h', x: 0, y: -0.18, xanchor: 'left', yanchor: 'top' },
      colorway: TRAY_LINES,
    } };
  } else if (view === 'map' && map) {
    const rows = map.y.length;
    plot = { data: [{
      type: 'heatmap', x: map.x, y: map.numeric ? map.y : map.labels, z: map.z, colorscale: MAP_COLORS, zsmooth: false,
      colorbar: { title: { text: intensityTitle, side: 'right' }, thickness: 12, outlinewidth: 0 },
      hovertemplate: `2θ %{x:.3f}°<br>${map.numeric ? `%{y} ${unit}` : '%{y}'}<br>${intensityTitle} %{z:.4g}<extra></extra>`,
    }], layout: {
      height: Math.max(380, 140 + rows * 28),
      margin: { t: 20, l: map.numeric ? 85 : 260, r: 25, b: 60 },
      yaxis: { ...AXIS, title: map.numeric ? (unit || 'Series variable') : '', type: map.numeric ? 'linear' : 'category', automargin: true },
    } };
  } else if (view === 'difference' && difference) {
    plot = { data: [
      { type: 'scatter', mode: 'lines', name: `A: ${shortName(patternA)}`, x: difference.x, y: difference.a, line: { width: 1.25, color: TRAY_LINES[0] },
        hovertemplate: '2θ %{x:.4f}°<br>A %{y:.4g}<extra></extra>' },
      { type: 'scatter', mode: 'lines', name: `${difference.k === 1 ? '' : `${Number(difference.k.toPrecision(4))}·`}B: ${shortName(patternB)}`,
        x: difference.x, y: difference.b, line: { width: 1.25, color: TRAY_LINES[1] }, hovertemplate: '2θ %{x:.4f}°<br>k·B %{y:.4g}<extra></extra>' },
      { type: 'scatter', mode: 'lines', name: 'A − k·B', x: difference.x, y: difference.difference, xaxis: 'x', yaxis: 'y2',
        line: { width: 1, color: '#8c8c8c' }, hovertemplate: '2θ %{x:.4f}°<br>A − k·B %{y:.4g}<extra></extra>' },
    ], layout: {
      height: 600,
      margin: { t: 40, l: 85, r: 25, b: 60 },
      yaxis: { ...AXIS, title: 'Measured intensity', domain: [0.34, 1] },
      yaxis2: { ...AXIS, title: 'A − k·B', domain: [0, 0.26], anchor: 'x', zeroline: true, zerolinecolor: '#c9cdc6' },
      legend: { orientation: 'h', x: 0, y: 1.02, xanchor: 'left', yanchor: 'bottom' },
    } };
  }
  const overlapNote = view === 'map' && map?.grid.differs
    ? `The measured 2θ ranges differ: the map shows their common range ${map.grid.range[0].toFixed(2)}–${map.grid.range[1].toFixed(2)}° at the finest step (${map.grid.step.toFixed(4)}°), each pattern interpolated linearly.`
    : null;

  return <Layout hasSider={false} title="Pattern comparison">
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Space wrap align="center"><Typography.Title level={2} style={{ margin: 0, fontSize: 'var(--dara-font-20)', fontWeight: 400 }}><a href="/results">Results</a>{' › '}<span style={{ fontWeight: 600 }}>Compare measured patterns</span></Typography.Title>
        {patterns.length > 1 ? <Tooltip title={`Original measured patterns, not calculated fits. ${NAVIGATION_HELP} Click a legend item to hide or show a sample. Controls change the view only.`}>
          <InfoCircleOutlined aria-label="About this comparison" style={{ color: 'var(--dara-text-hint)' }} />
        </Tooltip> : null}</Space>
      {errors.map(error => <Alert key={error} type="error" message={error} showIcon />)}
      {!loading && patterns.length < 2 && <div className="dara-empty">
        <h3>Nothing to compare yet</h3>
        <p>Select at least two completed tasks in Results to compare their measured patterns.</p>
        <Space><Button type="primary" href="/results">Go to Results</Button><Button href="/">Submit a pattern</Button></Space>
      </div>}
      {(wavelengths.size > 1 || missingRadiation) && patterns.length > 1 && <Alert type="warning" showIcon message="Check the radiation before comparing peak positions." description="The saved analysis wavelengths differ or are not recorded for every sample. Equal 2θ does not imply equal d-spacing when wavelengths differ." />}
      {patterns.some(pattern => pattern.dropped_points > 0) && <Alert type="warning" showIcon message="Non-finite points were omitted from the comparison view." />}
      {patterns.length > 0 && <div className="dara-toolbar" style={{ marginBottom: 0 }}>
        {patterns.length > 1 ? <div className="dara-toolbar-group">
          <Radio.Group size="small" optionType="button" aria-label="Comparison view" value={view} options={VIEWS}
            onChange={event => { setView(event.target.value); setViewRevision(previous => previous + 1); }} />
        </div> : null}
        {view !== 'difference' ? <div className="dara-toolbar-group" aria-label="Intensity">
          <label className="dara-toolbar-item"><span>Normalize max |I| to 100</span><Switch size="small" checked={normalize} onChange={changeNormalization} aria-label="Normalize" /></label>
          <label className="dara-toolbar-item"><span>√I (weak peaks)</span><Switch size="small" checked={sqrt} onChange={value => { setSqrt(value); setViewRevision(previous => previous + 1); }} aria-label="Square-root intensity" /></label>
        </div> : null}
        {view === 'stack' ? <div className="dara-toolbar-group">
          <label className="dara-toolbar-item" htmlFor="pattern-offset">Vertical offset
            <InputNumber size="small" id="pattern-offset" min={0} value={offset} onChange={value => setOffset(value ?? 0)} style={{ width: 96 }} /></label>
          <Button size="small" onClick={() => setOffset(0)}>Overlay</Button>
        </div> : null}
        {view === 'difference' ? <div className="dara-toolbar-group" aria-label="Difference">
          {['A', 'B'].map((name, index) => <label key={name} className="dara-toolbar-item"><span>{name}</span>
            <Select size="small" aria-label={`Pattern ${name}`} value={pair[index]} style={{ width: 220 }} dropdownMatchSelectWidth={false}
              onChange={value => setPair(previous => (index ? [previous[0], value] : [value, previous[1]]))}
              options={patterns.map(pattern => ({ value: pattern.task_id, label: shortName(pattern) }))} /></label>)}
          <label className="dara-toolbar-item"><Tooltip title="B is multiplied by k before it is subtracted. Auto: the k that minimises Σ(A − k·B)² over the common range."><span>k</span></Tooltip>
            <InputNumber size="small" aria-label="Scale factor of B" min={0} step={0.05} value={difference ? Number(difference.k.toPrecision(5)) : null}
              disabled={scale === 'auto'} onChange={value => setScale(Number.isFinite(value) ? value : 1)} style={{ width: 96 }} /></label>
          <Checkbox checked={scale === 'auto'} onChange={event => setScale(event.target.checked ? 'auto' : Number((difference?.k ?? 1).toPrecision(5)))}>Auto</Checkbox>
          <Button size="small" icon={<DownloadOutlined />} disabled={!difference}
            onClick={() => saveText(differenceCsv(difference, `#${patternA.task_id} ${patternA.name}`, `#${patternB.task_id} ${patternB.name}`),
              `dara_difference_${patternA.task_id}-${patternB.task_id}.csv`)}>Download CSV</Button>
        </div> : null}
        <div className="dara-toolbar-group">
          <label className="dara-toolbar-item" htmlFor="comparison-theta-min">2θ (°)
            <InputNumber size="small" id="comparison-theta-min" aria-label="Minimum two theta" precision={2} value={minimum} onChange={setMinimum} style={{ width: 84 }} /></label>
          <span>–</span>
          <InputNumber size="small" aria-label="Maximum two theta" precision={2} value={maximum} onChange={setMaximum} style={{ width: 84 }} />
          <Button size="small" disabled={!validRange} onClick={() => { setRange([minimum, maximum]); setViewRevision(previous => previous + 1); }}>Apply</Button>
          <Button size="small" onClick={resetView}>Reset view</Button>
        </div>
      </div>}
      {overlapNote ? <Typography.Text className="dara-footnote">{overlapNote}</Typography.Text> : null}
      {view === 'map' && patterns.length > 1 && !map ? <Alert type="warning" showIcon message="The measured 2θ ranges do not overlap: there is no common range to map." /> : null}
      {view === 'difference' && pair[0] === pair[1] ? <Alert type="info" showIcon message="Choose two different patterns for A and B." /> : null}
      {view === 'difference' && difference && !difference.x.length ? <Alert type="warning" showIcon message="A and B do not overlap in 2θ." /> : null}
      {loading ? <Spin /> : plot && <Plotly
        data={plot.data}
        layout={{
          autosize: true, hovermode: 'closest', dragmode: NAVIGATION_DRAGMODE, uirevision: `${taskQuery}-${view}-${viewRevision}`,
          ...plot.layout,
          // A copy: Plotly writes box zooms into the range array it is given, and bounds is the zoom limit.
          xaxis: { ...AXIS, title: '2θ (°)', range: xRange, autorange: !xRange, ...(view === 'difference' ? { anchor: 'y2' } : {}) },
        }}
        onRelayout={event => {
          const change = viewChangeFromRelayout(event, { full: bounds });
          if (change.xRange) { setRange(change.xRange); setMinimum(Number(Math.min(...change.xRange).toFixed(3))); setMaximum(Number(Math.max(...change.xRange).toFixed(3))); }
        }}
        onDoubleClick={resetView} xBounds={bounds}
        config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG, toImageButtonOptions: { format: 'svg', filename: `dara-pattern-${view}` } }}
        style={{ width: '100%' }} useResizeHandler
      />}
      {patterns.length > 0 && <Table size="small" rowKey="task_id" dataSource={patterns} pagination={false} scroll={{ x: 640 }} columns={[
        { title: 'Task / sample', render: (_, row) => <a href={`/task?id=${row.task_id}`}>#{row.task_id} — {row.name}</a> },
        ...(seriesValues ? [{ title: unit ? `Variable (${unit})` : 'Variable', className: 'dara-num', render: (_, row, index) => seriesValues[index] }] : []),
        { title: '2θ range (°)', render: (_, row) => `${row.angles[0].toFixed(3)}–${row.angles[row.angles.length - 1].toFixed(3)}` },
        { title: 'Points', className: 'dara-num', render: (_, row) => row.angles.length },
        { title: 'Analysis wavelength', dataIndex: 'wavelength', render: value => value || 'Not recorded' },
        { title: 'Pattern source', dataIndex: 'source' },
      ]} />}
    </Space>
  </Layout>;
}

export default function ComparePage({ location }) {
  return <MeasuredComparePage location={location} />;
}
