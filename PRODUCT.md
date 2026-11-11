# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Lab researchers who check their own powder XRD results right after a measurement, on a lab PC or an office desktop.
- XRD analysis staff who run and review many submitted samples in batches every day.

Both read the same screens; the staff use the lists, filters and comparisons more heavily.

## Product Purpose

DARA identifies the crystalline phases in a powder XRD pattern and quantifies them: it searches reference structures (COD, Materials Project, a local library, uploaded CIFs), ranks candidate phase sets by automated Rietveld refinement (BGMN), and lets the analyst refine further (add or remove phases, background, structure) as versions.

Success: the analyst decides which phase set is right, and can defend that decision with the fit, the amounts and the cautions, in minutes rather than an afternoon of manual refinement.

## Positioning

Automated phase search plus Rietveld refinement with explicit evidence: every candidate comes with its fit, residuals, unexplained peaks and automated cautions, and every manual change is a reproducible version beside the original search result.

## Operating Context

- Desktop browsers on lab and office PCs; 1366–1920 px wide screens.
- Patterns from lab diffractometers (Cu radiation mostly) with instrument profiles (.geq) per diffractometer.
- Workflow: Submit a pattern (elements or precursors, databases) → Results list → Task page (candidates, fit plot, phases, versions) → adopt a fit and write notes → report / export (HTML report, CSV, refined CIF, TOPAS/GSAS-II/HighScore/Profex inputs).
- Many tasks share a sample or file name; batches and series are common.

## Capabilities and Constraints

- Screens and their links stay as they are: Submit, Results, Task (with the fit workspace), Compare, Series, Help.
- The fit plot stays on Plotly with its interactions (zoom, pan, legend toggles, peak clicks).
- UI text is English; the product name is DARA.
- Terminology follows Rietveld practice: Rwp, GOF, wt% ± e.s.d., space group symbols, hkl, "at limit", "fixed (DB)".
- Stack: Gatsby + React 17 + antd 4 front end served by a FastAPI back end; the built bundle is committed under `src/dara/server/ui/public`.

## Brand Commitments

- Name: DARA.
- English UI throughout.

## Evidence on Hand

- Real analyses in the local database (e.g. YFeO3/Y2O3/Fe2O3, NH4H2PO4–Na2CO3 reaction products) and 14 benchmark samples with ground truth (`benchmarks/`).
- No customer quotes, usage statistics or external validation beyond the benchmark; do not invent any.

## Product Principles

1. The decision comes first: candidates, fit and amounts lead; settings and diagnostics support.
2. Evidence over verdicts: every judgement links to the numbers and the curve behind it.
3. Exceptions stand out, the normal stays quiet: colour and emphasis mark what needs attention.
4. Dense and calm, like instrument software: one line per item, details on demand.
5. Nothing is lost: every refinement is a version beside the original, never an overwrite.
