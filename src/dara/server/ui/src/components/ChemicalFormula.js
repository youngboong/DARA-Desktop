import React from 'react';
import { tidyFormula } from './composition.mjs';

// "P2_12_12_1", "Ia-3", "C12/c1", "P-3c1" or a bare "P212121": a space-group symbol, not a formula.
const isSpaceGroup = text => /[_/-]/.test(text) || /^[PIFCRA]\d+$/.test(text);

function Segment({ text }) {
  if (isSpaceGroup(text)) {
    // Monoclinic full symbols read better in short form: C12/c1 -> C2/c.
    const parts = text.replace(/^([A-Z])1(.+)1$/, '$1$2').split(/(_\d)/g).filter(Boolean);
    return <>{parts.map((part, index) => part.startsWith('_') ? <sub key={index}>{part.slice(1)}</sub> : <React.Fragment key={index}>{part}</React.Fragment>)}</>;
  }
  const parts = tidyFormula(text).split(/(\d+(?:\.\d+)?)/g).filter(Boolean);
  return <>{parts.map((part, index) => /^\d/.test(part) ? <sub key={index}>{part}</sub> : <React.Fragment key={index}>{part}</React.Fragment>)}</>;
}

/** A formula with its digits as subscripts: "Li2CO3" -> Li<sub>2</sub>CO<sub>3</sub>. Plain text in, nodes out.
 *  Labels joined with " · " ("Y2O3 · Ia-3", "Hematite · Fe2O3") are rendered piece by piece; a trailing
 *  " (R-3c)" or " #2" keeps its digits. */
export default function ChemicalFormula({ formula }) {
  const [, head, tail] = /^([\s\S]*?)(\s[#(][\s\S]*)?$/.exec(String(formula || ''));
  const segments = head.split(' · ');
  return <>{segments.map((segment, index) => <React.Fragment key={index}>{index ? ' · ' : ''}<Segment text={segment} /></React.Fragment>)}{tail}</>;
}

const FORMULA_WORD = /^((?:[A-Z][a-z]?\d*(?:\.\d+)?){2,})([.,;:)]*)$/;

/** Server prose ("Ambiguous evidence for Y2O3 · Ia-3.") with formula words set as formulas. */
export function FormulaText({ text }) {
  return <>{String(text ?? '').split(/(\s+)/).map((word, index) => {
    const match = /\d/.test(word) ? FORMULA_WORD.exec(word) : null;
    return match ? <React.Fragment key={index}><ChemicalFormula formula={match[1]} />{match[2]}</React.Fragment> : word;
  })}</>;
}
