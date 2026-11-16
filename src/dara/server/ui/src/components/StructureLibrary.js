import React, { useEffect, useState } from 'react';
import { Alert, Button, Descriptions, Input, Modal, Progress, Space, Spin, Typography } from 'antd';
import {
  cachedText, durationText, jobOutcome, parseElements, phaseLabel, progressPercent, progressText, sizeText,
} from './structureLibrary.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

function EstimateLines({ estimate, status }) {
  if (!estimate) return null;
  return (
    <Descriptions size="small" column={1} bordered>
      <Descriptions.Item label="Structures">
        {`COD ${estimate.counts.cod.toLocaleString('en-US')} · Materials Project ${estimate.mp_estimated ? '~' : ''}${estimate.counts.mp.toLocaleString('en-US')}`}
      </Descriptions.Item>
      <Descriptions.Item label="Download">{sizeText(estimate.download_mb.total)}</Descriptions.Item>
      <Descriptions.Item label="Disk space">{sizeText(estimate.disk_mb.total)}</Descriptions.Item>
      <Descriptions.Item label="Expected time">{durationText(estimate.minutes.total)}</Descriptions.Item>
      {status ? (
        <Descriptions.Item label="On this computer">
          {cachedText('COD', status.cod)}<br />{cachedText('Materials Project', status.mp)}
        </Descriptions.Item>
      ) : null}
    </Descriptions>
  );
}

/** "Structure library" button and modal: pre-download pack structures for offline use. */
export default function StructureLibrary() {
  const [open, setOpen] = useState(false);
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState(null);
  const [customText, setCustomText] = useState('');
  const [job, setJob] = useState(null);
  const custom = parseElements(customText);

  const load = (elements = []) => fetch(`${URL}/structure-library${elements.length ? `?elements=${elements.join(',')}` : ''}`)
    .then(response => response.json().then(data => (response.ok ? data : Promise.reject(new Error(data.detail || 'Unavailable')))))
    .then(data => { setOverview(data); setJob(data.job); setError(null); })
    .catch(reason => setError(String(reason.message || reason)));

  useEffect(() => { if (open) load(); }, [open]);

  // While a download runs, poll its progress (the job lives on the server: a refresh shows it again).
  useEffect(() => {
    if (!open || !job?.running) return undefined;
    const timer = setInterval(() => {
      fetch(`${URL}/structure-library/progress`).then(response => response.json()).then(data => {
        setJob(data.job);
        if (!data.job?.running) load(custom);
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [open, job?.running]);

  const post = (path, body) => fetch(`${URL}/structure-library/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  }).then(response => response.json().then(data => (response.ok ? data : Promise.reject(new Error(data.detail || 'Failed')))))
    .then(data => { setJob(data.job); setError(null); })
    .catch(reason => setError(String(reason.message || reason)));

  const pack = overview?.packs?.[0];
  const running = Boolean(job?.running);
  const line = job?.last;
  const percent = progressPercent(line);
  const outcome = jobOutcome(job);
  return (
    <>
      <Button type="link" size="small" onClick={() => setOpen(true)} style={{ padding: 0 }}>Structure library</Button>
      <Modal title="Structure library" open={open} onCancel={() => setOpen(false)} footer={null} width={640}>
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Download reference structures ahead of time: phase searches then work offline and the search-match needs no
            simulation. Downloads resume where they stopped; nothing changes until you download.
          </Typography.Paragraph>
          {error ? <Alert type="error" showIcon message={error} /> : null}
          {!overview && !error ? <Spin /> : null}
          {overview && !overview.mp_configured ? (
            <Alert type="warning" showIcon message="Materials Project is not configured: only COD structures can be downloaded." />
          ) : null}
          {pack ? (
            <>
              <Typography.Text strong>{pack.name}</Typography.Text>
              <Typography.Text type="secondary">{pack.elements.join(' ')}</Typography.Text>
              <EstimateLines estimate={pack.estimate} status={pack.status} />
              <Button type="primary" disabled={running} onClick={() => post('download', { packs: [pack.id] })}>Download</Button>
            </>
          ) : null}
          {overview ? (
            <>
              <Typography.Text strong>Custom elements</Typography.Text>
              <Space.Compact style={{ width: '100%' }}>
                <Input placeholder="e.g. Li Co Ni Mn O" value={customText} onChange={event => setCustomText(event.target.value)}
                  onPressEnter={() => custom.length && load(custom)} aria-label="Custom elements" />
                <Button disabled={!custom.length} onClick={() => load(custom)}>Estimate</Button>
                <Button disabled={!custom.length || running} onClick={() => post('download', { elements: custom })}>Download</Button>
              </Space.Compact>
              {overview.custom ? <EstimateLines estimate={overview.custom.estimate} status={overview.custom.status} /> : null}
            </>
          ) : null}
          {running || outcome ? (
            <div>
              {running ? (
                <>
                  <Typography.Text>{phaseLabel(line?.phase)}</Typography.Text>
                  <Progress percent={percent ?? 0} status={job.cancelling ? 'exception' : 'active'} />
                  <Typography.Text type="secondary">{progressText(line)}</Typography.Text>
                  <div style={{ marginTop: 8 }}>
                    <Button danger disabled={job.cancelling} onClick={() => post('cancel')}>
                      {job.cancelling ? 'Stopping...' : 'Cancel'}
                    </Button>
                  </div>
                </>
              ) : <Alert type={outcome.type} showIcon message={outcome.text} />}
            </div>
          ) : null}
        </Space>
      </Modal>
    </>
  );
}
