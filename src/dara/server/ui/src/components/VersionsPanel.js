import React from 'react';
import { Alert, Button, Dropdown, Modal, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { EllipsisOutlined, LineChartOutlined } from '@ant-design/icons';
import { EXPORT_FORMATS, exportHref } from './confidence.mjs';
import { downloadReport } from './ResultWorkflows';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import ChemicalFormula from './ChemicalFormula';
import { BASELINE_LABEL, QUICK_CHECK_HINT, REFINE_ALL_FIRST, comparisonBase, phaseLabels, phaseRecordLine, rememberDisplayFormulas, sameFitAs, versionLabel } from './versions.mjs';

const { Text } = Typography;

const ORIGIN_LABEL = {
  automatic: 'Automatic', add_hint: 'Hint', add_peak_search: 'Peak search', swap: 'Swap',
  remove: 'Remove', background: 'Background', manual: 'Manual', refine_all: 'Refine all', structure: 'Structure',
};

export const VERSIONS_POLL_MS = 15000;

/** Versions of one task. `available` is false when the server has no versions endpoint (404/405). */
export function useVersions(taskId) {
  const [versions, setVersions] = React.useState([]);
  const [available, setAvailable] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState('');
  const [baselineStale, setBaselineStale] = React.useState(false);
  const mounted = React.useRef(true);
  const refresh = React.useCallback(async ({ silent = false } = {}) => {
    try {
      const body = await requestJson(`/task/${taskId}/versions`);
      if (!mounted.current) return [];
      const list = Array.isArray(body) ? body : (body?.versions || []);
      if (!Array.isArray(body)) rememberDisplayFormulas(body?.display_formulas);
      setVersions(list);
      setBaselineStale(!Array.isArray(body) && body?.baseline_stale === true);
      setAvailable(true);
      setError('');
      return list;
    } catch (e) {
      if (!mounted.current) return [];
      if (e.missing) setAvailable(false);
      else if (!silent) setError(e.message);
      return [];
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [taskId]);
  React.useEffect(() => {
    mounted.current = true;
    setLoading(true);
    refresh();
    return () => { mounted.current = false; };
  }, [refresh]);
  // Versions are also created from other tabs and by jobs that finish while the page is open:
  // refetch on window focus and every 15 s (a cheap endpoint) while the page is visible.
  React.useEffect(() => {
    const quiet = () => { if (!document.hidden) refresh({ silent: true }); };
    const handle = setInterval(quiet, VERSIONS_POLL_MS);
    window.addEventListener('focus', quiet);
    document.addEventListener('visibilitychange', quiet);
    return () => {
      clearInterval(handle);
      window.removeEventListener('focus', quiet);
      document.removeEventListener('visibilitychange', quiet);
    };
  }, [refresh]);
  const remove = React.useCallback(async n => {
    await requestJson(`/task/${taskId}/versions/${n}`, { method: 'DELETE' });
    return refresh();
  }, [taskId, refresh]);
  return { versions, available, loading, error, refresh, remove, baselineStale };
}

/** Version numbers below the newest that the list does not show (numbers are never reused). */
const missingVersions = versions => {
  const shown = new Set(versions.map(item => item.version));
  return Array.from({ length: Math.max(0, ...shown) }, (_, i) => i + 1).filter(n => !shown.has(n));
};

const fixed = (value, digits = 2) => (value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits));

function createdText(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : `Created ${date.toLocaleString('en-US')}`;
}

function phaseSummary(phases = [], symbolOf = () => null) {
  if (!phases.length) return '—';
  return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phases.map(name => phaseRecordLine(name, symbolOf(name))).join('\n')}</div>}><span>{phaseLabels(phases, symbolOf).map((label, index) => <React.Fragment key={index}>{index ? ', ' : ''}<ChemicalFormula formula={label} /></React.Fragment>)}</span></Tooltip>;
}

function originTag(row) {
  if (row.label === BASELINE_LABEL) return <Tag>Baseline</Tag>;
  const kind = row.origin?.kind;
  return <Tag>{ORIGIN_LABEL[kind] || kind || '—'}</Tag>;
}

