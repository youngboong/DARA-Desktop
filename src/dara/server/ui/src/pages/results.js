import React from 'react';
import Layout from '../components/Layout';
import { AutoComplete, Button, Dropdown, Input, Modal, Radio, Select, Table, Tooltip, message } from 'antd';
import {
  DeleteOutlined, DownloadOutlined, DownOutlined, EllipsisOutlined, LineChartOutlined, ReloadOutlined, StopOutlined, TableOutlined,
} from '@ant-design/icons';
import { retryTask } from '../components/ResultWorkflows';
import { API as URL, postJson } from '../components/QuickRefineProgress';
import { readRemembered } from '../components/submitHelpers.mjs';
import { taskHref } from '../components/sampleNav.mjs';
import { resultColumns } from '../components/ResultColumns';
import { downloadResults, downloadTable, errorText, responseError } from '../components/exportTable';
import {
  ACTIVE_STATES, MAX_LIMIT, PAGE_SIZES, STATES, apiQuery, backQuery, deletable, filtersActive,
  hasActiveRows, isIdentificationEntry, mergeUsers, parseResultsView, partitionSelected, resultsSearch, retryable, stateLabel,
} from '../components/resultsList.mjs';

const POLL_MS = 5000;
const MAX_POLL_ERRORS = 3;
const MAX_COMPARE = 20;
const MAX_ZIP = 50; // the server's id limit, as for the table export
const VERDICT_OPTIONS = [
  { value: 'high', label: 'High' }, { value: 'check', label: 'Check' },
  { value: 'expert', label: 'Expert review' }, { value: 'no-fit', label: 'No fit' },
];
const STATE_OPTIONS = STATES.map(value => ({ value, label: stateLabel(value) }));
const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

async function fetchList(query, signal) {
  const response = await fetch(`${URL}/tasks?${query}`, { signal });
  if (!response.ok) throw await responseError(response, 'The task list could not be loaded');
  return response.json();
}

