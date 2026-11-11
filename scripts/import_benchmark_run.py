"""Import finished benchmark samples into the server database, so they show in Results.

The benchmark runner calls the analysis directly and keeps each result only as
``<run>/samples/<id>/document.json.gz``. This script rebuilds the same submission job from
the ground-truth entry, stores the document as that job's result and adds a COMPLETED task.
Later run folders win for a sample that appears in several. Already imported samples are
skipped (matched by run and sample id), so the script can be rerun.

Usage (point ``DARA_SERVER_MONTYDB_PATH`` at the database to fill; back it up first)::

    python scripts/import_benchmark_run.py benchmarks/r3-eval-1108/all benchmarks/r6-stability
    python scripts/import_benchmark_run.py --dry-run benchmarks/r3-eval-1108/all
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _samples(run_dirs: list[Path]) -> dict[str, tuple[Path, Path]]:
    """``{sample id: (run dir, sample dir)}``; a later run replaces an earlier one."""
    found = {}
    for run_dir in run_dirs:
        for sample_dir in sorted((run_dir / "samples").glob("*")):
            if (sample_dir / "document.json.gz").is_file():
                found[sample_dir.name] = (run_dir, sample_dir)
    return found


def _run_label(run_dir: Path) -> str:
    """``r3-eval-1108`` for ``benchmarks/r3-eval-1108/all`` (sub-runs are named all / pw / pm)."""
    return run_dir.parent.name if run_dir.name in ("all", "pw", "pm", "pm-retry") else run_dir.name


def _ground_truth() -> dict:
    from dara.benchmark.ground_truth import load_ground_truth

    return {sample.id: sample for name in ("pairwise", "precursor") for sample in load_ground_truth(name).samples}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path, help="benchmark run folders (with samples/)")
    parser.add_argument("--user", default="benchmark")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from monty.serialization import loadfn

    from dara.benchmark.ground_truth import repo_root
    from dara.server.api_router import build_submission_job
    from dara.server.task_summary import runtime_seconds, summarize_stored_result
    from dara.server.utils import get_job_store, get_result_store, get_worker_store
    from dara.xrd import load_pattern

    truth = _ground_truth()
    with get_worker_store() as store:
        existing = {
            ((task.get("job") or {}).get("metadata") or {}).get("submission_settings", {}).get("benchmark_key")
            for task in store.query({}, properties=["job.metadata"])
        }
        next_index = max([task["index"] for task in store.query({}, properties=["index"])] or [0]) + 1

    imported = 0
    for sample_id, (run_dir, sample_dir) in sorted(_samples(args.runs).items()):
        key = f"{run_dir.as_posix()}::{sample_id}"
        sample = truth.get(sample_id)
        if key in existing:
            print(f"skip (already imported) {sample_id}")
            continue
        if sample is None:
            print(f"skip (no ground-truth entry) {sample_id}")
            continue
        score = json.loads((sample_dir / "score.json").read_text(encoding="utf-8")) if (sample_dir / "score.json").is_file() else {}
        submission = sample.submission
        job = build_submission_job(
            load_pattern(repo_root() / sample.pattern), Path(sample.pattern).name,
            analysis_mode=submission.analysis_mode, precursor_formulas=list(submission.precursors),
            elements=list(submission.elements),
            temperature=submission.temperature_c if submission.temperature_c is not None else -1,
            atmosphere=submission.atmosphere, instrument_profile=submission.instrument_profile,
            wavelength=submission.wavelength, use_cod=True, use_mp=True,
            sample=f"Benchmark {_run_label(run_dir)}: {sample_id}",
        )
        job.metadata["submission_settings"]["benchmark_key"] = key
        end = datetime.fromtimestamp((sample_dir / "document.json.gz").stat().st_mtime, tz=timezone.utc)
        start = end - timedelta(seconds=float(score.get("runtime_s") or 0))
        print(f"{'would import' if args.dry_run else 'import'} {sample_id} as task {next_index}")
        if args.dry_run:
            next_index += 1
            continue
        document = loadfn(sample_dir / "document.json.gz")
        result_store = get_job_store(get_result_store())
        with result_store:
            result_store.update(
                {"uuid": job.uuid, "index": job.index, "output": document, "completed_at": end.isoformat(),
                 "metadata": job.metadata, "hosts": [], "name": job.name},
                key=["uuid", "index"],
            )
        record = {
            "uuid": job.uuid, "index": next_index, "user": args.user, "status": "COMPLETED",
            "job": job.as_dict(), "submitted_time": start, "start_time": start, "end_time": end,
        }
        record["summary"] = summarize_stored_result(job.uuid, runtime_seconds(start, end))
        with get_worker_store() as store:
            store.update(record)
        next_index += 1
        imported += 1
    print(f"{imported} imported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
