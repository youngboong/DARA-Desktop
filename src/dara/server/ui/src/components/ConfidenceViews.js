import React from 'react';
import { Alert, Button, Checkbox, Dropdown, Space, Tag, Tooltip } from 'antd';
import { DownloadOutlined, DownOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { FormulaText } from './ChemicalFormula';
import {
  EXPORT_FORMATS, FIGURE_FILES, displacementTag, evidenceLines, exportHref, figureHref, overallAlertClass, overallAlertType, patternHref,
  overallColor, overallShortLabel, patternFiles, reasonsText, screeningTags, verdictColor,
} from './confidence.mjs';

import { stateLabel } from './resultsList.mjs';
import { stateTone, toneColor } from './statusTone.mjs';
import { API } from './QuickRefineProgress';
import { downloadReport } from './ResultWorkflows';

const lines = values => <div style={{ whiteSpace: 'pre-line' }}>{values.map((value, index) => <div key={index}><FormulaText text={value} /></div>)}</div>;

// `format(text)` -> {text, parameters} words server prose for the page (fitCautions.cautionDisplay).
const plainFormat = text => ({ text, parameters: null });
const formatted = (format, values) => values.map(value => format(value).text);

/**
 * Overall banner at the top of a completed result; nothing for tasks without confidence data.
 * `subject` names the fit it judges ("v3 (adopted)"); raw BGMN parameter lists go to a tooltip.
 */
export function ConfidenceBanner({ confidence, action = null, style = null, subject = null, format = plainFormat }) {
  const overall = confidence?.overall;
  if (!overall?.level) return null;
  const reasons = reasonsText(overall).map(format);
  return <Alert
    style={{ marginBottom: 16, ...style }}
    showIcon
    type={overallAlertType(overall.level)}
    className={overallAlertClass(overall.level)}
    message={<>{overall.label || overall.level}{subject ? <span className="dara-banner-subject">{` — ${subject}`}</span> : null}</>}
    description={<>
      {reasons.length ? <ul style={{ margin: 0, paddingLeft: 20 }}>{reasons.map((reason, index) => <li key={index}><FormulaText text={reason.text} />
        {reason.parameters ? <Tooltip title={`BGMN parameters without a value: ${reason.parameters}`}>
          <InfoCircleOutlined aria-label="Undefined BGMN parameters" tabIndex={0} style={{ marginLeft: 6, color: 'var(--dara-text-hint)' }} />
        </Tooltip> : null}</li>)}</ul> : null}
      <Space wrap size="middle" style={{ marginTop: 4 }}>
        {action}
        <a href="/tutorial#result-confidence">How is this assessed?</a>
      </Space>
    </>}
  />;
}

/** Job state: plain text when Completed; a tag only for states that need attention. */
export function StateTag({ state }) {
  const tone = stateTone(state);
  return tone ? <Tag color={toneColor(tone)}>{stateLabel(state)}</Tag> : <span>{stateLabel(state)}</span>;
}

/** Per-phase verdict tag with the evidence behind it in the tooltip. */
export function VerdictTag({ verdict, format = plainFormat }) {
  if (!verdict?.level) return <span>—</span>;
  const content = [...formatted(format, reasonsText(verdict)), ...evidenceLines(verdict.evidence).map(line => `• ${line}`)];
  const tag = <Tag color={verdictColor(verdict.level)}>{verdict.label || verdict.level}</Tag>;
  return content.length ? <Tooltip title={lines(content)}>{tag}</Tooltip> : tag;
}

/** Overall candidate tag for the candidates table. */
export function OverallTag({ confidence, short = false, format = plainFormat }) {
  const overall = confidence?.overall;
  if (!overall?.level) return <span>—</span>;
  const reasons = formatted(format, reasonsText(overall));
  const full = overall.label || overall.level;
  const tag = <Tag color={overallColor(overall.level)}>{short ? overallShortLabel(overall.level) || full : full}</Tag>;
  const tip = short ? [full, ...reasons] : reasons;
  return tip.length ? <Tooltip title={lines(tip)}>{tag}</Tooltip> : tag;
}

/** Specimen-displacement estimate as a coloured tag. */
export function DisplacementTag({ confidence }) {
  const tag = displacementTag(confidence?.sample_displacement);
  if (!tag) return null;
  const element = <Tag color={tag.color}>{tag.text}</Tag>;
  return tag.tooltip ? <Tooltip title={lines([tag.tooltip])}>{element}</Tooltip> : element;
}

/** Tags describing candidate screening (CIFs excluded, computed-only phases, widened window). */
export function ScreeningTags({ candidateSummary }) {
  const tags = screeningTags(candidateSummary);
  if (!tags.length) return null;
  return <Space size={[4, 4]} wrap style={{ marginTop: 4 }}>
    {tags.map(tag => <Tooltip key={tag.key} title={tag.tooltip ? lines([tag.tooltip]) : undefined}>
      <Tag color={tag.color}>{tag.text}</Tag>
    </Tooltip>)}
  </Space>;
}

/**
 * One "Download" dropdown for a result selection: report, figure (PNG/SVG), export for other
 * programs, plus `extraItems` (`{key, label, onClick?}`). `selection` undefined = the adopted fit.
 * `blocked` (a reason) disables the whole menu, e.g. for a quick check; `reportBlocked` only the report.
 * `treatment` (`{kalpha2, smooth}` of the fit plot) adds the measured pattern as displayed to Pattern data.
 */
const HKL_KEY = 'dara-figure-hkl';

export function DownloadMenu({ taskId, selection, size, blocked = null, reportBlocked = null, extraItems = [], label = 'Download', treatment = null }) {
  const [hkl, setHkl] = React.useState(() => { try { return window.localStorage.getItem(HKL_KEY) === '1'; } catch (e) { return false; } });
  const toggleHkl = () => setHkl(on => { try { window.localStorage.setItem(HKL_KEY, on ? '0' : '1'); } catch (e) { /* private mode */ } return !on; });
  const items = [
    { key: 'report-formats', disabled: Boolean(reportBlocked), label: reportBlocked ? `Report (${reportBlocked})` : 'Report',
      children: [{ key: 'report', label: 'PDF + Word' }, { key: 'report-html', label: 'HTML' }] },
    { key: 'figure', label: 'Figure', children: FIGURE_FILES.map(file => ({
      key: file.key, label: <a href={figureHref(API, taskId, file.format, selection, hkl, file.widthMm)}>{file.label}</a> })) },
    { key: 'export', label: 'Export for…', children: EXPORT_FORMATS.map(format => ({
      key: `export:${format.key}`, label: <a href={exportHref(API, taskId, format.key, selection)}>{format.label}</a> })) },
    { key: 'pattern', label: 'Pattern data', children: patternFiles(treatment).map(file => ({
      key: `pattern:${file.key}`, label: <a href={patternHref(API, taskId, file.path, selection)}>{file.label}</a> })) },
    ...extraItems.map(({ onClick, ...item }) => item),
    { type: 'divider' },
    { key: 'hkl', label: <Checkbox checked={hkl}>Miller indices on the figure</Checkbox> },
  ];
  // Ticking "Miller indices" is a setting for the next download: the menu stays open for it.
  const [open, setOpen] = React.useState(false);
  const keepOpen = React.useRef(false);
  const onClick = ({ key }) => {
    if (key === 'report') downloadReport(taskId, selection, 'bundle', hkl);
    if (key === 'report-html') downloadReport(taskId, selection, 'html', hkl);
    // only the close that this click triggers is ignored; a later outside click closes the menu
    if (key === 'hkl') { keepOpen.current = true; setTimeout(() => { keepOpen.current = false; }, 0); toggleHkl(); }
    extraItems.find(item => item.key === key)?.onClick?.();
  };
  const onOpenChange = next => {
    if (!next && keepOpen.current) { keepOpen.current = false; return; }
    setOpen(next);
  };
  const button = <Button size={size} icon={<DownloadOutlined />} disabled={Boolean(blocked)}>{label} <DownOutlined /></Button>;
  if (blocked) return <Tooltip title={blocked}><span>{button}</span></Tooltip>;
  return <Dropdown menu={{ items, onClick }} trigger={['click']} open={open} onOpenChange={onOpenChange}>{button}</Dropdown>;
}
