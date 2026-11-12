import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Button, Dropdown, Input, Modal, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { DeleteOutlined, DownloadOutlined, EllipsisOutlined, LineChartOutlined, TableOutlined } from '@ant-design/icons';
import { parseComparisonTaskIds } from '../components/patternComparison.mjs';
import { runAfterSuccess } from '../components/submitHelpers.mjs';
import { retryTask } from '../components/ResultWorkflows';
import { API as URL } from '../components/QuickRefineProgress';
import { adoptedLabel, adoptedRwp, formatFixed, phasesLabel, phasesTooltip, stateLabel, tasksQuery } from '../components/resultsList.mjs';
import { overallColor, overallLabel, overallShortLabel } from '../components/confidence.mjs';
import { StateTag } from '../components/ConfidenceViews';
import ChemicalFormula from '../components/ChemicalFormula';
import StatusTag from '../components/StatusTag';

/** Status tag of a completed task followed by its overall confidence (same labels as the task page). */
function ConfidenceStatus({ tag, level }) {
  const label = overallLabel(level);
  if (!label) return tag;
  // A verdict only exists for completed tasks, so it stands in for the plain "Completed" text;
  // one word in the cell, the full verdict in the tooltip.
  return <Tooltip title={`Completed · ${label}. Open the task for the reasons.`}>
    <Tag color={overallColor(level)}>{overallShortLabel(level)}</Tag>
  </Tooltip>;
}

// "2026-11-08 18:31:36" -> "11-08 18:31": the year and seconds add width, not information.
const shortDate = value => (/^\d{4}-(\d{2}-\d{2}) (\d{2}:\d{2})/.exec(String(value || '')) || []).slice(1).join(' ') || value;

const Container = styled.div`
`

const { Search } = Input;

// Failed and cancelled rows have no metrics; their cells are left empty and dimmed.
// A completed search that found no fit (no phases, no Rwp) is told apart from a completed fit.
const noFit = entry => entry.state === 'COMPLETED' && entry.summary?.schema != null
  && entry.summary.final_rwp == null && !(entry.summary.final_phases || []).length;
const hasNoResult = entry => ['FIZZLED', 'CANCELLED'].includes(entry.state) || noFit(entry);
const metricCell = entry => ({ className: hasNoResult(entry) ? 'dara-dim' : '' });
const STATE_FILTERS = ['COMPLETED', 'FIZZLED', 'RUNNING', 'READY', 'CANCELLING', 'CANCELLED'].map(value => ({ text: stateLabel(value), value }));

