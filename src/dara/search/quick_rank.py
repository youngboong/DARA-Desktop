"""Quick phase ranking ("search-match"): a ranked list of candidates to refine, in seconds.

This is never a result: it reports no amounts and identifies nothing. It ranks the
reference structures a phase search could use by how much of the measured net
intensity their strong reflections can explain, then greedily combines the best ones
on the residual.

Per candidate (all numpy, no BGMN):

1. Observed profile as in :func:`dara.search.prefilter.observed_profile`.
2. Sticks merged within 0.1 deg; strong peaks = rel >= 0.10, at most 15.
3. Amount bound ``K`` from the :func:`~dara.search.prefilter.candidate_bound` grid (lattice
   scale 0.98..1.02, zero shift +-0.3 deg, worst 20 % of strong peaks ignored).
4. Explained fraction ``E`` at the 6 best grid points: Gaussian K-alpha1 + 0.5 K-alpha2 sticks
   of height ``K * rel``; ``E = sum(min(model, residual)) / sum(net)``.
5. ``score = E * (1 - 5 |s - 1|)``.
6. ``matched``/``expected``: strong peaks whose observed window maximum reaches half the model.
7. Greedy combination: subtract the best model, re-score, at most 3 more steps, stop below a
   gain of 0.03 or, as an analyst stops searching again, when no significant unexplained peak
   is left (:func:`residual_maxima`, the rule of :func:`residual_step`); a pick with gain below
   0.08 is "weak".
8. Grouping: same (formula, space group) is one row; same formula with another space group is a
   "polymorph"; a composition within 3.5 at% of a higher row is a "variant of X".

Simulated reflections ("sticks") are cached on disk per structure and wavelength
(:func:`stick_cache_dir`), so a warm pool of ~150 candidates scores in about a second.
"""

from __future__ import annotations

import itertools
import math
import os
import threading
import time
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d, maximum_filter1d

from dara.search.prefilter import (
    PrefilterConfig,
    _in_ranges,
    _merge_reflections,
    background_settings,
    candidate_bound,
    observed_profile,
    simulate_reflections,
)
from dara.utils import get_logger

logger = get_logger(__name__)

STICK_VERSION = 1
STICK_CACHE_ENV_VAR = "DARA_STICK_CACHE_DIR"
# Sticks are simulated over the smallest of these ranges that covers the pattern (+-6 deg),
# then cropped, so one file serves every pattern measured with that wavelength.
CANONICAL_RANGES = ((1.0, 90.0), (1.0, 130.0), (1.0, 178.0))

CONFIG = PrefilterConfig(scale_range=(0.98, 1.02), min_strong=2)
TOP_GRID_POINTS = 6
SCALE_PENALTY = 5.0
STOP_GAIN = 0.03
WEAK_GAIN = 0.08
MAX_EXTRA_STEPS = 3
VARIANT_AT_FRACTION = 0.035
MAX_ROWS = 60
MAX_STICKS = 60


