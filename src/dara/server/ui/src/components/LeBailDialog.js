import React from 'react';
import { Alert, Modal, Radio, Space, Typography } from 'antd';
import StatusTag from './StatusTag';
import { leBailRequest } from './versions.mjs';

const { Text } = Typography;

/**
 * "Le Bail fit of a phase…": choose one phase of the displayed fit to fit with free reflection
 * intensities (or, for one that already is, to return to Rietveld) as a new version.
 * `leBail` are the displayed fit's Le Bail phases; `onSubmit(request)` queues the quick refinement.
 */
export default function LeBailDialog({ open, phases = [], leBail = [], initial = null, renderPhase, busy, onCancel, onSubmit }) {
  const [phase, setPhase] = React.useState(null);
  React.useEffect(() => {
    if (open) setPhase(initial && phases.includes(initial) ? initial : leBail[0] || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial]);
  const back = phase !== null && leBail.includes(phase);
  const others = leBail.filter(name => name !== phase);
  return <Modal title="Le Bail fit of a phase" open={open} width={560} onCancel={onCancel} destroyOnClose
    okText={back ? 'Back to Rietveld as new version' : 'Refine as new version'} okButtonProps={{ disabled: !phase || busy }}
    onOk={() => onSubmit(leBailRequest(phase, leBail))}>
    <p style={{ marginTop: 0 }}>Reflection intensities are fitted freely: the cell and profile are refined, the phase gets no
      weight fraction. For a phase whose structure model is unreliable.</p>
    <Radio.Group value={phase} onChange={event => setPhase(event.target.value)} aria-label="Phase">
      <Space direction="vertical" size={6}>
        {phases.map(name => <Radio key={name} value={name}>
          {renderPhase ? renderPhase(name) : name}
          {leBail.includes(name) ? <StatusTag text="Le Bail" tooltip="Fitted by Le Bail in this fit; choose it to return it to Rietveld" /> : null}
        </Radio>)}
      </Space>
    </Radio.Group>
    <Text type="secondary" className="dara-footnote" style={{ marginTop: 12 }}>
      Only one phase per fit can be Le Bail (BGMN). The weight fractions of the other phases become relative to each other.
    </Text>
    {others.length && !back ? <Alert type="info" showIcon style={{ marginTop: 8 }}
      message={<>{renderPhase ? renderPhase(others[0]) : others[0]} returns to Rietveld in the new version.</>} /> : null}
  </Modal>;
}
