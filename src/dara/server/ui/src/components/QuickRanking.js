import React, { useEffect, useRef, useState } from 'react';
import Loadable from 'react-loadable';
import styled from 'styled-components';
import { Button, Progress, Spin, Switch, Table, Tag, Tooltip } from 'antd';
import { ArrowLeftOutlined, CloseOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons';
import ChemicalFormula from './ChemicalFormula';
import { progressPercent } from './structureLibrary.mjs';
import {
  MAX_REFINE, SORTERS, assignColors, defaultSelection, overlayTraces, percent, pollDelay, poolText, progressText,
  rankingRows, refineSelection, requestKey, rowKeyAction, sourceInfo, toggleKey,
} from './quickRanking.mjs';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <div className="dara-qr-plot-area" /> });
const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

// A cancelled job stops on the server; older servers have no DELETE, so its errors are ignored.
const cancelJob = id => { if (id) fetch(`${URL}/quick-ranking/${id}`, { method: 'DELETE' }).catch(() => {}); };

/**
 * State of the quick ranking (search-match) of the submission page; it lives with the page, so the
 * last ranking survives "Back to setup". `buildRequest()` returns the multipart body (pattern file,
 * elements, formulas, wavelength, range) or throws an Error with a user message.
 */
export function useQuickRanking(buildRequest) {
  const [shown, setShown] = useState(false);
  const [state, setState] = useState({ status: 'idle' }); // + key, data | error | progress
  const [selected, setSelectedKeys] = useState([]);
  const [colors, setColors] = useState({});
  const [focused, setFocused] = useState(null);
  const [theoretical, setTheoretical] = useState(false);
  const [refining, setRefining] = useState(false);
  const run = useRef(0);
  const job = useRef(null);
  const scroll = useRef(0);

  const setSelected = keys => {
    setSelectedKeys(keys);
    setColors(previous => assignColors(previous, keys));
  };
  const request = includeTheoretical => {
    const body = buildRequest();
    body.set('include_theoretical', String(includeTheoretical));
    return body;
  };
  let currentKey = null;
  try { currentKey = requestKey(request(theoretical).entries()); } catch (error) { /* no pattern yet */ }

  const start = async (includeTheoretical = theoretical) => {
    const sequence = ++run.current;
    cancelJob(job.current);
    job.current = null;
    setSelected([]); // a ticked row of an earlier ranking must not be refined with this one
    setFocused(null);
    let body;
    try {
      body = request(includeTheoretical);
    } catch (error) {
      setState({ status: 'error', error: String(error.message || error) });
      return;
    }
    const key = requestKey(body.entries());
    setState({ status: 'pending', key, progress: {} });
    try {
      let response = await fetch(`${URL}/quick-ranking`, { method: 'POST', body });
      let data = await response.json();
      for (let attempt = 0; response.ok && data.status === 'pending'; attempt += 1) {
        if (sequence !== run.current) { cancelJob(data.job_id); return; }
        job.current = data.job_id;
        setState({ status: 'pending', key, progress: data });
        await new Promise(resolve => setTimeout(resolve, pollDelay(attempt)));
        if (sequence !== run.current) return;
        response = await fetch(`${URL}/quick-ranking/${data.job_id}`);
        data = await response.json();
      }
      if (sequence !== run.current) return;
      job.current = null;
      if (!response.ok) throw new Error(data.detail || 'The search-match failed.');
      if (data.status === 'error') throw new Error(data.error || 'The search-match failed.');
      setState({ status: 'done', key, data });
      setSelected(defaultSelection(data));
    } catch (error) {
      if (sequence === run.current) setState({ status: 'error', key, error: String(error.message || error) });
    }
  };
  const cancel = () => {
    run.current += 1;
    cancelJob(job.current);
    job.current = null;
    setState({ status: 'idle' });
  };

  useEffect(() => () => { run.current += 1; cancelJob(job.current); }, []);

  const ranked = state.status === 'done' && state.key === currentKey ? state.data : null;
  return {
    shown, state, selected, setSelected, colors, focused, setFocused, theoretical, refining, ranked, start, cancel,
    /** Shows the workspace: the last ranking when its inputs are unchanged, else a new one. */
    open() {
      scroll.current = window.scrollY;
      setShown(true);
      window.scrollTo(0, 0);
      if (!ranked && state.status !== 'pending') start(theoretical);
    },
    /** Back to the setup; a pending ranking is cancelled. */
    close(restoreScroll = true) {
      if (state.status === 'pending') cancel();
      setShown(false);
      if (restoreScroll) requestAnimationFrame(() => window.scrollTo(0, scroll.current));
    },
    toggleTheoretical(checked) {
      setTheoretical(checked);
      start(checked);
    },
    /** `onRefine({referenceIds, formulas})` resolves to whether a refinement was submitted. */
    async refine(onRefine) {
      setRefining(true);
      let submitted = false;
      try {
        submitted = await onRefine(refineSelection(state.data, selected));
      } finally {
        setRefining(false);
      }
      if (submitted) setShown(false);
    },
  };
}