# --------------------------------------------------------------------------- #
# stick cache
# --------------------------------------------------------------------------- #
def stick_cache_dir() -> Path:
    """Folder of cached sticks: ``$DARA_STICK_CACHE_DIR`` or ``dara_sticks`` next to the COD cache."""
    configured = os.environ.get(STICK_CACHE_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    from dara.structure_db import cod_cache_dir

    return cod_cache_dir().parent / "dara_sticks"


def canonical_range(two_theta_range) -> tuple[float, float]:
    need_lo = max(1.0, float(two_theta_range[0]))
    need_hi = min(178.0, float(two_theta_range[1]))
    for lo, hi in CANONICAL_RANGES:
        if lo <= need_lo and hi >= need_hi:
            return lo, hi
    return CANONICAL_RANGES[-1]


def stick_path(source_id: str, wavelength_a: float) -> Path:
    """``<cache>/w<lambda>-v<version>/<source>_<id>.npz`` for ``source_id`` like ``cod:1000041``."""
    safe = str(source_id).replace(":", "_").replace("/", "_").replace("\\", "_")
    return stick_cache_dir() / f"w{wavelength_a:.5f}-v{STICK_VERSION}" / f"{safe}.npz"


_MEMO: "OrderedDict[str, tuple]" = OrderedDict()
_MEMO_MAX = 20000
_MEMO_LOCK = threading.Lock()


def _read_sticks(path: Path):
    key = str(path)
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit is not None:
            _MEMO.move_to_end(key)
            return hit
    try:
        with np.load(path) as data:
            hit = (
                float(data["lo"]), float(data["hi"]), bool(data["failed"]),
                data["two_theta"].copy(), data["intensity"].copy(), data["d"].copy(),
            )
    except Exception:  # noqa: BLE001 - unreadable or partial file: simulate again
        return None
    with _MEMO_LOCK:
        _MEMO[key] = hit
        while len(_MEMO) > _MEMO_MAX:
            _MEMO.popitem(last=False)
    return hit


def _write_sticks(path: Path, lo, hi, failed, two_theta, intensity, d) -> None:
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            np.savez(
                handle, lo=lo, hi=hi, failed=failed, version=STICK_VERSION,
                two_theta=np.asarray(two_theta, float), intensity=np.asarray(intensity, float),
                d=np.asarray(d, float),
            )
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    with _MEMO_LOCK:
        _MEMO.pop(str(path), None)


def cached_sticks(source_id: str, wavelength_a: float, two_theta_range):
    """Cached ``(two_theta, intensity, d)`` covering the range, ``"failed"``, or ``None`` (not cached)."""
    hit = _read_sticks(stick_path(source_id, wavelength_a))
    if hit is None:
        return None
    lo, hi, failed, two_theta, intensity, d = hit
    want = canonical_range(two_theta_range)
    if lo > want[0] or hi < want[1]:
        return None
    if failed:
        return "failed"
    keep = (two_theta >= two_theta_range[0]) & (two_theta <= two_theta_range[1])
    return two_theta[keep], intensity[keep], d[keep]


def simulate_sticks(source_id: str, cif_path, wavelength_a: float, two_theta_range):
    """Sticks of a structure for ``two_theta_range``: from the cache, else simulated and cached.

    Returns ``None`` when the structure cannot be simulated (remembered in the cache).
    """
    cached = cached_sticks(source_id, wavelength_a, two_theta_range)
    if cached is not None:
        return None if isinstance(cached, str) else cached
    lo, hi = canonical_range(two_theta_range)
    path = stick_path(source_id, wavelength_a)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            two_theta, intensity, d = simulate_reflections(Path(cif_path), wavelength_a, (lo, hi))
        failed = False
    except Exception as exc:  # noqa: BLE001
        logger.debug("Sticks of %s failed: %s", source_id, exc)
        two_theta = intensity = d = np.zeros(0)
        failed = True
    try:
        _write_sticks(path, lo, hi, failed, two_theta, intensity, d)
    except OSError:
        logger.debug("Could not cache sticks of %s", source_id, exc_info=True)
    if failed:
        return None
    keep = (two_theta >= two_theta_range[0]) & (two_theta <= two_theta_range[1])
    return two_theta[keep], intensity[keep], d[keep]


# --------------------------------------------------------------------------- #
# candidate pool
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    source: str  # "cod:1000041" or "mp:mp-19770"
    formula: str  # reduced formula
    spacegroup: int | None
    path: Path
    e_above_hull: float | None = None
    theoretical: bool = False
    sticks: tuple | None = None  # merged (two_theta, intensity, d)

    @property
    def kind(self) -> str:
        return self.source.split(":", 1)[0]


@dataclass
class Pool:
    candidates: list[Candidate] = field(default_factory=list)
    rows: int = 0
    cached: int = 0
    missing_cif: int = 0
    simulated: int = 0
    failed: int = 0
    downloaded: int = 0
    unavailable: int = 0
    sources: dict = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "rows": self.rows, "cached": self.cached, "missing_cif": self.missing_cif,
            "simulated": self.simulated, "failed": self.failed, "sources": dict(self.sources),
            "candidates": len(self.candidates), "downloaded": self.downloaded,
            "unavailable": self.unavailable,
        }


def _reduced(formula) -> str:
    from pymatgen.core import Composition

    try:
        return Composition(str(formula)).reduced_formula
    except Exception:  # noqa: BLE001
        return str(formula)


_THEORETICAL_MEMO: dict[str, bool] = {}


def _cod_theoretical(path: Path) -> bool:
    from dara.structure_db import is_theoretical_cif

    key = str(path)
    if key not in _THEORETICAL_MEMO:
        _THEORETICAL_MEMO[key] = is_theoretical_cif(path)
    return _THEORETICAL_MEMO[key]


_MP_ROWS_MEMO: dict[tuple, tuple[float, list[dict]]] = {}
MP_MEMO_TTL_S = 3600.0


def mp_pool_rows(elements, e_hull_cutoff: float, limit: int) -> list[dict]:
    """MP candidates of a chemical system through the normal search's query (cached on disk).

    Only the element set is sent to Materials Project. Raises when the query fails and no
    cached structures stand in.
    """
    from dara.structure_db import COMMON_GASES, MaterialsProjectDatabase, is_molecular_organic

    names = sorted({str(e) for e in elements})
    key = (tuple(names), float(e_hull_cutoff), int(limit))
    memo = _MP_ROWS_MEMO.get(key)
    if memo is not None and time.monotonic() - memo[0] < MP_MEMO_TTL_S:
        return memo[1]
    db = MaterialsProjectDatabase(max_structures=limit, e_hull_filter=e_hull_cutoff)
    systems = ["-".join(c) for n in range(1, len(names) + 1) for c in itertools.combinations(names, n)]
    # shortcut: chemical-system query only (the search also queries named precursor formulas,
    # which lie inside the system unless the 'limit' cap cuts them); add it if picks go missing.
    docs = db._query(chemsys=systems)
    rows = []
    for doc in docs:
        structure = db._doc_value(doc, "structure")
        if structure is None:
            continue
        row = db._index_row(doc, structure.composition)
        if row["formula"] in COMMON_GASES or is_molecular_organic(row["formula"]):
            continue
        rows.append(row)
    if not db.offline_cache_used:  # an offline stand-in is not kept for the hour
        _MP_ROWS_MEMO[key] = (time.monotonic(), rows)
    return rows


FETCH_CHUNK = 8  # COD ids per step: cancel and progress granularity (4 polite connections)


