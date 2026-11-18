"""Benchmark the quick ranking (search-match) on the dataset ground truth.

    python scripts/check_quick_rank.py [--mp] [--include-theoretical] [--json out.json] [id ...]

For each sample (the 2-min scans are skipped) the production code builds the candidate
pool (COD, plus Materials Project with ``--mp``) and ranks it. Reported per run:

* major phase in the top 3 of the first ranking,
* truth phases recovered by the greedy combination (of those present in the pool),
* false picks (combination picks that match no truth phase or allowed extra),
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
import time
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
        wavelength = sub.get("wavelength", "Cu")
        lam1, _ = quick_rank.wavelength_lines(wavelength)
        lo, hi = float(min(pattern.angles)), float(max(pattern.angles))
        started = time.perf_counter()
        pool = quick_rank.build_pool(
            sub["elements"], sub["precursors"], lam1, (max(1.0, lo - 6), min(178.0, hi + 6)),
            e_hull_cutoff=args.e_hull, use_mp=args.mp, include_theoretical=args.include_theoretical,
        )
        pool_s = time.perf_counter() - started
        started = time.perf_counter()
        ranked = quick_rank.rank(pattern.angles, pattern.intensities, pool.candidates, wavelength=wavelength,
                                 background=args.background)
        score_s = time.perf_counter() - started
        ranking, combination = ranked["ranking"], ranked["combination"]
        allowed = {_reduced(a) for a in sample.get("allowed_extra", [])}
        hits = []
        for phase in sample["phases"]:
            rank1 = next((r["rank"] for r in ranking if truth_match(r["formula"], r["spacegroup"], phase)), None)
            hits.append({
                "phase": phase["formula"], "wt": phase["wt_percent"],
                "in_pool": any(truth_match(c.formula, c.spacegroup, phase) for c in pool.candidates),
                "picked": any(truth_match(c["formula"], c["spacegroup"], phase) for c in combination),
                "rank": rank1,
            })
        false_picks = [
            f"{c['formula']}/{c['spacegroup']}{'/theo' if c['theoretical'] else ''}"
            for c in combination
            if not any(truth_match(c["formula"], c["spacegroup"], p) for p in sample["phases"])
            and _reduced(c["formula"]) not in allowed
        ]
        row = {
            "id": sample["id"], "pool": pool.summary(), "pool_s": round(pool_s, 2),
            "score_s": round(score_s, 2), "caveats": pool.caveats, "hits": hits,
            "picks": [f"{c['formula']}/{c['spacegroup']}/{c['source']}/{c['gain']}" for c in combination],
            "false_picks": false_picks,
        }
        results.append(row)
        print(json.dumps(row), flush=True)
    return summarize(results)


def summarize(results) -> dict:
    phases = [h for r in results for h in r["hits"]]
    major = [max(r["hits"], key=lambda h: h["wt"] or 0) for r in results]
    in_pool = [h for h in phases if h["in_pool"]]
    summary = {
        "samples": len(results),
        "phases": len(phases),
        "major_in_top3": sum(1 for h in major if h["rank"] and h["rank"] <= 3),
        "in_pool": len(in_pool),
        "missing_from_pool": [h["phase"] for h in phases if not h["in_pool"]],
        "recovered_by_combination": sum(1 for h in in_pool if h["picked"]),
        "picks": sum(len(r["picks"]) for r in results),
        "false_picks": sum(len(r["false_picks"]) for r in results),
        "false_pick_counts": Counter(f for r in results for f in r["false_picks"]).most_common(10),
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
