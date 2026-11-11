import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "import_benchmark_run", Path(__file__).parents[1] / "scripts" / "import_benchmark_run.py"
)
importer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(importer)


def test_later_runs_replace_a_sample_and_runs_are_labelled(tmp_path):
    for run, sample in (("r3/all", "A"), ("r3/all", "B"), ("r6", "B")):
        folder = tmp_path / run / "samples" / sample
        folder.mkdir(parents=True)
        (folder / "document.json.gz").write_bytes(b"x")
    (tmp_path / "r6" / "samples" / "C").mkdir()  # no document: a failed sample is not imported
    found = importer._samples([tmp_path / "r3" / "all", tmp_path / "r6"])
    assert sorted(found) == ["A", "B"]
    assert found["B"][0] == tmp_path / "r6"
    assert importer._run_label(tmp_path / "r3" / "all") == "r3"
    assert importer._run_label(tmp_path / "r6") == "r6"
