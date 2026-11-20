import React from 'react';
import { Button, Checkbox, Modal, Tooltip, Typography } from 'antd';
import { ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { cautionDisplay, cautionsBesideBanner, remedyForCaution } from './fitCautions.mjs';
import ChemicalFormula, { FormulaText } from './ChemicalFormula';

const { Text } = Typography;

/** One reason of a proposed removal: plain text, or `{duplicateOf}` named by `labelOf`. */
const reasonText = (reason, labelOf) => (typeof reason === 'string' ? reason
  : <>same compound as <ChemicalFormula formula={labelOf(reason.duplicateOf)} />, the heavier entry, which stays</>);

/**
 * "Clean up model": every removal the cautions propose, all ticked; one refinement of the rest as
 * one new version (the fit itself is kept). The analyst confirms what goes.
 */
function CleanUpDialog({ open, remedies, labelOf, phases, onCancel, onRun }) {
  const [picked, setPicked] = React.useState([]);
  // Ticked afresh each time it opens (the list is rebuilt on every render of the page).
  React.useEffect(() => { if (open) setPicked(remedies.map(entry => entry.phase)); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const left = phases.length - picked.length;
  return <Modal title="Clean up model" open={open} onCancel={onCancel} width={620}
    okText={`Refine without ${picked.length} phase${picked.length === 1 ? '' : 's'}`}
    okButtonProps={{ disabled: !picked.length || left < 1 }} onOk={() => onRun(picked)}>
    <Typography.Paragraph>The cautions propose removing these phases. The checked ones are removed and the
      remaining {left === 1 ? 'phase is' : `${left} phases are`} refined again as one new version; this fit is kept.</Typography.Paragraph>
    <ul className="dara-cleanup-list">
      {remedies.map(entry => <li key={entry.phase}>
        <Checkbox checked={picked.includes(entry.phase)}
          onChange={event => setPicked(previous => (event.target.checked ? [...previous, entry.phase] : previous.filter(name => name !== entry.phase)))}>
          <ChemicalFormula formula={labelOf(entry.phase)} />
        </Checkbox>
        <div className="dara-cleanup-why">{entry.reasons.map((reason, index) => <React.Fragment key={index}>{index ? '; ' : ''}{reasonText(reason, labelOf)}</React.Fragment>)}</div>
      </li>)}
    </ul>
    {left < 1 ? <Text type="danger">At least one phase must stay in the fit.</Text> : null}
  </Modal>;
}

/**
 * Advisory flags from the automated fit checks, as one compact list; renders nothing when there
 * are none. `besideBanner` leaves out what the confidence banner above already states.
 * `fit` (`cautionDisplay` context) words the cautions; with `onRemove(phases)` a caution that names
 * a removable phase gets "Refit without …", and two or more removals add "Clean up model…".
 * `remedies` are `cautionRemedies` of the same fit; `labelOf(name)` names a phase.
 */
export default function FitCautions({ cautions, style, besideBanner = false, bannerReasons = [], showHidden = false,
  fit = {}, remedies = [], onRemove = null, removeBlocked = null, labelOf = name => name }) {
  const items = cautionsBesideBanner(cautions, besideBanner, bannerReasons);
  // Same total as the fit workspace: the ones the summary banner already states are counted, not repeated.
  const inBanner = showHidden ? (cautions || []).length - items.length : 0;
  const [expanded, setExpanded] = React.useState(false);
  const [cleanUp, setCleanUp] = React.useState(false);
  if (!items.length) return null;
  // Warnings stay listed; informational notes fold away unless there is nothing else.
  const warnings = items.filter(item => item.level === 'warning');
  const notes = items.length - warnings.length;
  const shown = expanded || !warnings.length ? items : warnings;
  const actions = Boolean(onRemove);
  return (
    <section className="dara-caution-list" aria-label="Cautions" style={{ marginTop: 8, ...style }}>
      <div className="dara-caution-head">
        <Tooltip title={`Automated checks, not a verdict${inBanner ? `; ${inBanner} stated in the summary above` : ''}`}>
          <Text strong>{`Cautions (${items.length + inBanner})`}</Text>
        </Tooltip>
        {actions && remedies.length >= 2 ? <Tooltip title={removeBlocked || 'Remove the phases these cautions name, in one refinement'}>
          <span><Button size="small" disabled={Boolean(removeBlocked)} onClick={() => setCleanUp(true)}>Clean up model…</Button></span>
        </Tooltip> : null}
      </div>
      <ul>
        {shown.map((item, index) => {
          const { text, parameters } = cautionDisplay(item, fit);
          // One link per proposed removal, on the first caution that names it.
          const remedy = actions ? remedyForCaution(item, remedies, fit.confidence) : null;
          const firstFor = remedy && shown.findIndex(other => remedyForCaution(other, remedies, fit.confidence)?.phase === remedy.phase) === index;
          return <li key={`${item.code}-${item.phase || ''}-${index}`}>
            {item.level === 'warning'
              ? <ExclamationCircleOutlined aria-label="Warning" style={{ color: 'var(--dara-review)' }} />
              : <InfoCircleOutlined aria-label="Note" style={{ color: 'var(--dara-text-secondary)' }} />}
            <span>
              <FormulaText text={text} />
              {parameters ? <Tooltip title={`BGMN parameters without a value: ${parameters}`}>
                <InfoCircleOutlined aria-label="Undefined BGMN parameters" className="dara-caution-detail" tabIndex={0} />
              </Tooltip> : null}
              {firstFor ? <Tooltip title={removeBlocked || 'Refine the other phases without it, as a new version; this fit is kept'}>
                <Button type="link" size="small" className="dara-caution-action" disabled={Boolean(removeBlocked)} onClick={() => onRemove([remedy.phase])}>
                  Refit without <ChemicalFormula formula={labelOf(remedy.phase)} />
                </Button>
              </Tooltip> : null}
            </span>
          </li>;
        })}
      </ul>
      {warnings.length && notes ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(value => !value)}>
        {expanded ? 'Hide notes' : `Show ${notes} more note${notes === 1 ? '' : 's'}`}</Button> : null}
      {actions ? <CleanUpDialog open={cleanUp} remedies={remedies} labelOf={labelOf} phases={fit.phases || []}
        onCancel={() => setCleanUp(false)} onRun={phases => { setCleanUp(false); onRemove(phases); }} /> : null}
    </section>
  );
}
