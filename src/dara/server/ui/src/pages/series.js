import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Dropdown, Input, Popover, Progress, Select, Space, Spin, Switch, Table, Tag, Typography, message } from 'antd';
import { DownOutlined, DownloadOutlined, LineChartOutlined, ReloadOutlined } from '@ant-design/icons';
import Layout from '../components/Layout';
import { resultColumns } from '../components/ResultColumns';
import { downloadTable, errorText } from '../components/exportTable';
import { taskHref } from '../components/sampleNav.mjs';
import { formatDate } from '../components/resultsList.mjs';
import FigureExportDialog, { useFigureStyle } from '../components/FigureExportDialog';
import ChemicalFormula from '../components/ChemicalFormula';
import {
  LATTICE_PARAMETERS, fitTraces, hasActiveItems, latticeTraces, phaseLabels, phaseTraces, plotlyPhaseLabel, progress,
  seriesFigureHref, taskIds, xAxis,
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
      type: 'scatter', mode: 'lines+markers', name: plotlyPhaseLabel(trace.label), x: trace.x, y: trace.y,
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
        options={labels.map(label => ({ value: label, label: <ChemicalFormula formula={label} /> }))} /></label>
      <label>Parameter <Select aria-label="Lattice parameter" style={{ width: 120 }} value={parameter} onChange={setParameter}
        options={LATTICE_PARAMETERS.map(key => ({ value: key, label: PARAMETER_LABEL[key] }))} /></label>
    </Space>
    {trace.y.every(value => value === null)
      ? <Alert type="info" showIcon message={`${PARAMETER_LABEL[parameter]} is not reported for this phase.`} description="Parameters fixed by the crystal system are not listed." />
      : <Plotly
        data={[{ type: 'scatter', mode: 'lines+markers', name: `${plotlyPhaseLabel(selected)} — ${parameter}`, x: trace.x, y: trace.y, error_y: errorBars(trace.err), connectgaps: false }]}
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

/**
 * The series' measured patterns stacked (first task at the bottom) over one reference strip per
 * phase, as the journal figure itself: the preview is the SVG the Download menu saves.
 */
function StackedPatterns({ options, figure, onChange, src }) {
  const [shown, setShown] = React.useState({ src: null, failed: false });
  // Labels typed reach the figure after a 500 ms pause: each redraw takes seconds.
  const [draft, setDraft] = React.useState(null);
  const latest = React.useRef(figure);
  latest.current = figure;
  React.useEffect(() => {
    if (draft === null) return undefined;
    const timer = setTimeout(() => { onChange({ ...latest.current, labels: draft }); setDraft(null); }, 500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);
  const [labelsOpen, setLabelsOpen] = React.useState(false);
  const labels = draft ?? figure.labels ?? {};
  if (!options.tasks.length) return null;
  const loading = shown.src !== src;
  const weight = phase => (phase.max_weight_percent === null ? '' : ` (max ${phase.max_weight_percent.toFixed(1)} wt%)`);
  return <ChartBlock title="Stacked patterns">
    <div className="dara-toolbar">
      <div className="dara-toolbar-group">
        <label className="dara-toolbar-item"><span>Tasks</span>
          <Select mode="multiple" size="small" aria-label="Tasks in the figure" style={{ width: 150 }} maxTagCount={0}
            maxTagPlaceholder={() => `${figure.tasks.length} of ${options.tasks.length}`}
            value={figure.tasks} onChange={tasks => onChange({ ...figure, tasks })} dropdownMatchSelectWidth={false} placeholder="None"
            options={options.tasks.map(task => ({ value: task.task_id, label: `#${task.task_id} ${task.label}` }))} /></label>
        <label className="dara-toolbar-item"><span>Phases</span>
          <Select mode="multiple" size="small" aria-label="Phases in the figure" style={{ width: 150 }} maxTagCount={0}
            maxTagPlaceholder={() => `${figure.phases.length} of ${options.phases.length}`}
            value={figure.phases} onChange={phases => onChange({ ...figure, phases })} dropdownMatchSelectWidth={false} placeholder="None"
            options={options.phases.map(phase => ({ value: phase.key, label: `${phase.label}${weight(phase)}` }))} /></label>
      </div>
      <div className="dara-toolbar-group">
        <Popover trigger="click" placement="bottomLeft" title="Pattern labels" open={labelsOpen} onOpenChange={setLabelsOpen} content={
          <div className="dara-figure-labels" onKeyDown={event => { if (event.key === 'Escape') setLabelsOpen(false); }}>
            {options.tasks.filter(task => figure.tasks.includes(task.task_id)).map(task =>
              <label key={task.task_id}><span className="dara-num">#{task.task_id}</span>
                <Input size="small" value={labels[task.task_id] ?? ''} placeholder={task.label} maxLength={80}
                  aria-label={`Label of task ${task.task_id}`}
                  onChange={event => setDraft({ ...labels, [task.task_id]: event.target.value })} /></label>)}
          </div>}>
          <Button size="small">Labels</Button>
        </Popover>
        <label className="dara-toolbar-item"><span>Normalize each to its max</span>
          <Switch size="small" checked={figure.normalize} onChange={normalize => onChange({ ...figure, normalize })} aria-label="Normalize each pattern" /></label>
      </div>
    </div>
    {!figure.tasks.length ? <Alert type="info" showIcon message="Select at least one task." /> : <>
      {shown.src === src && shown.failed ? <Alert type="error" showIcon message="The figure could not be drawn." description="Check that DARA is running, then reload the page." /> : null}
      <div className="dara-figure-preview" aria-busy={loading}>
        <img key={src} src={src} alt="Stacked measured patterns with the reference patterns of the phases"
          onLoad={() => setShown({ src, failed: false })} onError={() => setShown({ src, failed: true })} />
        {loading ? <div className="dara-figure-preview-wait"><Spin /></div> : null}
      </div>
    </>}
  </ChartBlock>;
}

// "dara_series_ZnO anneal.csv": the series name, without characters a file name cannot hold.
const seriesCsvName = name => `dara_series_${String(name || 'series').replace(/[\\/:*?"<>|]+/g, '_').trim().slice(0, 80) || 'series'}.csv`;

// "300 °C" (value, a space, the unit); the token from the file name when it has no value.
const variableText = variable => (variable?.value !== null && variable?.value !== undefined
  ? `${variable.value}${variable.unit ? ` ${variable.unit}` : ''}` : variable?.raw ?? '—');

function SeriesDetail({ id, onName }) {
  const [data, setData] = React.useState(undefined);
  const [error, setError] = React.useState(null); // {text, notFound, retryIn}
  const [attempt, setAttempt] = React.useState(0);
  const [exporting, setExporting] = React.useState(false);
  const [figureOptions, setFigureOptions] = React.useState(null); // {tasks, phases} or {error}
  const [figure, setFigure] = React.useState({ tasks: [], phases: [], normalize: false });
  const [figureStyle, setFigureStyle] = useFigureStyle();
  const [figureOpen, setFigureOpen] = React.useState(false);
  const completedCount = (data?.items || []).filter(item => item.state === 'COMPLETED').length;

  // Tasks and phases of the stacked figure: reloaded when another task completes. The first load
  // selects all tasks and the default phases; later loads keep the user's choice and add only the
  // newly completed tasks and new default phases.
  const knownOptions = React.useRef(null);
  React.useEffect(() => {
    if (!completedCount) return undefined;
    let active = true;
    fetch(`${URL}/series/${encodeURIComponent(id)}/figure-options`)
      .then(response => (response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`))))
      .then(body => {
        if (!active) return;
        const known = knownOptions.current;
        knownOptions.current = body;
        setFigureOptions(body);
        const taskIdsNow = body.tasks.map(task => task.task_id);
        const defaults = body.phases.filter(phase => phase.default).map(phase => phase.key);
        setFigure(previous => (!known ? { ...previous, tasks: taskIdsNow, phases: defaults } : {
          ...previous,
          tasks: [...previous.tasks.filter(task => taskIdsNow.includes(task)),
            ...taskIdsNow.filter(task => !known.tasks.some(old => old.task_id === task))],
          phases: [...previous.phases.filter(key => body.phases.some(phase => phase.key === key)),
            ...defaults.filter(key => !known.phases.some(phase => phase.key === key))],
        }));
      })
      .catch(e => { if (active) setFigureOptions({ error: errorText(e) }); });
    return () => { active = false; };
  }, [id, completedCount]);

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
  const compareValues = axis.usesVariable ? `&values=${compareIds.map(id => valueOf.get(id)).join(',')}${axis.unit ? `&unit=${encodeURIComponent(axis.unit)}` : ''}` : '';
  // The table holds fits: identifications and unfinished tasks would be empty rows.
  const fitItems = items.filter(item => item.state === 'COMPLETED' && item.summary?.analysis_mode !== 'identification');
  const allIds = fitItems.map(item => item.task_id).slice(0, MAX_EXPORT);
  const seriesIds = taskIds(items);
  // A task opened here knows the series order (Previous / Next sample) and returns to this series.
  const back = `from=series&id=${encodeURIComponent(id)}`;
  const columns = resultColumns({ hrefOf: row => taskHref(row.fw_id, { ids: seriesIds, back }), sourceHref: task => taskHref(task, { back }) });
  const rows = items.map(item => ({ ...item, fw_id: item.task_id, adopted: item.adopted ?? item.summary?.adopted }));
  const figureReady = Boolean(figureOptions?.tasks?.length) && figure.tasks.length > 0;
  const figureHref = (style, download = false) => seriesFigureHref(URL, id, {
    tasks: figure.tasks, allTasks: figureOptions?.tasks || [], phases: figure.phases, normalize: figure.normalize,
    format: style.format, widthMm: style.widthMm, download, style, labels: figure.labels });
  const downloads = [
    { key: 'table', label: allIds.length ? 'Results table (CSV)' : 'Results table (CSV): no completed fit yet', disabled: !allIds.length },
    { key: 'figure', label: 'Stacked patterns figure…', disabled: !figureReady },
  ];
  const exportTable = async () => {
    setExporting(true);
    try {
      message.success(`Exported to ${await downloadTable(allIds, undefined, seriesCsvName(data.name))}.`);
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
        <Dropdown trigger={['click']} menu={{ items: downloads, onClick: ({ key }) => { if (key === 'table') exportTable(); if (key === 'figure') setFigureOpen(true); } }}>
          <Button icon={<DownloadOutlined />} loading={exporting}>{exporting ? 'Exporting…' : 'Download'} <DownOutlined /></Button>
        </Dropdown>
      </div>
    </div>
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Progress percent={total ? Math.round((done / total) * 100) : 0} showInfo={false} style={{ flex: '1 1 160px', minWidth: 0, margin: 0 }} />
        <Typography.Text style={{ whiteSpace: 'nowrap' }}>{`${done} / ${total} completed`}</Typography.Text>
      </div>
      {completedIds.length > MAX_COMPARE ? <div><Typography.Text type="secondary">{`Pattern comparison shows the first ${MAX_COMPARE} completed tasks.`}</Typography.Text></div> : null}
    </div>
    {fitItems.length > MAX_EXPORT ? <Typography.Text type="secondary">{`The table export takes the first ${MAX_EXPORT} fits; select the rest in Results to export them.`}</Typography.Text> : null}
    <Table size="small" className="dara-results-table" rowKey="fw_id" pagination={false} dataSource={rows} tableLayout="fixed" scroll={{ x: 1000 }}
      columns={[
        { title: 'Order', dataIndex: 'order', key: 'order', width: 64, className: 'dara-num', render: value => value + 1 },
        { title: 'Variable', key: 'variable', width: 100, className: 'dara-num', render: (_, row) => variableText(row.variable) },
        columns.name, columns.verdict, columns.rwp, columns.gof, columns.phases,
      ]} />
    {figureOptions?.error ? <Alert type="warning" showIcon message={`The stacked patterns could not be loaded: ${figureOptions.error}`} /> : null}
    {figureOptions?.tasks ? <StackedPatterns options={figureOptions} figure={figure} onChange={setFigure}
      src={figureHref({ ...figureStyle, format: 'svg', widthMm: 170 })} /> : null}
    {figureReady ? <FigureExportDialog title="Stacked patterns figure" open={figureOpen} onClose={() => setFigureOpen(false)}
      style={figureStyle} onStyle={setFigureStyle} hrefOf={figureHref} /> : null}
    {identifiedOnly ? <Alert type="info" showIcon message="Identifications: peak matching only, not verified by a fit"
      description="No refined wt%, lattice parameters or Rwp to plot. Refine with Rietveld on a task page verifies its phases." /> : <>
    {done < 2 ? <Alert type="info" showIcon message="Trend charts need at least two completed tasks."
      description={<><a href="/results/">Results</a> shows every task's progress; <a href="/?series=1">submit another series</a>.</>} /> : null}
    <ChartBlock title="Phase fractions" note="Relative to the modelled crystalline phases. Error bars are e.s.d.; a gap means the phase is not in that result.">
      <PhaseChart items={items} axis={axis} />
    </ChartBlock>
    <ChartBlock title="Lattice parameters">
      <LatticeChart items={items} axis={axis} />
    </ChartBlock>
    <ChartBlock title="Fit quality">
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
      Patterns analysed one by one with the same settings. Submit several files at once to start one.
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
