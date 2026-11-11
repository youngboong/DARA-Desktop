import React from 'react';
import { Button, Tooltip, Typography } from 'antd';
import { ExclamationCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { cautionsBesideBanner } from './fitCautions.mjs';
import { FormulaText } from './ChemicalFormula';

const { Text } = Typography;

/**
 * Advisory flags from the automated fit checks, as one compact list; renders nothing when there
 * are none. `besideBanner` leaves out what the confidence banner above already states.
 */
export default function FitCautions({ cautions, style, besideBanner = false, bannerReasons = [], showHidden = false }) {
  const items = cautionsBesideBanner(cautions, besideBanner, bannerReasons);
  // Same total as the fit workspace: the ones the summary banner already states are counted, not repeated.
  const inBanner = showHidden ? (cautions || []).length - items.length : 0;
  const [expanded, setExpanded] = React.useState(false);
  if (!items.length) return null;
  // Warnings stay listed; informational notes fold away unless there is nothing else.
  const warnings = items.filter(item => item.level === 'warning');
  const notes = items.length - warnings.length;
  const shown = expanded || !warnings.length ? items : warnings;
  return (
    <section className="dara-caution-list" aria-label="Cautions" style={{ marginTop: 8, ...style }}>
      <Tooltip title="Automated checks, not a verdict">
        <Text strong>{`Cautions (${items.length + inBanner})`}</Text>
      </Tooltip>
      {inBanner ? <Text type="secondary">{` · ${inBanner} stated in the summary above`}</Text> : null}
      <ul>
        {shown.map((item, index) => (
          <li key={`${item.code}-${item.phase || ''}-${index}`}>
            {item.level === 'warning'
              ? <ExclamationCircleOutlined aria-label="Warning" style={{ color: 'var(--dara-review)' }} />
              : <InfoCircleOutlined aria-label="Note" style={{ color: 'var(--dara-text-secondary)' }} />}
            <span><FormulaText text={item.message} /></span>
          </li>
        ))}
      </ul>
      {warnings.length && notes ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setExpanded(value => !value)}>
        {expanded ? 'Hide notes' : `Show ${notes} more note${notes === 1 ? '' : 's'}`}</Button> : null}
    </section>
  );
}
