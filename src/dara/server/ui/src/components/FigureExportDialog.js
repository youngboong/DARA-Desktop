import React from 'react';
import { Alert, Button, Modal, Radio, Spin } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { FIGURE_STYLE_DEFAULT, readFigureStyle, writeFigureStyle } from './figureExport.mjs';

const storage = () => {
  try {
    return window.localStorage;
  } catch (e) {
    return null;
  }
};

/** The figure look of this browser (`[style, setStyle]`), remembered across pages and visits. */
export function useFigureStyle() {
  const [style, setStyle] = React.useState(FIGURE_STYLE_DEFAULT);
  React.useEffect(() => setStyle(readFigureStyle(storage())), []);
  const update = React.useCallback(next => {
    setStyle(next);
    writeFigureStyle(storage(), next);
  }, []);
  return [style, update];
}

const FORMAT_LABELS = { png: 'PNG', svg: 'SVG', pdf: 'PDF' };

function Choice({ label, value, options, onChange }) {
  return <div className="dara-figure-choice">
    <span className="dara-figure-choice-label">{label}</span>
    <Radio.Group size="small" optionType="button" buttonStyle="solid" value={value} aria-label={label}
      onChange={event => onChange(event.target.value)} options={options} />
  </div>;
}

/**
 * "Figure…" of a Download menu: the phase-identification figure's look (peak labels, phase names,
 * reference bars), its width and file format, with the figure itself as preview. `hrefOf(style,
 * download)` is the figure URL; the preview is its SVG.
 */
export default function FigureExportDialog({ open, onClose, style, onStyle, hrefOf, title = 'Figure' }) {
  const src = hrefOf({ ...style, format: 'svg' }, false);
  const [shown, setShown] = React.useState({ src: null, failed: false });
  const set = key => value => onStyle({ ...style, [key]: value });
  const loading = shown.src !== src;
  // Focus leaves the dialog before it is hidden (aria-hidden on a focused element is refused by the browser).
  const close = () => { document.activeElement?.blur?.(); onClose(); };
  return <Modal title={title} open={open} onCancel={close} width={960} centered destroyOnClose
    footer={[
      <Button key="close" onClick={close}>Close</Button>,
      <Button key="download" type="primary" icon={<DownloadOutlined />} href={hrefOf(style, true)}>
        {`Download ${FORMAT_LABELS[style.format]}`}</Button>,
    ]}>
    <div className="dara-figure-export">
      <div>
        {shown.src === src && shown.failed ? <Alert type="error" showIcon message="The figure could not be drawn." /> : null}
        <div className="dara-figure-preview" aria-busy={loading}>
          {open ? <img key={src} src={src} alt="Preview of the figure"
            onLoad={() => setShown({ src, failed: false })} onError={() => setShown({ src, failed: true })} /> : null}
          {loading ? <div className="dara-figure-preview-wait"><Spin /></div> : null}
        </div>
      </div>
      <div className="dara-figure-choices">
        <Choice label="Peak labels" value={style.peaks} onChange={set('peaks')}
          options={[{ value: 'hkl', label: 'hkl' }, { value: 'symbols', label: 'Symbols' }, { value: 'none', label: 'None' }]} />
        <Choice label="Reference bars" value={style.bars} onChange={set('bars')}
          options={[{ value: true, label: 'Shown' }, { value: false, label: 'Hidden' }]} />
        <Choice label="Phase names" value={style.bars ? style.names : 'legend'} onChange={set('names')}
          options={[{ value: 'legend', label: 'Legend' }, { value: 'strips', label: 'On the bars', disabled: !style.bars }]} />
        <Choice label="Width" value={style.widthMm} onChange={set('widthMm')}
          options={[{ value: 85, label: '85 mm' }, { value: 170, label: '170 mm' }]} />
        <Choice label="Format" value={style.format} onChange={set('format')}
          options={[{ value: 'png', label: 'PNG 600 dpi' }, { value: 'svg', label: 'SVG' }, { value: 'pdf', label: 'PDF' }]} />
      </div>
    </div>
  </Modal>;
}