function ResultsPage({ location }) {
  const queryParameters = new URLSearchParams(location.search)
  const page = Number((queryParameters.get('page')) || "1")
  const limit = Number(queryParameters.get('limit') || "25")
  const [ user, setUser ] = React.useState(queryParameters.get('user') || null)
  const [ name, setName ] = React.useState(queryParameters.get('name') || null)
  const state = queryParameters.get('state') || null

  const [data, setData] = React.useState({})
  const [selectedTaskIds, setSelectedTaskIds] = React.useState(() => parseComparisonTaskIds(queryParameters.get('selected')));

  const onSearch = (value) => {
    setUser(value)
    window.location.href = `/results?` + tasksQuery({ page: 1, limit, user: value, name, state, selected: selectedTaskIds })
  }

  const onNameSearch = (value) => {
    const trimmed = (value || '').trim()
    setName(trimmed || null)
    window.location.href = `/results?` + tasksQuery({ page: 1, limit, user, name: trimmed, state, selected: selectedTaskIds })
  }

  React.useEffect(() => {
    fetch(`${URL}/tasks?` + tasksQuery({ page, limit, user, name, state }))
      .then(response => response.json())
      .then(data => setData(data));
  }, [page, limit, user, name, state]);

  const deleteTask = async (taskId) => {
    const response = await fetch(`${URL}/task/${taskId}`, { method: 'DELETE' });
    if (!response.ok) {
      const error = await response.json();
      message.error(error.detail || 'Could not delete the result.');
      return;
    }
    // The result is already deleted on the server; UI follow-up problems must not look like a failed delete.
    runAfterSuccess([
      () => setData(previous => ({
        ...previous,
        tasks: (previous.tasks || []).filter(task => task.fw_id !== taskId),
        total_tasks: Math.max((previous.total_tasks || 1) - 1, 0),
      })),
      () => setSelectedTaskIds(previous => previous.filter(id => id !== taskId)),
      () => message.success(`Task ${taskId} deleted.`),
    ]);
  };

  const columns = [
    {
      title: 'ID',
      dataIndex: 'fw_id',
      key: 'fw_id',
      width: 64,
      className: 'dara-num',
    },
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      // A share of the table, so a wide window gives its extra room to Final phases.
      width: '28%',
      // One line of name (sample, else file), one muted line of date · user (· series).
      render: (text, entry) => (
        <div className="dara-name-cell">
          <Tooltip title={entry.sample ? <>{entry.sample}<br />{text}</> : text} placement="topLeft" mouseEnterDelay={0.4}>
            <a href={`/task?id=${entry.fw_id}`}>{entry.sample || text}</a>
          </Tooltip>
          <span className="dara-name-meta">
            {[shortDate(entry.created_on), entry.user].filter(Boolean).join(' · ')}
            {entry.series?.id ? <> · <a href={`/series/?id=${encodeURIComponent(entry.series.id)}`}>{`${entry.series.name} #${(entry.series.order ?? 0) + 1}`}</a></> : null}
          </span>
        </div>
      )
    },
    {
      title: 'Status',
      dataIndex: 'state',
      key: 'state',
      width: 96,
      filters: STATE_FILTERS,
      // Filtered on the server (every page), not just the rows shown.
      filteredValue: state ? state.split(',') : null,
      // One tag per row; failure reasons and the verdict sit in its tooltip, actions in the row menu.
      render: (text, entry) => {
        if (noFit(entry)) return <StatusTag text="No fit" tone="review" style={{ marginInlineStart: 0 }}
          tooltip="The search finished without a fit. Open the task for the settings to widen." />;
        if (text === 'FIZZLED') return <Tooltip title={entry.error_summary?.title || 'Failed'}><span><StateTag state={text} /></span></Tooltip>;
        return <ConfidenceStatus tag={<StateTag state={text} />} level={text === 'COMPLETED' ? entry.summary?.confidence_overall : null} />;
      }
    },
    {
      title: <Tooltip title="Rwp of the adopted fit, or of the automatic fit when nothing is adopted"><span>Rwp (%)</span></Tooltip>,
      key: 'best_rwp',
      width: 84,
      className: 'dara-num',
      onCell: metricCell,
      render: (_, entry) => (hasNoResult(entry) ? null : formatFixed(adoptedRwp(entry.summary))),
    },
    {
      title: <Tooltip title="Goodness of fit = Rwp / Rexp; near 1 is ideal for counting statistics."><span>GOF</span></Tooltip>,
      key: 'gof',
      width: 64,
      className: 'dara-num',
      onCell: metricCell,
      render: (_, entry) => (hasNoResult(entry) ? null : formatFixed(entry.summary?.gof)),
    },
    {
      title: 'Final phases',
      key: 'final_phases',
      // No width: Final phases takes whatever the other columns leave.
      onCell: metricCell,
      render: (_, entry) => {
        const label = phasesLabel(entry.summary)
        if (!label) return hasNoResult(entry) ? null : '—'
        const labels = entry.summary.final_phase_labels
        const hidden = labels.length - 3
        return (
          <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phasesTooltip(entry.summary)}</div>}>
            <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {labels.slice(0, 3).map((item, index) => <React.Fragment key={index}>{index ? ', ' : ''}<ChemicalFormula formula={item} /></React.Fragment>)}
              {hidden > 0 ? `, … (+${hidden})` : ''}
            </span>
          </Tooltip>
        )
      }
    },
    {
      title: <Tooltip title="Result marked as adopted in the task review">Adopted</Tooltip>,
      key: 'adopted',
      width: 76,
      onCell: metricCell,
      render: (_, entry) => {
        const label = adoptedLabel(entry.adopted)
        return label ? <Tooltip title={label.title}><span className="dara-adopted-mark">{label.text}</span></Tooltip> : null
      }
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 88,
      fixed: 'right',
      render: (_, entry) => (
        <Space>
          <Tooltip title="Download (ZIP)">
            <Button
              icon={<DownloadOutlined />}
              aria-label={`Download task ${entry.fw_id}`}
              href={`${URL}/task/${entry.fw_id}/download`}
              disabled={entry.state !== 'COMPLETED'}
            />
          </Tooltip>
          {/* Rerun, retry and delete share one row menu, away from Download. */}
          <Dropdown trigger={['click']}
            menu={{ items: [
              ...(['READY', 'RUNNING', 'CANCELLING'].includes(entry.state) ? [] : [{ key: 'rerun', label: <a href={`/?clone_task=${entry.fw_id}`}>Rerun with these settings…</a> }]),
              ...(entry.state === 'FIZZLED' && entry.error_summary?.remedy?.label ? [{ key: 'retry', label: `Retry with fix (${entry.error_summary.remedy.label})` }] : []),
              { type: 'divider' },
              { key: 'delete', danger: true, icon: <DeleteOutlined />, label: 'Delete…', disabled: ['READY', 'RUNNING', 'CANCELLING'].includes(entry.state) },
            ], onClick: ({ key }) => {
              if (key === 'retry') Modal.confirm({
                title: `Retry task ${entry.fw_id} with the suggested fix (${entry.error_summary.remedy.label})?`,
                content: 'A new task is queued; the failed task is kept.', okText: 'Retry with fix', cancelText: 'Cancel',
                onOk: () => retryTask(entry.fw_id, true),
              });
              if (key === 'delete') Modal.confirm({
                title: `Delete task ${entry.fw_id}?`, content: 'This permanently removes the stored result.',
                okText: 'Delete', okButtonProps: { danger: true }, cancelText: 'Cancel',
                onOk: () => deleteTask(entry.fw_id),
              });
            } }}>
            <Button icon={<EllipsisOutlined />} aria-label={`More actions for task ${entry.fw_id}`} />
          </Dropdown>
        </Space>
      ),
    }
  ]

  return (
    <Layout hasSider={false} title="Results">
      <Container>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Search
            placeholder="Filter sample or file name"
            allowClear
            enterButton
            onSearch={onNameSearch}
            defaultValue={name}
            style={{ width: 'min(260px, 45vw)', minWidth: 180 }}
          />
          <Search
            placeholder="Filter username"
            allowClear
            enterButton
            onSearch={onSearch}
            defaultValue={user}
            style={{ width: 'min(260px, 45vw)', minWidth: 180 }}
          />
          <span style={{ flex: 1 }} />
          {selectedTaskIds.length > 0 && <Button type="link" onClick={() => setSelectedTaskIds([])}>Clear selection</Button>}
          <Tooltip title={selectedTaskIds.length < 2 ? 'Tick 2–20 completed tasks to compare their patterns' : null}>
            <span><Button
              icon={<LineChartOutlined />}
              disabled={selectedTaskIds.length < 2}
              href={selectedTaskIds.length >= 2 ? `/compare?tasks=${selectedTaskIds.join(',')}` : undefined}
            >Compare selected ({selectedTaskIds.length})</Button></span>
          </Tooltip>
          <Button
            icon={<TableOutlined />}
            disabled={!selectedTaskIds.length}
            href={selectedTaskIds.length ? `${URL}/tasks/table.csv?ids=${selectedTaskIds.join(',')}` : undefined}
          >Export table ({selectedTaskIds.length})</Button>
        </div>
        {
          data.tasks ? (
            <Table
              size="small"
              onChange={(_, filters, __, extra) => {
                if (extra?.action !== 'filter') return
                const next = (filters.state || []).join(',')
                window.location.href = `/results?` + tasksQuery({ page: 1, limit, user, name, state: next, selected: selectedTaskIds })
              }}
              rowKey="fw_id"
              // Fixed layout: short columns keep their widths and the long ones take the rest;
              // below 1100 px the table scrolls instead of squeezing the names.
              tableLayout="fixed"
              scroll={{ x: 1100 }}
              dataSource={data.tasks}
              columns={columns} 
              rowSelection={{
                selectedRowKeys: selectedTaskIds,
                preserveSelectedRowKeys: true,
                onChange: keys => {
                  if (keys.length > 20) message.warning('Compare up to 20 patterns at a time.');
                  setSelectedTaskIds(keys.slice(0, 20));
                },
                getCheckboxProps: record => ({ disabled: record.state !== 'COMPLETED' }),
              }}
              pagination={{
                total: data.total_tasks,
                pageSize: limit,
                current: page,
                showSizeChanger: true,
                pageSizeOptions: [10, 25, 50],
                onChange: (page, pageSize) => {
                  window.location.href = `/results?` + tasksQuery({ page, limit: pageSize, user, name, state, selected: selectedTaskIds })
                }
            }}/>
          ) : (
            <p>Loading...</p>
          )
        }
      </Container>

    </Layout>
  )
}

export default ResultsPage;
