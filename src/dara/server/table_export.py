"""Flat, spreadsheet-friendly (CSV) export of refined phases.

One row is written per (candidate, phase). Symmetry-constrained lattice values
are filled in from the space group so that every row carries a-gamma; filled
values have a blank e.s.d. because they were not refined independently.
"""

from __future__ import annotations

import csv
import re
from html import unescape
from io import StringIO

TABLE_COLUMNS: list[str] = [
    "task_id", "task_name", "candidate", "source_index", "adopted", "phase",
    "phase_label", "spacegroup_symbol", "spacegroup_number", "weight_percent",
    "weight_esd_percent", "weight_flag", "absolute_wt_percent", "absolute_wt_esd_percent",
    "amorphous_wt_percent", "a_angstrom", "a_esd", "a_state", "b_angstrom", "b_esd",
    "b_state", "c_angstrom", "c_esd", "c_state", "alpha_deg", "alpha_esd", "alpha_state",
    "beta_deg", "beta_esd", "beta_state", "gamma_deg", "gamma_esd", "gamma_state",
    "crystallite_size_nm", "crystallite_size_esd_nm", "microstrain_percent",
    "microstrain_esd_percent", "strain_state", "refinement_status", "refinement_codes",
    "rwp", "rp_percent", "rexp", "gof", "chi_squared", "durbin_watson",
    "sample_displacement_eps2", "wavelength", "anode", "missing_peaks",
    "missing_peaks_significant", "missing_peaks_minor", "extra_peaks", "two_theta_min",
    "two_theta_max", "cautions", "provenance", "verdict", "overall_confidence",
    "mass_balance_deviation",
]
# Decimal places per column (e.s.d. columns follow their value): lattice lengths 5, angles 3,
# wt% 2, size nm 1, Rwp/Rp 2. Other numeric columns keep 10 significant digits.
COLUMN_DIGITS: dict[str, int] = {
    **{f"{key}_{unit}": 5 for key in ("a", "b", "c") for unit in ("angstrom", "esd")},
    **{f"{key}_{unit}": 3 for key in ("alpha", "beta", "gamma") for unit in ("deg", "esd")},
    "weight_percent": 2, "weight_esd_percent": 2, "absolute_wt_percent": 2,
    "absolute_wt_esd_percent": 2, "amorphous_wt_percent": 2,
    "crystallite_size_nm": 1, "crystallite_size_esd_nm": 1,
    "microstrain_percent": 3, "microstrain_esd_percent": 3,
    "rwp": 2, "rp_percent": 2, "rexp": 2, "gof": 2, "chi_squared": 2, "durbin_watson": 2,
    "wavelength": 5,
}
MULTI_TASK_EXTRA_COLUMNS = ["user", "submitted_on"]
RHOMBOHEDRAL_GROUPS = {146, 148, 155, 160, 161, 166, 167}


