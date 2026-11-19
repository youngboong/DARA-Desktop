import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Progress, Select, Space, Spin, Table, Tag, Typography, message } from 'antd';
import { LineChartOutlined, ReloadOutlined, TableOutlined } from '@ant-design/icons';
import Layout from '../components/Layout';
import { resultColumns } from '../components/ResultColumns';
import { downloadTable, errorText } from '../components/exportTable';
import { taskHref } from '../components/sampleNav.mjs';
import { formatDate } from '../components/resultsList.mjs';
import {
  LATTICE_PARAMETERS, fitTraces, hasActiveItems, latticeTraces, phaseLabels, phaseTraces, progress,
  taskIds, xAxis,
} from '../components/seriesTrends.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const POLL_MS = 10000;
const MAX_RETRY_MS = 60000;
const MAX_COMPARE = 20;
const MAX_EXPORT = 50;

const Plotly = Loadable({
  loader: () => import('react-plotly.js'),
  loading: ({ timedOut }) => timedOut ? <Alert type="error" message="Could not load the plot. Refresh to retry." /> : <Spin />,
  timeout: 20000,
});

const PARAMETER_LABEL = { a: 'a (Å)', b: 'b (Å)', c: 'c (Å)', alpha: 'α (°)', beta: 'β (°)', gamma: 'γ (°)' };

const baseLayout = (axis, yTitle, extra = {}) => ({
  autosize: true, height: 420, margin: { t: 20, l: 70, r: 70, b: 60 },
  xaxis: { title: axis.title, showline: true, mirror: true, ticks: 'outside', zeroline: false },
  yaxis: { title: yTitle, showline: true, mirror: true, ticks: 'outside', zeroline: false },
  legend: { orientation: 'h', x: 0, y: -0.25 },
  hovermode: 'closest',
  ...extra,
});

const config = name => ({ responsive: true, displaylogo: false, toImageButtonOptions: { format: 'svg', filename: name } });

const errorBars = values => ({ type: 'data', array: values, visible: values.some(value => value !== null) });

function ChartBlock({ title, note, children }) {
  return <section style={{ marginBottom: 28 }}>
    <h3 style={{ marginBottom: 4 }}>{title}</h3>
    {note ? <Typography.Text type="secondary">{note}</Typography.Text> : null}
    <div style={{ marginTop: 8 }}>{children}</div>
  </section>;
}

function PhaseChart({ items, axis }) {
  const traces = phaseTraces(items);
  if (!traces.length) {
    return <Alert type="info" showIcon message="Phase fractions are not available yet" description="They appear once tasks are completed. Results stored by an older version are summarised when this page is opened." />;
  }
  return <Plotly
    data={traces.map(trace => ({
      type: 'scatter', mode: 'lines+markers', name: trace.label, x: trace.x, y: trace.y,
      error_y: errorBars(trace.err), connectgaps: false,
    }))}
    layout={baseLayout(axis, 'Weight fraction (wt%)')}
    config={config('dara-series-phase-fractions')} style={{ width: '100%' }} useResizeHandler />;
}

function LatticeChart({ items, axis }) {
  const labels = phaseLabels(items);
  const [phase, setPhase] = React.useState(undefined);
  const [parameter, setParameter] = React.useState('a');
  const selected = labels.includes(phase) ? phase : labels[0];
  if (!labels.length) return <Alert type="info" showIcon message="Lattice parameters are not available yet" />;
  const trace = latticeTraces(items, selected, parameter);
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <label>Phase <Select aria-label="Phase" style={{ width: 260 }} value={selected} onChange={setPhase}
        options={labels.map(label => ({ value: label, label }))} /></label>
      <label>Parameter <Select aria-label="Lattice parameter" style={{ width: 120 }} value={parameter} onChange={setParameter}
        options={LATTICE_PARAMETERS.map(key => ({ value: key, label: PARAMETER_LABEL[key] }))} /></label>
    </Space>
    {trace.y.every(value => value === null)
      ? <Alert type="info" showIcon message={`${PARAMETER_LABEL[parameter]} is not reported for this phase.`} description="Parameters fixed by the crystal system are not listed." />
      : <Plotly
        data={[{ type: 'scatter', mode: 'lines+markers', name: `${selected} — ${parameter}`, x: trace.x, y: trace.y, error_y: errorBars(trace.err), connectgaps: false }]}
        layout={baseLayout(axis, PARAMETER_LABEL[parameter], { showlegend: false })}
        config={config(`dara-series-lattice-${parameter}`)} style={{ width: '100%' }} useResizeHandler />}
  </>;
}

