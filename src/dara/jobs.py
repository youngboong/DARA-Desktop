"""Jobflow jobs for DARA."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from inspect import signature
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING

from monty.serialization import dumpfn
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition

from dara import known_phases
from dara.cif import Cif
from dara.prediction.core import PhasePredictor
from dara.refine import RefinementPhase, do_refinement, do_refinement_no_saving, with_single_refinement_threads
from dara.schema import PhaseSearchDocument, RefinementDocument
from dara.search import candidate_cost, search_phases
from dara.search.data_model import SearchResult
from dara.structure_db import CODDatabase, MaterialsProjectDatabase, StructureDatabase, deduplicate_cif_paths
from dara.utils import get_logger

if TYPE_CHECKING:
    from dara.xrd import XRDData

try:
    from jobflow import Maker, job
except ImportError:
    raise ImportError(
        "Please install jobflow to use the dara jobs module! (pip install 'dara[jobflow]')"
    )


logger = get_logger(__name__)


def _phase_equivalence_ids(results: list[SearchResult]) -> dict[Path, str]:
    """Assign one identity to crystallographically equivalent CIF references.

    Formula and space-group labels alone are not sufficient: distinct ordered
    structures can share both. StructureMatcher is therefore the authority for
    merging references. If a CIF cannot be parsed, its file identity is kept so
    that a potentially distinct phase is never silently discarded.
    """
    matcher = StructureMatcher()
    identities: dict[Path, str] = {}
    representatives: dict[str, list[tuple[str, object]]] = {}

    unique_phases = {
        phase.path: phase
        for result in results
        for phases in result.phases
        for phase in phases
    }
    for path, phase in sorted(unique_phases.items(), key=lambda item: str(item[0])):
        try:
            structure = Cif.from_file(phase.path).to_structure()
            formula = structure.composition.reduced_formula
            matches = representatives.setdefault(formula, [])
            matched = next(
                (
                    identity
                    for identity, other in matches
                    if matcher.fit(structure, other)
                ),
                None,
            )
            if matched is None:
                matched = f"structure:{formula}:{len(matches)}"
                matches.append((matched, structure))
            identities[path] = matched
        except Exception as exc:
            logger.warning(
                "Could not compare phase %s structurally; preserving it as a distinct reference: %s",
                path,
                exc,
            )
            identities[path] = f"file:{path.stem}"
    return identities


def _distinct_phase_combinations(
    results: list[SearchResult], limit: int
) -> list[tuple[SearchResult, tuple[tuple[RefinementPhase, float, float], ...]]]:
    """Expand grouped references into distinct, ranked phase hypotheses.

    Search-tree paths that differ only by phase insertion order collapse to one
    hypothesis. Within each phase slot, only crystallographically equivalent
    database records collapse; distinct structures remain separate candidates.
    """
    hypotheses, _ = _collect_distinct_phase_combinations(results, limit)
    return hypotheses


def _collect_distinct_phase_combinations(
    results: list[SearchResult], limit: int
) -> tuple[
    list[tuple[SearchResult, tuple[tuple[RefinementPhase, float, float], ...]]],
    dict[str, int],
]:
    """Collect ranked structural hypotheses and report how grouping affected them."""
    phase_identities = _phase_equivalence_ids(results)
    hypotheses = []
    seen: set[tuple[str, ...]] = set()
    combinations_evaluated = 0
    equivalent_combinations_merged = 0
    for result in sorted(results, key=lambda item: item.refinement_result.lst_data.rwp):
        slot_choices = []
        for phases, foms, strains in zip(
            result.phases, result.foms, result.lattice_strains
        ):
            best_by_identity = {}
            for phase, fom, strain in zip(phases, foms, strains):
                identity = phase_identities[phase.path]
                current = best_by_identity.get(identity)
                if current is None or fom > current[1]:
                    best_by_identity[identity] = (phase, fom, strain)
            slot_choices.append(
                sorted(best_by_identity.values(), key=lambda item: item[1], reverse=True)[
                    :limit
                ]
            )

        combinations = sorted(
            product(*slot_choices),
            key=lambda combination: sum(item[1] for item in combination),
            reverse=True,
        )
        for combination in combinations:
            combinations_evaluated += 1
            key = tuple(sorted(phase_identities[item[0].path] for item in combination))
            if key in seen:
                equivalent_combinations_merged += 1
                continue
            seen.add(key)
            hypotheses.append((result, combination))
    summary = {
        "screening_results_retained": len(results),
        "combinations_evaluated": combinations_evaluated,
        "structurally_distinct_considered": len(hypotheses),
        "equivalent_combinations_merged": equivalent_combinations_merged,
        "selected_for_final_refinement": min(len(hypotheses), limit),
    }
    return hypotheses[:limit], summary


def _expanded_search_results(
    results: list[SearchResult], limit: int
) -> tuple[list[SearchResult], list[float], dict[str, int]]:
    """Create one SearchResult per distinct crystallographic phase combination."""
    expanded = []
    screening_rwps = []
    hypotheses, summary = _collect_distinct_phase_combinations(results, limit)
    for source, combination in hypotheses:
        expanded.append(
            source.model_copy(
                deep=True,
                update={
                    "phases": tuple((item[0],) for item in combination),
                    "foms": tuple((item[1],) for item in combination),
                    "lattice_strains": tuple((item[2],) for item in combination),
                },
            )
        )
        screening_rwps.append(source.refinement_result.lst_data.rwp)
    return expanded, screening_rwps, summary


#: Peak-shape parameters the search tree writes per phase as screening warm starts
#: (with the narrow screening bounds).  They must never reach the final refinement.
SCREENING_ONLY_PHASE_KEYS = frozenset({"k1", "k2", "b1"})


def final_refinement_phases(
    result: SearchResult,
    additional_cif_names: set[str] | frozenset[str] = frozenset(),
    additional_cif_params: dict | None = None,
) -> list[RefinementPhase]:
    """Fresh phases for the final refinement of one search hypothesis.

    User-supplied CIFs get their own ``additional_cif_params`` (exactly as submitted);
    every other phase drops the screening-only peak parameters so that the global
    ``final_refinement_params`` windows (k1/k2/b1) apply.  This mirrors
    ``dara.final_refinement_spec.build_final_refinement_spec``.
    """
    phases = []
    for phase_group in result.phases:
        phase = RefinementPhase.make(phase_group[0])
        if phase.path.stem in additional_cif_names:
            params = dict(additional_cif_params or {})
        else:
            params = {
                key: value
                for key, value in phase.params.items()
                if key not in SCREENING_ONLY_PHASE_KEYS
            }
        phases.append(RefinementPhase(path=phase.path, params=params))
    return phases


def _fit_weights(refined) -> dict[str, float]:
    """Weight fractions by phase name; empty when the fit lists no phases."""
    return refined.get_phase_weights() if getattr(refined.lst_data, "phases_results", None) else {}


def _without_phases(result: SearchResult, names: set[str]) -> SearchResult:
    """``result`` without the phase slots whose reference (first of the group) is in ``names``."""
    keep = [index for index, group in enumerate(result.phases) if group[0].path.stem not in names]
    pick = lambda values: tuple(values[index] for index in keep) if values else values  # noqa: E731
    return result.model_copy(
        update={"phases": pick(result.phases), "foms": pick(result.foms), "lattice_strains": pick(result.lattice_strains)}
    )


def _phases_key(phases) -> tuple:
    """Identity of a refined phase set: the phase files and their parameters, whatever their order.

    BGMN results depend on the phase order only at the 0.01 wt% level, and a candidate with the
    same set is removed as a duplicate anyway.
    """
    return tuple(sorted((str(phase.path), json.dumps(phase.params, sort_keys=True, default=str)) for phase in phases))


def _prune_final_fit(
    refined, phases, pattern_path, working_dir: Path, phase_params, refinement_kwargs, protected, finished=None
):
    """Refit one final hypothesis without failed, negligible or duplicate phases (``final_pruning``).

    The phases are removed one at a time (failed first, then the lightest); a removal is kept
    only when the refit still fits as well as the original fit, so a minor phase that explains
    peaks stays. Returns ``(refined, phases, dropped)``; an accepted refit replaces the files in
    ``working_dir``.

    ``finished`` maps ``_phases_key`` of an already finished fit (same pattern, same settings) to
    ``(refinement, directory, phases)``. A refit of such a phase set would reproduce that fit, so
    its result, files and phase order are reused instead of refined again.
    """
    from dara.final_pruning import phases_to_prune, refit_is_acceptable

    phase_results = getattr(refined.lst_data, "phases_results", None)
    if not phase_results:
        return refined, phases, {}
    candidates = phases_to_prune(phase_results, refined.get_phase_weights(), set(protected))
    original_rwp = refined.lst_data.rwp
    dropped: dict[str, str] = {}
    refit_dir = working_dir.with_name(f"{working_dir.name}_pruned")
    for name, reason in candidates.items():
        trial = [phase for phase in phases if phase.path.stem != name]
        if not trial or len(trial) == len(phases):
            continue
        reused = (finished or {}).get(_phases_key(trial))
        if reused:
            refit, trial = reused[0], reused[2]  # the phase order of the reused fit
            logger.info("Refit without %s skipped: the phase set was already refined.", name)
        else:
            shutil.rmtree(refit_dir, ignore_errors=True)
            refit_dir.mkdir()
            dumpfn(phase_params, refit_dir / "phase_params.json")
            try:
                refit = do_refinement(
                    pattern_path=pattern_path,
                    phases=trial,
                    working_dir=refit_dir,
                    phase_params=phase_params,
                    show_progress=True,
                    **refinement_kwargs,
                )
            except Exception:
                logger.exception("Refit without %s failed; keeping it.", name)
                continue
        if not refit_is_acceptable(original_rwp, refit.lst_data.rwp):
            logger.info("Refit without %s: Rwp %.3f -> %.3f; keeping it.", name, original_rwp, refit.lst_data.rwp)
            continue
        logger.info("Dropped %s (%s) from the final fit: Rwp %.3f -> %.3f.", name, reason, original_rwp, refit.lst_data.rwp)
        shutil.rmtree(working_dir)
        if reused:
            shutil.copytree(reused[1], working_dir)
        else:
            refit_dir.rename(working_dir)
        refined, phases, dropped[name] = refit, trial, reason
    shutil.rmtree(refit_dir, ignore_errors=True)
    return refined, phases, dropped


def _with_refinement(result: SearchResult, refined) -> SearchResult:
    """``result`` carrying a (final) refinement and its unexplained / extra peak lists."""
    metrics = refined.refinement_metrics
    return result.model_copy(
        deep=True,
        update={
            "refinement_result": refined,
            "missing_peaks": (
                metrics.missing_peaks.tolist() if metrics and metrics.missing_peaks is not None else None
            ),
            "extra_peaks": (
                metrics.extra_peaks.tolist() if metrics and metrics.extra_peaks is not None else None
            ),
        },
    )


def _unexplained_peaks(refined, xrd_data, search_kwargs) -> int:
    """Unexplained peaks classified significant or minor (``unexplained_count`` of the task card).

    Noise and instrument-line peaks do not count. Without a fit profile to classify against, the
    raw ``refinement_metrics.missing_peaks`` count is used.
    """
    metrics = getattr(refined, "refinement_metrics", None)
    peaks = getattr(metrics, "missing_peaks", None)
    if peaks is None:
        return 0
    try:
        from dara.plot import peaks_in_fit_range
        from dara.server import peak_significance
        from dara.server.data_quality import intensities_look_like_counts

        try:
            counts_like = intensities_look_like_counts(xrd_data.intensities, getattr(xrd_data, "intensity_unit", None))
        except Exception:  # noqa: BLE001 - unknown count type, as in the report
            counts_like = None
        classified = peak_significance.classify_missing_peaks(
            refined, peaks_in_fit_range(peaks, refined.plot_data.x).tolist(),
            wavelength=peak_significance.resolve_wavelength(search_kwargs.get("wavelength", "Cu")),
            counts_like=counts_like, range_params=search_kwargs.get("refinement_params"),
        )
    except Exception:  # noqa: BLE001
        classified = None
    if classified is None:
        return len(peaks)
    counts = classified["counts_by_class"]
    return counts["significant"] + counts["minor"]


#: Weight fraction below which a phase of the best fit is tested by leave-one-out.
LEAVE_ONE_OUT_BELOW = 0.05


def _one_structure_per_formula(results, screening_rwps, groups, rwp_by_reference):
    """Known-phase mode: each hypothesis keeps one structure per listed formula and space group.

    Of several structures with the same formula and space group (the same structure type) the
    ``pick_stem`` one stays; structures of one formula with different space-group numbers are
    polymorphs and stay together. Then the hypotheses are grouped by the set of listed formulas
    they carry (and their unlisted phases); of each group only the one with the lowest search Rwp
    (``screening_rwps``, the Rwp of the search fit; the first on a tie) stays, so there is one final
    refinement per formula set. The leave-one-out later tests any polymorph of a kept fit.
    """
    group_of = {stem: group for group in groups for stem in group.stems}
    collapsed, best = [], {}
    for result, rwp in zip(results, screening_rwps):
        stems = [phases[0].path.stem for phases in result.phases]
        drop = set()
        for group in {group_of[stem] for stem in stems if stem in group_of}:
            by_space_group: dict[int | None, list[str]] = {}
            for stem in stems:
                if stem in group.stems:
                    by_space_group.setdefault(candidate_cost.stem_spacegroup(stem), []).append(stem)
            for here in by_space_group.values():
                drop.update(set(here) - {known_phases.pick_stem(known_phases.Group(group.label, tuple(here)), rwp_by_reference)})
        if drop:
            result = _without_phases(result, drop)
        key = (
            frozenset(group_of[stem].label for stem in stems if stem in group_of),
            frozenset(stem for stem in stems if stem not in group_of),
        )
        collapsed.append((result, rwp))
        if key not in best or rwp < collapsed[best[key]][1]:
            best[key] = len(collapsed) - 1
    kept = [collapsed[i] for i in sorted(best.values())]
    return [item[0] for item in kept], [item[1] for item in kept]


def _check_listed_phases(
    result: SearchResult,
    best_dir: Path,
    groups: list[known_phases.Group],
    rwp_by_reference: dict[str, float],
    phases_by_stem: dict[str, RefinementPhase],
    removed: dict[str, tuple[str, float | None]],
    *,
    prepare,
    refit,
    count,
    out_of_time,
):
    """Known-phase mode: check the best fit against the listed formulas, in two steps.

    The evidence of a phase is the number of significant or minor unexplained peaks (``count``).
    1. Leave-one-out: each phase below 5 wt% (lightest first; never a user structure, never the
       last phase) is refit without it. If that does not raise the count, the phase is dropped and
       the refit becomes the best fit.
    2. Additive: for each listed formula still without a phase, the best single structure of the
       formula (``pick_stem``) is added in a trial refit; it is kept, and later trials build on it,
       only when the count falls. A formula dropped in step 1 is not tried again.
    A phase dropped in step 1 is recorded in ``removed`` as ``removed_no_peak_evidence``, a rejected trial of
    step 2 as ``no_peak_evidence``, each with the weight it had (``failed`` when the trial did not refine).
    When the current fit has no unexplained peak (count 0) no trial can be kept, so the formula is recorded as
    ``no_unexplained_peaks`` without a refit. After ``out_of_time()`` no more refits are
    run: remaining formulas of step 2 are recorded as ``not_checked``, remaining phases of step 1 stay.
    The files of ``best_dir`` are replaced by every kept refit.

    ``prepare(result)`` gives the phases of a hypothesis for the final refinement and
    ``refit(phases, directory)`` refines them. Returns ``(result, phases, changed)``.
    """
    from dara.task_progress import report_progress

    exact = {stem for group in groups for stem in group.stems if group.exact}
    weights = _fit_weights(result.refinement_result)
    light = sorted(
        (stem for stem in (phases[0].path.stem for phases in result.phases)
         if weights.get(stem, 1.0) < LEAVE_ONE_OUT_BELOW and stem not in exact),
        key=weights.get,
    )
    present = {phases[0].path.stem for phases in result.phases}
    todo = [
        group for group in groups
        if group.stems and not present.intersection(group.stems) and not set(group.stems) & set(removed)
    ]
    trial_dir = best_dir.with_name(f"{best_dir.name}_trial")

    def trial_fit(trial_result):
        """``(phases, refinement)`` of a hypothesis in ``trial_dir``; ``None`` if it did not refine."""
        shutil.rmtree(trial_dir, ignore_errors=True)
        trial_dir.mkdir()
        trial_phases = prepare(trial_result)
        try:
            return trial_phases, refit(trial_phases, trial_dir)
        except Exception:
            logger.exception("Trial refit of %s failed.", [phase.path.stem for phase in trial_phases])
            shutil.rmtree(trial_dir, ignore_errors=True)
            return None

    phases, changed, before = None, False, count(result.refinement_result)
    total = len(light) + len(todo)
    for done, stem in enumerate(light):
        report_progress("Checking listed phases", done, total)
        weight = _fit_weights(result.refinement_result).get(stem)
        if out_of_time():
            break
        if weight is None or weight >= LEAVE_ONE_OUT_BELOW or len(result.phases) < 2:
            continue
        trial_result = _without_phases(result, {stem})
        fitted = trial_fit(trial_result)
        if fitted is None:
            continue
        trial_phases, trial = fitted
        after = count(trial)
        if after > before:
            logger.info("Phase %s explains %d unexplained peak(s); kept.", stem, after - before)
            shutil.rmtree(trial_dir, ignore_errors=True)
            continue
        logger.info("Phase %s explains no unexplained peak (%d -> %d without it); dropped.", stem, before, after)
        shutil.rmtree(best_dir)
        trial_dir.rename(best_dir)
        result, phases, changed, before = _with_refinement(trial_result, trial), trial_phases, True, after
        removed[stem] = ("removed_no_peak_evidence", float(weight))

    for done, group in enumerate(todo, start=len(light)):
        report_progress("Checking listed phases", done, total)
        stem = known_phases.pick_stem(group, rwp_by_reference)
        if before == 0 and getattr(result.refinement_result, "refinement_metrics", None) is not None:
            # A trial is kept only if the count falls, and it cannot fall below 0. Without metrics the 0 is
            # only the fallback of ``count``, so the trial still runs.
            removed[stem] = ("no_unexplained_peaks", None)
            continue
        if out_of_time():
            removed[stem] = ("not_checked", None)
            continue
        trial_result = result.model_copy(
            update={
                "phases": result.phases + ((phases_by_stem[stem],),),
                "foms": result.foms + ((0.0,),),
                "lattice_strains": result.lattice_strains + ((0.0,),),
            }
        )
        fitted = trial_fit(trial_result)
        if fitted is None:
            removed[stem] = ("failed", None)
            continue
        trial_phases, trial = fitted
        after = count(trial)
        if after < before:
            logger.info("Listed phase %s explains %d more peak(s); kept.", stem, before - after)
            shutil.rmtree(best_dir)
            trial_dir.rename(best_dir)
            result, phases, changed, before = _with_refinement(trial_result, trial), trial_phases, True, after
        else:
            logger.info("Listed phase %s explains no additional peak (%d -> %d); not detected.", stem, before, after)
            shutil.rmtree(trial_dir, ignore_errors=True)
            removed[stem] = ("no_peak_evidence", float(_fit_weights(trial).get(stem, 0.0)))
    report_progress("Checking listed phases", total, total)
    return result, phases, changed


@dataclass
class RefinementMaker(Maker):
    """Maker to create a job for performing Rietveld refinement using BGMN.

    Args:
        name: The name of the job. This is automatically created if not provided.
    """

    name: str = "refine"
    save: bool = True
    refinement_params: dict | None = None
    show_progress: bool = True

    @job(output_schema=RefinementDocument)
    def make(
        self,
        xrd_data: XRDData,
        cifs: list[Cif],
        phase_params: dict | None = None,
        instrument_name: str = "Aeris-fds-Pixcel1d-Medipix3",
    ):
        """Perform Rietveld refinement.

        Args:
            xrd_data: _description_
            cifs: _description_

        Returns
        -------
            _description_
        """
        pattern_path = Path("xrd_data.xy")
        xrd_data.to_xy_file(pattern_path)

        phase_paths = []
        for cif in cifs:
            cif_path = Path(f"{next(iter(cif.data.keys()))}.cif")
            phase_paths.append(cif_path)

            with open(cif_path, "w") as f:
                f.write(str(cif))

        args = {
            "pattern_path": pattern_path,
            "phase_paths": phase_paths,
            "instrument_name": instrument_name,
            "phase_params": phase_params,
            "refinement_params": self.refinement_params,
            "show_progress": self.show_progress,
        }

        result = do_refinement(**args) if self.save else do_refinement_no_saving(**args)

        return RefinementDocument(
            task_label=self.name,
            result=result,
            rwp=result.lst_data.rwp,
            xrd_data=xrd_data,
            input_cifs=cifs,
            refinement_params=self.refinement_params,
            phase_params=phase_params,
            instrument_name=instrument_name,
        )


@dataclass
class PhaseSearchMaker(Maker):
    """Maker to create a job for performing phase search using BGMN."""

    name: str = "phase_search"
    phase_predictor: PhasePredictor | None = field(default_factory=PhasePredictor)
    verbose: bool = True
    run_final_refinement: bool = True
    cifs_folder_name: str = "dara_cifs"
    max_num_results: int | None = 10

    @job(output_schema=PhaseSearchDocument)
    def make(
        self,
        xrd_data: XRDData,
        cifs: list[Cif] | None = None,
        cif_dbs: list[StructureDatabase] | None = None,
        additional_cifs: list[Cif] | None = None,
        additional_cif_params: dict | None = None,
        precursors: list[str] | None = None,
        elements: list[str] | None = None,
        analysis_mode: str = "reaction",
        database_e_hull_filter: float = 0.05,
        deduplicate_structures: bool = True,
        predict_kwargs: dict | None = None,
        search_kwargs: dict | None = None,
        final_refinement_params: dict | None = None,
        final_refinement_overrides: dict | None = None,
        computed_entries=None,
        candidate_prefilter: bool | None = None,
        screening_timeout_s: float | None = None,
        max_runtime_s: float | None = None,
        fixed_phase_cifs: list[Cif] | None = None,
    ):
        """Perform phase search.

        Args:
            xrd_data: the XRD pattern
            cifs: A list of CIFs to search against. WARNING: If provided, phase
                prediction and CIF download from DBs will be skipped. If you want to
                provide additional CIFs to a database result, use "additional_cifs".
            cif_dbs: A list of CIF databases to search against; if not provided, will
                default to using the COD database.
            additional_cifs: Additional CIFs to include in the search. These are added
                to the CIFs from the database.
            additional_cif_params: Additional parameters for the CIFs, it will overwrite
                the default parameters in the refinement.
            precursors: A list of precursor formulas to predict phases for.
            elements: Elements allowed in an elemental phase-identification search.
            analysis_mode: One of reaction, elemental, or known_mixture.
            database_e_hull_filter: Maximum energy above hull for computed references.
            deduplicate_structures: Remove equivalent structures across databases.
            predict_kwargs: Keyword arguments for the phase predictor.
            search_kwargs: Keyword arguments for the search.
            final_refinement_params: Parameters for the final refinement.
            computed_entries: Computed entries to use for phase prediction.
            candidate_prefilter: Drop candidates that cannot be present in the pattern
                before screening (None: off unless ``DARA_CANDIDATE_PREFILTER=on``).
            screening_timeout_s: BGMN time limit per screening candidate in seconds
                (None: the legacy 1200 s unless ``DARA_SCREENING_TIMEOUT_S`` is set).
            max_runtime_s: Run-time budget in seconds (minimum 300; None: unlimited
                unless ``DARA_MAX_RUNTIME_S`` is set). The search stops early and
                reports the best result found so far.
            fixed_phase_cifs: Known-mixture mode only: the exact structures of an earlier
                result. No database lookup; each structure is required in every hypothesis.
        """
        t_start = time.monotonic()
        budget = candidate_cost.resolve_budget_s(max_runtime_s)
        if budget:
            screening_deadline = t_start + 0.55 * budget
            search_deadline = t_start + 0.80 * budget
            final_deadline = t_start + budget
            expansion_bgmn_timeout = min(1200.0, max(120.0, 0.25 * budget))
        else:
            screening_deadline = search_deadline = final_deadline = None
            expansion_bgmn_timeout = None

        directory = Path(os.getcwd()).resolve()

        if predict_kwargs is None:
            predict_kwargs = {}
        if search_kwargs is None:
            search_kwargs = {}

        # for backward compatibility
        # the instrument_name is now instrument_profile
        if "instrument_name" in search_kwargs:
            search_kwargs["instrument_profile"] = search_kwargs.pop("instrument_name")

        if final_refinement_params is None:
            final_refinement_params = {
                "gewicht": "SPHAR4",
                "lattice_range": 0.02,
                "k1": "0_0^1",
                "k2": "0_0^0.001",
                "b1": "0_0^0.05",
            }

        pattern_path = directory / "xrd_data.xy"
        cifs_path = directory / self.cifs_folder_name
        if cifs_path.is_dir():
            logger.info(f"Removing existing CIFs directory, located at: {cifs_path}")
            shutil.rmtree(cifs_path)

        cifs_path.mkdir()

        xrd_data.to_xy_file(pattern_path)

        if precursors is None:
            precursors = []
        if elements is None:
            elements = sorted(
                {str(elem) for formula in precursors for elem in Composition(formula).elements}
            )
        if analysis_mode not in {"reaction", "elemental", "known_mixture"}:
            raise ValueError(f"Unknown analysis mode: {analysis_mode}")

        from dara.task_progress import report_progress, checkpoint_candidate

        report_progress("Loading reference structures / reaction prediction")
        cod_unavailable: list[str] = []
        fixed_stems: list[str] = []
        mp_fallback_formulas: list[str] = []
        fallback_dbs: list[StructureDatabase] = []
        # Known-phase mode: the formula of every user-supplied structure, by stem. It satisfies a
        # listed formula of the same reduced formula, which is then neither looked up nor grouped twice.
        user_formulas = {}
        if analysis_mode == "known_mixture":
            user_formulas = {
                **{(cif.filename or cif.name): known_phases.formula_of_cif(cif) for cif in fixed_phase_cifs or []},
                **{cif.name: known_phases.formula_of_cif(cif) for cif in additional_cifs or []},
            }
        if fixed_phase_cifs:
            if analysis_mode != "known_mixture":
                raise ValueError("Fixed phase structures need known-mixture mode.")
            logger.info("Using the %d exact structures of an earlier result.", len(fixed_phase_cifs))
            for cif in fixed_phase_cifs:
                stem = cif.filename or cif.name
                (cifs_path / f"{stem}.cif").write_text(str(cif))
                fixed_stems.append(stem)
            for cif in additional_cifs or []:
                (cifs_path / f"{cif.name}.cif").write_text(str(cif))
        elif cifs is not None:
            logger.info("CIFs provided. Skipping CIF download and phase prediction.")
            for cif in cifs + (additional_cifs or []):
                cp = cifs_path / f"{cif.name}.cif"

                with open(cp, "w") as f:
                    f.write(str(cif))
        else:
            if cif_dbs is None:
                cif_dbs = [CODDatabase()]

            if self.phase_predictor is None:
                if analysis_mode == "known_mixture":
                    if not precursors:
                        raise ValueError("Known-mixture mode requires phase formulas.")
                    logger.info("Known-mixture mode; loading exact formula references.")
                    user_keys = {known_phases.reduced_formula(formula) for formula in user_formulas.values()}
                    lookup = [formula for formula in precursors if known_phases.reduced_formula(formula) not in user_keys]
                    for db in cif_dbs if lookup else []:
                        db.get_cifs_by_formulas(
                            lookup,
                            e_hull_filter=database_e_hull_filter,
                            copy_files=True,
                            dest_dir=cifs_path.as_posix(),
                        )
                    # Only for this run: a formula without a structure in the enabled databases
                    # is taken from Materials Project (formulas that have one are not queried).
                    missing = known_phases.formulas_without_structure(
                        lookup, (path.stem for path in cifs_path.glob("*.cif"))
                    )
                    if (
                        missing
                        and not any(isinstance(db, MaterialsProjectDatabase) for db in cif_dbs)
                        and known_phases.mp_configured()
                    ):
                        try:
                            fallback = MaterialsProjectDatabase(e_hull_filter=database_e_hull_filter)
                            fallback.get_cifs_by_formulas(
                                missing,
                                e_hull_filter=database_e_hull_filter,
                                copy_files=True,
                                dest_dir=cifs_path.as_posix(),
                            )
                            fallback_dbs.append(fallback)
                        except Exception:  # noqa: BLE001 - the formula is then reported as no_structure
                            logger.warning("Materials Project lookup for %s failed.", missing, exc_info=True)
                        still = known_phases.formulas_without_structure(
                            missing, (path.stem for path in cifs_path.glob("*.cif"))
                        )
                        mp_fallback_formulas = [formula for formula in missing if formula not in still]
                else:
                    if not elements:
                        raise ValueError("Elemental mode requires at least one element.")
                    logger.info("Elemental mode; loading phases in %s.", "-".join(elements))
                    for db in cif_dbs:
                        db.get_cifs_by_chemsys(
                            elements,
                            e_hull_filter=database_e_hull_filter,
                            copy_files=True,
                            dest_dir=cifs_path.as_posix(),
                        )

                    # Known starting/expected phases should never be lost just because
                    # the broad database query was capped or filtered.
                    for db in cif_dbs:
                        db.get_cifs_by_formulas(
                            precursors,
                            e_hull_filter=database_e_hull_filter,
                            copy_files=True,
                            dest_dir=cifs_path.as_posix(),
                        )
            else:
                if not precursors:
                    raise ValueError("Reaction mode requires precursor formulas.")
                logger.info("Predicting phases...")
                self.phase_predictor.cif_dbs = (
                    cif_dbs  # ensure CIF databases match as provided in job
                )
                self._predict_folder(
                    precursors,
                    cifs_path=cifs_path,
                    computed_entries=computed_entries,
                    e_hull_filter=database_e_hull_filter,
                    **predict_kwargs,
                )
            if additional_cifs:
                for cif in additional_cifs:
                    cp = cifs_path / f"{cif.name}.cif"
                    with open(cp, "w") as f:
                        f.write(str(cif))
            for db in [*cif_dbs, *fallback_dbs]:
                cod_unavailable.extend(
                    str(i) for i in getattr(db, "last_unavailable", []) if str(i) not in cod_unavailable
                )

        cif_paths = list(cifs_path.glob("*.cif"))
        # A reused structure set is never merged: each structure must stay one required phase.
        if deduplicate_structures and not fixed_stems:
            cif_paths, candidate_summary = deduplicate_cif_paths(cif_paths)
        else:
            candidate_summary = {
                "before": len(cif_paths),
                "after": len(cif_paths),
                "removed_duplicates": 0,
            }
        if not cif_paths:
            named = known_phases.formulas_without_structure(precursors, []) if analysis_mode == "known_mixture" else []
            raise ValueError(
                f"No reference structure was found for {', '.join(named)}. Enable Materials Project or add a CIF."
                if named
                else "No reference CIFs were found for this analysis."
            )

        from dara.structure_quality import computed_only_phases, screen_candidate_cifs

        repaired: list[dict] = []
        cif_paths, rejected, validation_warnings = screen_candidate_cifs(
            cif_paths,
            allowed_elements=elements if analysis_mode == "elemental" else None,
            protected_stems={cif.name for cif in (additional_cifs or [])} | set(fixed_stems),
            repaired=repaired,
        )
        candidate_summary["cod_unavailable"] = cod_unavailable
        if analysis_mode == "known_mixture":
            candidate_summary["mp_fallback_formulas"] = mp_fallback_formulas
        candidate_summary["rejected"] = rejected
        candidate_summary["repaired"] = repaired
        candidate_summary["validation_warnings"] = validation_warnings
        candidate_summary["computed_only_phases"] = computed_only_phases(cif_paths)

        from dara.search.prefilter import prefilter_candidates, resolve_prefilter_enabled

        enabled, reason = resolve_prefilter_enabled(
            candidate_prefilter, analysis_mode, explicit_cifs=cifs is not None or bool(fixed_stems)
        )
        if enabled:
            report_progress("Pre-filtering candidates")
        range_params = search_kwargs.get("refinement_params") or {}
        outcome = prefilter_candidates(
            getattr(xrd_data, "angles", None) if enabled else None,
            getattr(xrd_data, "intensities", None) if enabled else None,
            cif_paths,
            wavelength=search_kwargs.get("wavelength", "Cu"),
            wmin=range_params.get("wmin"),
            wmax=range_params.get("wmax"),
            excluded_ranges=range_params.get("excluded_ranges"),
            protected_stems={cif.name for cif in (additional_cifs or [])} | set(fixed_stems),
            protected_formulas=precursors,
            enabled=enabled,
        )
        if not enabled:
            outcome.report["reason_not_applied"] = reason
        cif_paths = outcome.kept
        candidate_summary["prefilter"] = outcome.report

        additional_cifs_name_set = {cif.name for cif in (additional_cifs or [])}
        if additional_cifs:
            for i in range(len(cif_paths)):
                if cif_paths[i].stem in additional_cifs_name_set:
                    cif_paths[i] = RefinementPhase(
                        path=cif_paths[i], params=dict(additional_cif_params or {})
                    )

        known_groups = None
        if analysis_mode == "known_mixture":
            known_groups = known_phases.build_groups(
                precursors, (Path(getattr(path, "path", path)).stem for path in cif_paths), user_formulas
            )

        report_progress("Peak detection, reference screening and phase-combination search")
        screening_timeouts = candidate_cost.resolve_screening_timeouts(screening_timeout_s)
        runtime_report: dict = {}
        results = search_phases(
            pattern_path=pattern_path,
            phases=cif_paths,
            **search_kwargs,
            screening_timeouts=screening_timeouts,
            screening_deadline=screening_deadline,
            screening_priority=(outcome.scores or None) if budget else None,
            search_deadline=search_deadline,
            expansion_bgmn_timeout=expansion_bgmn_timeout,
            search_report=runtime_report,
        )
        screening_report = runtime_report.get("screening") or {}
        search_loop = runtime_report.get("search") or {}
        candidate_summary["screening"] = screening_report
        result_limit = self.max_num_results or 10
        results, screening_rwps, search_summary = _expanded_search_results(
            results, result_limit
        )
        if known_groups and results:
            results, screening_rwps = _one_structure_per_formula(
                results, screening_rwps, known_groups, screening_report.get("rwp_by_reference") or {}
            )
        self._save_results(results)

        best_result = None
        mp_applied_all: set[str] = set()
        final_refinement_skipped = 0
        if self.run_final_refinement and results:
            logger.info("Final-refining %d distinct candidate hypotheses...", len(results))
            refinement_kwargs = {
                key: value
                for key, value in search_kwargs.items()
                if key in signature(do_refinement).parameters
            }
            refinement_kwargs.pop("bgmn_timeout", None)
            refined_candidates = []
            pruned_phases = []
            removed_by_dir: dict[Path, dict[str, tuple[str, float | None]]] = {}
            final_refinement_attempted = len(results)
            from dara.refinement_controls import override_phase_settings
            from dara.structure_quality import apply_mp_lattice_window, mp_window_enabled

            def prepare(result):
                """Phases of one hypothesis for the final refinement."""
                phases = final_refinement_phases(result, additional_cifs_name_set, additional_cif_params)
                phases, applied = apply_mp_lattice_window(
                    phases, final_refinement_params, enabled=mp_window_enabled()
                )
                mp_applied_all.update(applied)
                if final_refinement_overrides:
                    phases = override_phase_settings(phases, final_refinement_overrides)
                return phases

            def refit(phases, working_dir):
                dumpfn(final_refinement_params, working_dir / "phase_params.json")
                return do_refinement(
                    pattern_path=pattern_path,
                    phases=phases,
                    working_dir=working_dir,
                    phase_params=final_refinement_params,
                    show_progress=True,
                    **with_single_refinement_threads(refinement_kwargs),
                )

            finished: dict[tuple, tuple] = {}  # final phase set of a finished candidate -> (fit, directory, phases)

            def prune(result, refined, phases, working_dir, candidate_index, finished=None):
                # A phase that collapsed (zero weight, undefined lattice) or repeats a heavier
                # phase of the fit is dropped and the fit refined again, if it fits as well.
                weights_before = _fit_weights(refined) if known_groups else {}
                refined, phases, dropped = _prune_final_fit(
                    refined, phases, pattern_path, working_dir, final_refinement_params,
                    with_single_refinement_threads(refinement_kwargs), additional_cifs_name_set, finished,
                )
                removed_by_dir.setdefault(working_dir, {}).update(
                    {name: (reason, float(weights_before.get(name, 0.0))) for name, reason in dropped.items()}
                )
                if dropped:
                    result = _without_phases(result, set(dropped))
                    pruned_phases.extend(
                        {"candidate": candidate_index, "phase": name, "reason": reason}
                        for name, reason in sorted(dropped.items())
                    )
                return result, refined, phases
            for candidate_index, (result, screening_rwp) in enumerate(
                zip(results, screening_rwps), start=1
            ):
                if (
                    final_deadline is not None
                    and candidate_index >= 2
                    and refined_candidates
                    and time.monotonic() >= final_deadline
                ):
                    final_refinement_skipped = len(results) - candidate_index + 1
                    final_refinement_attempted = candidate_index - 1
                    break
                report_progress("Final candidate refinement", candidate_index - 1, len(results))
                candidate_dir = directory / f"_final_candidate_{candidate_index}"
                if candidate_dir.exists():
                    shutil.rmtree(candidate_dir)
                candidate_dir.mkdir()
                selected_phases = prepare(result)
                logger.info(
                    "Final refinement %d effective phase params: %s",
                    candidate_index,
                    {
                        phase.path.stem: {**final_refinement_params, **phase.params}
                        for phase in selected_phases
                    },
                )
                try:
                    refined = refit(selected_phases, candidate_dir)
                except Exception:
                    logger.exception(
                        "Final refinement failed for candidate %d; omitting it.",
                        candidate_index,
                    )
                    shutil.rmtree(candidate_dir, ignore_errors=True)
                    report_progress("Final candidate refinement", candidate_index, len(results))
                    continue

                result, refined, selected_phases = prune(
                    result, refined, selected_phases, candidate_dir, candidate_index, finished
                )
                finished[_phases_key(selected_phases)] = (refined, candidate_dir, selected_phases)
                checkpoint_candidate(candidate_index, refined, candidate_dir)
                report_progress("Final candidate refinement", candidate_index, len(results))
                refined_candidates.append((_with_refinement(result, refined), screening_rwp, candidate_dir))

            if not refined_candidates:
                raise ValueError("Final refinement failed for every candidate hypothesis.")

            def by_rwp(item):
                return item[0].refinement_result.lst_data.rwp

            refined_candidates.sort(key=by_rwp)
            if known_groups:
                # A minor phase stays in the best fit only if it explains unexplained peaks; a listed
                # phase the search did not pick joins it only if a trial fit shows it does.
                best, screening_rwp, best_dir = refined_candidates[0]
                best_index = int(best_dir.name.rsplit("_", 1)[1])
                best, trial_phases, changed = _check_listed_phases(
                    best, best_dir, known_groups, screening_report.get("rwp_by_reference") or {},
                    {Path(getattr(path, "path", path)).stem: RefinementPhase.make(path) for path in cif_paths},
                    removed_by_dir.setdefault(best_dir, {}), prepare=prepare, refit=refit,
                    count=lambda fit: _unexplained_peaks(fit, xrd_data, search_kwargs),
                    out_of_time=lambda: final_deadline is not None and time.monotonic() >= final_deadline,
                )
                pruned_phases.extend(  # a phase removed by leave-one-out is shown like a pruned one, with its weight
                    {"candidate": best_index, "phase": stem, "reason": reason, "weight_percent": round(100 * weight, 2)}
                    for stem, (reason, weight) in removed_by_dir[best_dir].items()
                    if reason == "removed_no_peak_evidence"
                )
                if changed:  # the checked fit stays the first candidate, whatever its Rwp
                    best, refined, _ = prune(best, best.refinement_result, trial_phases, best_dir, best_index)
                    checkpoint_candidate(best_index, refined, best_dir)
                    refined_candidates[0] = (_with_refinement(best, refined), screening_rwp, best_dir)
            results = [item[0] for item in refined_candidates]
            screening_rwps = [item[1] for item in refined_candidates]
            search_summary.update(
                {
                    "final_refinement_attempted": final_refinement_attempted,
                    "final_refinement_succeeded": len(refined_candidates),
                    "final_refinement_failed": (
                        final_refinement_attempted - len(refined_candidates)
                    ),
                    "displayed_hypotheses": len(refined_candidates),
                    "pruned_phases": pruned_phases,
                }
            )
            if known_groups:
                # The adopted (best) fit: what became of each listed phase.
                search_summary["known_phases"] = known_phases.known_phase_report(
                    known_groups,
                    _fit_weights(results[0].refinement_result),
                    removed_by_dir.get(refined_candidates[0][2], {}),
                )
            for rank, (result, _, source_dir) in enumerate(refined_candidates, start=1):
                prefix = "0_best" if rank == 1 else f"final_candidate_{rank}"
                target_dir = directory / (
                    f"{prefix}_rwp_{round(result.refinement_result.lst_data.rwp, 2)}"
                )
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                source_dir.rename(target_dir)
            best_result = results[0].refinement_result
        else:
            search_summary.update(
                {
                    "final_refinement_attempted": 0,
                    "final_refinement_succeeded": 0,
                    "final_refinement_failed": 0,
                    "displayed_hypotheses": len(results),
                }
            )
            if known_groups and not results:  # the search found no fit: still say what became of each phase
                search_summary["known_phases"] = known_phases.known_phase_report(
                    known_groups, {}, {}, reason="no_fit"
                )

        stopped_at_stage = None
        if screening_report.get("not_screened_deadline", 0) > 0:
            stopped_at_stage = "screening"
        elif search_loop.get("stopped"):
            stopped_at_stage = "search"
        elif final_refinement_skipped > 0:
            stopped_at_stage = "final_refinement"
        search_summary.update(
            {
                "runtime_budget_s": budget,
                "elapsed_s": round(time.monotonic() - t_start, 1),
                "stopped_early": stopped_at_stage is not None,
                "stopped_at_stage": stopped_at_stage,
                "unexpanded_nodes": int(search_loop.get("unexpanded_nodes", 0) or 0),
                "final_refinement_skipped": final_refinement_skipped,
                "screening_timed_out": int(screening_report.get("timed_out", 0) or 0),
                "screening_not_screened": int(
                    screening_report.get("not_screened_deadline", 0) or 0
                ),
                "candidates_screened_total": int(screening_report.get("total", 0) or 0),
                "prefilter_excluded": int(outcome.report.get("excluded", 0) or 0),
            }
        )

        parsed_results = [
            (
                [
                    [Cif.from_file(phase.path) for phase in phases]
                    for phases in result.phases
                ],
                result.refinement_result,
            )
            for result in results
        ]
        grouped_phases = [r.grouped_phases for r in results]

        # convert Path to str
        for i in range(len(grouped_phases)):
            for j in range(len(grouped_phases[i])):
                for k in range(len(grouped_phases[i][j])):
                    grouped_phases[i][j][k] = (
                        grouped_phases[i][j][k][0],
                        [p.stem for p in grouped_phases[i][j][k][1]],
                    )

        foms = [[list(f) for f in r.foms] for r in results]
        lattice_strains = [[list(ls) for ls in r.lattice_strains] for r in results]
        missing_peaks = [r.missing_peaks for r in results]
        extra_peaks = [r.extra_peaks for r in results]
        intensity_mismatch_peaks = []
        for result in results:
            metrics = result.refinement_result.refinement_metrics
            mismatch = metrics.intensity_mismatch_peaks if metrics else None
            intensity_mismatch_peaks.append(
                mismatch.tolist() if mismatch is not None else None
            )

        all_rwp = [i[1].lst_data.rwp for i in parsed_results]

        best_rwp = min(all_rwp, default=None)

        from dara.structure_quality import MP_LATTICE_WINDOW, mp_window_enabled

        candidate_summary["mp_lattice_window"] = {
            "enabled": mp_window_enabled(),
            "range": list(MP_LATTICE_WINDOW) if mp_window_enabled() else None,
            "applied_to": sorted(mp_applied_all),
        }

        return PhaseSearchDocument(
            task_label=self.name,
            results=parsed_results,
            screening_rwps=screening_rwps,
            foms=foms,
            lattice_strains=lattice_strains,
            missing_peaks=missing_peaks,
            extra_peaks=extra_peaks,
            intensity_mismatch_peaks=intensity_mismatch_peaks,
            final_result=best_result,
            best_rwp=best_rwp,
            xrd_data=xrd_data,
            input_cifs=cifs,
            precursors=precursors,
            elements=elements,
            analysis_mode=analysis_mode,
            candidate_sources=[db.name for db in [*(cif_dbs or []), *fallback_dbs]],
            candidate_summary=candidate_summary,
            search_summary=search_summary,
            phase_predictor=self.phase_predictor,
            predict_kwargs=predict_kwargs,
            search_kwargs=search_kwargs,
            final_refinement_params=final_refinement_params,
            run_final_refinement=self.run_final_refinement,
            cifs_folder_name=self.cifs_folder_name,
            grouped_phases=grouped_phases,
        )

    def _predict_folder(
        self, precursors, cifs_path, e_hull_filter: float = 0.05, **kwargs
    ) -> dict:
        if self.phase_predictor is None:
            raise ValueError("Phase predictor not provided!")

        prediction = self.phase_predictor.predict(precursors, **kwargs)
        dumpfn(prediction, "prediction.json")
        cost_cutoff = 0.1

        if any("CO3" in f for f in precursors):
            logger.info(
                "Carbonate compound detected, raising cost threshold to account for "
                "incorrectly modeled thermodynamics of CO2 production."
            )
            cost_cutoff = 0.5

        self.phase_predictor.write_cifs_from_formulas(
            prediction,
            dest_dir=cifs_path,
            cost_cutoff=cost_cutoff,
            e_hull_filter=e_hull_filter,
        )

        return prediction

    def _save_results(self, results: list[SearchResult]):
        results_sorted = sorted(results, key=lambda x: x.refinement_result.lst_data.rwp)
        for idx, search_result in enumerate(results_sorted):
            phases = search_result.phases
            main_folder = phases[0][0].path.parent.parent

            rwp = search_result.refinement_result.lst_data.rwp
            folder_path = main_folder / f"{idx+1}_result_rwp_{round(rwp,2)}"

            for fn in os.listdir(main_folder):
                if f"{idx+1}_result_rwp" in fn:
                    if os.path.isdir(main_folder / fn):
                        shutil.rmtree(main_folder / fn)
                    else:
                        os.remove(main_folder / fn)

                    logger.info(f"Removing old result: {fn}")

            os.mkdir(folder_path)

            for phase_i, phases_ in enumerate(phases):
                phase_folder = folder_path / f"phase_{phase_i+1}"
                phase_folder.mkdir(exist_ok=True)
                for phase in phases_:
                    shutil.copy(phase.path, phase_folder)

            logger.info(f"Successfully saved result {idx+1}")
