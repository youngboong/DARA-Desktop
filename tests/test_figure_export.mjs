import assert from 'node:assert/strict';
import { FIGURE_COLORS, exportOptions, publicationFigure, radiationLabel } from '../src/dara/server/ui/src/components/figureExport.mjs';

assert.equal(radiationLabel({ wavelength: 'Cu', wavelength_angstrom: 1.540598 }), 'Cu Kα');
assert.equal(radiationLabel({ wavelength_angstrom: 1.78901 }), 'Co Kα');
assert.equal(radiationLabel({ wavelength: 0.07293, wavelength_angstrom: 0.7293 }), 'λ = 0.7293 Å');
assert.equal(radiationLabel({}), null);

{
  // A Rietveld fit view: observed, calculated, background, difference, a Bragg row, a peak marker.
  const data = [
    { name: 'Observed', x: [1, 2], y: [1, 2], mode: 'markers', marker: { color: 'rgba(0,0,0,.75)' } },
    { name: 'Calculated', x: [1, 2], y: [1, 2], line: { color: '#d4380d' } },
    { name: 'Background', x: [1, 2], y: [0, 0], line: { dash: 'dash' }, opacity: 0.8 },
    { name: 'Difference', meta: 'difference', xaxis: 'x2', yaxis: 'y2', x: [1, 2], y: [0, 0] },
    { name: 'TiO2 (76 wt%)', meta: 'reflection', legendgroup: 'T', x: [1.5], y: [-1], marker: { symbol: 142, color: '#3f6c8f' } },
    { name: 'Unexplained peaks', meta: 'diagnostic', x: [1.2], y: [0] },
  ];
  const layout = {
    title: { text: 'xrd_data.xy (Rwp=6.8%)' }, meta: { wavelength: 'Cu', wavelength_angstrom: 1.540598, counts: true },
    xaxis: { range: [1, 2], showgrid: true }, yaxis: { range: [-2, 3], gridcolor: '#eee' }, xaxis2: {}, yaxis2: { title: 'Obs − calc' },
    annotations: [{ xref: 'paper', text: 'TiO2', font: { color: '#3f6c8f', size: 11 } },
      { xref: 'x', textangle: -90, text: '(101)', font: { color: '#3f6c8f', size: 11 } }],
    shapes: [{ type: 'rect', fillcolor: 'rgba(130,130,130,0.15)' }, { type: 'line' }],
  };
  const figure = publicationFigure(data, layout, { widthMm: 85 });
  assert.equal(figure.data.length, 5, 'peak markers are screen-only');
  const by = name => figure.data.find(trace => trace.name === name);
  assert.equal(by('Observed').marker.color, '#000000');
  assert.equal(by('Calculated').line.color, '#D7191C');
  assert.equal(by('Background').line.dash, undefined, 'solid grey background');
  assert.equal(figure.data[4].marker.color, FIGURE_COLORS[0], 'Okabe-Ito phase colour');
  assert.equal(figure.layout.title, undefined, 'no title inside the figure');
  assert.equal(figure.layout.xaxis.showgrid, false);
  assert.equal(figure.layout.yaxis.ticks, 'inside');
  assert.equal(figure.layout.yaxis.mirror, 'ticks', 'closed frame');
  assert.equal(figure.layout.xaxis2.title.text, '2θ (°, Cu Kα)', 'the 2θ title under the lower panel');
  assert.equal(figure.layout.yaxis.title.text, 'Intensity (counts)');
  assert.equal(figure.layout.yaxis2.title.text, 'Difference');
  assert.match(figure.layout.font.family, /^Arial, Helvetica/);
  assert(Math.abs(figure.layout.font.size - 8 * 96 / 72) < 1e-9, '8 pt');
  assert.equal(figure.layout.annotations[1].font.color, '#000000', 'hkl labels in black');
  assert.equal(figure.layout.annotations[0].font.color, FIGURE_COLORS[0], 'row name in its phase colour');
  assert.equal(figure.layout.shapes.length, 1, 'the excluded range stays');
  assert.equal(figure.width, Math.round(85 * 96 / 25.4), 'single column');
  const png = exportOptions(figure, 'png', 'f');
  assert(Math.abs(png.width * png.scale / (85 / 25.4) - 600) < 2, '600 dpi');
  assert.equal(exportOptions(figure, 'svg', 'f').scale, 1);
}

{
  // An identification: the measured pattern as a thin black line, sticks in phase colours, a.u. when not counts.
  const figure = publicationFigure([
    { name: 'Observed', x: [1], y: [1], mode: 'markers' },
    { name: 'In2O3', meta: 'reference-sticks', legendgroup: 'I', x: [1], y: [1], line: { color: '#123' } },
  ], { meta: { counts: false }, xaxis: {}, yaxis: {} });
  assert.equal(figure.data[0].mode, 'lines');
  assert.equal(figure.data[1].line.color, FIGURE_COLORS[0]);
  assert.equal(figure.layout.yaxis.title.text, 'Intensity (a.u.)');
  assert.equal(figure.layout.xaxis.title.text, '2θ (°)');
}
console.log('figure export passed.');