/** Steps of the greedy suggestion: "Fe2O3 34 % + ZnO 12 % (weak)" with formula subscripts. */
function Suggestion({ combination }) {
  return combination.map((step, index) => <React.Fragment key={step.source}>
    {index ? ' + ' : ''}<ChemicalFormula formula={step.formula} /> <span className="dara-num">{percent(step.gain)}</span>
    {step.weak ? ' (weak)' : ''}
  </React.Fragment>);
}

/** "Ia-3 (206)" with the symbol set as a space group; a bare number when there is no symbol. */
const SpaceGroup = ({ row }) => (row.spacegroup_symbol
  ? <><ChemicalFormula formula={row.spacegroup_symbol} />{` (${row.spacegroup})`}</> : String(row.spacegroup ?? '—'));

/** The last ranking on the Run card, when its inputs are still the form's. */
export function QuickRankingSummary({ quick }) {
  const data = quick.ranked;
  if (!data) return null;
  const count = (data.ranking || []).length;
  return <div className="dara-qr-summary">
    <div>
      <div><b>Search-match</b> <span className="dara-qr-summary-meta">
        {count ? `${data.pool?.candidates ?? count} candidates ranked · ${quick.selected.length} selected` : 'no candidate ranked'}</span></div>
      {data.combination?.length ? <div>Suggested: <Suggestion combination={data.combination} /></div> : null}
    </div>
    <Button size="small" onClick={quick.open}>Open</Button>
  </div>;
}