function ResultsPage({ location }) {
  const [view, setView] = React.useState(null);
  const [remembered, setRemembered] = React.useState('');
  const [data, setData] = React.useState(null); // {tasks, total_tasks} of the current query
  const [loadError, setLoadError] = React.useState(null);
  const [pollErrors, setPollErrors] = React.useState(0);
  const [reloadToken, setReloadToken] = React.useState(0);
  const [exporting, setExporting] = React.useState(null); // {done, total} while an export runs
  const [busy, setBusy] = React.useState(false);
  const [users, setUsers] = React.useState([]);
  const [userText, setUserText] = React.useState('');
  const [userCounts, setUserCounts] = React.useState({}); // lower-case user → task count
  const [zipping, setZipping] = React.useState(false);
  const rowsById = React.useRef(new Map()); // every row seen, for bulk actions on selections across pages

  // The view lives in the address: filters, sort, page, size and selection (Back from a task restores it).
  React.useEffect(() => {
    const params = new URLSearchParams(location.search);
    // A task opened from a series returns there (its `back` is "from=series&id=…").
    if (params.get('from') === 'series' && params.get('id')) {
      window.location.replace(`/series/?id=${encodeURIComponent(params.get('id'))}`);
      return;
    }
    const user = readRemembered(typeof window === 'undefined' ? null : window.localStorage)?.fields?.user || '';
    setRemembered(String(user).trim());
    const initial = parseResultsView(location.search, user);
    setView(initial);
    setUserText(initial.user);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    if (view) window.history.replaceState(window.history.state, '', `${window.location.pathname}?${resultsSearch(view)}`);
  }, [view]);

  const query = view ? apiQuery(view) : null;
  const update = patch => setView(previous => ({ ...previous, page: 1, ...patch }));
  const setSelected = selected => setView(previous => ({ ...previous, selected }));

  const remember = tasks => {
    (tasks || []).forEach(entry => rowsById.current.set(entry.fw_id, entry));
    setUsers(previous => mergeUsers(previous, (tasks || []).map(entry => entry.user)));
  };
  // Every user with stored tasks (and their counts), not only those of the loaded rows.
  React.useEffect(() => {
    fetch(`${URL}/tasks/users`).then(response => (response.ok ? response.json() : null)).then(body => {
      if (!body?.users) return;
      setUserCounts(Object.fromEntries(body.users.map(item => [item.user.toLowerCase(), item.count])));
      setUsers(previous => mergeUsers(previous, body.users.map(item => item.user)));
    }).catch(() => {}); // an older server: suggestions from the loaded rows only
  }, []);

  // First load of a query: errors replace the table (with Retry).
  React.useEffect(() => {
    if (!query) return undefined;
    const controller = new AbortController();
    setLoadError(null);
    setPollErrors(0);
    fetchList(query, controller.signal)
      .then(body => { setData({ ...body, query }); remember(body.tasks); })
      .catch(error => { if (!controller.signal.aborted) setLoadError(errorText(error)); });
    return () => controller.abort();
  }, [query, reloadToken]);

  // Live list: while a shown row is queued or running, refresh every 5 s (not in a hidden tab);
  // rows are replaced in place, so selection, scroll and open menus stay. Three failures in a row pause it.
  const live = Boolean(data && data.query === query && hasActiveRows(data.tasks));
  const paused = pollErrors >= MAX_POLL_ERRORS;
  React.useEffect(() => {
    if (!live || paused) return undefined;
    let stopped = false;
    const refresh = () => {
      if (document.hidden) return;
      fetchList(query)
        .then(body => { if (!stopped) { setData({ ...body, query }); remember(body.tasks); setPollErrors(0); } })
        .catch(() => { if (!stopped) setPollErrors(count => count + 1); });
    };
    const timer = setInterval(refresh, POLL_MS);
    const onVisible = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); };
  }, [live, paused, query]);

  const reload = () => { setPollErrors(0); setReloadToken(token => token + 1); };

  if (!view) return <Layout hasSider={false} title="Results"><div className="dara-results" /></Layout>;

  const tasks = data?.query === query ? data.tasks : null;
  const total = data?.query === query ? data.total_tasks : null;
  const pageIds = (tasks || []).map(entry => entry.fw_id);
  const back = backQuery(view);
  const hrefOf = entry => taskHref(entry.fw_id, { ids: pageIds, back });
  const sourceHref = id => taskHref(id, { back });
  const sortOrder = key => (view.sort === key ? (view.order === 'asc' ? 'ascend' : view.order === 'desc' ? 'descend'
    : key === 'submitted' ? 'descend' : 'ascend') : !view.sort && key === 'submitted' ? 'descend' : null);
  const sorter = key => ({
    sorter: true, sortOrder: sortOrder(key), showSorterTooltip: false,
    sortDirections: key === 'submitted' ? ['descend', 'ascend', 'descend'] : ['ascend', 'descend', 'ascend'],
  });

  const deleteTask = async taskId => {
    try {
      const response = await fetch(`${URL}/task/${taskId}`, { method: 'DELETE' });
      if (!response.ok) throw await responseError(response, 'The task could not be deleted');
      message.success(`Task ${taskId} deleted.`);
      setSelected(view.selected.filter(id => id !== taskId));
      reload();
    } catch (error) {
      message.error(errorText(error)); // the confirm closes; the row stays
    }
  };
  const stopTask = async entry => {
    try {
      await postJson(`/task/${entry.fw_id}/cancel`, {});
      message.success(entry.state === 'READY' ? `Task ${entry.fw_id} removed from the queue.` : `Task ${entry.fw_id} is stopping.`);
      reload();
    } catch (error) {
      message.error(errorText(error));
    }
  };

  const rowMenu = entry => {
    const active = ACTIVE_STATES.includes(entry.state);
    const remedy = entry.state === 'FIZZLED' ? entry.error_summary?.remedy?.label : null;
    return {
      items: [
        ...(active ? [] : [{ key: 'rerun', label: <a href={`/?clone_task=${entry.fw_id}`}>Rerun with these settings…</a> }]),
        ...(retryable(entry) ? [{ key: 'retry', icon: <ReloadOutlined />, label: 'Retry with the same settings' }] : []),
        ...(remedy ? [{ key: 'retry-fix', label: `Retry with fix (${remedy})` }] : []),
        ...(entry.state === 'READY' ? [{ key: 'stop', icon: <StopOutlined />, label: 'Remove from queue' }] : []),
        ...(entry.state === 'RUNNING' ? [{ key: 'stop', icon: <StopOutlined />, label: 'Stop' }] : []),
        { type: 'divider' },
        { key: 'delete', danger: true, icon: <DeleteOutlined />, label: 'Delete…', disabled: active },
      ],
      onClick: ({ key }) => {
        if (key === 'retry') retryTask(entry.fw_id, false);
        if (key === 'retry-fix') Modal.confirm({
          title: `Retry task ${entry.fw_id} with the suggested fix (${remedy})?`,
          content: 'A new task is queued; the failed task is kept.', okText: 'Retry with fix', cancelText: 'Cancel',
          onOk: () => retryTask(entry.fw_id, true),
        });
        if (key === 'stop') Modal.confirm({
          title: entry.state === 'READY' ? `Remove task ${entry.fw_id} from the queue?` : `Stop task ${entry.fw_id}?`,
          content: entry.state === 'READY' ? 'The task is cancelled before it starts.' : 'The analysis stops at its next check; candidates finished so far can still be downloaded.',
          okText: entry.state === 'READY' ? 'Remove from queue' : 'Stop', okButtonProps: { danger: true }, cancelText: 'Keep running',
          onOk: () => stopTask(entry),
        });
        if (key === 'delete') Modal.confirm({
          title: `Delete task ${entry.fw_id}?`, content: 'This permanently removes the stored result.',
          okText: 'Delete', okButtonProps: { danger: true }, cancelText: 'Cancel',
          onOk: () => deleteTask(entry.fw_id),
        });
      },
    };
  };

  const columns = resultColumns({ hrefOf, sourceHref, sorter });
  const tableColumns = [
    columns.id, columns.name, columns.verdict, columns.rwp, columns.gof, columns.phases, columns.submitted, columns.user,
    {
      title: <span className="dara-sr-only">Actions</span>, key: 'actions', width: 72, fixed: 'right',
      render: (_, entry) => (
        <span className="dara-row-actions">
          <Tooltip title={isIdentificationEntry(entry) ? 'An identification has no fit files: open the task for its downloads'
            : entry.state === 'COMPLETED' ? 'Download results (ZIP)' : 'No results to download yet'}>
            <Button type="text" size="small" icon={<DownloadOutlined />} aria-label={`Download results of task ${entry.fw_id} (ZIP)`}
              href={entry.state === 'COMPLETED' && !isIdentificationEntry(entry) ? `${URL}/task/${entry.fw_id}/download` : undefined}
              disabled={entry.state !== 'COMPLETED' || isIdentificationEntry(entry)} />
          </Tooltip>
          <Dropdown trigger={['click']} menu={rowMenu(entry)}>
            <Button type="text" size="small" icon={<EllipsisOutlined />} aria-label={`More actions for task ${entry.fw_id}`} />
          </Dropdown>
        </span>
      ),
    },
  ];

  // --- selection and bulk actions ---------------------------------------------------------
  const selected = view.selected;
  const compare = partitionSelected(selected, rowsById.current, entry => entry.state === 'COMPLETED');
  const fit = partitionSelected(selected, rowsById.current, entry => !isIdentificationEntry(entry));
  const removable = partitionSelected(selected, rowsById.current, deletable);
  const retryIds = selected.filter(id => retryable(rowsById.current.get(id)));
  const compareTip = compare.ids.length < 2 ? 'Tick 2–20 completed tasks to compare their patterns'
    : compare.ids.length > MAX_COMPARE ? `Compare takes at most ${MAX_COMPARE} patterns: ${compare.ids.length} completed tasks are selected`
      : compare.skipped.length ? `${plural(compare.skipped.length, 'unfinished task')} left out` : null;

  const exportIds = async (ids, skipped) => {
    if (!ids.length) { message.info('No task with a fit to export.'); return; }
    setExporting({ done: 0, total: ids.length });
    try {
      const filename = await downloadTable(ids, done => setExporting(previous => ({ ...previous, done })));
      message.success(`${plural(ids.length, 'task')} exported to ${filename}${skipped ? ` (${plural(skipped, 'identification task')} without a fit left out)` : ''}.`);
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setExporting(null);
    }
  };
  // Every task matching the filter, in the list's order: the ids are gathered page by page.
  const exportAll = async () => {
    setExporting({ done: 0, total: total || 0 });
    let ids = [];
    let skipped = 0;
    try {
      for (let page = 1; page <= Math.ceil((total || 0) / MAX_LIMIT); page += 1) {
        const body = await fetchList(apiQuery(view, { page, limit: MAX_LIMIT }));
        remember(body.tasks);
        body.tasks.forEach(entry => { if (isIdentificationEntry(entry)) skipped += 1; else ids.push(entry.fw_id); });
      }
    } catch (error) {
      setExporting(null);
      message.error(errorText(error));
      return;
    }
    ids = [...new Set(ids)];
    await exportIds(ids, skipped);
  };
  const confirmExportAll = () => {
    if ((total || 0) <= 200) { exportAll(); return; }
    Modal.confirm({
      title: `Export all ${total} matching tasks?`,
      content: `The table is built in parts of 50 tasks; a first export takes about 10–20 s per part (about ${Math.ceil(total / 50 * 15 / 60)} min here). Repeated exports are fast.`,
      okText: 'Export', cancelText: 'Cancel', onOk: () => { exportAll(); },
    });
  };
  const exportMenu = {
    items: [
      { key: 'selected', label: `Selected tasks (${fit.ids.length})`, disabled: !fit.ids.length },
      { key: 'all', label: `All ${total ?? ''} matching the filter`, disabled: !total },
    ],
    onClick: ({ key }) => (key === 'all' ? confirmExportAll() : exportIds(fit.ids, fit.skipped.length)),
  };

  const runBulk = async (ids, request, done) => {
    setBusy(true);
    const failed = [];
    for (const id of ids) {
      try { await request(id); } catch (error) { failed.push(`${id}: ${errorText(error)}`); }
    }
    setBusy(false);
    done(ids.length - failed.length, failed);
    reload();
  };
  const bulkDelete = () => Modal.confirm({
    title: `Delete ${plural(removable.ids.length, 'task')}?`,
    content: <>This permanently removes the stored results.{removable.skipped.length
      ? ` ${plural(removable.skipped.length, 'queued or running task')} ${removable.skipped.length === 1 ? 'is' : 'are'} skipped.` : ''}</>,
    okText: `Delete ${removable.ids.length}`, okButtonProps: { danger: true }, cancelText: 'Cancel',
    onOk: () => runBulk(removable.ids, async id => {
      const response = await fetch(`${URL}/task/${id}`, { method: 'DELETE' });
      if (!response.ok) throw await responseError(response, 'Not deleted');
    }, (count, failed) => {
      setSelected(selected.filter(id => !removable.ids.includes(id) || failed.some(line => line.startsWith(`${id}:`))));
      if (count) message.success(`${plural(count, 'task')} deleted.`);
      if (failed.length) message.error(`Not deleted: ${failed.join('; ')}`, 8);
    }),
  });
  const bulkRetry = () => Modal.confirm({
    title: `Retry ${plural(retryIds.length, 'failed task')} with the same settings?`,
    content: 'Their failures are usually temporary (memory, start-up). New tasks are queued; the failed tasks are kept.',
    okText: 'Retry', cancelText: 'Cancel',
    onOk: () => runBulk(retryIds, id => postJson(`/task/${id}/retry`, { apply_remedy: false }), (count, failed) => {
      if (count) message.success(`${count} ${count === 1 ? 'retry' : 'retries'} queued.`);
      if (failed.length) message.error(`Not retried: ${failed.join('; ')}`, 8);
    }),
  });
  // The ZIP goes to the server with every selected id: tasks without fit files are named in its SKIPPED.txt.
  const zipReady = partitionSelected(selected, rowsById.current, entry => entry.state === 'COMPLETED' && !isIdentificationEntry(entry));
  const zipLabel = selected.length > MAX_ZIP ? `Download results (ZIP): at most ${MAX_ZIP} tasks`
    : `Download results (ZIP) (${zipReady.ids.length})`;
  const downloadZip = async () => {
    setZipping(true);
    try {
      const filename = await downloadResults(selected);
      const left = selected.length - zipReady.ids.length;
      message.success(`Saved ${filename}${left ? ` (${plural(left, 'task')} without fit files, listed in SKIPPED.txt)` : ''}.`);
    } catch (error) {
      message.error(errorText(error));
    } finally {
      setZipping(false);
    }
  };
  const bulkMenu = {
    items: [
      { key: 'zip', icon: <DownloadOutlined />, label: zipLabel, disabled: !zipReady.ids.length || selected.length > MAX_ZIP },
      { key: 'retry', icon: <ReloadOutlined />, label: `Retry failed (${retryIds.length})`, disabled: !retryIds.length },
      { key: 'delete', icon: <DeleteOutlined />, danger: true, label: `Delete (${removable.ids.length})…`, disabled: !removable.ids.length },
      { type: 'divider' },
      { key: 'clear', label: 'Clear selection' },
    ],
    onClick: ({ key }) => {
      if (key === 'zip') downloadZip();
      if (key === 'retry') bulkRetry();
      if (key === 'delete') bulkDelete();
      if (key === 'clear') setSelected([]);
    },
  };

  // --- states -------------------------------------------------------------------------------
  const mine = remembered && view.user === remembered;
  const anyFilter = filtersActive(view);
  const clearFilters = () => update({ name: '', state: [], verdict: [] });
  let body;
  if (loadError) {
    body = <div className="dara-empty" role="alert">
      <h3>Results could not be loaded</h3>
      <p>{loadError}</p>
      <Button type="primary" icon={<ReloadOutlined />} onClick={reload}>Retry</Button>
    </div>;
  } else if (tasks && total === 0) {
    body = anyFilter || view.user ? <div className="dara-empty">
      <h3>{anyFilter ? 'No tasks match these filters' : `No tasks by ${view.user}`}</h3>
      <p>{anyFilter ? 'Clear the filters to see every task, or submit a new pattern.' : 'Show the tasks of all users, or submit a new pattern.'}</p>
      <div className="dara-empty-actions">
        {anyFilter ? <Button type="primary" onClick={clearFilters}>Clear filters</Button>
          : <Button type="primary" onClick={() => { setUserText(''); update({ user: '' }); }}>Show all users</Button>}
        <Button href="/">Submit a pattern</Button>
      </div>
    </div> : <div className="dara-empty">
      <h3>No analyses yet</h3>
      <p>Submit a powder pattern with the elements it may contain; its phases, fit and amounts appear here when the analysis finishes.</p>
      <div className="dara-empty-actions"><Button type="primary" href="/">Submit a pattern</Button><a href="/tutorial/">Read the tutorial</a></div>
    </div>;
  } else {
    body = <Table
      size="small"
      className="dara-results-table"
      loading={!tasks}
      rowKey="fw_id"
      // Fixed layout: short columns keep their widths, Name and Final phases share the rest;
      // below 1100 px the table scrolls instead of squeezing the names.
      tableLayout="fixed"
      scroll={{ x: 1100 }}
      dataSource={tasks || []}
      columns={tableColumns}
      showSorterTooltip={false}
      onChange={(pagination, _, sort, extra) => {
        if (extra?.action === 'sort') {
          const key = sort.order ? sort.columnKey : null;
          update({ sort: key, order: key ? (sort.order === 'ascend' ? 'asc' : 'desc') : null });
        }
        if (extra?.action === 'paginate') {
          setView(previous => ({ ...previous, page: pagination.pageSize !== previous.limit ? 1 : pagination.current, limit: pagination.pageSize }));
        }
      }}
      rowSelection={{
        selectedRowKeys: selected,
        preserveSelectedRowKeys: true,
        columnWidth: 36,
        onChange: keys => setSelected(keys),
      }}
      pagination={{
        total: total || 0, pageSize: view.limit, current: view.page, size: 'small',
        showSizeChanger: true, pageSizeOptions: PAGE_SIZES, showTotal: count => plural(count, 'task'),
      }} />;
  }

  return (
    <Layout hasSider={false} title="Results">
      <div className="dara-results">
        <div className="dara-page-head">
          <h1>Results</h1>
          <span className="dara-page-note" aria-live="polite">
            {exporting ? 'First export of these tasks can take up to a minute.'
              : paused ? <>Updates paused <Button type="link" size="small" onClick={reload}>Retry</Button></>
              : live ? 'Updating while tasks run' : null}
          </span>
          <div className="dara-page-actions">
            <Tooltip title={compareTip}>
              <span><Button icon={<LineChartOutlined />} disabled={compare.ids.length < 2 || compare.ids.length > MAX_COMPARE}
                href={compare.ids.length >= 2 && compare.ids.length <= MAX_COMPARE ? `/compare/?tasks=${compare.ids.join(',')}&back=${encodeURIComponent(back)}` : undefined}>
                {`Compare patterns (${compare.ids.length})`}</Button></span>
            </Tooltip>
            <Dropdown.Button icon={<DownOutlined />} menu={exportMenu} loading={Boolean(exporting)} disabled={!total && !fit.ids.length}
              buttonsRender={([left, right]) => [
                <Tooltip key="left" title={fit.skipped.length ? `${plural(fit.skipped.length, 'identification task')} (no fit) left out of the table` : null}>{left}</Tooltip>,
                React.cloneElement(right, { 'aria-label': 'Export options' }),
              ]}
              onClick={() => (fit.ids.length ? exportIds(fit.ids, fit.skipped.length) : confirmExportAll())}>
              <TableOutlined />
              {exporting ? `Exporting ${exporting.done} / ${exporting.total}…`
                : `Export table (CSV)${fit.ids.length ? ` · ${fit.ids.length}` : ''}`}
            </Dropdown.Button>
            <Dropdown menu={bulkMenu} disabled={!selected.length || busy || zipping} trigger={['click']}>
              <Button loading={busy || zipping}>{zipping ? 'Preparing ZIP…' : `${selected.length} selected`} <DownOutlined /></Button>
            </Dropdown>
          </div>
        </div>
        <div className="dara-filters" role="search" aria-label="Filter the results">
          <Input.Search key={`name-${view.name}`} placeholder="Sample or file name" allowClear defaultValue={view.name}
            onSearch={value => update({ name: value.trim() })} aria-label="Filter by sample or file name" className="dara-filter-name" />
          {remembered ? <Radio.Group optionType="button" value={mine ? 'mine' : !view.user ? 'all' : null} aria-label="Whose tasks"
            options={[{ value: 'mine', label: 'My tasks' }, { value: 'all', label: 'All users' }]}
            onChange={event => { const user = event.target.value === 'mine' ? remembered : ''; setUserText(user); update({ user }); }} /> : null}
          <AutoComplete value={userText} onChange={setUserText} className="dara-filter-user" dropdownMatchSelectWidth={240}
            options={users.map(value => ({ value, label: userCounts[value.toLowerCase()]
              ? <span className="dara-user-option">{value}<span className="dara-num">{plural(userCounts[value.toLowerCase()], 'task')}</span></span> : value }))}
            filterOption={(input, option) => option.value.toLowerCase().includes(input.toLowerCase())}
            onSelect={value => update({ user: value })}>
            <Input.Search placeholder="User contains…" allowClear aria-label="Filter by user (contains)" onSearch={value => update({ user: value.trim() })} />
          </AutoComplete>
          <Select mode="multiple" allowClear placeholder="Status" aria-label="Status" value={view.state} options={STATE_OPTIONS}
            maxTagCount="responsive" className="dara-filter-select" onChange={state => update({ state })} />
          <Select mode="multiple" allowClear placeholder="Verdict" aria-label="Verdict" value={view.verdict} options={VERDICT_OPTIONS}
            maxTagCount="responsive" className="dara-filter-select" onChange={verdict => update({ verdict })} />
          {anyFilter ? <Button type="link" onClick={clearFilters}>Clear filters</Button> : null}
        </div>
        {body}
      </div>
    </Layout>
  );
}

export default ResultsPage;
