---
name: DARA
description: Phase identification and Rietveld refinement workbench for powder XRD (Specimen Drawer world)
colors:
  shell: "#24382e"
  shell-text: "#f7f8f5"
  shell-mark: "#c99a2e"
  ground: "#f7f8f5"
  surface: "#ffffff"
  surface-sunken: "#f2f4f0"
  ink: "#1c1f1d"
  ink-secondary: "#4a504c"
  hairline: "#e4e7e1"
  hairline-strong: "#c9cdc6"
  accent: "#3f6c8f"
  accent-hover: "#2d5170"
  accent-bg: "#e9f0f5"
  catalogue-red: "#b5482a"
  ok: "#237804"
  review: "#874d00"
  fail: "#a8071a"
typography:
  body:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "12px"
    fontWeight: 400
  title:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "20px"
    fontWeight: 600
rounded:
  sm: "2px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  top-bar:
    backgroundColor: "{colors.shell}"
    textColor: "{colors.shell-text}"
    height: "48px"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
  adopted-mark:
    backgroundColor: "{colors.catalogue-red}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
  tray-code-shown:
    backgroundColor: "{colors.shell}"
    textColor: "{colors.shell-text}"
---

## Overview

Specimen Drawer: instrument software with the calm of a mineral cabinet. A flat felt-green top bar, white label stock on a pale ground, ink text and one-pixel rules. Every candidate phase set is a tray whose compartments are sized by wt%. Tokens live in `src/dara/server/ui/src/components/tokens.css`; layout pieces in `layout.css`.

## Colors

- Shell green fills whole regions only: the top bar and the code cell of the tray or version shown in the plot. Never as a tint or gradient.
- Catalogue red marks only the adopted fit (`.dara-adopted-mark`).
- Data blue is the one interactive colour: links, primary buttons, focus ring, checked controls.
- Status colours (ok / review / fail) appear only where they carry a verdict.
- Tray compartment fills come from `TRAY_FILLS` in `composition.mjs`; a phase (formula + space group) keeps one fill across all trays of a task.

## Typography

System sans at three sizes: 12 (meta, table heads), 14 (body), 20 (page title). Numbers are tabular and right-aligned (`.dara-num`).

## Layout

Operate surface, desktop first (1366–1920 px). Task page order: title row, verdict, candidate trays, fit plot of the picked tray with cursor readout, notes, versions, phases, details. Tables scroll horizontally on narrow screens; the page itself never does.

## Elevation & Depth

Flat. Hairline borders separate regions; only popups (menus, selects, modals) carry a soft `0 2px 8px` shadow.

## Shapes

2 px radii everywhere. No cards inside cards.

## Components

- Candidate tray (`.dara-tray`): a 100 % composition bar; compartments under 16 % move to a legend line below the bar. Tooltips carry the database record and other matching records.
- Short codes: candidates `C1…`, versions `v1…` (`.dara-code`).
- Cursor readout (`.dara-readout`): 2θ, d-spacing (when the wavelength is known) and the hovered trace's intensity, written to the DOM on hover without re-rendering the plot.
- Row menus: rare actions (compare, report, export, delete) sit behind `⋯`.

## Do's and Don'ts

- Do keep navigation and controls standard antd; the world lends only palette, density, the tray bar and the readout.
- Do use drawn icons (`@ant-design/icons`), never unicode glyphs.
- Don't add wood, felt or paper textures; flat colour only.
- Don't use red for anything but the adopted mark and errors.
