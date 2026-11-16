"""Structure library packs: pre-download reference structures for offline use and instant ranking.

A pack is a list of elements (``data/structure_packs.json``). It contains every structure
whose elements all lie inside that list, from COD and from Materials Project, with the
filters a phase search applies to its candidates (energy above hull, common gases,
molecular organics; COD files flagged theoretical are downloaded and then skipped like in a
search). Nothing else is left out: no limit on elements per compound, no deduplication.

The download goes into the folders a search and the quick ranking already read
(``structure_db.cod_cache_dir()``, ``structure_db.mp_cache_dir()``,
``quick_rank.stick_cache_dir()``), in order of usefulness (fewest elements first, then the
most common chemical systems), so a partial download is already useful. Every step is
resumable and every file is written atomically, so a cancel at any time is safe.

Command line (runs without the server and without ray)::

    python -m dara.structure_library packs --json
    python -m dara.structure_library estimate --packs recommended [--elements Li O ...] --json
    python -m dara.structure_library download --packs recommended [--elements ...]
        [--sources cod,mp] [--no-sticks] [--workers N] [--cancel-file PATH] --progress-json
    python -m dara.structure_library status [--packs ...] [--elements ...] --json

``download`` prints one JSON line per step:
``{"phase", "done", "total", "bytes", "eta_s", "message"}`` and exits with 0 (done),
2 (cancelled or partial: run it again to resume) or 1 (error).
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

PACKS_FILE = Path(__file__).parent / "data" / "structure_packs.json"
FORMAT_VERSION = 1
EXIT_DONE, EXIT_ERROR, EXIT_PARTIAL = 0, 1, 2
COD_CHUNK = 40
MP_CHUNK = 200
STICK_CHUNK = 64
STICK_WAVELENGTH = "Cu"
# cached as the canonical (1, 130) range of quick_rank: serves every scan up to 2theta = 124 deg
# (the ranking needs the scan +-6 deg); (1, 90) would only serve scans ending below 84 deg
STICK_RANGE = (1.0, 130.0)


class Cancelled(Exception):
    """The user asked the download to stop."""


# --------------------------------------------------------------------------- #
# packs
# --------------------------------------------------------------------------- #
def load_packs(path: Path = PACKS_FILE) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"Unsupported structure pack format {data.get('format_version')}")
    return data


def element_set(pack_ids=(), elements=(), data: dict | None = None) -> list[str]:
    """Union of the packs' elements and extra ``elements`` (validated symbols)."""
    from pymatgen.core import Element

    data = data or load_packs()
    by_id = {pack["id"]: pack for pack in data["packs"]}
    chosen: set[str] = set()
    for pack_id in pack_ids or ():
        if pack_id not in by_id:
            raise ValueError(f"Unknown pack {pack_id!r}; known: {', '.join(by_id)}")
        chosen.update(by_id[pack_id]["elements"])
    for symbol in elements or ():
        chosen.add(str(Element(str(symbol).strip().capitalize())))
    if not chosen:
        raise ValueError("Choose a pack or at least one element.")
    return sorted(chosen)


def _search_cutoff(data: dict) -> float:
    return float(data.get("e_hull_cutoff", 0.05))


def cod_selection(elements, e_hull_cutoff: float, database=None) -> list[dict]:
    """COD index rows a search could use whose elements all lie in ``elements``, best first.

    Same filters as ``structure_db.cod_candidate_rows`` (common gases, e-hull cutoff,
    molecular organics); order: fewest elements, then most common chemical system, then id.
    """
    from dara.structure_db import COMMON_GASES, is_molecular_organic, shared_cod_database

    info = (database or shared_cod_database()).preparsed_info
    allowed = set(elements)
    rows, seen = [], set()
    for chemsys, entries in info.items():
        names = chemsys.split("-")
        if not set(names) <= allowed:
            continue
        for formula, code, sg, e_hull in entries:
            if formula in COMMON_GASES or (e_hull is not None and e_hull > e_hull_cutoff):
                continue
            if is_molecular_organic(formula) or str(code) in seen:
                continue
            seen.add(str(code))
            rows.append({"id": str(code), "formula": formula, "spacegroup": sg, "chemsys": chemsys,
                         "n_elements": len(names), "popularity": len(entries)})
    rows.sort(key=lambda r: (r["n_elements"], -r["popularity"], r["chemsys"], r["id"]))
    return rows


def _mp_rows_file(elements, cutoff) -> Path:
    from dara.structure_db import mp_cache_dir

    digest = hashlib.sha1(f"{','.join(sorted(elements))}|{cutoff}".encode()).hexdigest()[:12]
    return mp_cache_dir() / f"library-rows-{digest}.json"


