import React from 'react';
import { Alert, Button, Input, message, Modal, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { EditOutlined, InfoCircleOutlined, SaveOutlined } from '@ant-design/icons';
import { runAfterSuccess } from './submitHelpers.mjs';
import { API, NOT_AVAILABLE, postJson, requestJson } from './QuickRefineProgress';
import { DEFAULT_CONTROLS, RefinementControlsEditor } from './PracticalControls';
import { selectionQuery, withHkl } from './confidence.mjs';
import { selectOptionGroups, selectionLabel, selectedPhasesRequest } from './versions.mjs';

async function responseJson(response) {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (HTTP ${response.status}).`);
  return body;
}

export function useTaskReview(taskId) {
  const [saved, setSaved] = React.useState({ selection: null, notes: '' });
  const [selection, setSelection] = React.useState(null);
  const [notes, setNotes] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetch(`${API}/task/${taskId}/review`, { signal: controller.signal })
      .then(responseJson).then(body => {
        setSaved(body); setSelection(body.selection); setNotes(body.notes); setError('');
      }).catch(e => { if (e.name !== 'AbortError') setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [taskId]);
  const save = async (choice = selection) => {
    setSaving(true);
    try {
      const body = await responseJson(await fetch(`${API}/task/${taskId}/review`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection: choice, notes }),
      }));
      // Already saved on the server; UI follow-up problems must not look like a failed save.
      runAfterSuccess([
        () => setSaved(body), () => setSelection(body.selection), () => setError(''),
        () => message.success('Review saved.'),
      ]);
      return true;
    } catch (e) { message.error(e.message); return false; }
    finally { setSaving(false); }
  };
  const dirty = selection !== saved.selection || notes !== saved.notes;
  // Unsaved notes or choice: the browser asks before the page is left.
  React.useEffect(() => {
    if (!dirty) return undefined;
    const warn = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  return { saved, selection, setSelection, notes, setNotes, loading, saving, error, save, dirty };
}

/** Download the printable HTML report of `selection` (undefined = the saved adopted choice). */
/**
 * Downloads the report: `format` 'bundle' = one ZIP with the PDF and an editable Word file,
 * 'html' = the HTML page. The file name is the server's.
 */
export async function downloadReport(taskId, selection, format = 'bundle', hkl = false) {
  const bundle = format === 'bundle';
  const hide = message.loading(bundle ? 'Preparing the PDF and Word report…' : 'Preparing the report…', 0);
  try {
    const path = bundle ? 'report.zip' : 'report';
    const response = await fetch(`${API}/task/${taskId}/${path}${withHkl(selection === undefined ? '' : selectionQuery(selection), hkl)}`);
    if (!response.ok) await responseJson(response);
    const blob = await response.blob();
    const named = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1];
    const objectUrl = window.URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = named || `dara_task_${taskId}_${String(selection ?? 'adopted').replace(':', '_')}_report.${bundle ? 'zip' : 'html'}`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);
    hide();
    message.success(bundle ? 'Report downloaded: PDF and Word (.docx) in one ZIP.' : 'Report downloaded. Open it in a browser to print or save as PDF.');
  } catch (e) { hide(); message.error(e.message || 'Report download failed.'); }
}

export function ReviewPanel({ data, review, versions = [], style = null }) {
  // Quick checks are approximate fits: they are never offered for adoption.
  const groups = selectOptionGroups(data.all_results, versions, { adoptable: true, current: review.selection });
  const saved = review.saved.selection;
  const savedLabel = saved == null ? 'Not adopted yet' : selectionLabel(saved, { all_results: data.all_results, versions }) || String(saved);
  return <section aria-label="Result review" className="dara-panel" style={{ marginBottom: 20, padding: 16, border: '1px solid var(--dara-border)', borderRadius: 'var(--dara-radius)', background: 'var(--dara-surface)', ...style }}>
    <h3>Adopted fit &amp; notes</h3>
    {review.error ? <Alert type="error" showIcon message={review.error} /> : null}
    <Space wrap style={{ marginBottom: 12 }}>
      {/* The select shows the choice; the saved one is named only while they differ. */}
      {(review.selection ?? null) !== (saved ?? null) ? <Tag>{`Saved: ${savedLabel}`}</Tag> : null}
      <Select aria-label="Adopted fit" style={{ width: 'min(420px, calc(100vw - 96px))' }}
        disabled={review.loading || !!review.error || review.saving}
        value={review.selection ?? 'unselected'}
        onChange={value => review.setSelection(value === 'unselected' ? null : value)}
        options={[{ value: 'unselected', label: 'No adopted result' }, ...groups]} />
    </Space>
    <Input.TextArea aria-label="Lab notes" value={review.notes} maxLength={10000} showCount rows={3}
      disabled={review.loading || !!review.error || review.saving} onChange={event => review.setNotes(event.target.value)}
      placeholder="Sample treatment, interpretation, reasons for choosing a fit, remaining unexplained peaks…" />
    <Space wrap style={{ marginTop: 12 }}>
      <Tooltip title="The report and downloads use the saved choice">
        <Button type="primary" icon={<SaveOutlined />} loading={review.saving} disabled={review.loading || !!review.error || !review.dirty} onClick={() => review.save()}>Save review</Button>
      </Tooltip>
      {review.dirty ? <Typography.Text type="warning">Unsaved changes</Typography.Text> : null}
    </Space>
  </section>;
}

/**
 * "Select phases…" button and checklist modal. The checked phases are submitted through the quick
 * re-refinement path (`onSubmit(request)`), which stores a new version of the same task.
 */
/** `openSignal` (a counter) opens the dialog from elsewhere, e.g. a menu; `hideButton` drops the own button. */
export function PhaseSelectionButton({ taskId, phases = [], extraPhases = [], symbolOf, renderPhase, busy = false, onSubmit, disabledReason = null, openSignal = 0, hideButton = false, ...props }) {
  const [visible, setVisible] = React.useState(false);
  const [available, setAvailable] = React.useState([]);
  const [selected, setSelected] = React.useState([]);
  const [filter, setFilter] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [controls, setControls] = React.useState(DEFAULT_CONTROLS);
  const open = async () => {
    setVisible(true); setLoading(true); setFilter(''); setError(''); setAvailable([]); setSelected([]);
    setControls(DEFAULT_CONTROLS);
    try {
      const body = await responseJson(await fetch(`${API}/task/${taskId}/phase-options`));
      // Phases added in versions (not stored with the task) are offered too.
      const offered = [...new Set([...body.phases, ...extraPhases.map(item => item.ref)])];
      setAvailable(offered);
      setSelected(phases.filter(phase => offered.includes(phase)));
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };
  // Only a change after mounting opens the dialog (the counter outlives this button).
  const lastSignal = React.useRef(openSignal);
  React.useEffect(() => {
    if (openSignal === lastSignal.current) return;
    lastSignal.current = openSignal;
    open();
  }, [openSignal]); // eslint-disable-line react-hooks/exhaustive-deps
  const sources = Object.fromEntries(extraPhases.map(item => [item.ref, item.source]));
  const request = selectedPhasesRequest({ current: phases, selected, available, controls, defaultControls: DEFAULT_CONTROLS, sources, symbolOf });
  const submit = () => {
    if (!request) return;
    setVisible(false);
    onSubmit(request);
  };
  return <>
    {hideButton ? null : <Tooltip title={disabledReason || 'Choose the phases to refine as a new version'}>
      <span><Button {...props} icon={<EditOutlined />} disabled={busy || props.disabled || Boolean(disabledReason)} onClick={open}>Select phases…</Button></span>
    </Tooltip>}
    <Modal title="Select phases" open={visible} width={850}
      onCancel={() => setVisible(false)} onOk={submit} okText="Refine and compare"
      okButtonProps={{ disabled: loading || !request || selected.length > 20 }}>
      <Typography.Paragraph type="secondary">
        Checked phases are refined together as a new version.{' '}
        <Tooltip title="Uses the stored references of this task and the COD references added in its versions; the original result is kept. Check one reference per phase: equivalent CIFs are not extra components.">
          <InfoCircleOutlined />
        </Tooltip>
      </Typography.Paragraph>
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
      <Input.Search aria-label="Filter phase references" placeholder="Filter formula or COD / MP reference" value={filter} onChange={e => setFilter(e.target.value)} />
      <p style={{ marginTop: 8 }}>{`${selected.length} / 20`}</p>
      <Table size="small" loading={loading} rowKey="reference" pagination={{ pageSize: 8 }}
        rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: values => { setSelected(values); setError(''); } }}
        dataSource={available.filter(name => name.toLowerCase().includes(filter.toLowerCase())).map(reference => ({ reference }))}
        columns={[{ title: 'Reference phase', dataIndex: 'reference', render: ref => renderPhase ? renderPhase(ref) : ref },
          { title: 'Reference ID', dataIndex: 'reference', render: ref => <Typography.Text style={{ overflowWrap: 'anywhere' }} type="secondary">{ref}</Typography.Text> },
          { title: 'In this fit', dataIndex: 'reference', render: ref => phases.includes(ref) ? <Tag>Included</Tag> : null }]} />
      <RefinementControlsEditor value={controls} onChange={setControls} />
    </Modal>
  </>;
}

/** Queue a new analysis from a FIZZLED task. Navigates to the new task on success. */
export async function retryTask(taskId, applyRemedy) {
  try {
    const body = await postJson(`/task/${taskId}/retry`, { apply_remedy: Boolean(applyRemedy) });
    if (body?.wf_id == null) throw new Error('The server did not return the new task.');
    message.success(`Retry queued as task ${body.wf_id}.`);
    window.location.href = `/task/?id=${body.wf_id}`;
  } catch (e) {
    message.error(e.missing ? NOT_AVAILABLE : e.message);
  }
}

const changeText = value => (value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value));

/** Retry buttons of a failed task: suggested fix (after a confirmation of the changes) and a copy of the conditions. */
export function RetryActions({ taskId, errorSummary }) {
  const [busy, setBusy] = React.useState(false);
  const remedy = errorSummary?.remedy;
  const retryWithFix = async () => {
    setBusy(true);
    try {
      const plan = await requestJson(`/task/${taskId}/retry-plan`);
      if (plan.supported === false || !plan.changes) {
        message.info(plan.reason || 'No automatic fix applies to this failure. Use “Rerun”.');
        return;
      }
      Modal.confirm({
        title: `${plan.label || remedy?.label || 'Retry with suggested fix'}?`,
        width: 520,
        content: <div>
          <p>A new task is queued with these changes; the failed task is kept.</p>
          <ul>{Object.entries(plan.changes).map(([key, value]) => <li key={key}><code>{key}</code>{' → '}<code>{changeText(value)}</code></li>)}</ul>
        </div>,
        okText: 'Retry with fix',
        cancelText: 'Cancel',
        onOk: () => retryTask(taskId, true),
      });
    } catch (e) {
      message.error(e.missing ? NOT_AVAILABLE : e.message);
    } finally {
      setBusy(false);
    }
  };
  return <Space wrap>
    <Button type="primary" href={`/?clone_task=${taskId}`}>Rerun</Button>
    {remedy?.label ? <Button loading={busy} onClick={retryWithFix}>{`Retry with suggested fix (${remedy.label})`}</Button> : null}
  </Space>;
}

/** Failed task: what failed, the likely cause and the recommended action; the raw error is under "Details". */
export function TaskFailurePanel({ taskId, errorSummary, errorTb }) {
  // The backend's last traceback line ("ValueError: message") is the plain cause; the class name alone says little.
  const cause = String(errorSummary?.detail || '').split('\n')[0].trim();
  return <section className="dara-failure" role="alert" aria-label="Analysis failed">
    <h3>{errorSummary?.title || 'The analysis failed'}</h3>
    <p>{errorSummary?.hint || 'No cause was recorded. Open Details for the error text, then rerun with the same settings.'}</p>
    {cause ? <p style={{ overflowWrap: 'anywhere' }}>{cause}</p> : null}
    {errorTb ? <details><summary>Details (full traceback)</summary><pre>{errorTb}</pre></details> : null}
    <RetryActions taskId={taskId} errorSummary={errorSummary} />
  </section>;
}
