"""Shared test set-up."""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _private_structure_caches(tmp_path_factory):
    """Materials Project and stick caches of tests never touch the real cache folders."""
    root = tmp_path_factory.mktemp("structure-caches")
    saved = {name: os.environ.get(name) for name in ("DARA_MP_CACHE_DIR", "DARA_STICK_CACHE_DIR")}
    os.environ["DARA_MP_CACHE_DIR"] = str(root / "mp")
    os.environ["DARA_STICK_CACHE_DIR"] = str(root / "sticks")
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _fresh_document_cache():
    """Decoded task documents are cached by task uuid; tests reuse uuids with other documents."""
    from dara.server import quick_refine

    quick_refine._DOCUMENTS.clear()
    yield
    quick_refine._DOCUMENTS.clear()
