---
version: 1
slug: "src-dara-server-ui-src-pages-task-js"
primary_target: "src/dara/server/ui/src/pages/task.js"
related_targets: ["src/dara/server/ui/src/pages/results.js","src/dara/server/ui/src/components/Layout.js"]
---

# Task page (and the shared DARA shell)

Mode: Operate. Build path: code-led (no image generation).

## Direction contract

THESIS: Every candidate phase set is a specimen tray: its phases sit as compartments sized by wt%, so the composition reads before any number. Refuses the category default of a grey antd table with phase names and numbers stacked in text.

OWN-WORLD: Flat felt-green shell (#24382e) for the top bar and the selected tray, white label stock (#ffffff) on a pale ground (#f7f8f5), ink #1c1f1d, one-pixel hairlines (#dfe2dc), 2 px radii, no shadows. Catalogue red #b5482a marks only the adopted fit; data blue #3f6c8f for links and focus; ochre #c99a2e for the active nav underline. Three text sizes (12/14/20), tabular numbers right-aligned.

STORY: The analyst sees the fit and the trays, reads which phase set explains the pattern and how much of each phase, checks Rwp/GOF/unexplained/cautions on the same line, previews a tray in the plot, then adopts it.

FIRST VIEWPORT: Green top bar (DARA, Submit, Results, Series, Help). Task title row with Download / Rerun / more. Verdict banner. The candidate trays first (the main result): code C1…, a 100 % composition bar, Rwp, GOF, unexplained, cautions, confidence, Open / Adopt. Under them the full-width fit plot of the picked tray, with a live 2θ / d / I cursor readout and "Open fit workspace" as the primary action.

FORM: Specimen Drawer, position 1 of the ordered list (assigned by the roll). seed:acea239fc2e10f81

Signature interaction: hover the plot to read 2θ, d-spacing and intensity; click a tray to show its fit above (selected tray turns felt green at its code cell).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
