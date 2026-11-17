import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Modal, Popconfirm, Select, Spin, Typography } from 'antd';
import { API } from './QuickRefineProgress';
import { cagliotiLine, curveRangeText, curveText } from './instrumentCurve.mjs';

const { Text } = Typography;
const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <Spin /> });

const STANDARDS = [{ value: 'LaB6', label: 'LaB6 (Pm-3m)' }, { value: 'Si', label: 'Si (Fd-3m)' }, { value: 'Al2O3', label: 'Al2O3 corundum' }];
const RADIATIONS = ['Cu', 'Co', 'Cr', 'Fe', 'Mo'];
const curveUrl = profile => `${API}/instrument-profiles/${encodeURIComponent(profile)}/fwhm-curve`;
const detail = async (response, fallback) => {
  try { const body = await response.json(); return typeof body.detail === 'string' ? body.detail : fallback; } catch (e) { return fallback; }
};
const esdText = (value, esd) => (value == null ? '—'
  : `${Number(value).toPrecision(4).replace(/^-/, '−')}${esd != null ? ` ± ${Number(esd).toPrecision(2)}` : ''}`);

/** Fitted FWHM of the standard's peaks (with e.s.d.) and the Caglioti line through them. */
function CurvePlot({ curve }) {
  const peaks = curve.peaks || [];
  const data = [{ x: peaks.map(peak => peak.two_theta), y: peaks.map(peak => peak.fwhm_deg), type: 'scatter', mode: 'markers', name: 'Fitted peaks',
    error_y: { type: 'data', array: peaks.map(peak => peak.fwhm_esd_deg), visible: true, thickness: 1, width: 3, color: '#3f6c8f' },
    marker: { size: 7, color: '#3f6c8f' }, hovertemplate: '2θ %{x:.3f}°<br>FWHM %{y:.4f}°<extra></extra>' }];
  if (curve.u != null && curve.two_theta_min != null) {
    const line = cagliotiLine(curve);
    data.push({ x: line.x, y: line.y, type: 'scatter', mode: 'lines', name: 'Caglioti curve', line: { color: '#4a504c', width: 1.5 },
      hovertemplate: '2θ %{x:.1f}°<br>FWHM %{y:.4f}°<extra>Caglioti</extra>' });
  }
  const layout = { height: 230, margin: { l: 64, r: 12, t: 8, b: 40 }, showlegend: false,
    xaxis: { title: { text: '2θ (°)' }, zeroline: false }, yaxis: { title: { text: 'FWHM (°)', standoff: 8 }, automargin: true, zeroline: false } };
  return <Plot data={data} layout={layout} config={{ displaylogo: false, responsive: true }} useResizeHandler style={{ width: '100%', height: 230 }} />;
}

/** Result of a measurement or the stored curve: verdict, plot and the fitted numbers. */
function CurveReport({ curve, fresh }) {
  const verdict = curve.usable
    ? <Alert type="success" showIcon style={{ width: '100%' }} message={fresh ? 'Usable: stored for this profile' : `Stored curve: ${curveText(curve)}`}
      description="Observed-peak sizes and Williamson–Hall of tasks with this profile and radiation use it by default." />
    : <Alert type="warning" showIcon style={{ width: '100%' }} message="Not usable: nothing was stored" description={curve.reason} />;
  const rows = [
    ['U', esdText(curve.u, curve.u_esd)], ['V', esdText(curve.v, curve.v_esd)], ['W', esdText(curve.w, curve.w_esd)],
    ['Peaks used', curve.peak_count ?? '—'], ['RMS (°)', curve.rms_deg == null ? '—' : Number(curve.rms_deg).toFixed(4)],
    ['Range', curveRangeText(curve) || '—'], ['Standard', [curve.standard, curve.wavelength].filter(Boolean).join(' · ')],
    ['Pattern', curve.pattern || '—'], ['Measured on', curve.measured_on || '—'],
  ];
  return <>
    {verdict}
    {(curve.peaks || []).length ? <CurvePlot curve={curve} /> : null}
    <dl className="dara-facts">
      {rows.map(([term, value]) => <React.Fragment key={term}><dt>{term}</dt><dd>{value}</dd></React.Fragment>)}
    </dl>
  </>;
}

/**
 * Instrument FWHM curve of `profile` from a measured line-broadening standard: shows the stored
 * curve, measures a new one (POST, stored when usable) or removes it. `onChange(curve | null)`
 * after a curve was stored or removed.
 */