def mp_selection(elements, e_hull_cutoff: float, *, refresh_days: float = 7.0, offline: bool = False) -> list[dict] | None:
    """MP rows (same filters as a search) inside ``elements``, best first.

    Kept in the MP cache for ``refresh_days``; ``offline`` returns the kept list or ``None``.
    """
    from dara.structure_db import MaterialsProjectDatabase, atomic_write_text

    path = _mp_rows_file(elements, e_hull_cutoff)
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        fresh = time.time() - kept["fetched_at"] < refresh_days * 86400
    except (OSError, ValueError, KeyError):
        kept, fresh = None, False
    if offline or (kept and fresh):
        return kept["rows"] if kept else None
    rows = MaterialsProjectDatabase(e_hull_filter=e_hull_cutoff).rows_within(elements, e_hull_cutoff, timeout_s=300)
    popularity = Counter(r["chemsys"] for r in rows)
    for row in rows:
        row["n_elements"] = len(str(row["chemsys"]).split("-"))
        row["popularity"] = popularity[row["chemsys"]]
    rows.sort(key=lambda r: (r["n_elements"], -r["popularity"], str(r["chemsys"]), r["material_id"]))
    atomic_write_text(path, json.dumps({"fetched_at": time.time(), "cutoff": e_hull_cutoff,
                                        "elements": sorted(elements), "rows": rows}))
    return rows


def _listing(folder: Path, suffix: str) -> set[str]:
    try:
        return {entry.name[: -len(suffix)] for entry in os.scandir(folder) if entry.name.endswith(suffix)}
    except OSError:
        return set()


def _unavailable_file() -> Path:
    from dara.structure_db import cod_cache_dir

    return cod_cache_dir() / ".library-unavailable.json"