function FitChart({ items, axis }) {
  const fit = fitTraces(items);
  if (!fit.x.length) return <Alert type="info" showIcon message="Fit statistics are not available yet" />;
  return <Plotly
    data={[
      { type: 'scatter', mode: 'lines+markers', name: 'Rwp (%)', x: fit.x, y: fit.rwp, connectgaps: false },
      { type: 'scatter', mode: 'lines+markers', name: 'GOF', x: fit.x, y: fit.gof, yaxis: 'y2', connectgaps: false },
    ]}
    layout={baseLayout(axis, 'Rwp (%)', { yaxis2: { title: 'GOF', overlaying: 'y', side: 'right', showline: true, ticks: 'outside', zeroline: false } })}
    config={config('dara-series-fit')} style={{ width: '100%' }} useResizeHandler />;
}

// "300 °C" (value, a space, the unit); the token from the file name when it has no value.
const variableText = variable => (variable?.value !== null && variable?.value !== undefined
  ? `${variable.value}${variable.unit ? ` ${variable.unit}` : ''}` : variable?.raw ?? '—');

function SeriesDetail({ id, onName }) {
  const [data, setData] = React.useState(undefined);
  const [error, setError] = React.useState(null); // {text, notFound, retryIn}
  const [attempt, setAttempt] = React.useState(0);
  const [exporting, setExporting] = React.useState(false);

  // Polls while tasks are queued or running; a failed refresh retries with a growing delay
  // (10 s, 20 s, 40 s, then every minute) instead of stopping.
  React.useEffect(() => {
    let active = true;
    let timer = null;
    let failures = 0;
    const load = () => fetch(`${URL}/series/${encodeURIComponent(id)}`)
      .then(async response => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          const failure = new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
          failure.notFound = response.status === 404;
          throw failure;
        }
        return body;
      })
      .then(body => {
        if (!active) return;
        failures = 0;
        setData(body);
        setError(null);
        if (hasActiveItems(body.items)) timer = window.setTimeout(load, POLL_MS);
      })
      .catch(e => {
        if (!active) return;
        if (e.notFound) { setError({ text: errorText(e), notFound: true }); setData(null); return; }
        const delay = Math.min(POLL_MS * 2 ** failures, MAX_RETRY_MS);
        failures += 1;
        setError({ text: errorText(e), retryIn: delay / 1000 });
        setData(previous => previous ?? null);
        timer = window.setTimeout(load, delay);
      });
    setData(undefined);
    load();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [id, attempt]);
  React.useEffect(() => { if (data?.name) onName(data.name); }, [data?.name, onName]);

  if (data === undefined) return <Spin size="large" />;
  if (data === null) {
    return <div className="dara-empty" role="alert">
      <h3>{error?.notFound ? 'Series not found' : 'The series could not be loaded'}</h3>
      <p>{error?.notFound ? 'It may have been deleted, or the link is incomplete.' : `${error?.text} Retrying in ${error?.retryIn} s.`}</p>
      <div className="dara-empty-actions">
        {error?.notFound ? null : <Button type="primary" icon={<ReloadOutlined />} onClick={() => setAttempt(value => value + 1)}>Retry now</Button>}
        <Button type={error?.notFound ? 'primary' : 'default'} href="/series/">All series</Button>
        <a href="/results/">Results</a>
      </div>
    </div>;
  }

  const items = data.items || [];
  const axis = xAxis(items);
  const { done, total } = progress(items);
  // A batch of identifications ("Identify all") has no refined amounts or fit quality to plot.
  const identifiedOnly = items.length > 0 && items.every(item => item.summary?.analysis_mode === 'identification');
  const completedIds = taskIds(items, { completedOnly: true });
  const compareIds = completedIds.slice(0, MAX_COMPARE);
  // The series variable goes along, so the comparison map can use it as its y axis.
  const valueOf = new Map(items.map((item, index) => [item.task_id, axis.values[index]]));
  const compareValues = axis.usesVariable ? `&values=${compareIds.map(id => valueOf.get(id)).join(',')}${axis.title ? `&unit=${encodeURIComponent(axis.title)}` : ''}` : '';
  const allIds = taskIds(items).slice(0, MAX_EXPORT);
  const seriesIds = taskIds(items);
  // A task opened here knows the series order (Previous / Next sample) and returns to this series.
  const back = `from=series&id=${encodeURIComponent(id)}`;
  const columns = resultColumns({ hrefOf: row => taskHref(row.fw_id, { ids: seriesIds, back }), sourceHref: task => taskHref(task, { back }) });
  const rows = items.map(item => ({ ...item, fw_id: item.task_id, adopted: item.adopted ?? item.summary?.adopted }));
  const exportTable = async () => {
    setExporting(true);
    try {
      message.success(`Exported to ${await downloadTable(allIds)}.`);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setExporting(false);
    }
  };

  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    {error ? <Alert type="warning" showIcon message={`Refresh failed: ${error.text}`}
      description={`Retrying in ${error.retryIn} s.`} action={<Button size="small" onClick={() => setAttempt(value => value + 1)}>Retry now</Button>} /> : null}
    <div className="dara-page-head">
      <h1><a href="/series/" style={{ fontWeight: 400 }}>Series</a>{' › '}{data.name}</h1>
      <Space wrap size={4}>
        <Tag>{`${total} tasks`}</Tag>
        {data.unit ? <Tag>{`Variable: ${data.unit}`}</Tag> : <Tag>No variable parsed — ordered by file name</Tag>}
        {data.created_on ? <Tag>{`Submitted ${formatDate(data.created_on)}`}</Tag> : null}
      </Space>
      <div className="dara-page-actions">
        <Button icon={<LineChartOutlined />} disabled={completedIds.length < 2}
          href={completedIds.length >= 2 ? `/compare/?tasks=${compareIds.join(',')}${compareValues}&from=series&id=${encodeURIComponent(id)}` : undefined}>Compare patterns</Button>
        <Button icon={<TableOutlined />} loading={exporting} onClick={exportTable}>{exporting ? 'Exporting…' : 'Export table (CSV)'}</Button>
      </div>
    </div>
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Progress percent={total ? Math.round((done / total) * 100) : 0} showInfo={false} style={{ flex: '1 1 160px', minWidth: 0, margin: 0 }} />
        <Typography.Text style={{ whiteSpace: 'nowrap' }}>{`${done} / ${total} completed`}</Typography.Text>
      </div>
      {hasActiveItems(items) ? <Typography.Text type="secondary">Updating every {POLL_MS / 1000} s while tasks are queued or running.</Typography.Text> : null}
      {completedIds.length > MAX_COMPARE ? <div><Typography.Text type="secondary">{`Pattern comparison shows the first ${MAX_COMPARE} completed tasks.`}</Typography.Text></div> : null}
    </div>
    {items.length > MAX_EXPORT ? <Typography.Text type="secondary">{`The table export takes the first ${MAX_EXPORT} tasks; select the rest in Results to export them.`}</Typography.Text> : null}
    <Table size="small" className="dara-results-table" rowKey="fw_id" pagination={false} dataSource={rows} tableLayout="fixed" scroll={{ x: 1000 }}
      columns={[
        { title: 'Order', dataIndex: 'order', key: 'order', width: 64, className: 'dara-num', render: value => value + 1 },
        { title: 'Variable', key: 'variable', width: 100, className: 'dara-num', render: (_, row) => variableText(row.variable) },
        columns.name, columns.verdict, columns.rwp, columns.gof, columns.phases,
      ]} />
    {identifiedOnly ? <Alert type="info" showIcon message="Identifications: peak matching only, not verified by a fit"
      description="No refined wt%, lattice parameters or Rwp to plot. Refine with Rietveld on a task page verifies its phases." /> : <>
    {done < 2 ? <Alert type="info" showIcon message="Trend charts need at least two completed tasks."
      description={<><a href="/results/">Results</a> shows every task's progress; <a href="/?series=1">submit another series</a>.</>} /> : null}
    <ChartBlock title="Phase fractions" note="Weight fractions are relative to the modelled crystalline phases; error bars are BGMN estimated standard deviations. Gaps mean the phase was not in the final result of that sample.">
      <PhaseChart items={items} axis={axis} />
    </ChartBlock>
    <ChartBlock title="Lattice parameters" note="Per phase identity; error bars are BGMN estimated standard deviations.">
      <LatticeChart items={items} axis={axis} />
    </ChartBlock>
    <ChartBlock title="Fit quality" note="Rwp and goodness of fit of the final refinement of each sample.">
      <FitChart items={items} axis={axis} />
    </ChartBlock>
    </>}
  </Space>;
}

function SeriesList() {
  const [data, setData] = React.useState(undefined);
  const [error, setError] = React.useState('');
  const [attempt, setAttempt] = React.useState(0);
  React.useEffect(() => {
    setData(undefined);
    setError('');
    fetch(`${URL}/series`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error(`The series list could not be loaded (HTTP ${response.status}).`)))
      .then(body => setData(body.series || []))
      .catch(e => { setError(errorText(e)); setData([]); });
  }, [attempt]);
  return <>
    <div className="dara-page-head"><h1>Series</h1></div>
    {data?.length ? <Typography.Paragraph type="secondary">
      A series is a set of patterns analysed independently with identical settings. Drop several files on the Submit page (or switch on “Several files”) to submit one.
    </Typography.Paragraph> : null}
    {error ? <div className="dara-empty" role="alert"><h3>The series list could not be loaded</h3><p>{error}</p>
      <Button type="primary" icon={<ReloadOutlined />} onClick={() => setAttempt(value => value + 1)}>Retry</Button></div> : null}
    {error ? null : data !== undefined && !data.length ? <div className="dara-empty">
      <h3>No series yet</h3>
      <p>Submit several patterns of one sample (for example a temperature or time series) with identical settings, then follow their phases here.</p>
      <Button type="primary" href="/?series=1">Submit a series</Button>
    </div> : data === undefined ? <Spin /> : <Table size="small" rowKey="series_id" dataSource={data} pagination={{ pageSize: 20, hideOnSinglePage: true }}
      locale={{ emptyText: <>No series have been submitted yet. <a href="/?series=1">Submit a series</a>.</> }}
      columns={[
        { title: 'Name', dataIndex: 'name', render: (text, row) => <a href={`/series/?id=${encodeURIComponent(row.series_id)}`}>{text || row.series_id}</a> },
        { title: 'Submitted', dataIndex: 'created_on', render: formatDate },
        { title: 'Tasks', dataIndex: 'n_tasks', className: 'dara-num' },
        { title: 'Completed', dataIndex: 'n_completed', className: 'dara-num' },
        { title: 'Variable', dataIndex: 'unit', render: value => value || '—' },
      ]} />}
  </>;
}

export default function SeriesPage({ location }) {
  const id = new URLSearchParams(location.search).get('id');
  const [name, setName] = React.useState('');
  return <Layout hasSider={false} title={id && name ? `Series · ${name}` : 'Series'}>
    {id ? <SeriesDetail id={id} onName={setName} /> : <SeriesList />}
  </Layout>;
}