const Workspace = styled.section`
  font-size: var(--dara-font-14);
  .dara-qr-head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px;
    padding-bottom: 12px; border-bottom: 1px solid var(--dara-border);
  }
  .dara-qr-title { flex: 1 1 320px; min-width: 0; }
  .dara-qr-title h1 { margin: 0; font-size: var(--dara-font-20); font-weight: 600; line-height: 28px; }
  .dara-qr-title h1:focus { outline: none; }
  .dara-qr-context { margin: 0; color: var(--dara-text-secondary); font-size: var(--dara-font-12); overflow-wrap: anywhere; }
  .dara-qr-context > span + span::before { content: ' · '; }
  .dara-qr-note { margin: 8px 0 12px; color: var(--dara-text-hint); font-size: var(--dara-font-12); }
  > .ant-alert { margin-bottom: 12px; }
  .dara-qr-panel {
    background: var(--dara-surface); border: 1px solid var(--dara-border); border-radius: var(--dara-radius);
  }
  .dara-qr-plot { padding: 8px 12px 4px; margin-bottom: 16px; }
  .dara-qr-plot-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 16px; min-height: 22px; font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-plot-head b { color: var(--dara-text); font-weight: 600; }
  .dara-qr-plot-area { height: max(280px, 40vh); }
  .dara-qr-preview-mark { display: inline-block; width: 0; height: 12px; margin: 0 6px -1px 0; border-left: 2px dotted var(--dara-text); }
  .dara-qr-wait { display: flex; flex-direction: column; justify-content: center; align-items: flex-start; gap: 12px; padding: 0 24px; }
  .dara-qr-wait .ant-progress { width: min(480px, 100%); }
  .dara-qr-body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(260px, 320px); gap: 16px; align-items: start; }
  .dara-qr-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin-bottom: 8px; }
  .dara-qr-toolbar label { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
  .dara-qr-pool { margin-left: auto; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-caution-list { margin-bottom: 8px; font-size: var(--dara-font-13); }
  .dara-caution-list .anticon { color: var(--dara-review); }
  /* The list scrolls under the plot so the preview stays in view while browsing candidates. */
  .dara-qr-table { max-height: max(320px, calc(58vh - 250px)); overflow: auto; }
  .dara-qr-table .ant-table-thead > tr > th { position: sticky; top: 0; z-index: 2; }
  .dara-qr-table .ant-table-tbody > tr.ant-table-row { cursor: pointer; }
  /* The checkbox marks a selected row; the tint is kept for the previewed (focused) row. */
  .dara-qr-table .ant-table-tbody > tr.ant-table-row-selected:not(.dara-row-shown) > td { background: var(--dara-surface); }
  .dara-qr-table .ant-table-tbody > tr.ant-table-row-selected:not(.dara-row-shown):hover > td { background: var(--dara-surface-sunken); }
  .dara-qr-table .ant-table-tbody > tr.ant-table-row:focus-visible { outline-offset: -2px; }
  .dara-qr-dup { margin-left: 4px; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-qr-selected { padding: 12px; }
  .dara-qr-selected h2 { display: flex; justify-content: space-between; margin: 0 0 8px; font-size: var(--dara-font-14); font-weight: 600; }
  .dara-qr-selected h2 span { font-weight: 400; color: var(--dara-text-secondary); font-size: var(--dara-font-12); }
  .dara-qr-selected ul { margin: 0; padding: 0; list-style: none; }
  .dara-qr-selected li {
    display: grid; grid-template-columns: 12px minmax(0, 1fr) auto auto; align-items: center; gap: 0 8px;
    padding: 4px 0; border-top: 1px solid var(--dara-border);
  }
  .dara-qr-selected li:first-child { border-top: 0; }
  .dara-qr-swatch { width: 12px; height: 12px; border-radius: var(--dara-radius); }
  .dara-qr-phase { min-width: 0; }
  .dara-qr-phase > div { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dara-qr-phase .meta { font-size: var(--dara-font-12); color: var(--dara-text-secondary); }
  .dara-qr-empty { margin: 0; color: var(--dara-text-secondary); }
  .dara-qr-suggest { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--dara-border-strong); }
  .dara-qr-suggest p { margin: 0 0 8px; }
  .dara-qr-limit { margin: 8px 0 0; color: var(--dara-review); }
  @media (max-width: 1079px) {
    .dara-qr-body { grid-template-columns: minmax(0, 1fr); }
  }
`;

/**
 * The quick ranking workspace that replaces the submission form while it is open: the measured
 * pattern with reference sticks of the selected and the focused candidate, the candidate table and
 * the selected phases. `onRefine` submits a Known-phase refinement of the selected structures.
 */
