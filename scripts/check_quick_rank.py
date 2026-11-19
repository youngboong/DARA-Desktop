"""Benchmark the quick ranking (search-match) on the dataset ground truth.

    python scripts/check_quick_rank.py [--mp] [--include-theoretical] [--json out.json] [id ...]

For each sample (the 2-min scans are skipped) the app's own path runs
(:func:`dara.search.quick_rank.quick_ranking`: pool with missing CIFs downloaded, ranking, and
structures of unphysical density left out), with COD plus Materials Project with ``--mp``.
Reported per run:

* major phase in the top 3 of the first ranking,
* truth phases recovered by the greedy combination (of those present in the pool), split into
  auto-selected (a pick that is not weak) and only weak (the user must add it by hand),
* picks split the same way, each true or false (false: matches no truth phase or allowed extra),
* truth phases missing from the pool,
* time: pool (stick simulation when cold) and scoring.

A truth phase matches a row when the reduced formula is accepted (formula or
``accept_any_of``) with the listed space groups, or when the composition lies within
3.5 at% of an accepted formula (the "lenient" rule of the prototype).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
warnings.simplefilter("ignore")


def _reduced(formula):
    from pymatgen.core import Composition

    try:
        return Composition(formula).reduced_formula
    except Exception:  # noqa: BLE001
        return formula


def truth_match(formula, sg, phase) -> bool:
    from dara.search.quick_rank import _close_composition

    accepted = [phase["formula"], *phase["accept_any_of"]]
    if any(_close_composition(_reduced(formula), _reduced(f)) for f in accepted):
        return True
    if formula not in {_reduced(f) for f in accepted}:
        return False
    if formula == _reduced(phase["formula"]) and phase["spacegroup"] is not None and not phase["accept_any_of"]:
        return sg in set(phase["spacegroup_any_of"]) | {phase["spacegroup"]}
    if phase["spacegroup_any_of"] and formula == _reduced(phase["formula"]):
        return sg in set(phase["spacegroup_any_of"]) | ({phase["spacegroup"]} if phase["spacegroup"] else set())
    return True


def samples(only):
    out = []
    for name in ("pairwise_reactions", "precursor_mixture"):
        data = json.loads((ROOT / f"dataset/ground_truth/{name}.json").read_text(encoding="utf-8"))
        out += [s for s in data["samples"] if s.get("scan") != "2min"]
    return [s for s in out if not only or any(o in s["id"] for o in only)]


def run(args) -> dict:
    from dara.search import quick_rank
    from dara.xrd import load_pattern

    results = []
    for sample in samples(args.ids):
        sub = sample["submission"]
        pattern = load_pattern(ROOT / sample["pattern"])
        ranked = quick_rank.quick_ranking(
            pattern.angles, pattern.intensities, elements=sub["elements"], precursors=sub["precursors"],
            wavelength=sub.get("wavelength", "Cu"), e_hull_cutoff=args.e_hull, use_mp=args.mp,
            include_theoretical=args.include_theoretical, background=args.background,
        )
        pool_s = ranked["pool_s"]
        score_s = ranked["elapsed_s"] - pool_s
        ranking, combination, candidates = ranked["ranking"], ranked["combination"], ranked["_candidates"]
        allowed = {_reduced(a) for a in sample.get("allowed_extra", [])}

        def is_true(c):
            return any(truth_match(c["formula"], c["spacegroup"], p) for p in sample["phases"]) or (
                _reduced(c["formula"]) in allowed)

        hits = []
        for phase in sample["phases"]:
            rank1 = next((r["rank"] for r in ranking if truth_match(r["formula"], r["spacegroup"], phase)), None)
            matching = [c for c in combination if truth_match(c["formula"], c["spacegroup"], phase)]
            hits.append({
                "phase": phase["formula"], "wt": phase["wt_percent"],
                "in_pool": any(truth_match(c.formula, c.spacegroup, phase) for c in candidates),
                "picked": bool(matching), "auto": any(not c["weak"] for c in matching),
                "rank": rank1,
            })
        false_picks = [
            f"{c['formula']}/{c['spacegroup']}{'/theo' if c['theoretical'] else ''}{'/weak' if c['weak'] else ''}"
            for c in combination if not is_true(c)
        ]
        row = {
            "id": sample["id"], "pool": ranked["pool"], "pool_s": round(pool_s, 2),
            "score_s": round(score_s, 2), "caveats": ranked["caveats"], "hits": hits,
            "picks": [f"{c['formula']}/{c['spacegroup']}/{c['source']}/{c['gain']}" for c in combination],
            "pick_kinds": [("weak" if c["weak"] else "auto") + ("_true" if is_true(c) else "_false")
                           for c in combination],
            "false_picks": false_picks,
        }
        results.append(row)
        print(json.dumps(row), flush=True)
    return summarize(results)


def summarize(results) -> dict:
    phases = [h for r in results for h in r["hits"]]
    major = [max(r["hits"], key=lambda h: h["wt"] or 0) for r in results]
    in_pool = [h for h in phases if h["in_pool"]]
    kinds = Counter(k for r in results for k in r["pick_kinds"])
    summary = {
        "samples": len(results),
        "phases": len(phases),
        "major_in_top3": sum(1 for h in major if h["rank"] and h["rank"] <= 3),
        "in_pool": len(in_pool),
        "missing_from_pool": [h["phase"] for h in phases if not h["in_pool"]],
        "recovered_by_combination": sum(1 for h in in_pool if h["picked"]),
        "truth_auto_selected": sum(1 for h in phases if h["auto"]),
        "truth_only_weak": sum(1 for h in phases if h["picked"] and not h["auto"]),
        "truth_missed": sum(1 for h in phases if not h["picked"]),
        "picks": sum(len(r["picks"]) for r in results),
        "auto_true": kinds["auto_true"], "auto_false": kinds["auto_false"],
        "weak_true": kinds["weak_true"], "weak_false": kinds["weak_false"],
        "false_picks": sum(len(r["false_picks"]) for r in results),
        "false_pick_counts": Counter(f for r in results for f in r["false_picks"]).most_common(40),
        "pool_s_total": round(sum(r["pool_s"] for r in results), 1),
        "score_s_median": sorted(r["score_s"] for r in results)[len(results) // 2] if results else None,
        "score_s_max": max((r["score_s"] for r in results), default=None),
        "pool_size_median": sorted(r["pool"]["candidates"] for r in results)[len(results) // 2] if results else None,
        "pool_size_max": max((r["pool"]["candidates"] for r in results), default=None),
        "simulated": sum(r["pool"]["simulated"] for r in results),
    }
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="*", help="only samples whose id contains one of these")
    parser.add_argument("--mp", action="store_true", help="add Materials Project candidates")
    parser.add_argument("--include-theoretical", action="store_true", help="rank computed-only MP entries")
    parser.add_argument("--e-hull", type=float, default=0.05, help="e-hull cutoff (search default 0.05)")
    parser.add_argument("--json", type=Path, help="write the summary here")
    parser.add_argument("--background", type=json.loads, default=None,
                        help='background settings (JSON), e.g. {"method": "sonneveld_visser", "granularity": 20}')
    args = parser.parse_args(argv)
    summary = run(args)
    print("SUMMARY " + json.dumps(summary))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