def _format(value, column: str | None = None) -> str:
    if value is None or isinstance(value, bool):
        return "" if value is None else str(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return ""
        digits = COLUMN_DIGITS.get(column)
        return f"{number:.{digits}f}" if digits is not None else f"{number:.10g}"
    text = str(value)
    # Keep user-controlled text (file names) from being run as a spreadsheet formula.
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def provenance(phase_name: str) -> tuple[str, str | None]:
    """``(label, url)`` of a reference structure's source (shared by the table and the report)."""
    try:
        from dara.structure_quality import reference_provenance

        info = reference_provenance(phase_name)
        if info.get("label"):
            return str(info["label"]), (str(info["url"]) if info.get("url") else None)
    except Exception:  # W1 module missing or failing: fall back to the plain parser
        pass
    cod = re.search(r"cod[_-]?(\d+)", phase_name, re.I)
    mp = re.search(r"mp[-_](\d+)", phase_name, re.I)
    if cod:
        record = cod.group(1)
        return f"COD {record}", f"https://www.crystallography.net/cod/{record}.html"
    if mp:
        record = mp.group(1)
        return f"Materials Project mp-{record}", f"https://materialsproject.org/materials/mp-{record}"
    return "User-provided / source not recorded", None


def _provenance(phase_name: str) -> str:
    return provenance(phase_name)[0]


def _phase_label(phase_name: str) -> str:
    try:
        from dara.plot import phase_display_name
        return unescape(re.sub(r"<[^>]+>", "", phase_display_name(phase_name)))
    except Exception:
        return phase_name


def _parameter(parameters: dict, key: str) -> tuple[float | None, float | None]:
    entry = (parameters or {}).get(key)
    if not isinstance(entry, dict):
        return None, None
    return entry.get("value"), entry.get("esd")


def lattice_with_symmetry(parameters: dict, spacegroup_number) -> dict[str, tuple]:
    """Return ``{a..gamma: (value, esd)}`` with symmetry-implied values filled in."""
    parameters = parameters or {}
    values = {key: _parameter(parameters, key) for key in ("a", "b", "c", "alpha", "beta", "gamma")}
    try:
        number = int(spacegroup_number)
    except (TypeError, ValueError):
        number = 0
    blank = (None, None)

    def first(*keys):
        return next((values[key] for key in keys if values[key][0] is not None), blank)

    if number in RHOMBOHEDRAL_GROUPS and values["alpha"][0] is not None:
        a = first("a", "b", "c")
        alpha = values["alpha"]
        values.update(a=a, b=(a[0], None), c=(a[0], None))
        values.update(beta=(alpha[0], None), gamma=(alpha[0], None))
    elif 195 <= number <= 230:
        a = first("a", "b", "c")
        values.update(a=a, b=(a[0], None), c=(a[0], None))
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(90.0, None))
    elif 143 <= number <= 194:
        a = first("a", "b")
        values.update(a=a, b=(a[0], None))
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(120.0, None))
    elif 75 <= number <= 142:
        a = first("a", "b")
        values.update(a=a, b=(a[0], None))
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(90.0, None))
    elif 16 <= number <= 74:
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(90.0, None))
    elif 3 <= number <= 15:
        values.update(alpha=(90.0, None), gamma=(90.0, None))
    return values


def _is_adopted(selection, candidate_kind: str, source_index) -> bool:
    if selection is None:
        return False
    if candidate_kind == "final":
        return selection == "best"
    return (
        not isinstance(selection, bool)
        and isinstance(selection, int)
        and selection == source_index
    )


def _context(summary: dict | None) -> dict:
    """Radiation of the task (``wavelength`` in angstrom and lab ``anode``), for every row."""
    value = ((summary or {}).get("additional_search_options") or {}).get("wavelength", "Cu")
    try:
        from dara.export.peak_list import wavelength_angstrom

        angstrom = wavelength_angstrom(value)
    except Exception:
        angstrom = None
    try:
        float(value)
        anode = ""  # a numeric wavelength (nm) is not a lab anode
    except (TypeError, ValueError):
        anode = str(value)
    return {"wavelength": angstrom, "anode": anode}


def _standard_block(task, result: dict | None) -> dict | None:
    """Internal-standard block of the task's standard for ``result`` (``None`` without one)."""
    try:
        from dara.server.api_router import internal_standard_block

        return internal_standard_block(task, result)
    except Exception:
        return None


