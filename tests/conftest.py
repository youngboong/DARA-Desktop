"""Shared test set-up."""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _private_structure_caches(tmp_path_factory):
    """Caches and server folders of tests never touch the user's real folders.

    Every server folder derives from ``DaraServerSettings.montydb_path`` (default
    ``~/.dara-server/montydb``): the task store, ``quick-refine``, ``task-checkpoints``,
    ``instrument-profiles`` (registry) and ``reference-cifs``. Tests that need their own store
    still set ``DARA_SERVER_MONTYDB_PATH`` per test (monkeypatch restores this one).
    """
    root = tmp_path_factory.mktemp("structure-caches")
    names = ("DARA_MP_CACHE_DIR", "DARA_STICK_CACHE_DIR", "DARA_SERVER_MONTYDB_PATH", "DARA_PEAK_CACHE_DIR")
    saved = {name: os.environ.get(name) for name in names}
    os.environ["DARA_MP_CACHE_DIR"] = str(root / "mp")
    os.environ["DARA_STICK_CACHE_DIR"] = str(root / "sticks")
    os.environ["DARA_SERVER_MONTYDB_PATH"] = str(root / "dara-server" / "montydb")
    os.environ["DARA_PEAK_CACHE_DIR"] = str(root / "peak-cache")
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _no_real_rsync(monkeypatch):
    """COD downloads in tests never start a real rsync (tests that want one inject a fake)."""
    from dara import structure_library

    monkeypatch.delenv("DARA_RSYNC", raising=False)
    monkeypatch.setattr(structure_library, "BUNDLED_RSYNC", structure_library.Path(__file__).parent / "no-such-rsync.exe")
    monkeypatch.setattr(structure_library, "_rsync_failed", False)


@pytest.fixture(autouse=True)
def _fresh_document_cache():
    """Decoded task documents are cached by task uuid; tests reuse uuids with other documents."""
    from dara.server import quick_refine

    quick_refine._DOCUMENTS.clear()
    yield
    quick_refine._DOCUMENTS.clear()
