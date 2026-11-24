import React, { useEffect, useState } from 'react';
import { Alert, Button, Descriptions, Modal, Progress, Space, Typography } from 'antd';
import { isBusy, refusal, updateStateText } from './appUpdate.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

/** The in-app update dialog (opened from the header): what is new, then download, verify, install. */
export default function UpdateDialog({ open, onClose, status, onStatus }) {
  const [refused, setRefused] = useState(null);
  const [starting, setStarting] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false); // the check failed (not the install): Retry checks again
  const busy = isBusy(status);

  // Opening the dialog asks GitHub again; the header uses the server's 6 h cache.
  const check = () => {
    setCheckFailed(false);
    onStatus({ ...(status || {}), state: 'checking' });
    fetch(`${URL}/update?refresh=1`).then(response => response.json()).then(onStatus)
      .catch(() => { setCheckFailed(true); onStatus({ ...(status || {}), state: 'error', error: 'DARA did not answer. Try again.' }); });
  };
  useEffect(() => {
    if (!open) return;
    setRefused(null);
    if (busy) return;
    check();
  }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  // The download runs on the server; follow it until DARA closes for the installer.
  useEffect(() => {
    if (!open || !busy) return undefined;
    const timer = setInterval(() => {
      fetch(`${URL}/update`).then(response => response.json()).then(onStatus).catch(() => {});
    }, 1000);
    return () => clearInterval(timer);
  }, [open, busy]);  // eslint-disable-line react-hooks/exhaustive-deps

  const install = force => {
    setCheckFailed(false);
    setRefused(null);
    setStarting(true);
    fetch(`${URL}/update/install${force ? '?force=1' : ''}`, { method: 'POST' })
      .then(response => response.json().then(body => (response.ok ? onStatus(body) : setRefused(refusal(body)))))
      .catch(() => setRefused({ message: 'DARA did not answer. Try again.', force: false }))
      .finally(() => setStarting(false));
  };

  const failed = status?.state === 'error';
  const checking = status?.state === 'checking';
  const offer = Boolean(status?.packaged && status.available);
  const published = status?.published_at ? String(status.published_at).slice(0, 10) : null;
  return (
    <Modal title="Update DARA" open={open} onCancel={onClose} footer={null} width={560} maskClosable={!busy}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {offer ? (
          <>
            <Descriptions size="small" column={1} bordered labelStyle={{ width: 140 }}>
              <Descriptions.Item label="Installed">{status.current}</Descriptions.Item>
              <Descriptions.Item label="New version">{status.latest}</Descriptions.Item>
              {published ? <Descriptions.Item label="Released">{published}</Descriptions.Item> : null}
              {status.size_mb ? <Descriptions.Item label="Download">{`${status.size_mb} MB`}</Descriptions.Item> : null}
            </Descriptions>
            {/* Release notes are untrusted text: shown as text, never as HTML. */}
            {status.notes ? <pre className="dara-update-notes">{status.notes}</pre> : null}
          </>
        ) : null}
        {busy || checking || !offer ? (
          <Typography.Text type={!offer && !busy ? 'secondary' : undefined}>{updateStateText(status)}</Typography.Text>
        ) : null}
        {status?.state === 'downloading' ? <Progress percent={status.progress ?? 0} status="active" /> : null}
        {failed && offer ? <Alert type="error" showIcon message={status.error} /> : null}
        {refused ? <Alert type="warning" showIcon message={refused.message} /> : null}
        {offer && !busy && !checking ? (
          <Space>
            <Button type="primary" loading={starting} onClick={() => (checkFailed ? check() : install(false))}>{failed || refused ? 'Retry' : 'Update now'}</Button>
            {refused?.force ? <Button disabled={starting} onClick={() => install(true)}>Update anyway</Button> : null}
          </Space>
        ) : null}
      </Space>
    </Modal>
  );
}