export default function QuickRankingWorkspace({ quick, context, onRefine, alert }) {
  const { state, selected, setSelected, focused, setFocused, colors } = quick;
  const heading = useRef(null);
  useEffect(() => { heading.current?.focus(); }, []);

  const data = state.status === 'done' ? state.data : null;
  const rows = rankingRows(data);
  const rowByKey = Object.fromEntries(rows.map(row => [row.key, row]));
  const chosen = selected.map(key => rowByKey[key]).filter(Boolean);
  const focusedRow = rowByKey[focused];
  const over = selected.length > MAX_REFINE;
  const suggestion = data ? defaultSelection(data) : [];
  const usingSuggestion = suggestion.length === selected.length && suggestion.every(key => selected.includes(key));

  const onRowKey = (event, row) => {
    const action = rowKeyAction(event.key);
    if (!action || (action === 'toggle' && event.target !== event.currentTarget)) return; // Space on the checkbox ticks it
    event.preventDefault();
    if (action === 'toggle') { setSelected(toggleKey(selected, row.key)); return; }
    const next = action === 'next' ? event.currentTarget.nextElementSibling : event.currentTarget.previousElementSibling;
    if (next?.classList.contains('ant-table-row')) next.focus();
  };

  const columns = [
    { title: 'Rank', dataIndex: 'rank', width: 64, className: 'dara-num', sorter: SORTERS.rank, defaultSortOrder: 'ascend' },
    { title: 'Formula', dataIndex: 'formula', render: formula => <ChemicalFormula formula={formula} /> },
    { title: 'Space group', dataIndex: 'spacegroupText', render: (_, row) => <span style={{ whiteSpace: 'nowrap' }}><SpaceGroup row={row} /></span> },
    {
      title: 'Source', dataIndex: 'source',
      render: (source, row) => {
        const { label, url } = sourceInfo(source);
        return <span style={{ whiteSpace: 'nowrap' }}>{url ? <a href={url} target="_blank" rel="noreferrer">{label}</a> : label}
          {row.duplicates ? <Tooltip title={`${row.duplicates} more record${row.duplicates === 1 ? '' : 's'} with the same structure`}>
            <span className="dara-qr-dup">+{row.duplicates}</span></Tooltip> : null}</span>;
      },
    },
    {
      title: <Tooltip title="Share of the measured net intensity the structure can explain (lattice-penalised)">Score</Tooltip>,
      dataIndex: 'scoreText', width: 84, className: 'dara-num', sorter: SORTERS.score, sortDirections: ['descend', 'ascend'],
    },
    {
      title: <Tooltip title="Strong calculated peaks found in the pattern / strong peaks in range">Peaks</Tooltip>,
      dataIndex: 'peaksText', width: 84, className: 'dara-num', sorter: SORTERS.peaks, sortDirections: ['descend', 'ascend'],
    },
    {
      title: <Tooltip title="Lattice change that fits best (d scale − 1)">Lattice</Tooltip>,
      dataIndex: 'latticeText', width: 92, className: 'dara-num', sorter: SORTERS.lattice,
    },
    {
      title: 'Flags', dataIndex: 'tags',
      render: tags => tags.map(tag => <Tag key={tag.label} color={tag.color} style={{ marginBottom: 2 }}>{tag.label}</Tag>),
    },
  ];

  const canRefine = selected.length > 0 && !over && !!data;
  let body;
  if (state.status === 'pending') {
    const percentDone = progressPercent(state.progress);
    body = <div className="dara-qr-panel dara-qr-plot-area dara-qr-wait" role="status">
      <span>{percentDone === null ? <Spin size="small" style={{ marginRight: 8 }} /> : null}{progressText(state.progress)}</span>
      {percentDone !== null ? <Progress percent={percentDone} size="small" /> : null}
      <Button onClick={quick.cancel}>Cancel</Button>
    </div>;
  } else if (state.status === 'error') {
    body = <div className="dara-failure" role="alert">
      <h3>The search-match failed</h3>
      <p>{state.error}</p>
      <Button onClick={() => quick.start()}>Retry</Button>
    </div>;
  } else if (state.status === 'idle') {
    body = <div className="dara-empty">
      <h3>Search-match cancelled</h3>
      <p>Run it again to rank candidate structures for this pattern.</p>
      <Button type="primary" onClick={() => quick.start()}>Run search-match</Button>
    </div>;
  } else {
    const caveats = data.caveats || [];
    body = <>
      <section className="dara-qr-panel dara-qr-plot" aria-label="Measured pattern and reference sticks">
        <div className="dara-qr-plot-head">
          <span>{focusedRow
            ? <><span className="dara-qr-preview-mark" />Preview <b><ChemicalFormula formula={focusedRow.formula} /></b>
              {` · ${focusedRow.sourceLabel}`}{selected.includes(focusedRow.key) ? ' (selected)' : ''}</>
            : 'Click a candidate to preview its reflections; tick it to keep it.'}</span>
          <span>Measured pattern, background removed</span>
        </div>
        {data.profile ? <Plot data={overlayTraces(data, selected, colors, focused)} useResizeHandler
          xBounds={[data.profile.two_theta?.[0], data.profile.two_theta?.slice(-1)[0]]}
          className="dara-qr-plot-area" style={{ width: '100%' }}
          layout={{
            autosize: true, uirevision: state.key, showlegend: false, hovermode: 'closest',
            margin: { l: 56, r: 12, t: 8, b: 40 }, font: { size: 12 },
            xaxis: { title: { text: '2θ (°)' } }, yaxis: { title: { text: 'Net intensity' }, rangemode: 'tozero' },
          }}
          config={{ displaylogo: false, responsive: true }} /> : null}
      </section>
      <div className="dara-qr-body">
        <div>
          <div className="dara-qr-toolbar">
            <label><Switch size="small" checked={quick.theoretical} onChange={quick.toggleTheoretical} />
              Include theoretical (computed) structures</label>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => quick.start()}>Run again</Button>
            <span className="dara-qr-pool">{poolText(data)}</span>
          </div>
          {caveats.length ? <div className="dara-caution-list" role="note"><ul>
            {caveats.map(text => <li key={text}><WarningOutlined />{text}</li>)}</ul></div> : null}
          {rows.length ? <div className="dara-qr-table">
            <Table size="small" columns={columns} dataSource={rows} pagination={false} tableLayout="auto"
              rowSelection={{ selectedRowKeys: selected, onChange: setSelected, columnWidth: 36 }}
              rowClassName={row => (row.key === focused ? 'dara-row-shown' : '')}
              onRow={row => ({
                tabIndex: 0, title: 'Preview in the plot; Space selects', 'aria-current': row.key === focused || undefined,
                onFocus: () => setFocused(row.key), onKeyDown: event => onRowKey(event, row),
              })} />
          </div> : <div className="dara-empty">
            <h3>No candidate could be ranked</h3>
            <p>{caveats.length ? 'See the notes above.' : 'No reference structure matched the elements in this 2θ range.'}
              {' '}Check the elements and databases in the setup{quick.theoretical ? '.' : ', or include theoretical structures.'}</p>
          </div>}
        </div>
        {rows.length ? <aside className="dara-qr-panel dara-qr-selected" aria-label="Selected phases">
          <h2>Selected phases <span>{`${selected.length} of at most ${MAX_REFINE}`}</span></h2>
          {chosen.length ? <ul>{chosen.map(row => <li key={row.key}>
            <i className="dara-qr-swatch" style={{ background: colors[row.key] }} aria-hidden="true" />
            <div className="dara-qr-phase">
              <div><ChemicalFormula formula={row.formula} /> <span className="meta"><SpaceGroup row={row} /></span></div>
              <div className="meta">{row.sourceLabel}</div>
            </div>
            <span className="dara-num">{row.scoreText}</span>
            <Button type="text" size="small" icon={<CloseOutlined />} aria-label={`Remove ${row.formula}`}
              onClick={() => setSelected(selected.filter(key => key !== row.key))} />
          </li>)}</ul>
            : <p className="dara-qr-empty">No phase selected. Tick candidates in the table to draw their sticks on the pattern, then refine them together.</p>}
          {over ? <p className="dara-qr-limit">{`A refinement takes at most ${MAX_REFINE} phases: remove ${selected.length - MAX_REFINE}.`}</p> : null}
          {data.combination?.length ? <div className="dara-qr-suggest">
            <p>Suggested: <Suggestion combination={data.combination} /></p>
            <Button size="small" onClick={() => setSelected(suggestion)} disabled={usingSuggestion}>Use suggestion</Button>
          </div> : null}
        </aside> : null}
      </div>
    </>;
  }

  return <Workspace aria-labelledby="dara-qr-title">
    <header className="dara-qr-head">
      <Button icon={<ArrowLeftOutlined />} onClick={() => quick.close()}>Back to setup</Button>
      <div className="dara-qr-title">
        <h1 id="dara-qr-title" ref={heading} tabIndex={-1}>Search-match</h1>
        <p className="dara-qr-context">{context.filter(Boolean).map((item, index) => <span key={index}>{item}</span>)}</p>
      </div>
      <Tooltip title={over ? `At most ${MAX_REFINE} phases per refinement` : 'Known-phase Rietveld refinement of the selected structures'}>
        <Button type="primary" onClick={() => quick.refine(onRefine)} loading={quick.refining} disabled={!canRefine}>
          {`Refine selected (${selected.length})`}
        </Button>
      </Tooltip>
    </header>
    <p className="dara-qr-note">A ranked list of candidates, not an identification: each score is the share of the net intensity a structure explains on its own, with no amounts fitted. Refine the selected structures to test them.</p>
    {alert}
    {body}
  </Workspace>;
}
