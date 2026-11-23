import React from 'react';
import { tidyFormula } from './composition.mjs';
import { spaceGroupParts } from './versions.mjs';

// "P2_12_12_1", "Ia-3", "C12/c1", "P-3c1" or a bare "P212121": a space-group symbol, not a formula.
// Also one already written by formatSpaceGroup ("P2₁2₁2₁": subscript digits).
// A formula never has a lower-case letter after a digit or two in a row: "P63mc", "Pbcm" are symbols.
const isSpaceGroup = text => /[_/\-₀-₉]/.test(text) || /^[PIFCRA]\d+$/.test(text) || /\d[a-z]|[a-z]{2}/.test(text);

function Segment({ text }) {
  // A database record ("COD 1010912") is plain text: its digits are not counts.
  if (/\s/.test(text)) return <>{text}</>;
  if (isSpaceGroup(text)) return <SpaceGroupSymbol symbol={text} />;
  const parts = tidyFormula(text).split(/(\d+(?:\.\d+)?)/g).filter(Boolean);
  return <>{parts.map((part, index) => /^\d/.test(part) ? <sub key={index}>{part}</sub> : <React.Fragment key={index}>{part}</React.Fragment>)}</>;
}

const OVERBAR = { textDecoration: 'overline' };

/** A space-group symbol as International Tables print it: italic letters, subscript screw
 *  components, overbars on rotoinversions (Fd-3m -> Fd3̄m, P6_3mc -> P6₃mc, C12/c1 -> C2/c). */
export function SpaceGroupSymbol({ symbol }) {
  return <span>{spaceGroupParts(symbol).map((part, index) => {
    if (part.kind === 'italic') return <i key={index}>{part.text}</i>;
    if (part.kind === 'screw') return <sub key={index}>{part.text}</sub>;
    if (part.kind === 'bar') return <span key={index} style={OVERBAR}>{part.text}</span>;
    return <React.Fragment key={index}>{part.text}</React.Fragment>;
  })}</span>;
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