def _read_unavailable() -> set[str]:
    try:
        return set(json.loads(_unavailable_file().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


# --------------------------------------------------------------------------- #
# estimate / status
# --------------------------------------------------------------------------- #
def estimate(pack_ids=(), elements=(), data: dict | None = None) -> dict:
    """Counts, MB and minutes without network access.

    COD counts come from the bundled index; MP counts are the measured pack counts (a custom
    element set scales them by its COD count, marked ``mp_estimated``).
    """
    data = data or load_packs()
    chosen = element_set(pack_ids, elements, data)
    cutoff = _search_cutoff(data)
    m = data["measured"]
    cod = len(cod_selection(chosen, cutoff))
    exact = next((p for p in data["packs"] if sorted(p["elements"]) == chosen), None)
    if exact is not None and exact.get("counts", {}).get("mp") is not None:
        mp, mp_estimated = int(exact["counts"]["mp"]), False
    else:
        ref = next((p for p in data["packs"] if p["id"] == "recommended"), data["packs"][0])
        ratio = ref["counts"]["mp"] / max(1, ref["counts"]["cod"])
        mp, mp_estimated = int(round(cod * ratio)), True
    sticks = round((cod * (1 - m["cod_theoretical_fraction"]) + mp))
    workers = m["stick_workers"]
    result = {
        "elements": chosen,
        "e_hull_cutoff": cutoff,
        "counts": {"cod": cod, "mp": mp, "sticks": sticks},
        "mp_estimated": mp_estimated,
        "download_mb": {
            "cod": round(cod * m["cod_mean_cif_kb"] / 1024, 1),
            "mp": round(mp * m["mp_mean_cif_kb"] / 1024, 1),
        },
        "disk_mb": {
            "cod": round(cod * m["cod_disk_kb"] / 1024, 1),
            "mp": round(mp * m["mp_disk_kb"] / 1024, 1),
            "sticks": round(sticks * m["stick_disk_kb"] / 1024, 1),
        },
        "minutes": {
            "cod": round(cod / m["cod_per_s"] / 60, 1),
            "mp": round(mp / m["mp_per_s"] / 60 + m["mp_index_s"] / 60, 1),
            "sticks": round(sticks * m["stick_s_per_cif"] / workers / 60, 1),
        },
        "stick_workers": workers,
    }
    result["download_mb"]["total"] = round(sum(result["download_mb"].values()), 1)
    result["disk_mb"]["total"] = round(sum(result["disk_mb"].values()), 1)
    result["minutes"]["total"] = round(sum(result["minutes"].values()), 1)
    return result


def status(pack_ids=(), elements=(), data: dict | None = None) -> dict:
    """What is cached for the chosen element set (no network)."""
    from dara.search.quick_rank import stick_path, wavelength_lines
    from dara.structure_db import cod_cache_dir, mp_cache_dir

    data = data or load_packs()
    chosen = element_set(pack_ids, elements, data)
    cutoff = _search_cutoff(data)
    cod_ids = [r["id"] for r in cod_selection(chosen, cutoff)]
    cod_have = _listing(cod_cache_dir(), ".cif")
    unavailable = _read_unavailable()
    mp_rows = mp_selection(chosen, cutoff, offline=True)
    mp_have = _listing(mp_cache_dir(), ".cif")
    lam1, _ = wavelength_lines(STICK_WAVELENGTH)
    stick_dir = stick_path("cod:0", lam1).parent
    stick_have = _listing(stick_dir, ".npz")
    cod_cached = sum(1 for i in cod_ids if i in cod_have)
    mp_ids = [r["material_id"] for r in mp_rows] if mp_rows is not None else None
    sticks_cached = sum(1 for i in cod_ids if f"cod_{i}" in stick_have)
    if mp_ids:
        sticks_cached += sum(1 for i in mp_ids if f"mp_{i}" in stick_have)
    return {
        "elements": chosen,
        "cod": {"total": len(cod_ids), "cached": cod_cached,
                "unavailable": sum(1 for i in cod_ids if i in unavailable)},
        "mp": {"total": len(mp_ids) if mp_ids is not None else None,
               "cached": sum(1 for i in mp_ids if i in mp_have) if mp_ids is not None else None},
        "sticks": {"cached": sticks_cached},
        "folders": {"cod": str(cod_cache_dir()), "mp": str(mp_cache_dir()), "sticks": str(stick_dir)},
    }


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #
class Progress:
    def __init__(self, enabled: bool, stream=None):
        self.enabled = enabled
        self.stream = stream or sys.stdout
        self.phase_started = time.monotonic()
        self.phase = None

    def emit(self, phase, done, total, nbytes=0, message="", eta_s=None):
        if phase != self.phase:
            self.phase, self.phase_started = phase, time.monotonic()
            self.phase_done0 = done
        if eta_s is None and total and done > self.phase_done0:
            rate = (done - self.phase_done0) / max(1e-6, time.monotonic() - self.phase_started)
            eta_s = round((total - done) / rate, 1) if rate > 0 else None
        line = {"phase": phase, "done": int(done), "total": int(total), "bytes": int(nbytes),
                "eta_s": eta_s, "message": message}
        if self.enabled:
            self.stream.write(json.dumps(line) + "\n")
            self.stream.flush()
        return line


def _check_cancel(cancel_file):
    """Raise ``Cancelled`` when ``cancel_file`` (a path, or a callable like ``should_stop``) says stop."""
    if cancel_file and (cancel_file() if callable(cancel_file) else Path(cancel_file).exists()):
        raise Cancelled()


def _download_cod(ids, progress: Progress, cancel_file, chunk_size: int | None = None) -> tuple[int, int]:
    """Fetch missing COD CIFs with the search's own polite downloader. Returns (failed, done).

    Cancel and progress are checked every ``chunk_size`` ids (default ``COD_CHUNK``).
    """
    from dara.structure_db import CODDatabase, _is_not_found_error, atomic_write_text, cod_cache_dir

    folder = cod_cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    have = _listing(folder, ".cif")
    unavailable = _read_unavailable()
    missing = [i for i in ids if i not in have and i not in unavailable]
    total, done, nbytes, failed = len(ids), len(ids) - len(missing), 0, 0
    progress.emit("cod", done, total, 0, f"{len(missing)} COD structures to download")
    db = CODDatabase.__new__(CODDatabase)  # the index is not needed to download
    db.last_unavailable = []
    original = CODDatabase._download_cod
    not_found: list[str] = []

    def fetch(cod_id):
        try:
            return original(cod_id)
        except Exception as exc:
            if _is_not_found_error(exc):
                not_found.append(cod_id)
            raise

    db._download_cod = fetch
    step = chunk_size or COD_CHUNK
    for start in range(0, len(missing), step):
        _check_cancel(cancel_file)
        chunk = missing[start : start + step]
        cifs, bad = db._fetch_many(chunk, folder)
        failed += len(bad) - len([i for i in bad if i in not_found])
        nbytes += sum((folder / f"{i}.cif").stat().st_size for i in cifs if (folder / f"{i}.cif").is_file())
        done += len(chunk)
        if not_found:
            atomic_write_text(_unavailable_file(), json.dumps(sorted(_read_unavailable() | set(not_found))))
        progress.emit("cod", done, total, nbytes, f"{len(cifs)} downloaded, {len(bad)} failed in this step")
        if len(bad) == len(chunk) and len(chunk) >= 8 and not set(bad) <= set(not_found):
            raise ConnectionError("COD (www.crystallography.net) is not reachable; try again later.")
    return failed, done


def _download_mp(rows, progress: Progress, cancel_file) -> int:
    from dara.structure_db import MaterialsProjectDatabase, mp_cache_dir, mp_cache_rows, write_mp_index

    folder = mp_cache_dir()
    have = _listing(folder, ".cif")
    # a kill between writing CIFs and their index rows leaves unindexed CIFs: index them now
    indexed = mp_cache_rows()
    keys = ("material_id", "formula", "spacegroup", "e_above_hull", "theoretical", "chemsys")
    unindexed = [{k: r.get(k) for k in keys} for r in rows
                 if r["material_id"] in have and r["material_id"] not in indexed]
    if unindexed:
        write_mp_index("library", unindexed)
    missing = [r["material_id"] for r in rows if r["material_id"] not in have]
    total, done, nbytes = len(rows), len(rows) - len(missing), 0
    progress.emit("mp", done, total, 0, f"{len(missing)} Materials Project structures to download")
    db = MaterialsProjectDatabase()
    for start in range(0, len(missing), MP_CHUNK):
        _check_cancel(cancel_file)
        chunk = missing[start : start + MP_CHUNK]
        db.download_to_cache(chunk, "library")
        nbytes += sum((folder / f"{i}.cif").stat().st_size for i in chunk if (folder / f"{i}.cif").is_file())
        done += len(chunk)
        progress.emit("mp", done, total, nbytes, f"{done} of {total} Materials Project structures")
    have = _listing(folder, ".cif")
    return sum(1 for r in rows if r["material_id"] not in have)


def _stick_job(items, wavelength_a, two_theta_range):
    """Worker: simulate and cache sticks of ``[(source_id, path)]``; returns the count done."""
    import warnings

    warnings.simplefilter("ignore")
    from dara.search.quick_rank import simulate_sticks

    for source_id, path in items:
        simulate_sticks(source_id, path, wavelength_a, two_theta_range)
    return len(items)


def _precompute_sticks(cod_ids, mp_ids, workers: int, progress: Progress, cancel_file) -> None:
    from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

    from dara.search.quick_rank import stick_path, wavelength_lines
    from dara.structure_db import cod_cache_dir, is_theoretical_cif, mp_cache_dir

    lam1, _ = wavelength_lines(STICK_WAVELENGTH)
    have = _listing(stick_path("cod:0", lam1).parent, ".npz")
    cod_folder, mp_folder = cod_cache_dir(), mp_cache_dir()
    cod_files, mp_files = _listing(cod_folder, ".cif"), _listing(mp_folder, ".cif")
    todo = []
    for i in cod_ids:
        if i in cod_files and f"cod_{i}" not in have and not is_theoretical_cif(cod_folder / f"{i}.cif"):
            todo.append((f"cod:{i}", str(cod_folder / f"{i}.cif")))
    for i in mp_ids:
        if i in mp_files and f"mp_{i}" not in have:
            todo.append((f"mp:{i}", str(mp_folder / f"{i}.cif")))
    total = len(todo)
    progress.emit("sticks", 0, total, 0, f"{total} structures to simulate with {workers} worker(s)")
    chunks = [todo[i : i + STICK_CHUNK] for i in range(0, total, STICK_CHUNK)]
    done = 0
    if workers <= 1:
        for chunk in chunks:
            _check_cancel(cancel_file)
            done += _stick_job(chunk, lam1, STICK_RANGE)
            progress.emit("sticks", done, total, 0, "")
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = set()
        queue = iter(chunks)
        try:
            for chunk in itertools.islice(queue, workers * 2):
                pending.add(pool.submit(_stick_job, chunk, lam1, STICK_RANGE))
            while pending:
                finished, pending = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
                for future in finished:
                    done += future.result()
                    nxt = next(queue, None)
                    if nxt is not None:
                        pending.add(pool.submit(_stick_job, nxt, lam1, STICK_RANGE))
                if finished:
                    progress.emit("sticks", done, total, 0, "")
                _check_cancel(cancel_file)
        except BaseException:
            for future in pending:
                future.cancel()
            raise


def download(pack_ids=(), elements=(), sources=("cod", "mp"), *, sticks=True, workers=None,
             cancel_file=None, progress: Progress | None = None, data: dict | None = None) -> int:
    """Download (resumable) and return the exit code."""
    progress = progress or Progress(False)
    data = data or load_packs()
    try:
        chosen = element_set(pack_ids, elements, data)
    except (ValueError, KeyError) as exc:
        progress.emit("error", 0, 0, 0, str(exc))
        return EXIT_ERROR
    cutoff = _search_cutoff(data)
    workers = workers or int(data["measured"].get("stick_workers", 2))
    partial = False
    cod_ids: list[str] = []
    mp_ids: list[str] = []
    try:
        progress.emit("select", 0, 1, 0, f"Selecting structures of {len(chosen)} elements")
        if "cod" in sources:
            cod_ids = [r["id"] for r in cod_selection(chosen, cutoff)]
        mp_rows = []
        if "mp" in sources:
            from dara import known_phases

            if not known_phases.mp_configured():
                progress.emit("mp", 0, 0, 0, "Materials Project is not configured (no MP_API_KEY): skipped.")
                partial = True
            else:
                _check_cancel(cancel_file)
                progress.emit("select", 0, 1, 0, "Listing Materials Project entries (about 2 minutes)")
                try:
                    mp_rows = mp_selection(chosen, cutoff)
                except Exception as exc:  # noqa: BLE001 - a bad key or no network must not stop COD
                    mp_rows = mp_selection(chosen, cutoff, offline=True) or []
                    progress.emit("mp", 0, 0, 0, f"Materials Project skipped: {type(exc).__name__}"
                                  + ("; using the stored listing" if mp_rows else ""))
                    partial = True
                mp_ids = [r["material_id"] for r in mp_rows]
        progress.emit("select", 1, 1, 0, f"{len(cod_ids)} COD and {len(mp_ids)} Materials Project structures")
        if cod_ids:
            failed, _ = _download_cod(cod_ids, progress, cancel_file)
            partial |= failed > 0
        if mp_rows:
            try:
                partial |= _download_mp(mp_rows, progress, cancel_file) > 0
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the sticks of what is cached
                progress.emit("mp", 0, 0, 0, f"Materials Project skipped: {type(exc).__name__}")
                partial = True
        if sticks:
            _precompute_sticks(cod_ids, mp_ids, max(1, int(workers)), progress, cancel_file)
    except (Cancelled, KeyboardInterrupt):
        progress.emit("cancelled", 0, 0, 0, "Stopped; run the download again to resume.")
        return EXIT_PARTIAL
    except Exception as exc:  # noqa: BLE001
        progress.emit("error", 0, 0, 0, f"{type(exc).__name__}: {exc}")
        return EXIT_PARTIAL if (cod_ids or mp_ids) else EXIT_ERROR
    progress.emit("done", 1, 1, 0, "Finished with some structures missing; run again to retry them."
                  if partial else "Finished.")
    return EXIT_PARTIAL if partial else EXIT_DONE


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def _split(values) -> list[str]:
    return [v for item in values or [] for v in str(item).replace(",", " ").split() if v]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dara.structure_library", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("packs", "estimate", "download", "status"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true", help="print JSON")
        if name != "packs":
            command.add_argument("--packs", nargs="*", default=[], help="pack ids (e.g. recommended)")
            command.add_argument("--elements", nargs="*", default=[], help="extra element symbols")
        if name == "download":
            command.add_argument("--sources", default="cod,mp", help="cod,mp")
            command.add_argument("--no-sticks", action="store_true", help="skip the stick precompute")
            command.add_argument("--workers", type=int, default=None, help="stick simulation processes")
            command.add_argument("--cancel-file", default=None, help="stop when this file exists")
            command.add_argument("--progress-json", action="store_true", help="one JSON line per step")
    args = parser.parse_args(argv)
    packs = _split(getattr(args, "packs", []))
    elements = _split(getattr(args, "elements", []))
    try:
        if args.command == "packs":
            data = load_packs()
            out = {"format_version": data["format_version"], "e_hull_cutoff": _search_cutoff(data),
                   "measured": data["measured"], "packs": data["packs"]}
        elif args.command == "estimate":
            out = estimate(packs, elements)
        elif args.command == "status":
            out = status(packs, elements)
        else:
            sources = [s for s in _split([args.sources]) if s in ("cod", "mp")]
            return download(packs, elements, sources, sticks=not args.no_sticks, workers=args.workers,
                            cancel_file=args.cancel_file, progress=Progress(args.progress_json))
    except (ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(json.dumps(out, indent=None if args.json else 1))
    return EXIT_DONE


if __name__ == "__main__":
    raise SystemExit(main())