def _rows_for_result(
    task_id,
    task_name,
    candidate: str,
    source_index,
    adopted: bool,
    result: dict,
    context: dict | None = None,
    standard: dict | None = None,
) -> list[dict]:
    context = context or {}
    absolute = {
        item.get("name"): item for item in (standard or {}).get("phases") or [] if isinstance(item, dict)
    }
    amorphous = ((standard or {}).get("amorphous") or {}).get("mixture_wt_percent")
    quality = result.get("fit_quality") or {}
    details = result.get("phase_details") or {}
    metadata = result.get("phase_metadata") or {}
    diagnostics = result.get("peak_diagnostics") or {}
    counts = diagnostics.get("counts") or {}
    by_class = diagnostics.get("counts_by_class")
    by_class = by_class if isinstance(by_class, dict) else {}
    cautions = ";".join(c.get("code", "") for c in result.get("cautions") or [])
    confidence = result.get("confidence") if isinstance(result.get("confidence"), dict) else {}
    overall_level = ((confidence.get("overall") or {}).get("level")) or ""
    phase_verdicts = confidence.get("phases") or {}
    mass_balance = result.get("mass_balance") if isinstance(result.get("mass_balance"), dict) else {}
    mass_balance_deviation = mass_balance.get("max_deviation")
    phases = list(details) or list(result.get("highlighted_phases") or result.get("phases") or [])
    rows = []
    for phase in phases:
        if not isinstance(phase, str):
            continue
        phase_details = details.get(phase) or {}
        phase_meta = metadata.get(phase) or {}
        number = phase_meta.get("spacegroup_number")
        lattice = lattice_with_symmetry(phase_details.get("lattice_parameters"), number)
        grain = next(iter(phase_details.get("grain_sizes") or []), None) or {}
        status = phase_details.get("refinement_status") or {}
        strain = (phase_details.get("size_strain") or {}).get("microstrain_percent") or {}
        row = {
            "task_id": task_id,
            "task_name": task_name,
            "candidate": candidate,
            "source_index": source_index,
            "adopted": "yes" if adopted else "",
            "phase": phase,
            "phase_label": _phase_label(phase),
            "spacegroup_symbol": phase_meta.get("spacegroup_symbol"),
            "spacegroup_number": number,
            "weight_percent": phase_details.get("weight_percent"),
            "weight_esd_percent": phase_details.get("weight_esd_percent"),
            "weight_flag": (phase_verdicts.get(phase) or {}).get("weight_flag") or "",
            "absolute_wt_percent": (absolute.get(phase) or {}).get("absolute_wt_percent"),
            "absolute_wt_esd_percent": (absolute.get(phase) or {}).get("absolute_esd_percent"),
            "amorphous_wt_percent": amorphous,
            "crystallite_size_nm": grain.get("value"),
            "crystallite_size_esd_nm": grain.get("esd"),
            "microstrain_percent": strain.get("value"),
            "microstrain_esd_percent": strain.get("esd"),
            "strain_state": (phase_details.get("size_strain") or {}).get("strain_state") or "",
            "refinement_status": status.get("level") or "",
            "refinement_codes": ";".join(status.get("codes") or []),
            "rwp": quality.get("rwp", result.get("rwp")),
            "rp_percent": quality.get("rp"),
            "rexp": quality.get("rexp"),
            "gof": quality.get("gof"),
            "chi_squared": quality.get("chi_squared"),
            "durbin_watson": quality.get("durbin_watson"),
            "sample_displacement_eps2": quality.get("sample_displacement_eps2"),
            "wavelength": context.get("wavelength"),
            "anode": context.get("anode"),
            "missing_peaks": counts.get("missing"),
            "missing_peaks_significant": by_class.get("significant"),
            "missing_peaks_minor": by_class.get("minor"),
            "extra_peaks": counts.get("extra"),
            "two_theta_min": quality.get("two_theta_min"),
            "two_theta_max": quality.get("two_theta_max"),
            "cautions": cautions,
            "provenance": _provenance(phase),
            "verdict": ((phase_verdicts.get(phase) or {}).get("level")) or "",
            "overall_confidence": overall_level,
            "mass_balance_deviation": mass_balance_deviation,
        }
        for key, column in (
            ("a", "a_angstrom"), ("b", "b_angstrom"), ("c", "c_angstrom"),
            ("alpha", "alpha_deg"), ("beta", "beta_deg"), ("gamma", "gamma_deg"),
        ):
            value, esd = lattice[key]
            row[column] = value
            row[f"{key}_esd"] = esd
            given = (phase_details.get("lattice_parameters") or {}).get(key)
            # symmetry-implied values (filled above) are dependent, not refined
            row[f"{key}_state"] = (
                (given or {}).get("state") or ("dependent" if value is not None and not given else "")
            )
        rows.append(row)
    return rows


