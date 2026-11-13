"""Copy the DARA sources into the desktop build folder (run by packaging/build_dist.ps1).

The bundled CPython already has every dependency installed. This script adds what is
specific to DARA, keeping the repository layout so the existing scripts work unchanged:

    <app>/src/dara        the package (no BGMNwin, __pycache__, UI source, *.map, stale UI chunks)
    <app>/scripts         launch_dara_web.py, ensure_bgmn.py, windows_ca_bundle.py, materials_project_key.ps1
    <app>/python/Lib/site-packages/dara-src.pth   relative path to <app>/src

Standard library only.
"""

from __future__ import annotations

import argparse
import compileall
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

SCRIPTS = ("launch_dara_web.py", "ensure_bgmn.py", "windows_ca_bundle.py", "materials_project_key.ps1")
# Only ui/public is served; the rest of ui/ is the Gatsby source and its caches.
UI_KEEP = {"public"}
# Gatsby asset names: <name>-<20 hex>.js, styles.<20 hex>.css, plus their .LICENSE.txt files.
HASHED_ASSET = re.compile(r"^.+[-.][0-9a-f]{20}\.(?:js|css)$")
ASSET_TOKEN = re.compile(r"[\w.~-]+\.(?:js|css)")
RUNTIME_MAP = re.compile(r'return\(\{([^}]*)\}\[e\]\|\|e\)\+"-"\+\{([^}]*)\}\[e\]\+"\.js"')
MAP_ENTRY = re.compile(r'(\d+):"([^"]+)"')


def live_ui_assets(public: Path) -> tuple[set[str], set[str]]:
    """Root-level JS/CSS files the current Gatsby build can load: (all referenced, must exist).

    Every build leaves its hashed files behind in ui/public; only the ones referenced by
    webpack.stats.json, chunk-map.json, the HTML pages and the current webpack runtime's
    chunk table belong to the build that index.html serves.
    """
    stats = json.loads((public / "webpack.stats.json").read_text(encoding="utf-8"))
    runtime = next(name for name in stats["assetsByChunkName"]["app"] if name.startswith("webpack-runtime-"))
    texts = [
        (public / "webpack.stats.json").read_text(encoding="utf-8"),
        (public / "chunk-map.json").read_text(encoding="utf-8"),
    ]
    texts += [page.read_text(encoding="utf-8", errors="replace") for page in public.rglob("*.html") if page.is_file()]
    runtime_text = (public / runtime).read_text(encoding="utf-8")
    texts.append(runtime_text)
    live = {Path(token).name for text in texts for token in ASSET_TOKEN.findall(text)}
    match = RUNTIME_MAP.search(runtime_text)
    if match is None:
        raise SystemExit(f"Cannot read the chunk table of {runtime}; update RUNTIME_MAP in {__file__}.")
    names = dict(MAP_ENTRY.findall(match.group(1)))
    required = {f"{names.get(chunk_id, chunk_id)}-{digest}.js" for chunk_id, digest in MAP_ENTRY.findall(match.group(2))}
    required |= {name for group in stats["assetsByChunkName"].values() for name in group}
    # The CSS-only "styles" chunk has a table entry but no .js file and is never requested.
    required = {name for name in required if (public / name).is_file()}
    return live | required, required


def make_ignore(src_root: Path, public: Path, live: set[str]):
    def ignore(directory: str, names: list[str]) -> set[str]:
        here = Path(directory)
        skipped = {name for name in names if name in {"__pycache__", "BGMNwin"} or name.endswith((".pyc", ".map"))}
        skipped |= {name for name in names if name.startswith(".bgmn-download-") or name == "bgmnwin.zip"}
        if here == src_root / "server" / "ui":
            skipped |= {name for name in names if name not in UI_KEEP}
        if here == public:
            for name in names:
                asset = name.removesuffix(".LICENSE.txt")
                if HASHED_ASSET.match(asset) and asset not in live:
                    skipped.add(name)
        return skipped

    return ignore


def check_ui(public_out: Path, required: set[str]) -> None:
    """Self-check: every chunk the current build loads was copied."""
    assert len(required) > 10, "too few UI chunks found; the UI pruning rules no longer match the build"
    missing = sorted(name for name in required if not (public_out / name).is_file())
    assert not missing, f"UI assets referenced by the build are missing: {missing}"
    assert (public_out / "index.html").is_file(), "index.html missing"


def folder_mb(path: Path) -> float:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) / 2**20


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    args = parser.parse_args(argv)
    repo, app = args.repo.resolve(), args.app.resolve()
    src_root = repo / "src" / "dara"
    public = src_root / "server" / "ui" / "public"

    live, required = live_ui_assets(public)
    target = app / "src" / "dara"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src_root, target, ignore=make_ignore(src_root, public, live))  # copy2 keeps mtimes for the .pyc checks
    public_out = target / "server" / "ui" / "public"
    check_ui(public_out, required)
    kept = sum(1 for item in public_out.iterdir() if HASHED_ASSET.match(item.name))
    total = sum(1 for item in public.iterdir() if HASHED_ASSET.match(item.name))
    print(f"UI: kept {kept} of {total} hashed root assets ({folder_mb(public_out):.1f} MB)")

    scripts = app / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copy2(repo / "scripts" / name, scripts / name)
    shutil.copy2(repo / "LICENSE", app / "LICENSE.txt")

    site_packages = app / "python" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        raise SystemExit(f"{site_packages} not found: install the dependencies first.")
    # site.addpackage joins relative .pth lines with the site-packages folder, so the bundle stays relocatable.
    (site_packages / "dara-src.pth").write_text("../../../src\n", encoding="ascii")
    # dara reads its own version with importlib.metadata.version("dara-xrd"): give it the metadata
    # that an (editable) install would have written, without installing a second copy of dara.
    project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dist_info = site_packages / f"dara_xrd-{project['version']}.dist-info"
    dist_info.mkdir(exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {project['version']}\n", encoding="utf-8")
    (dist_info / "INSTALLER").write_text("DARA Desktop build\n", encoding="utf-8")

    ok = compileall.compile_dir(app / "src", quiet=1, workers=0)
    ok &= compileall.compile_dir(scripts, quiet=1, workers=0)
    if not ok:
        raise SystemExit("compileall reported errors")
    # The standard library was copied without __pycache__; site-packages is already compiled by uv.
    compileall.compile_dir(app / "python" / "Lib", quiet=2, workers=0, rx=re.compile(r"[\\/](test|tests|idlelib|turtledemo)[\\/]"))
    print(f"src: {folder_mb(app / 'src'):.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
