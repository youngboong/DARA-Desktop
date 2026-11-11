import React from 'react';
import { Alert, Button, Input, Space, Tag, Tooltip, Typography, Upload } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { API, requestJson } from './QuickRefineProgress';
import ChemicalFormula from './ChemicalFormula';

const { Text } = Typography;

/**
 * "Add a known phase" in the Add phase drawer: search the reference library and the COD index by
 * formula or COD id, or upload a CIF to the library; Add refines it into the displayed fit as a
 * new version (through `onAdd`, as for peak-search candidates).
 */
export default function ReferenceSearch({ onAdd, busy = false }) {
  const [query, setQuery] = React.useState('');
  const [items, setItems] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [adding, setAdding] = React.useState(null);
  const [error, setError] = React.useState('');

  const search = async value => {
    const text = (value || '').trim();
    setQuery(text);
    if (!text) { setItems(null); return; }
    setLoading(true); setError('');
    try {
      setItems((await requestJson(`/reference-search?q=${encodeURIComponent(text)}`)).items || []);
    } catch (e) { setError(e.message); setItems(null); }
    finally { setLoading(false); }
  };

  const add = async item => {
    setAdding(item.source); setError('');
    try {
      // A COD entry outside the local cache is downloaded first (quick refinements read the cache).
      if (item.source.startsWith('cod:') && !item.cached) {
        await requestJson(`/reference-search/cod/${item.database_id}`, { method: 'POST' });
      }
      onAdd({ ref: item.ref, source: item.source, formula: item.formula, spacegroup_number: item.spacegroup_number });
    } catch (e) { setError(e.message); }
    finally { setAdding(null); }
  };

  const upload = async ({ file, onSuccess, onError }) => {
    setError('');
    const form = new FormData();
    form.append('file', file);
    try {
      const response = await fetch(`${API}/reference-cifs`, { method: 'POST', body: form });
      const body = await response.json().catch(() => ({}));
      let item = body;
      if (response.status === 409 && body.id) {
        // Already in the library: use the stored entry.
        item = ((await requestJson('/reference-cifs')).items || []).find(entry => entry.id === body.id);
      } else if (!response.ok) {
        throw new Error(body.detail || body.message || `HTTP ${response.status}`);
      }
      onSuccess?.(item);
      const stem = String(item.filename || item.id).replace(/\.cif$/i, '');
      onAdd({ ref: stem, source: `library:${item.id}`, formula: item.formula, spacegroup_number: item.spacegroup_number });
    } catch (e) { setError(e.message); onError?.(e); }
  };

  return <div aria-label="Add a known phase" style={{ marginBottom: 16 }}>
    <Typography.Title level={5} style={{ marginTop: 0 }}>Add a known phase</Typography.Title>
    <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
      <Input.Search placeholder="Formula (Fe2O3) or COD id" allowClear enterButton="Search" loading={loading}
        aria-label="Search references by formula or COD id" onSearch={search} />
    </Space.Compact>
    <Upload accept=".cif" showUploadList={false} customRequest={upload} disabled={busy}>
      <Button size="small" icon={<UploadOutlined />} disabled={busy}>Upload CIF and add…</Button>
    </Upload>
    {error ? <Alert type="error" showIcon style={{ marginTop: 8 }} message={error} /> : null}
    {items && !items.length ? <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>{`No reference found for ${query}.`}</Text> : null}
    {items?.length ? <div style={{ marginTop: 8 }}>
      {items.map(item => <div key={item.source} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: '1px solid var(--dara-border)' }}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <strong><ChemicalFormula formula={item.formula || item.ref} /></strong>
          <Text type="secondary">{` · ${item.spacegroup_number ? `SG ${item.spacegroup_number}` : 'SG ?'} · ${item.database} ${item.database_id || ''}`}</Text>
          {item.e_hull != null ? <Tooltip title="Energy above hull (eV/atom) of the matching Materials Project entry"><Text type="secondary">{` · E_hull ${Number(item.e_hull).toFixed(3)}`}</Text></Tooltip> : null}
          {item.source.startsWith('cod:') && !item.cached ? <Tooltip title="Downloaded from crystallography.net when added"><Tag style={{ marginLeft: 6 }}>download</Tag></Tooltip> : null}
        </span>
        <Button size="small" loading={adding === item.source} disabled={busy || (adding && adding !== item.source)} onClick={() => add(item)}>Add</Button>
      </div>)}
    </div> : null}
  </div>;
}