export function FwhmCurveDialog({ profile, profileLabel = profile, open, onClose, onChange, wavelength = 'Cu' }) {
  const [stored, setStored] = React.useState(undefined); // undefined: loading, null: none
  const [result, setResult] = React.useState(null);
  const [measuring, setMeasuring] = React.useState(false);
  const [standard, setStandard] = React.useState('LaB6');
  const [radiation, setRadiation] = React.useState(RADIATIONS.includes(wavelength) ? wavelength : 'Cu');
  const [file, setFile] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    if (!open || !profile) return undefined;
    let current = true;
    setStored(undefined); setResult(null); setMeasuring(false); setFile(null); setError('');
    fetch(curveUrl(profile))
      .then(async response => {
        if (response.status === 404) return null;
        if (!response.ok) throw new Error(await detail(response, `HTTP ${response.status}`));
        return response.json();
      })
      .then(curve => { if (current) { setStored(curve); setMeasuring(!curve); } })
      .catch(failure => { if (current) { setStored(null); setMeasuring(true); setError(failure.message); } });
    return () => { current = false; };
  }, [open, profile]);
  const measure = async () => {
    if (!file) { setError('Select the measured pattern of the standard.'); return; }
    const data = new FormData();
    data.append('standard', standard); data.append('wavelength', radiation); data.append('pattern_file', file);
    setBusy(true); setError('');
    try {
      const response = await fetch(curveUrl(profile), { method: 'POST', body: data });
      if (!response.ok) throw new Error(await detail(response, 'Check the standard and the pattern file.'));
      const curve = await response.json();
      setResult(curve); setMeasuring(false);
      if (curve.stored) { setStored(curve); onChange?.(curve); }
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  const remove = async () => {
    setBusy(true); setError('');
    try {
      const response = await fetch(curveUrl(profile), { method: 'DELETE' });
      if (!response.ok) throw new Error(await detail(response, `HTTP ${response.status}`));
      setStored(null); setResult(null); setMeasuring(true); onChange?.(null);
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  const shown = result || stored;
  const footer = measuring
    ? [<Button key="cancel" onClick={shown ? () => setMeasuring(false) : onClose}>{shown ? 'Back' : 'Cancel'}</Button>,
      <Button key="measure" type="primary" loading={busy} onClick={measure}>Measure curve</Button>]
    : [stored ? <Popconfirm key="remove" title="Remove the FWHM curve of this profile? Sizes then use a constant FWHM or no correction."
      okText="Remove" okButtonProps={{ danger: true }} onConfirm={remove}><Button danger disabled={busy}>Remove curve</Button></Popconfirm> : null,
    <Button key="again" onClick={() => { setMeasuring(true); setError(''); }}>Measure again…</Button>,
    <Button key="close" type="primary" onClick={onClose}>Close</Button>].filter(Boolean);
  return <Modal title={`Instrument FWHM curve · ${profileLabel}`} open={open} width={600} onCancel={onClose} footer={stored === undefined ? null : footer} destroyOnClose>
    <p className="dara-footnote" style={{ marginBottom: 12 }}>
      A line-broadening standard (LaB6, Si or corundum: large, strain-free crystallites) broadens its peaks by the instrument alone.
      DARA fits its peak widths with the Caglioti curve FWHM² = U·tan²θ + V·tanθ + W and stores the curve for this instrument profile;
      each sample peak is then corrected with the width at its own angle.
    </p>
    {stored === undefined ? <Spin /> : null}
    {measuring ? <div className="dara-toolbar" style={{ alignItems: 'flex-end' }}>
      <label>Standard<br /><Select aria-label="Standard material" value={standard} onChange={setStandard} style={{ width: 160 }} options={STANDARDS} /></label>
      <label>Radiation<br /><Select aria-label="Radiation of the standard pattern" value={radiation} onChange={setRadiation} style={{ width: 90 }}
        options={RADIATIONS.map(value => ({ value, label: value }))} /></label>
      <label>Measured pattern of the standard<br /><input aria-label="Standard pattern file" type="file" onChange={event => setFile(event.target.files[0] || null)} /></label>
    </div> : null}
    {!measuring && shown ? <CurveReport curve={shown} fresh={Boolean(result)} /> : null}
    {result && !result.usable && stored ? <Text type="secondary" className="dara-footnote">{`The stored curve (${curveText(stored)}) is unchanged.`}</Text> : null}
    {error ? <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} /> : null}
  </Modal>;
}

/** Button that opens the curve dialog of `profile` (the profile list shows which profiles have a curve). */
export function FwhmCurveButton({ profile, profileLabel, wavelength, onChange, size = 'middle', disabled = false }) {
  const [open, setOpen] = React.useState(false);
  return <>
    <Button size={size} disabled={disabled || !profile} onClick={() => setOpen(true)}>
      FWHM curve…
    </Button>
    <FwhmCurveDialog profile={profile} profileLabel={profileLabel} open={open} onClose={() => setOpen(false)} onChange={onChange} wavelength={wavelength} />
  </>;
}
