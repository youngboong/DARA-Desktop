import React from 'react';
import { Tag, Tooltip } from 'antd';
import { toneColor } from './statusTone.mjs';
import { STATUS_TAGS } from './statusTags.mjs';

/** Status word as a tag (`kind` is a key of STATUS_TAGS, or pass `text`/`tone`); the tooltip is optional. */
export default function StatusTag({ kind, text, tone, tooltip, style }) {
  const base = STATUS_TAGS[kind] || {};
  const tag = <Tag color={toneColor(tone || base.tone)} style={{ marginInlineStart: 6, ...style }}>{text || base.text}</Tag>;
  return tooltip ? <Tooltip title={tooltip}>{tag}</Tooltip> : tag;
}
