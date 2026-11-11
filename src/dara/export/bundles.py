"""ZIP bundles for importing a DARA refinement into other programs."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import numpy as np

from dara.cif_names import cif_stem, strip_none

logger = logging.getLogger("dara.export")

FORMATS = ("profex", "topas", "gsas2", "highscore")

_PROGRAM_NAMES = {
    "profex": "Profex / BGMN",
    "topas": "TOPAS",
    "gsas2": "GSAS-II",
    "highscore": "HighScore",
}


def safe_name(value: str) -> str:
    """Filesystem-safe name for a phase (a trailing ``-None`` of an unknown e_hull is dropped)."""
    return re.sub(r"[^A-Za-z0-9._()+-]+", "_", strip_none(value)).strip("._") or "phase"


def write_pattern_xy(document, path: Path) -> Path:
    """Two-column (2θ, intensity) pattern with six decimals."""
    xrd = document.xrd_data
    angles = np.asarray(xrd.angles, dtype=float)
    intensities = np.asarray(xrd.intensities, dtype=float)
    np.savetxt(path, np.column_stack([angles, intensities]), fmt="%.6f")
    return path


def _phase_meta(refinement, document, meta, name) -> dict:
    """Meta of ``refined_cif_text``: pattern-level values, ``meta`` and ``meta["phases"][name]``."""
    meta = meta or {}
    search = getattr(document, "search_kwargs", None) or {}
    lst = getattr(refinement, "lst_data", None)
    merged = {
        "wavelength": search.get("wavelength"),
        "rwp": getattr(lst, "rwp", None),
        "rexp": getattr(lst, "rexp", None),
        "pattern_name": getattr(document, "task_label", None),
        "effective_params": getattr(document, "final_refinement_params", None),
    }
    try:
        merged["weight_percent"] = float(refinement.get_phase_weights()[name]) * 100
    except Exception:
        pass
    merged.update({key: value for key, value in meta.items() if key != "phases"})
    merged.update((meta.get("phases") or {}).get(name) or {})
    return merged


def _structure_refined(meta) -> bool:
    meta = meta or {}
    return bool(meta.get("structure_spec")) or any(
        (phase or {}).get("structure_spec") for phase in (meta.get("phases") or {}).values()
    )


def write_refined_cifs(
    refinement, out_dir: Path, reference_cifs=None, *, document=None, meta=None, missing=None
) -> list[Path]:
    """Refined CIF per phase in ``refined_cifs/`` (see ``refined_cif.refined_cif_text``).

    A phase whose refined structure cannot be written gets its unrefined reference CIF in
    ``reference_cifs/`` (marked ``# NOT REFINED``). A phase without any CIF is appended to
    ``missing`` as ``(name, reason)``; no phase is skipped silently.
    """
    from dara.export.refined_cif import CifNotExportable, refined_cif_text

    written: list[Path] = []
    for name in refinement.lst_data.phases_results:
        reference = next((cif for cif in reference_cifs or [] if cif_stem(cif) == name), None)
        try:
            text, folder_name = refined_cif_text(
                name, refinement, meta=_phase_meta(refinement, document, meta, name),
                reference_cif=reference,
            )
        except CifNotExportable as exc:
            logger.warning("No CIF could be exported for phase %s: %s", name, exc)
            if missing is not None:
                missing.append((name, str(exc)))
            continue
        folder = Path(out_dir) / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{safe_name(name)}.cif"
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def _reference_lines(*keys: str) -> str:
    from dara.server.report.references import reference_text

    return "\n".join(f"  {reference_text(key)}" for key in keys)


def _fit_summary(refinement) -> str:
    try:
        fit = refinement.lst_data
        return f"Rwp {float(fit.rwp):.2f}%, Rexp {float(fit.rexp):.2f}%"
    except Exception:
        return "fit statistics not available"


def readme_text(
    fmt: str, refinement, document, *, missing=None, unconverted=None, structure_refined=False
) -> str:
    """English README of one bundle (``missing``: ``[(phase, reason)]`` without any CIF)."""
    program = _PROGRAM_NAMES[fmt]
    lines = [
        f"DARA export for {program}",
        "=" * 40,
        "",
        "This archive contains the DARA refinement model as starting values for another",
        "Rietveld program. DARA itself used BGMN (instrument profile "
        f"\"{(getattr(document, 'search_kwargs', None) or {}).get('instrument_profile', 'not recorded')}\").",
        f"DARA fit of the exported model: {_fit_summary(refinement)}.",
        "",
        "Common files",
        "  pattern.xy           Measured pattern, two columns (2theta in degrees, intensity).",
        "  refined_cifs/*.cif   Crystal structure of each refined phase at the refined lattice",
        "                       parameters (with e.s.d.); "
        + (
            "atomic positions, occupancies and B are"
            if structure_refined
            else "atomic positions and occupancies are"
        ),
        "                       "
        + (
            "refined as listed in _refine_special_details."
            if structure_refined
            else "those of the reference (DARA does not refine them)."
        ),
        "                       Each file states what was refined in _refine_special_details.",
        "  reference_cifs/*.cif Only present when a refined structure could not be written:",
        "                       the unrefined reference CIF of that phase.",
        "  peak_list.csv        Reflections: hkl, 2theta, d, intensity, FWHM (BGMN peak list).",
        "  dara_refinement.lst  Raw BGMN listing of the DARA refinement.",
        "",
    ]
    if fmt == "profex":
        try:
            rwp = f"{float(refinement.lst_data.rwp):.2f}"
        except Exception:
            rwp = "the DARA value"
        lines += [
            "Profex / BGMN files",
            "  *.str     BGMN phase files; the lattice parameters start at the refined values",
            "            with the same relative bounds as in the DARA final refinement.",
            "  *.sav     BGMN control file (instrument profile, phases, EPS2 start value).",
            "  *.geq, *.ger  Instrument profile files copied from DARA.",
            "",
            "Open the .sav control file in Profex (Doebelin & Kleeberg, 2015) or run",
            "`bgmn pattern.sav`. The starting model uses the refined lattice parameters and",
            "scale factors of the DARA final model, but B1 and k1 start at 0 and the SPHAR4",
            "preferred-orientation coefficients are not transferred. The Rwp of the first",
            f"cycle will therefore differ from the DARA value ({rwp}%) until they are refined.",
            "",
            "Reference",
            _reference_lines("profex", "bgmn"),
        ]
    elif fmt == "topas":
        lines += [
            "TOPAS file",
            "  dara_model.inp  Starting model in TOPAS input syntax.",
            "",
            "The file is a starting point and must be reviewed before use: the space-group",
            "setting, the origin choice, the peak-shape model and the sign convention of the",
            "specimen displacement were not converted automatically from BGMN. Scale factors",
            "start at a nominal value and are refined by TOPAS. Instrument parameters of",
            "your diffractometer must be added.",
            "",
            "Isotropic B = 1.0 \u00c5\u00b2 for every site, as in the DARA/BGMN refinement "
            "(BGMN TDS = 0.01 nm\u00b2); sites of a phase with a refined B use that value.",
        ]
    else:
        lines += [
            f"Import into {program}",
            "  1. Import pattern.xy as a two-column 2theta/intensity powder histogram.",
            "  2. Add each file in refined_cifs/ (and reference_cifs/, if present) as a phase.",
            "  3. Select the instrument parameters of your diffractometer; DARA's BGMN",
            "     instrument profile is not converted.",
            "",
            "No project file is written; scale factors, profile and background parameters",
            f"start from the defaults of {program}.",
        ]
    if missing:
        lines += ["", "Phases not exported (no refined structure and no reference CIF)"]
        lines += [f"  {name}: {reason}" for name, reason in missing]
    if unconverted:
        lines += ["", "Phases without a BGMN .str file (not part of the control file)"]
        lines += [f"  {name}" for name in unconverted]
    return "\n".join(lines) + "\n"


def write_common_files(
    refinement, document, out_dir: Path, reference_cifs=None, *, meta=None, missing=None
) -> list[Path]:
    """``pattern.xy``, ``refined_cifs/``, ``peak_list.csv``, ``dara_refinement.lst``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    created = [write_pattern_xy(document, out_dir / "pattern.xy")]
    created += write_refined_cifs(
        refinement, out_dir, reference_cifs, document=document, meta=meta, missing=missing
    )
    try:
        from dara.export.peak_list import peak_csv_bytes, peak_rows

        wavelength = (getattr(document, "search_kwargs", None) or {}).get("wavelength", "Cu")
        rows = peak_rows(refinement, wavelength=wavelength)
        if rows:
            peaks = out_dir / "peak_list.csv"
            peaks.write_bytes(peak_csv_bytes(rows))
            created.append(peaks)
    except Exception:
        logger.warning("Peak list could not be written", exc_info=True)
    raw = getattr(refinement.lst_data, "raw_lst", None)
    if raw:
        listing = out_dir / "dara_refinement.lst"
        listing.write_text(raw, encoding="utf-8")
        created.append(listing)
    return created


def write_cif_bundle(
    refinement, document, out_dir: Path, *, fmt: str, reference_cifs=None, meta=None
) -> list[Path]:
    """Generic bundle (GSAS-II, HighScore): pattern, refined CIFs, listing and README."""
    missing: list[tuple[str, str]] = []
    created = write_common_files(
        refinement, document, out_dir, reference_cifs, meta=meta, missing=missing
    )
    readme = Path(out_dir) / "README.txt"
    readme.write_text(
        readme_text(fmt, refinement, document, missing=missing,
                    structure_refined=_structure_refined(meta)),
        encoding="utf-8",
    )
    return created + [readme]


def build_bundle(
    fmt: str, refinement, document, out_dir: Path, reference_cifs=None, meta=None
) -> list[Path]:
    """Write the bundle of ``fmt`` into ``out_dir`` and return all created files.

    ``meta`` (optional) carries per-phase export details for the refined CIFs and the TOPAS
    model: common keys plus ``meta["phases"][phase_name]`` (see ``refined_cif_text``).
    """
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported export format: {fmt}")
    out_dir = Path(out_dir)
    if fmt in {"gsas2", "highscore"}:
        return write_cif_bundle(
            refinement, document, out_dir, fmt=fmt, reference_cifs=reference_cifs, meta=meta
        )
    missing: list[tuple[str, str]] = []
    unconverted: list[str] = []
    created = write_common_files(
        refinement, document, out_dir, reference_cifs, meta=meta, missing=missing
    )
    if fmt == "profex":
        from dara.export.bgmn_bundle import write_bgmn_bundle

        created += write_bgmn_bundle(
            refinement, document, out_dir, reference_cifs, meta=meta, failed=unconverted
        )
    else:
        from dara.export.topas_inp import topas_input

        model = out_dir / "dara_model.inp"
        model.write_text(topas_input(refinement, document, meta=meta, reference_cifs=reference_cifs), encoding="utf-8")
        created.append(model)
    readme = out_dir / "README.txt"
    readme.write_text(
        readme_text(fmt, refinement, document, missing=missing, unconverted=unconverted,
                    structure_refined=_structure_refined(meta)),
        encoding="utf-8",
    )
    return created + [readme]


def zip_directory(directory: Path) -> bytes:
    """Deflate every file below ``directory`` into an in-memory ZIP."""
    directory = Path(directory)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()
