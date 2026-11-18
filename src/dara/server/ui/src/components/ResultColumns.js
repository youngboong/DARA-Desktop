import React from 'react';
import { Tag, Tooltip } from 'antd';
import ChemicalFormula from './ChemicalFormula';
import { StateTag } from './ConfidenceViews';
import { toneColor } from './statusTone.mjs';
import {
  adoptedLabel, adoptedRwp, displayName, formatDate, formatFixed, isIdentificationEntry, middleSplit, phasesTooltip,
  rowStatusLine, verdictView,
} from './resultsList.mjs';

// Results, Series (and any list of tasks) share these cells: one meaning, one form.

/** Text that keeps its distinguishing end when it does not fit ("Fe2O3_long…--8min.xrdml"). */
export function MiddleText({ text, className = '' }) {
  const [head, tail] = middleSplit(text);
  return <span className={`dara-mid ${className}`}><span className="dara-mid-head">{head}</span>
    {tail ? <span className="dara-mid-tail">{tail}</span> : null}</span>;
}

// Failed and cancelled rows and searches without a fit have no metrics: their cells are dimmed.
const noResult = entry => ['FIZZLED', 'CANCELLED'].includes(entry.state) || verdictView(entry)?.text === 'No fit';
const metricCell = entry => ({ className: noResult(entry) ? 'dara-dim' : '' });

/**
 * Sample name first, the file as secondary text (middle-ellipsised), then the adoption mark, the
 * rerun source and the series. Unfinished rows add one line: stage and percent, queue position or
 * the failure title.
 */
export function NameCell({ entry, href, sourceHref = id => `/task/?id=${id}` }) {
  const { primary, secondary } = displayName(entry);
  const adopted = adoptedLabel(entry.adopted ?? entry.summary?.adopted);
  const status = rowStatusLine(entry);
  const source = entry.source_task_id;
  return <div className="dara-name-cell">
    <div className="dara-row-name">
      <Tooltip title={secondary ? <>{primary}<br />{secondary}</> : primary} placement="topLeft" mouseEnterDelay={0.5}>
        <a href={href} className="dara-row-primary">{secondary ? primary : <MiddleText text={primary} />}</a>
      </Tooltip>
      {secondary ? <MiddleText text={secondary} className="dara-row-file" /> : null}
      {adopted ? <Tooltip title={adopted.title}><span className="dara-adopted-mark">{adopted.text}</span></Tooltip> : null}
      {source != null ? <a className="dara-row-meta" href={sourceHref(source)}>{`rerun of #${source}`}</a> : null}
      {entry.series?.id ? <a className="dara-row-meta" href={`/series/?id=${encodeURIComponent(entry.series.id)}`}>
        {`${entry.series.name} #${(entry.series.order ?? 0) + 1}`}</a> : null}
    </div>
    {status ? <div className={`dara-row-status dara-row-status-${status.tone}`}>
      {status.percent !== null && status.percent !== undefined
        ? <span className="dara-row-progress" aria-hidden="true"><i style={{ width: `${status.percent}%` }} /></span> : null}
      {status.text}
    </div> : null}
  </div>;
}

/** Verdict of a completed task (High · Check · Expert review · No fit); otherwise the job state. */
export function VerdictCell({ entry }) {
  const verdict = verdictView(entry);
  if (verdict) {
    return <Tooltip title={`${verdict.title}. Open the task for the reasons.`}>
      <Tag color={toneColor(verdict.tone)}>{verdict.text}</Tag></Tooltip>;
  }
  if (entry.state === 'COMPLETED' && isIdentificationEntry(entry)) {
    return <Tooltip title="Phases accepted from a search-match without refinement: no Rwp, amounts by RIR."><Tag>Identification</Tag></Tooltip>;
  }
  return <StateTag state={entry.state} />;
}

/** Up to three phases as formulas (space groups in the shared format), the full list in the tooltip. */
export function PhasesCell({ entry, limit = 3 }) {
  const labels = Array.isArray(entry.summary?.final_phase_labels) ? entry.summary.final_phase_labels : [];
  if (!labels.length) return noResult(entry) || entry.state !== 'COMPLETED' ? null : '—';
  const hidden = labels.length - limit;
  return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phasesTooltip(entry.summary)}</div>}>
    <span className="dara-row-phases">
      {labels.slice(0, limit).map((item, index) => <React.Fragment key={index}>{index ? ', ' : ''}<ChemicalFormula formula={item} /></React.Fragment>)}
      {hidden > 0 ? `, … (+${hidden})` : ''}
    </span>
  </Tooltip>;
}

/**
 * Column definitions by name. `hrefOf(entry)` is the task link; `sorter(key)` adds server-side
 * sorting props (Results), or nothing (Series).
 */
export function resultColumns({ hrefOf, sourceHref, sorter = () => ({}) }) {
  return {
    id: { title: 'ID', dataIndex: 'fw_id', key: 'fw_id', width: 60, className: 'dara-num' },
    name: {
      title: 'Name', key: 'name', width: '27%', ...sorter('name'),
      render: (_, entry) => <NameCell entry={entry} href={hrefOf(entry)} sourceHref={sourceHref} />,
    },
    verdict: { title: 'Verdict', key: 'verdict', width: 118, ...sorter('verdict'), render: (_, entry) => <VerdictCell entry={entry} /> },
    rwp: {
      title: <Tooltip title="Rwp of the adopted fit, or of the automatic fit when nothing is adopted"><span>Rwp (%)</span></Tooltip>,
      key: 'rwp', width: 84, className: 'dara-num', onCell: metricCell, ...sorter('rwp'),
      render: (_, entry) => (noResult(entry) || entry.state !== 'COMPLETED' ? null : formatFixed(adoptedRwp(entry.summary))),
    },
    gof: {
      title: <Tooltip title="Goodness of fit = Rwp / Rexp of the adopted fit; near 1 is ideal for counting statistics."><span>GOF</span></Tooltip>,
      key: 'gof', width: 70, className: 'dara-num', onCell: metricCell, ...sorter('gof'),
      render: (_, entry) => (noResult(entry) || entry.state !== 'COMPLETED' ? null : formatFixed(entry.summary?.gof)),
    },
    phases: { title: 'Final phases', key: 'phases', onCell: metricCell, render: (_, entry) => <PhasesCell entry={entry} /> },
    submitted: {
      title: 'Submitted', key: 'submitted', dataIndex: 'created_on', width: 136, className: 'dara-nowrap', ...sorter('submitted'),
      render: value => formatDate(value) || '—',
    },
    user: { title: 'User', dataIndex: 'user', key: 'user', width: 96, ellipsis: true },
  };
}