export default function VersionsPanel({ taskId, store, review, onView, onCompare, onRefineAll, refineBusy = false, extra, symbolOf = () => null, onPreview = null, shownSel }) {
  const { versions, available, loading, error, remove } = store;
  const [deleting, setDeleting] = React.useState(null);
  if (available === false) return null;
  const adopted = review?.saved?.selection;
  const confirmDelete = row => Modal.confirm({
    title: `Delete version ${row.version}?`,
    content: "Removes this version's refinement.",
    okText: 'Delete', okButtonProps: { danger: true }, cancelText: 'Cancel',
    async onOk() {
      setDeleting(row.version);
      try {
        await remove(row.version);
        message.success(`Version ${row.version} deleted.`);
      } catch (e) {
        message.error(e.missing ? NOT_AVAILABLE : e.message);
      } finally {
        setDeleting(null);
      }
    },
  });
  const isAdoptedRow = n => adopted === `version:${n}` || (n === 1 && adopted === 'best');
  const rows = versions.map(item => ({ ...item, key: item.version }));
  const columns = [
    { title: missingVersions(versions).length ? <Tooltip title={`Not listed: ${missingVersions(versions).map(n => `v${n}`).join(', ')} (deleted, or quick checks replaced by their full refinement).`}><span>Version</span></Tooltip> : 'Version',
      dataIndex: 'version', width: 90, className: 'dara-tray-code', render: (n, row) => <Space size={4} direction="vertical">
      <Tooltip title={createdText(row.created_at)}><span className="dara-code">{`v${n}`}</span></Tooltip>
      {isAdoptedRow(n) ? <span className="dara-adopted-mark">Adopted</span> : null}
    </Space> },
    { title: 'Label', dataIndex: 'label', render: (label, row) => {
      const same = sameFitAs(row, versions);
      return <>{row.version === 1 ? 'Automatic fit' : versionLabel(row, versions, symbolOf) || '—'}
        {same ? <Tooltip title="Same phases and Rwp: this route converged on an existing fit.">
          <Text type="secondary">{` · same fit as v${same.version}`}</Text></Tooltip> : null}</>;
    } },
    { title: 'Origin', key: 'origin', render: (_, row) => <Space size={4} wrap>
      {originTag(row)}
      {row.refine_mode === 'fast' ? <Tooltip title={QUICK_CHECK_HINT}><Tag color="gold">Quick check</Tag></Tooltip> : null}
    </Space> },
    { title: <Tooltip title="Weighted-profile R factor: misfit between observed and calculated pattern (lower is better)."><span>Rwp (%)</span></Tooltip>, dataIndex: 'rwp', className: 'dara-num', render: value => fixed(value) },
    { title: 'Phases', dataIndex: 'phases', render: (phases, row) => phaseSummary(phases, name => row.phase_symbols?.[name] ?? symbolOf(name)) },
    // Same format as the candidates table: significant (+minor).
    { title: 'Unexplained', dataIndex: 'n_unexplained', className: 'dara-num', render: (value, row) => (value == null ? '—'
      : row.n_unexplained_minor ? `${value - row.n_unexplained_minor} (+${row.n_unexplained_minor} minor)` : value) },
    { title: 'Actions', key: 'actions', render: (_, row) => {
      const sel = row.version === 1 ? 'best' : `version:${row.version}`;
      const isAdopted = adopted === sel;
      const quickCheck = row.refine_mode === 'fast';
      const deleteBlocked = row.version === 1 || row.deletable === false ? 'the best fit is kept'
        : adopted === sel ? 'un-adopt first' : null;
      const reportBlocked = quickCheck ? REFINE_ALL_FIRST.toLowerCase() : review.dirty ? 'save the review first' : null;
      const exportSel = row.version === 1 ? 'best' : sel;
      const compareWith = adopted != null && adopted !== sel ? adopted : comparisonBase('best', versions, row.version);
      const items = [
        ...(row.version > 1 ? [{ key: 'compare', label: 'Compare…' }, { type: 'divider' }] : []),
        { key: 'report', disabled: Boolean(reportBlocked), label: reportBlocked ? `Report (${reportBlocked})` : 'Report (PDF + Word)' },
        { key: 'report-html', disabled: Boolean(reportBlocked), label: 'Report (HTML)' },
        { key: 'export', label: 'Export for…', disabled: quickCheck, children: EXPORT_FORMATS.map(format => ({
          key: `export:${format.key}`, label: <a href={exportHref(API, taskId, format.key, exportSel)}>{format.label}</a> })) },
        { type: 'divider' },
        { key: 'delete', danger: true, disabled: Boolean(deleteBlocked), label: deleteBlocked ? `Delete (${deleteBlocked})` : 'Delete…' },
      ];
      const onMenu = ({ key }) => {
        if (key === 'compare') onCompare(compareWith, sel);
        if (key === 'report') downloadReport(taskId, exportSel);
        if (key === 'report-html') downloadReport(taskId, exportSel, 'html');
        if (key === 'delete') confirmDelete(row);
      };
      return <Space size="small">
        <Button size="small" icon={<LineChartOutlined />} onClick={() => onView(row.version === 1 ? null : sel)}>Open</Button>
        {quickCheck
          ? (onRefineAll ? <Tooltip title="Refine all phases, then compare">
            <Button size="small" type="primary" disabled={refineBusy} onClick={() => onRefineAll(row.version)}>Refine all phases</Button>
          </Tooltip> : null)
          : <Tooltip title="Adopt (saves notes too)">
            <span><Button size="small" disabled={review.loading || !!review.error || review.saving}
              onClick={() => review.save(sel)}>{isAdopted ? 'Adopted' : 'Adopt'}</Button></span>
          </Tooltip>}
        <Dropdown menu={{ items, onClick: onMenu }} trigger={['click']}>
          <Button size="small" icon={<EllipsisOutlined />} loading={deleting === row.version} aria-label={`More actions for version ${row.version}`} />
        </Dropdown>
      </Space>;
    } },
  ];
  return <section aria-label="Result versions" style={{ marginTop: 16, marginBottom: 12 }}>
    <h3 style={{ marginBottom: 8 }}>Versions</h3>
    {extra}
    {error ? <Alert type="error" showIcon style={{ marginBottom: 8 }} message={error} /> : null}
    <Table size="small" pagination={false} loading={loading} rowKey="version" dataSource={rows} tableLayout="auto"
      // A row click shows that version's fit in the page plot (buttons keep their own action).
      onRow={row => {
        if (!onPreview) return {};
        const sel = row.version === 1 ? null : `version:${row.version}`;
        return { onClick: event => { if (!event.target.closest('button, a')) onPreview(sel); },
          onKeyDown: event => { if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) { event.preventDefault(); onPreview(sel); } },
          tabIndex: 0, 'aria-selected': sel === shownSel, style: { cursor: 'pointer' }, title: 'Show this fit in the plot' };
      }}
      rowClassName={row => ((row.version === 1 ? null : `version:${row.version}`) === shownSel ? 'dara-row-shown' : '')}
      scroll={{ x: 'max-content' }} columns={columns} />
    {!loading && versions.length <= 1 ? <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
      Only the automatic fit so far. Use Add phase… in the fit workspace to create a version.</Text> : null}
  </section>;
}
