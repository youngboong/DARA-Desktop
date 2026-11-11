import React from 'react';
import { Alert, Button, InputNumber, Space, Spin, Switch, Table, Tooltip, Typography } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import Loadable from 'react-loadable';
import Layout from '../components/Layout';
import { comparisonTraces, measuredAngularBounds, parseComparisonTaskIds } from '../components/patternComparison.mjs';
import { TRAY_LINES } from '../components/composition.mjs';
import { viewChangeFromRelayout } from '../components/plotNavigation.mjs';
import { NAVIGATION_CONFIG, NAVIGATION_DRAGMODE, NAVIGATION_HELP } from '../components/usePlotView';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const Plotly = Loadable({
  loader: () => import('../components/RefinementPlotly'),
  loading: ({ timedOut }) => timedOut ? <Alert type="error" message="Could not load the plot. Refresh to retry." /> : <Spin />,
  timeout: 20000,
});

function MeasuredComparePage({ location }) {
  const taskQuery = new URLSearchParams(location.search).get('tasks') || '';
  const [patterns, setPatterns] = React.useState([]);
  const [errors, setErrors] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [normalize, setNormalize] = React.useState(true);
  const [sqrt, setSqrt] = React.useState(false);
  const [offset, setOffset] = React.useState(110);
  const [minimum, setMinimum] = React.useState(null);
  const [maximum, setMaximum] = React.useState(null);
  const [range, setRange] = React.useState(undefined);
  const [viewRevision, setViewRevision] = React.useState(0);

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
      const bounds = measuredAngularBounds(loaded);
      setMinimum(bounds ? bounds[0] : null);
      setMaximum(bounds ? bounds[1] : null);
      setLoading(false);
    });
    return () => controller.abort();
  }, [taskQuery]);

  const traces = React.useMemo(() => comparisonTraces(patterns, { normalize, offset, sqrt }), [patterns, normalize, offset, sqrt]);
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
      {patterns.length > 0 && <Space wrap size="large">
        <Space><Switch checked={normalize} onChange={changeNormalization} /><span>Normalize max |intensity| to 100</span></Space>
        <Space><Switch checked={sqrt} onChange={value => { setSqrt(value); setViewRevision(previous => previous + 1); }} aria-label="Square-root intensity" /><span>√I scale (weak peaks)</span></Space>
        <Space><label htmlFor="pattern-offset">Vertical offset</label><InputNumber id="pattern-offset" min={0} value={offset} onChange={value => setOffset(value ?? 0)} style={{ width: 115 }} /><Button onClick={() => setOffset(0)}>Overlay</Button></Space>
        <Space><label htmlFor="comparison-theta-min">2θ range (°)</label><InputNumber id="comparison-theta-min" aria-label="Minimum two theta" precision={2} value={minimum} onChange={setMinimum} /><span>–</span><InputNumber aria-label="Maximum two theta" precision={2} value={maximum} onChange={setMaximum} /><Button disabled={!validRange} onClick={() => { setRange([minimum, maximum]); setViewRevision(previous => previous + 1); }}>Apply</Button><Button onClick={resetView}>Reset view</Button></Space>
      </Space>}
      {loading ? <Spin /> : patterns.length > 0 && <Plotly
        data={traces}
        layout={{
          autosize: true, height: 530 + patterns.length * 23,
          margin: { t: 25, l: 85, r: 25, b: 80 + patterns.length * 23 },
          // A copy: Plotly writes box zooms into the range array it is given, and bounds is the zoom limit.
          xaxis: { title: '2θ (°)', range: (range || bounds) ? [...(range || bounds)] : undefined, autorange: !(range || bounds), showline: true, mirror: true, ticks: 'outside', zeroline: false },
          yaxis: { title: `${normalize ? 'Normalized' : 'Measured'} ${sqrt ? '√intensity' : 'intensity'} + offset`, showline: true, ticks: 'outside', zeroline: false },
          legend: { orientation: 'h', x: 0, y: -0.18, xanchor: 'left', yanchor: 'top' },
          hovermode: 'closest', dragmode: NAVIGATION_DRAGMODE, uirevision: `${taskQuery}-${viewRevision}`,
          colorway: TRAY_LINES,
        }}
        onRelayout={event => {
          const change = viewChangeFromRelayout(event, { full: bounds });
          if (change.xRange) { setRange(change.xRange); setMinimum(Number(Math.min(...change.xRange).toFixed(3))); setMaximum(Number(Math.max(...change.xRange).toFixed(3))); }
        }}
        onDoubleClick={resetView} xBounds={bounds}
        config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG, toImageButtonOptions: { format: 'svg', filename: 'dara-pattern-comparison' } }}
        style={{ width: '100%' }} useResizeHandler
      />}
      {patterns.length > 0 && <Table size="small" rowKey="task_id" dataSource={patterns} pagination={false} scroll={{ x: 640 }} columns={[
        { title: 'Task / sample', render: (_, row) => <a href={`/task?id=${row.task_id}`}>#{row.task_id} — {row.name}</a> },
        { title: '2θ range (°)', render: (_, row) => `${row.angles[0].toFixed(3)}–${row.angles[row.angles.length - 1].toFixed(3)}` },
        { title: 'Points', render: (_, row) => row.angles.length },
        { title: 'Analysis wavelength', dataIndex: 'wavelength', render: value => value || 'Not recorded' },
        { title: 'Pattern source', dataIndex: 'source' },
      ]} />}
    </Space>
  </Layout>;
}

export default function ComparePage({ location }) {
  return <MeasuredComparePage location={location} />;
}