def task_table_rows(
    task_id: int, task: dict, summary: dict, adopted_version: dict | None = None
) -> list[dict]:
    """Rows for the final result, the adopted version and every displayed candidate of one task.

    ``task`` is the stored worker record (for the review selection), ``summary`` the
    payload of ``GET /api/task/{id}`` and ``adopted_version`` the stored record of an
    adopted re-refinement (``version:<n>``), whose rows follow the final result's.
    Phase labels use the conventional formulas the payload carries (display only).
    """
    from dara.plot import display_formulas_scope

    with display_formulas_scope((summary or {}).get("display_formulas")):
        return _task_table_rows(task_id, task, summary, adopted_version)


def _task_table_rows(task_id, task, summary, adopted_version) -> list[dict]:
    selection = ((task or {}).get("review") or {}).get("selection")
    name = summary.get("task_label") or ((task or {}).get("job") or {}).get("name") or ""
    context = _context(summary)
    rows: list[dict] = []
    final = summary.get("final_result")
    if final:
        rows += _rows_for_result(
            task_id, name, "final", None, _is_adopted(selection, "final", None), final, context,
            summary.get("internal_standard"),
        )
    if adopted_version and adopted_version.get("summary"):
        number = adopted_version.get("version") or adopted_version.get("index")
        rows += _rows_for_result(
            task_id, name, f"version {number}", None,
            selection == f"version:{number}", adopted_version["summary"], context,
            _standard_block(task, adopted_version["summary"]),
        )
    for number, candidate in enumerate(summary.get("all_results") or [], start=1):
        source_index = candidate.get("source_index", number - 1)
        label = str(number)
        adopted = _is_adopted(selection, "candidate", source_index)
        member = None if adopted else next(
            (
                item
                for item in candidate.get("merged_results") or []
                if _is_adopted(selection, "candidate", item.get("source_index"))
            ),
            None,
        )
        if member is not None:
            # the adopted result is this candidate refined with another database record;
            # same wording as the UI, without the leading "Candidate"
            from dara.server.versions import merged_label

            label = merged_label(
                number, member.get("source_index"), member.get("differing_phases")
            ).removeprefix("Candidate ")
            adopted = True
        rows += _rows_for_result(task_id, name, label, source_index, adopted, candidate, context)
    return rows


def multi_task_rows(entries: list[tuple[int, dict | None, dict | None]]) -> list[dict]:
    """Final-result rows for several tasks.

    ``entries`` holds ``(task_id, task_record_or_None, summary_or_None)``. A task
    without a completed summary yields one ``status: ...`` row.
    """
    from dara.server.utils import convert_to_local_tz

    rows: list[dict] = []
    for task_id, task, summary in entries:
        extra = {}
        if task:
            extra["user"] = task.get("user") or ""
            submitted = task.get("submitted_time")
            if submitted is not None:
                try:
                    extra["submitted_on"] = convert_to_local_tz(submitted).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    extra["submitted_on"] = str(submitted)
        if summary is None or not summary.get("final_result"):
            status = (task or {}).get("status") or "NOT_FOUND"
            row = {
                "task_id": task_id,
                "task_name": ((task or {}).get("job") or {}).get("name") or "",
                "candidate": f"status: {status}",
                **extra,
            }
            rows.append(row)
            continue
        from dara.plot import display_formulas_scope

        selection = ((task or {}).get("review") or {}).get("selection")
        name = summary.get("task_label") or ((task or {}).get("job") or {}).get("name") or ""
        with display_formulas_scope(summary.get("display_formulas")):
            result_rows = _rows_for_result(
                task_id, name, "final", None, _is_adopted(selection, "final", None),
                summary["final_result"], _context(summary), summary.get("internal_standard"),
            )
        for row in result_rows:
            rows.append({**row, **extra})
    return rows


def rows_to_csv(rows: list[dict], columns: list[str]) -> bytes:
    """UTF-8 (with BOM, for Excel) CSV with CRLF line endings."""
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_format(row.get(column), column) for column in columns])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