def _fetch_missing(pool: Pool, sources: list[str], report, should_stop) -> bool:
    """Download ``cod:<id>`` / ``mp:<id>`` CIFs into the shared caches; ``True`` when stopped.

    Reuses the structure library's COD downloader (polite, remembers 404s) and the MP cache
    download. A source that is unreachable adds a caveat; the ranking goes on with the cache.
    """
    from types import SimpleNamespace

    from dara import structure_library as lib
    from dara.structure_db import MaterialsProjectDatabase

    cod_ids = [s.split(":", 1)[1] for s in sources if s.startswith("cod:")]
    mp_ids = [s.split(":", 1)[1] for s in sources if s.startswith("mp:")]
    total = len(sources)
    report("downloading", 0, total)
    try:
        if cod_ids:
            emit = SimpleNamespace(emit=lambda phase, done, _total, *args, **kwargs: report("downloading", done, total))
            try:
                lib._download_cod(cod_ids, emit, should_stop, chunk_size=FETCH_CHUNK)
            except ConnectionError:
                logger.warning("Quick ranking: COD is not reachable", exc_info=True)
                pool.caveats.append(
                    "COD (www.crystallography.net) is not reachable: the structures not downloaded "
                    "yet were not ranked; rank again when the connection is back."
                )
        if mp_ids:
            lib._check_cancel(should_stop)
            try:
                MaterialsProjectDatabase().download_to_cache(mp_ids, "queries")
            except Exception as exc:  # noqa: BLE001 - offline or a bad key: rank what is cached
                logger.warning("Quick ranking: Materials Project download failed", exc_info=True)
                pool.caveats.append(
                    f"Materials Project structures could not be downloaded ({type(exc).__name__})."
                )
            report("downloading", total, total)
    except lib.Cancelled:
        return True
    return False


