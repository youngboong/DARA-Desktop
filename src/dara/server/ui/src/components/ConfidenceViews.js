import React from 'react';
import { Alert, Button, Checkbox, Dropdown, Space, Tag, Tooltip } from 'antd';
import { DownloadOutlined, DownOutlined } from '@ant-design/icons';
import { FormulaText } from './ChemicalFormula';
import {
  EXPORT_FORMATS, displacementTag, evidenceLines, exportHref, figureHref, overallAlertType,
  overallColor, overallShortLabel, reasonsText, screeningTags, verdictColor,
} from './confidence.mjs';

import { stateLabel } from './resultsList.mjs';
import { stateTone, toneColor } from './statusTone.mjs';
import { API } from './QuickRefineProgress';
import { downloadReport } from './ResultWorkflows';

const lines = values => <div style={{ whiteSpace: 'pre-line' }}>{values.map((value, index) => <div key={index}><FormulaText text={value} /></div>)}</div>;

/**
 * Overall banner at the top of a completed result; nothing for tasks without confidence data.
 */
export function ConfidenceBanner({ confidence, action = null, style = null }) {
  const overall = confidence?.overall;
  if (!overall?.level) return null;
  const reasons = reasonsText(overall);
  return <Alert
    style={{ marginBottom: 16, ...style }}
    showIcon
    type={overallAlertType(overall.level)}
    message={overall.label || overall.level}
    description={<>
      {reasons.length ? <ul style={{ margin: 0, paddingLeft: 20 }}>{reasons.map((reason, index) => <li key={index}><FormulaText text={reason} /></li>)}</ul> : null}
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
export function VerdictTag({ verdict }) {
  if (!verdict?.level) return <span>—</span>;
  const content = [...reasonsText(verdict), ...evidenceLines(verdict.evidence).map(line => `• ${line}`)];
  const tag = <Tag color={verdictColor(verdict.level)}>{verdict.label || verdict.level}</Tag>;
  return content.length ? <Tooltip title={lines(content)}>{tag}</Tooltip> : tag;
}

/** Overall candidate tag for the candidates table. */
export function OverallTag({ confidence, short = false }) {
  const overall = confidence?.overall;
  if (!overall?.level) return <span>—</span>;
  const reasons = reasonsText(overall);
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
 */
const HKL_KEY = 'dara-figure-hkl';

export function DownloadMenu({ taskId, selection, size, blocked = null, reportBlocked = null, extraItems = [], label = 'Download' }) {
  const [hkl, setHkl] = React.useState(() => { try { return window.localStorage.getItem(HKL_KEY) === '1'; } catch (e) { return false; } });
  const toggleHkl = () => setHkl(on => { try { window.localStorage.setItem(HKL_KEY, on ? '0' : '1'); } catch (e) { /* private mode */ } return !on; });
  const items = [
    { key: 'report', disabled: Boolean(reportBlocked), label: reportBlocked ? `Report (${reportBlocked})` : 'Report (PDF + Word)' },
    { key: 'report-html', disabled: Boolean(reportBlocked), label: 'Report (HTML)' },
    { key: 'png', label: <a href={figureHref(API, taskId, 'png', selection, hkl)}>Figure (PNG)</a> },
    { key: 'svg', label: <a href={figureHref(API, taskId, 'svg', selection, hkl)}>Figure (SVG)</a> },
    { key: 'export', label: 'Export for…', children: EXPORT_FORMATS.map(format => ({
      key: `export:${format.key}`, label: <a href={exportHref(API, taskId, format.key, selection)}>{format.label}</a> })) },
    ...extraItems.map(({ onClick, ...item }) => item),
    { type: 'divider' },
    { key: 'hkl', label: <Checkbox checked={hkl}>Miller indices on the figure</Checkbox> },
  ];
  const onClick = ({ key }) => {
    if (key === 'report') downloadReport(taskId, selection, 'bundle', hkl);
    if (key === 'report-html') downloadReport(taskId, selection, 'html', hkl);
    if (key === 'hkl') toggleHkl();
    extraItems.find(item => item.key === key)?.onClick?.();
  };
  const button = <Button size={size} icon={<DownloadOutlined />} disabled={Boolean(blocked)}>{label} <DownOutlined /></Button>;
  if (blocked) return <Tooltip title={blocked}><span>{button}</span></Tooltip>;
  return <Dropdown menu={{ items, onClick }} trigger={['click']}>{button}</Dropdown>;
}
