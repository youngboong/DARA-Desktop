import React, { useEffect, useRef, useState } from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Checkbox, Drawer, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import ChemicalFormula from './ChemicalFormula';
import {
  MAX_REFINE, combinationText, defaultSelection, overlayTraces, pollDelay, poolText, rankingRows, refineSelection, sourceInfo,
} from './quickRanking.mjs';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <Spin /> });
const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const TITLE = 'Search-match ranking - candidates to refine, not a result';

const COLUMNS = [
  { title: 'Rank', dataIndex: 'rank', width: 60 },
  { title: 'Formula', dataIndex: 'formula', render: formula => <ChemicalFormula formula={formula} /> },
  { title: 'Space group', dataIndex: 'spacegroupText' },
  {
    title: 'Source', dataIndex: 'source',
    render: (source, row) => {
      const { label, url } = sourceInfo(source);
      const more = row.duplicates ? <Typography.Text type="secondary"> +{row.duplicates} duplicates</Typography.Text> : null;
      return <span>{url ? <a href={url} target="_blank" rel="noreferrer">{label}</a> : label}{more}</span>;
    },
  },
  {
    title: <Tooltip title="Share of the measured net intensity the structure can explain (lattice-penalised)">Score</Tooltip>,
    dataIndex: 'scoreText', width: 80,
  },
  {
    title: <Tooltip title="Strong calculated peaks found in the pattern / strong peaks in range">Peaks</Tooltip>,
    dataIndex: 'peaksText', width: 70,
  },
  { title: <Tooltip title="Lattice change that fits best (d scale − 1)">Lattice</Tooltip>, dataIndex: 'latticeText', width: 80 },
  {
    title: 'Flags', dataIndex: 'tags',
    render: tags => <Space size={[0, 4]} wrap>{tags.map(tag => <Tag key={tag.label} color={tag.color}>{tag.label}</Tag>)}</Space>,
  },
];

/**
 * "Quick ranking" button and drawer of the submission page.
 *
 * `buildRequest()` returns the multipart body (pattern file, elements, formulas, wavelength,
 * range) or throws an Error with a user message; `onRefine({referenceIds, formulas})` submits a
 * Known-phase refinement of the chosen structures and resolves to whether it was submitted.
 */
export default function QuickRanking({ buildRequest, onRefine, disabled, disabledReason }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ status: 'idle' });
  const [selected, setSelected] = useState([]);
  const [theoretical, setTheoretical] = useState(false);
  const [refining, setRefining] = useState(false);
  const run = useRef(0);

  const start = async includeTheoretical => {
    const sequence = ++run.current;
    setSelected([]); // a ticked row of an earlier ranking must not be refined with this one
    let body;
    try {
      body = buildRequest();
    } catch (error) {
      setState({ status: 'error', error: String(error.message || error) });
      return;
    }
    body.set('include_theoretical', String(includeTheoretical));
    setState({ status: 'pending' });
    try {
      let response = await fetch(`${URL}/quick-ranking`, { method: 'POST', body });
      let data = await response.json();
      for (let attempt = 0; response.ok && data.status === 'pending'; attempt += 1) {
        if (sequence !== run.current) return;
        setState({ status: 'pending', elapsed: data.elapsed_s });
        await new Promise(resolve => setTimeout(resolve, pollDelay(attempt)));
        response = await fetch(`${URL}/quick-ranking/${data.job_id}`);
        data = await response.json();
      }
      if (sequence !== run.current) return;
      if (!response.ok) throw new Error(data.detail || 'The quick ranking failed.');
      if (data.status === 'error') throw new Error(data.error || 'The quick ranking failed.');
      setState({ status: 'done', data });
      setSelected(defaultSelection(data));
    } catch (error) {
      if (sequence === run.current) setState({ status: 'error', error: String(error.message || error) });
    }
  };

  useEffect(() => () => { run.current += 1; }, []);

  const openDrawer = () => {
    setOpen(true);
    start(theoretical);
  };
  const toggleTheoretical = event => {
    setTheoretical(event.target.checked);
    start(event.target.checked);
  };
  const refine = async () => {
    setRefining(true);
    try {
      // stays open when nothing was submitted (invalid form, cancelled, server error)
      if (await onRefine(refineSelection(state.data, selected))) setOpen(false);
    } finally {
      setRefining(false);
    }
  };

  const data = state.status === 'done' ? state.data : null;
  const rows = rankingRows(data);
  const traces = overlayTraces(data, selected);
  return (
    <>
      <Tooltip title={disabled ? disabledReason : 'Rank candidate structures in seconds, without refinement'}>
        <Button block onClick={openDrawer} disabled={disabled} style={{ marginBottom: 12 }}>Quick ranking</Button>
      </Tooltip>
      <Drawer title={TITLE} open={open} onClose={() => { run.current += 1; setOpen(false); }}
        width="min(980px, 96vw)" destroyOnClose
        footer={(
          <Space style={{ justifyContent: 'space-between', width: '100%' }}>
            <Typography.Text type="secondary">
              {selected.length ? `${selected.length} selected` : 'Tick the structures to refine'}
              {selected.length > MAX_REFINE ? ` (at most ${MAX_REFINE})` : ''}
            </Typography.Text>
            <Button type="primary" onClick={refine} loading={refining}
              disabled={!selected.length || selected.length > MAX_REFINE}>
              Refine selected
            </Button>
          </Space>
        )}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Alert type="info" showIcon
            message="A ranked list of candidates, not an identification"
            description="Scores are the share of net intensity a structure could explain on its own; no amounts are fitted. Refine the selected structures to test them." />
          <Checkbox checked={theoretical} onChange={toggleTheoretical} disabled={state.status === 'pending'}>
            Include theoretical (computed) structures
          </Checkbox>
          {state.status === 'pending' ? (
            <Alert type="info" showIcon message={<Space><Spin size="small" />
              {`Ranking candidates${state.elapsed ? ` (${Math.round(state.elapsed)} s; structures not seen before are simulated once)` : '...'}`}</Space>} />
          ) : null}
          {state.status === 'error' ? <Alert type="error" showIcon message={state.error} /> : null}
          {(data?.caveats || []).map(text => <Alert key={text} type="warning" showIcon message={text} />)}
          {data ? <Typography.Text type="secondary">{poolText(data)}</Typography.Text> : null}
          {data?.combination?.length ? (
            <Typography.Text>Greedy combination: {combinationText(data.combination)}</Typography.Text>
          ) : null}
          {data?.profile ? (
            <Plot data={traces} useResizeHandler style={{ width: '100%', height: 300 }}
              layout={{
                margin: { l: 56, r: 16, t: 8, b: 40 }, showlegend: true, legend: { orientation: 'h', y: -0.25 },
                xaxis: { title: '2θ (°)' }, yaxis: { title: 'Net intensity', rangemode: 'tozero' },
              }}
              config={{ displaylogo: false, responsive: true }} />
          ) : null}
          {data ? (
            <Table size="small" columns={COLUMNS} dataSource={rows} pagination={{ pageSize: 20, hideOnSinglePage: true }}
              rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
              locale={{ emptyText: 'No candidate could be ranked' }} />
          ) : null}
        </Space>
      </Drawer>
    </>
  );
}