def build_pool(
    elements,
    precursors,
    wavelength_a: float,
    two_theta_range,
    *,
    e_hull_cutoff: float = 0.05,
    use_cod: bool = True,
    use_mp: bool = True,
    mp_limit: int = 100,
    include_theoretical: bool = False,
    simulate: bool = True,
    fetch: bool = False,
    should_stop=None,
    progress=None,
    extra=(),
) -> Pool:
    """Candidates of the normal phase search with sticks (cache first, then simulation).

    With ``fetch`` the CIFs missing from the caches are downloaded first, into the caches a
    search and the structure library use; one that cannot be fetched is skipped and counted.
    ``progress(phase, done, total)`` reports "listing", "downloading" and "simulating".
    ``should_stop()`` ends the pool early (between downloads and between simulations).
    ``extra`` are the user's own structures (``lib:<id>`` reference-library CIFs), ranked with
    the database candidates whatever their elements.
    """
    from dara import known_phases
    from dara.structure_db import cod_cache_dir, cod_candidate_rows, mp_cache_dir

    report = progress or (lambda phase, done, total: None)
    report("listing", 0, 0)
    pool = Pool()
    entries: list[Candidate] = []
    cod_rows: list[tuple] = []
    mp_rows: list[dict] = []
    if use_cod:
        cod_rows = cod_candidate_rows(list(elements), list(precursors or []), e_hull_cutoff)
    if use_mp:
        if not known_phases.mp_configured():
            pool.caveats.append("Materials Project is not configured (no MP_API_KEY): COD only.")
        else:
            try:
                listed = mp_pool_rows(elements, e_hull_cutoff, mp_limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Quick ranking: Materials Project query failed", exc_info=True)
                pool.caveats.append(
                    f"The Materials Project query failed ({type(exc).__name__}): COD only."
                )
                listed = []
            mp_rows = [r for r in listed if include_theoretical or not r.get("theoretical")]
            hidden = len(listed) - len(mp_rows)
            if hidden:
                pool.caveats.append(
                    f"{hidden} computed-only (theoretical) Materials Project structures are hidden; "
                    "tick 'Include theoretical (computed) structures' to rank them."
                )
    cod_folder, mp_folder = cod_cache_dir(), mp_cache_dir()
    missing = [f"cod:{code}" for _, code, _, _ in cod_rows if not (cod_folder / f"{code}.cif").is_file()]
    missing += [f"mp:{r['material_id']}" for r in mp_rows if not (mp_folder / f"{r['material_id']}.cif").is_file()]
    if fetch and missing and _fetch_missing(pool, missing, report, should_stop):
        return pool
    for formula, code, sg, e_hull in cod_rows:
        path = cod_folder / f"{code}.cif"
        if not path.is_file():
            pool.missing_cif += 1
        elif not _cod_theoretical(path):
            entries.append(Candidate(
                source=f"cod:{code}", formula=_reduced(formula),
                spacegroup=int(sg) if sg is not None else None, path=path, e_above_hull=e_hull,
            ))
    for row in mp_rows:
        path = mp_folder / f"{row['material_id']}.cif"
        if not path.is_file():
            pool.missing_cif += 1
            continue
        entries.append(Candidate(
            source=f"mp:{row['material_id']}", formula=row["formula"],
            spacegroup=row.get("spacegroup"), path=path,
            e_above_hull=row.get("e_above_hull"), theoretical=bool(row.get("theoretical")),
        ))
    entries.extend(extra)
    pool.rows = len(cod_rows) + len(mp_rows) + len(extra)
    if fetch and missing:
        pool.downloaded = len(missing) - pool.missing_cif
        pool.unavailable = pool.missing_cif
    if pool.missing_cif:
        pool.caveats.append(
            f"{pool.missing_cif} of {pool.rows} reference structures "
            + ("could not be downloaded" if fetch else "are not downloaded yet")
            + " and were not ranked."
        )
    for index, entry in enumerate(entries):
        if should_stop is not None and should_stop():
            break
        report("simulating", index, len(entries))
        cached = cached_sticks(entry.source, wavelength_a, two_theta_range)
        if cached is None:
            if not simulate:
                continue
            cached = simulate_sticks(entry.source, entry.path, wavelength_a, two_theta_range)
            pool.simulated += 1
        else:
            pool.cached += 1
        if cached is None or isinstance(cached, str):
            pool.failed += 1
            continue
        two_theta, intensity, d = (np.asarray(v, float) for v in cached)
        ok = np.isfinite(two_theta) & np.isfinite(intensity) & np.isfinite(d) & (d > 0) & (intensity > 0)
        if not ok.any():
            pool.failed += 1
            continue
        entry.sticks = _merge_reflections(two_theta[ok], intensity[ok], d[ok], CONFIG.merge_deg)
        pool.candidates.append(entry)
        pool.sources[entry.kind] = pool.sources.get(entry.kind, 0) + 1
    return pool


# --------------------------------------------------------------------------- #
# scoring
# --------------------------------------------------------------------------- #
def wavelength_lines(wavelength) -> tuple[float, float | None]:
    """``(K-alpha1, K-alpha2 or None)`` in angstrom for a target name or a value in nm."""
    from dara.search.candidate_cost import wavelength_angstrom
    from dara.utils import ANODE_LINES_A

    if isinstance(wavelength, str) and wavelength.strip().lower() in ANODE_LINES_A:
        alpha1, alpha2, _ = ANODE_LINES_A[wavelength.strip().lower()]
        return alpha1, alpha2
    return wavelength_angstrom(wavelength), None


def estimate_fwhm(x: np.ndarray, net: np.ndarray, step: float) -> float:
    """FWHM of the strongest net peak, clipped to 0.05..0.5 deg."""
    if not len(net) or not np.isfinite(net).any():
        return 0.1
    i = int(np.nanargmax(net))
    half = net[i] / 2
    left = i
    while left > 0 and net[left] > half:
        left -= 1
    right = i
    while right < len(net) - 1 and net[right] > half:
        right += 1
    return float(np.clip((right - left) * step, 0.05, 0.5))


@dataclass
class _Context:
    profile: object  # ObservedProfile with window_max of the current residual
    x: np.ndarray
    index_of: object  # 2theta -> fractional sample index
    residual: np.ndarray
    total: float
    fwhm: float
    lam1: float
    lam2: float | None
    window: int = 1  # samples of the window_max filter


def _context(angles, intensities, *, wavelength, wmin, wmax, excluded_ranges,
             background=None) -> tuple[_Context | None, str | None]:
    """``(context, None)`` of a pattern for scoring, or ``(None, caveat)`` when it cannot be ranked.

    ``background``: ``prefilter.background_settings`` (validated) or ``None`` for the default."""
    lam1, lam2 = wavelength_lines(wavelength)
    profile = observed_profile(
        angles, intensities, wmin=wmin, wmax=wmax, excluded_ranges=excluded_ranges, config=CONFIG,
        background=background,
    )
    if not profile.usable or profile.net is None:
        return None, "The pattern is too short or too flat to rank candidates."
    x = profile.angles
    valid = (x >= profile.lo) & (x <= profile.hi) & ~_in_ranges(x, profile.excluded)
    residual = np.where(valid, np.maximum(profile.net, 0.0), 0.0)
    total = float(residual.sum())
    if total <= 0:
        return None, "No net intensity above the background."
    window = max(1, round(2 * CONFIG.window_hw_deg / profile.step)) + 1
    samples = np.arange(len(x), dtype=float)
    return _Context(
        profile=replace(profile, window_max=maximum_filter1d(residual, window, mode="nearest")),
        x=x, index_of=lambda t: np.interp(t, x, samples), residual=residual, total=total,
        fwhm=estimate_fwhm(x, residual, profile.step), lam1=lam1, lam2=lam2, window=window,
    ), None


def _subtract(ctx: _Context, model) -> None:
    """Remove an accepted phase's model from the residual (one step of the greedy combination)."""
    ctx.residual = np.maximum(ctx.residual - model, 0.0)
    ctx.profile = replace(ctx.profile, window_max=maximum_filter1d(ctx.residual, ctx.window, mode="nearest"))


def _positions(d, scale, shift, lam):
    arg = lam / (2.0 * scale * d)
    ok = arg < 1.0
    two_theta = 2.0 * np.degrees(np.arcsin(np.clip(arg, 0.0, 1.0)))
    return two_theta + shift * np.cos(np.radians(two_theta / 2.0)), ok


def _models(ctx: _Context, sticks, rel, points) -> np.ndarray:
    """Unit-amplitude model profiles ``(len(points), len(x))``: height ``rel`` per stick."""
    x = ctx.x
    n = len(x)
    lo, hi = ctx.profile.lo - 1.0, ctx.profile.hi + 1.0
    _, _, d = sticks
    sig2 = (ctx.fwhm / 2.3548) ** 2
    half = int(math.ceil(4.0 * math.sqrt(sig2) / ctx.profile.step)) + 1
    offsets = np.arange(-half, half + 1)
    lines = [(ctx.lam1, 1.0)] + ([(ctx.lam2, 0.5)] if ctx.lam2 else [])
    flat_index, flat_value = [], []
    for row, (scale, shift) in enumerate(points):
        for lam, weight in lines:
            pos, ok = _positions(d, scale, shift, lam)
            sel = ok & (pos > lo) & (pos < hi)
            if not sel.any():
                continue
            pos = pos[sel]
            center = np.rint(ctx.index_of(pos)).astype(int)
            index = center[:, None] + offsets[None, :]
            good = (index >= 0) & (index < n)
            value = (weight * rel[sel])[:, None] * np.exp(
                -((x[np.clip(index, 0, n - 1)] - pos[:, None]) ** 2) / (2.0 * sig2)
            )
            flat_index.append(index[good] + row * n)
            flat_value.append(value[good])
    if not flat_index:
        return np.zeros((len(points), n))
    return np.bincount(
        np.concatenate(flat_index), np.concatenate(flat_value), minlength=len(points) * n
    ).reshape(len(points), n)


def _score(ctx: _Context, cand: Candidate) -> dict | None:
    two_theta, weight, d = cand.sticks
    bound = candidate_bound(
        ctx.profile, two_theta, weight, d, wavelength_a=ctx.lam1,
        excluded_ranges=ctx.profile.excluded, config=CONFIG, top_k=TOP_GRID_POINTS,
    )
    if bound["K"] is None or not bound.get("grid_top"):
        return None
    points = [(k, s, z) for k, s, z in bound["grid_top"] if np.isfinite(k) and k > 0]
    if not points:
        return {"score": 0.0, "explained": 0.0, "amplitude": 0.0, "scale": 1.0, "shift": 0.0,
                "matched": 0, "expected": int(bound["n_strong"]), "model": None, "rel": None}
    lo, hi = ctx.profile.lo, ctx.profile.hi
    inside = (two_theta > lo + CONFIG.edge_margin_deg) & (two_theta < hi - CONFIG.edge_margin_deg)
    rel = weight / weight[inside].max()
    amplitude = np.array([k for k, _, _ in points])
    models = _models(ctx, cand.sticks, rel, [(s, z) for _, s, z in points]) * amplitude[:, None]
    explained = np.minimum(models, ctx.residual[None, :]).sum(axis=1) / ctx.total
    scales = np.array([s for _, s, _ in points])
    scores = explained * (1.0 - SCALE_PENALTY * np.abs(scales - 1.0))
    best = int(np.argmax(scores))
    _, scale, shift = points[best]
    a = float(amplitude[best])
    usable = inside & ~_in_ranges(two_theta, ctx.profile.excluded, CONFIG.excluded_margin_deg) & (
        rel >= CONFIG.strong_rel
    )
    strong = np.where(usable)[0]
    strong = strong[np.argsort(-rel[strong], kind="stable")][: CONFIG.max_strong]
    pos, _ = _positions(d[strong], scale, shift, ctx.lam1)
    observed = np.interp(pos, ctx.profile.angles, ctx.profile.window_max)
    matched = int(np.sum(observed >= 0.5 * a * rel[strong]))
    return {
        "score": float(scores[best]), "explained": float(explained[best]), "amplitude": a,
        "scale": float(scale), "shift": float(shift), "matched": matched,
        "expected": int(len(strong)), "model": models[best], "rel": rel,
    }


_FRACTIONS: dict[str, dict | None] = {}


def _fractions(formula: str) -> dict | None:
    if formula not in _FRACTIONS:
        from pymatgen.core import Composition

        try:
            comp = Composition(formula).fractional_composition
            _FRACTIONS[formula] = {e.symbol: float(v) for e, v in comp.items()}
        except Exception:  # noqa: BLE001
            _FRACTIONS[formula] = None
    return _FRACTIONS[formula]


def _close_composition(a: str, b: str, tolerance: float = VARIANT_AT_FRACTION) -> bool:
    fa, fb = _fractions(a), _fractions(b)
    if fa is None or fb is None or set(fa) != set(fb):
        return False
    return max(abs(fa[e] - fb[e]) for e in fa) <= tolerance


def group_rows(scored: list[tuple[Candidate, dict]], flags: bool = True) -> list[dict]:
    """One row per (formula, space group), best first; COD (experimental) represents a group.

    ``scored`` holds ``(candidate, score dict)``. Each row has ``key``, ``candidate``, ``best``,
    ``members`` and ``flags`` (``polymorph``, ``variant of X``, ``theoretical``).
    """
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for cand, result in sorted(scored, key=lambda item: -item[1]["score"]):
        groups.setdefault((cand.formula, cand.spacegroup), []).append((cand, result))
    rows = []
    for key, members in groups.items():
        # the user's own structure represents its group, then COD (experimental) over MP
        experimental = [m for m in members if m[0].kind == "lib"] or [m for m in members if m[0].kind == "cod"]
        cand, result = (experimental or members)[0]
        rows.append({"key": key, "candidate": cand, "best": result, "members": members, "flags": []})
    rows.sort(key=lambda row: -row["best"]["score"])
    if not flags:
        return rows
    sgs_by_formula: dict[str, set] = {}
    for row in rows:
        sgs_by_formula.setdefault(row["key"][0], set()).add(row["key"][1])
    for index, row in enumerate(rows):
        formula = row["key"][0]
        if len(sgs_by_formula[formula]) > 1:
            row["flags"].append("polymorph")
        for higher in rows[:index]:
            other = higher["key"][0]
            if other != formula and _close_composition(formula, other):
                row["flags"].append(f"variant of {other}")
                break
        if row["candidate"].theoretical:
            row["flags"].append("theoretical")
    return rows


def _sticks_for_display(cand: Candidate, result: dict, lam1: float, lo: float, hi: float) -> list:
    two_theta, _, d = cand.sticks
    rel = result.get("rel")
    if rel is None:
        return []
    pos, ok = _positions(d, result["scale"], result["shift"], lam1)
    keep = np.where(ok & (pos >= lo) & (pos <= hi) & (rel >= 0.01))[0]
    keep = keep[np.argsort(-rel[keep])][:MAX_STICKS]
    keep = keep[np.argsort(pos[keep])]
    return [[round(float(pos[i]), 3), round(float(result["amplitude"] * rel[i]), 2)] for i in keep]


def _row_payload(rank: int, row: dict, lam1: float, lo: float, hi: float) -> dict:
    cand, result = row["candidate"], row["best"]
    sources = [m[0].source for m in row["members"]]
    return {
        "rank": rank,
        "formula": cand.formula,
        "spacegroup": cand.spacegroup,
        "source": cand.source,
        "source_kind": cand.kind,  # "cod", "mp" or "lib" (the user's reference library)
        "sources": sources[:20],
        "score": round(result["score"], 4),
        "explained": round(result["explained"], 4),
        "matched": result["matched"],
        "expected": result["expected"],
        "lattice_scale": round(result["scale"], 4),
        "shift": round(result["shift"], 3),
        "amplitude": round(result["amplitude"], 3),
        "flags": list(row["flags"]),
        "duplicates": len(row["members"]) - 1,
        "e_above_hull": cand.e_above_hull,
        "theoretical": bool(cand.theoretical),
        "sticks": _sticks_for_display(cand, result, lam1, lo, hi),
    }


def _score_rows(ctx: _Context, candidates, excluded_keys, flags: bool = True) -> list[dict]:
    """Score every candidate against the current residual, grouped (:func:`group_rows`)."""
    scored = []
    for cand in candidates:
        if (cand.formula, cand.spacegroup) in excluded_keys:
            continue
        try:
            result = _score(ctx, cand)
        except Exception:  # noqa: BLE001
            logger.debug("Scoring %s failed", cand.source, exc_info=True)
            continue
        if result is not None:
            scored.append((cand, result))
    return group_rows(scored, flags)


def rank(
    angles,
    intensities,
    candidates: list[Candidate],
    *,
    wavelength="Cu",
    wmin=None,
    wmax=None,
    excluded_ranges=None,
    max_rows: int = MAX_ROWS,
    background=None,
) -> dict:
    """Rank candidates and combine the best ones; see the module docstring.

    Returns ``{ranking, combination, fwhm, profile, background, caveats}``. ``profile`` is the
    net (background-removed) pattern, decimated, for an overlay of the sticks; ``background`` the
    curve that was removed (:func:`background_view`). ``background``: settings for
    ``prefilter.background_settings`` (``None``: the default method).
    """
    settings = background_settings(background)
    ctx, caveat = _context(angles, intensities, wavelength=wavelength, wmin=wmin, wmax=wmax,
                           excluded_ranges=excluded_ranges, background=settings)
    if ctx is None:
        return {"ranking": [], "combination": [], "fwhm": None, "profile": None,
                "background": {"settings": settings}, "caveats": [caveat]}
    profile, x, lam1, fwhm = ctx.profile, ctx.x, ctx.lam1, ctx.fwhm
    usable = [c for c in candidates if c.sticks is not None]

    def score_all(excluded_keys, flags=True):
        return _score_rows(ctx, usable, excluded_keys, flags)

    rows = score_all(set())
    ranking = [_row_payload(i + 1, row, lam1, profile.lo, profile.hi) for i, row in enumerate(rows[:max_rows])]

    combination, picked, explained_total, lines = [], set(), 0.0, []
    # a residual step may regroup to another member; report the ranking row's representative
    representative = {row["key"]: row["candidate"] for row in rows}
    stage_rows = rows
    for step in range(MAX_EXTRA_STEPS + 1):
        if step:
            stage_rows = score_all(picked, flags=False)
        if not stage_rows:
            break
        top = stage_rows[0]
        gain = top["best"]["explained"]
        if step and gain < STOP_GAIN:
            break
        if top["best"]["model"] is None:
            break
        explained_total += gain
        cand = representative.get(top["key"], top["candidate"])
        combination.append({
            "step": step + 1, "formula": cand.formula, "spacegroup": cand.spacegroup,
            "source": cand.source, "gain": round(gain, 4), "explained_total": round(explained_total, 4),
            "weak": gain < WEAK_GAIN, "theoretical": bool(cand.theoretical),
            "flags": list(top["flags"]) + (["weak"] if gain < WEAK_GAIN else []),
        })
        picked.add(top["key"])
        lines.append(_line_positions(ctx, top["candidate"], top["best"]))
        _subtract(ctx, top["best"]["model"])
        if not residual_maxima(ctx, np.concatenate(lines)):
            break  # as an analyst stops searching again: no significant unexplained peak is left
    picked_sources = {c["source"] for c in combination}
    for row in ranking:
        if row["source"] in picked_sources:
            row["flags"].append("picked")
        weak = next((c["weak"] for c in combination if c["source"] == row["source"]), False)
        if weak:
            row["flags"].append("weak")

    return {
        "ranking": ranking,
        "combination": combination,
        "fwhm": round(fwhm, 3),
        "profile": _decimated(x, np.maximum(profile.net, 0.0)),
        "background": background_view(profile, settings),
        "caveats": [],
    }


def background_view(profile, settings: dict) -> dict:
    """The removed background, decimated like ``profile``: ``two_theta``, ``observed`` (the
    smoothed pattern it was taken from), ``intensity`` (the curve) and the ``settings`` used."""
    stride = max(1, len(profile.angles) // 3000)
    return {
        "two_theta": [round(float(v), 4) for v in profile.angles[::stride]],
        "observed": [round(float(v), 2) for v in (profile.net + profile.background)[::stride]],
        "intensity": [round(float(v), 2) for v in profile.background[::stride]],
        "settings": settings,
    }


def _decimated(x, values) -> dict:
    stride = max(1, len(x) // 3000)
    return {"two_theta": [round(float(v), 4) for v in x[::stride]],
            "net": [round(float(v), 2) for v in values[::stride]]}


def _line_positions(ctx: _Context, cand: Candidate, result: dict) -> np.ndarray:
    """K-alpha1 and K-alpha2 positions of a scored candidate's displayed sticks (rel >= 0.01)."""
    keep = result["rel"] >= 0.01
    d = cand.sticks[2][keep]
    return np.concatenate([_positions(d, result["scale"], result["shift"], lam)[0]
                           for lam in (ctx.lam1, ctx.lam2) if lam])


def residual_maxima(ctx: _Context, lines=()) -> list[list[float]]:
    """``[2theta, height]`` of the residual's local maxima (over the scoring window) that are above
    the ranking's noise floor (``ObservedProfile.floor``: 6 sigma or 3 % of the strongest net peak)
    and significant by the unexplained-peak classification (``peak_significance``: 8 sigma and 2 %),
    except those on one of ``lines`` (the accepted phases' line positions): within max(0.1°, model
    FWHM), the peak list's assignment rule (``peak_list.observed_peak_rows``). A peak on an
    accepted line is assigned, whatever its intensity, as a peak list assigns it."""
    from dara.export.peak_list import ASSIGN_TOLERANCE_DEG
    from dara.server.peak_significance import SIGNIFICANT_REL, SIGNIFICANT_SNR

    profile = ctx.profile
    floor = max(profile.floor, SIGNIFICANT_SNR * profile.sigma, SIGNIFICANT_REL * profile.net_max)
    res, top = ctx.residual, profile.window_max
    rising = res > np.r_[-np.inf, res[:-1]]  # first sample of a flat top only
    index = np.flatnonzero((res >= top) & rising & (res > floor))
    lines = np.asarray(lines, dtype=float)
    if lines.size:
        tolerance = max(ASSIGN_TOLERANCE_DEG, ctx.fwhm)
        index = [i for i in index if np.min(np.abs(lines - ctx.x[i])) > tolerance]
    return [[round(float(ctx.x[i]), 3), round(float(res[i]), 2)] for i in index]


def residual_step(angles, intensities, candidates, selected, *, wavelength="Cu", wmin=None, wmax=None,
                  excluded_ranges=None, max_rows: int = MAX_ROWS, background=None) -> dict:
    """One more step of the greedy combination (:func:`rank`) for any selection.

    The models of the ``selected`` sources are subtracted from the net pattern in the given order,
    as the combination subtracts its picks. Returns ``explained`` (share of the net intensity the
    selection explains together), ``steps`` (``source``, ``gain`` of each), ``profile`` (the
    residual, decimated like the ranking's), ``unexplained_peaks`` (:func:`residual_maxima`) and
    ``next``: ranking rows scored against the residual without the selection's (formula, space
    group) groups. Raises ``KeyError(source)`` for a source that is not a scored candidate.
    """
    by_source = {c.source: c for c in candidates if c.sticks is not None}
    for source in selected:
        if source not in by_source:
            raise KeyError(source)
    settings = background_settings(background)
    ctx, caveat = _context(angles, intensities, wavelength=wavelength, wmin=wmin, wmax=wmax,
                           excluded_ranges=excluded_ranges, background=settings)
    if ctx is None:
        return {"explained": None, "steps": [], "profile": None, "unexplained_peaks": [], "next": [],
                "background": {"settings": settings}, "caveats": [caveat]}
    steps, lines = [], []
    for source in selected:
        result = _score(ctx, by_source[source])
        model = None if result is None else result["model"]
        steps.append({"source": source, "gain": 0.0 if model is None else round(result["explained"], 4)})
        if model is not None:
            lines.append(_line_positions(ctx, by_source[source], result))
            _subtract(ctx, model)
    keys = {(by_source[s].formula, by_source[s].spacegroup) for s in selected}
    rows = _score_rows(ctx, list(by_source.values()), keys)
    lo, hi = ctx.profile.lo, ctx.profile.hi
    return {
        "explained": round(1.0 - float(ctx.residual.sum()) / ctx.total, 4),
        "steps": steps,
        "profile": _decimated(ctx.x, ctx.residual),
        "background": background_view(ctx.profile, settings),
        "unexplained_peaks": residual_maxima(ctx, np.concatenate(lines) if lines else ()),
        "next": [_row_payload(i + 1, row, ctx.lam1, lo, hi) for i, row in enumerate(rows[:max_rows])],
        "caveats": [],
    }


@lru_cache(maxsize=4096)
def _unphysical_density(path: Path) -> bool:
    """Whether the CIF's computed density is outside the range the phase search accepts."""
    from pymatgen.core import Structure

    from dara.structure_quality import _DENSITY_RANGE

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            density = float(Structure.from_file(path).density)
    except Exception:  # noqa: BLE001 - unreadable here: the refinement reports it
        return False
    return not _DENSITY_RANGE[0] <= density <= _DENSITY_RANGE[1]


def quick_ranking(
    angles,
    intensities,
    *,
    elements,
    precursors=(),
    wavelength="Cu",
    wmin=None,
    wmax=None,
    excluded_ranges=None,
    e_hull_cutoff: float = 0.05,
    use_cod: bool = True,
    use_mp: bool = True,
    mp_limit: int = 100,
    include_theoretical: bool = False,
    progress=None,
    should_stop=None,
    extra_candidates=(),
    background=None,
) -> dict:
    """Pool (missing CIFs downloaded) + ranking in the response shape of ``/api/quick-ranking``.

    ``progress(phase, done, total)`` as in :func:`build_pool`, then ``"ranking"``. Raises
    ``structure_library.Cancelled`` when ``should_stop()`` turns true before the ranking.
    """
    from dara.structure_library import Cancelled

    started = time.perf_counter()
    lam1, _ = wavelength_lines(wavelength)
    x = np.asarray(angles, float)
    lo = float(np.nanmin(x)) if wmin is None else float(wmin)
    hi = float(np.nanmax(x)) if wmax is None else float(wmax)
    two_theta_range = (max(1.0, lo - 6.0), min(178.0, hi + 6.0))
    stopped = should_stop or (lambda: False)
    if stopped():
        raise Cancelled()
    settings = background_settings(background)
    ctx, caveat = _context(angles, intensities, wavelength=wavelength, wmin=wmin, wmax=wmax,
                           excluded_ranges=excluded_ranges, background=settings)
    if ctx is None:  # a too short or flat pattern: nothing is listed, downloaded or simulated
        return {"status": "done", "pool": Pool().summary(), "ranking": [], "combination": [], "fwhm": None,
                "profile": None, "background": {"settings": settings},
                "elapsed_s": round(time.perf_counter() - started, 3), "pool_s": 0.0, "caveats": [caveat],
                "_candidates": []}
    pool = build_pool(
        elements, precursors, lam1, two_theta_range, e_hull_cutoff=e_hull_cutoff,
        use_cod=use_cod, use_mp=use_mp, mp_limit=mp_limit, include_theoretical=include_theoretical,
        fetch=True, should_stop=should_stop, progress=progress, extra=list(extra_candidates),
    )
    if stopped():
        raise Cancelled()
    if progress is not None:
        progress("ranking", 0, 0)
    pool_s = time.perf_counter() - started
    candidates = pool.candidates
    for _ in range(4):
        result = rank(
            angles, intensities, candidates, wavelength=wavelength, wmin=wmin, wmax=wmax,
            excluded_ranges=excluded_ranges, background=background,
        )
        # a suggested structure with an impossible density makes the refinement of the whole
        # selection fail: drop it and rank again
        bad = {step["source"] for step in result["combination"]} & {
            c.source for c in candidates if c.path is not None and _unphysical_density(c.path)
        } if result["combination"] else set()
        if not bad:
            break
        candidates = [c for c in candidates if c.source not in bad]
        pool.caveats.append(
            f"{len(bad)} structure{'s' if len(bad) != 1 else ''} with an unphysical density "
            f"({', '.join(sorted(bad))}) left out of the ranking."
        )
    return {
        "status": "done",
        "pool": pool.summary(),
        "ranking": result["ranking"],
        "combination": result["combination"],
        "fwhm": result["fwhm"],
        "profile": result["profile"],
        "background": result["background"],
        "elapsed_s": round(time.perf_counter() - started, 3),
        "pool_s": round(pool_s, 3),
        "caveats": pool.caveats + result["caveats"],
        "_candidates": candidates,  # the ranked pool, kept by the router for residual steps; not JSON
    }


# --------------------------------------------------------------------------- #
# reference ids -> CIFs ("Refine selected")
# --------------------------------------------------------------------------- #
def reference_path(ref: str) -> Path:
    """Cached CIF of ``cod:<id>`` / ``mp:<material id>``, or the reference-library CIF of
    ``lib:<id>``; ``KeyError(ref)`` when malformed or not available."""
    import re

    from dara.structure_db import cod_cache_dir, is_valid_cif_file, mp_cache_dir

    match = re.fullmatch(r"(cod):(\d{5,9})|(mp):((?:mp|mvc)-\d+)|lib:([0-9a-f]{32})", ref)
    if not match:
        raise KeyError(ref)
    if match.group(5):
        from dara.server.reference_library_router import library_directory

        path = library_directory() / f"{match.group(5)}.cif"
    else:
        path = cod_cache_dir() / f"{match.group(2)}.cif" if match.group(1) else mp_cache_dir() / f"{match.group(4)}.cif"
    if not is_valid_cif_file(path):
        raise KeyError(ref)
    return path


def reference_cifs(ids) -> list:
    """Cached CIFs for ``cod:<id>`` / ``mp:<material id>`` in the order given.

    Each CIF is named like a search candidate (``Fe2O3_167_(cod_1234)``) so the result keeps
    its provenance. Raises ``KeyError(id)`` for a malformed id or a structure not cached.
    """
    from dara.cif import Cif
    from dara.structure_db import mp_cache_rows

    cifs = []
    for ref in dict.fromkeys(str(ref).strip() for ref in ids):
        path = reference_path(ref)
        code = path.stem
        tag = f"{ref.split(':', 1)[0]}_{code}"
        cif = Cif.from_file(path)
        structure = cif.to_structure()
        row = mp_cache_rows().get(code) if tag.startswith("mp_") else None
        number = row.get("spacegroup") if row else None
        if number is None:
            try:
                number = structure.get_space_group_info(symprec=0.1)[1]
            except Exception:  # noqa: BLE001
                number = "unknown"
        cif.filename = f"{structure.composition.reduced_formula}_{number}_({tag})"
        cifs.append(cif)
    return cifs
