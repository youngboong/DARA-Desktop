import subprocess
from pathlib import Path

from dara.benchmark.runner import _execute_with_retry, _is_runtime_fault, _temp_dir


def test_runtime_fault_detection():
    assert _is_runtime_fault(OSError("[WinError 50] x"))
    assert _is_runtime_fault(type("LocalRayletDiedError", (Exception,), {})("died"))
    assert not _is_runtime_fault(ValueError("bad pattern"))


def test_retry_once_on_runtime_fault(monkeypatch):
    import dara.benchmark.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_ensure_ray", lambda: True)
    monkeypatch.setattr("ray.shutdown", lambda: None)
    calls = []

    def flaky(job, path):
        calls.append(Path(path))
        if len(calls) == 1:
            raise OSError("[WinError 50] x")
        return "ok"

    job = type("Job", (), {"hosts": ["old-flow"]})()
    assert _execute_with_retry(flaky, job, _temp_dir) == "ok"
    assert len(calls) == 2
    assert job.hosts == []


def test_no_retry_on_sample_error():
    def bad(job, path):
        raise ValueError("bad")

    try:
        _execute_with_retry(bad, None, _temp_dir)
    except ValueError:
        return
    raise AssertionError


def test_bgmn_stdin_is_devnull(monkeypatch, tmp_path):
    from dara import bgmn_worker

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(bgmn_worker, "run", fake_run)
    worker = bgmn_worker.BGMNWorker()
    worker.run_refinement_cmd(tmp_path / "a.sav")
    assert seen["stdin"] == subprocess.DEVNULL


def test_worker_count_follows_free_memory():
    from dara.hardware import memory_capped_workers

    assert memory_capped_workers(12, 1.0, available_gb=20) == 12
    assert memory_capped_workers(12, 1.0, available_gb=4) == 4
    assert memory_capped_workers(12, 1.0, available_gb=0.5) == 2
    assert memory_capped_workers(1, 1.0, available_gb=0.5) == 1


def test_explicit_ray_cpus_is_not_capped(monkeypatch):
    from dara import hardware
    from dara.settings import ray_init_options

    monkeypatch.setenv("DARA_RAY_NUM_CPUS", "7")
    monkeypatch.setattr(hardware, "available_memory_gb", lambda: 0.1)
    assert ray_init_options()["num_cpus"] == 7


def test_expansion_tasks_share_one_copy_of_the_screening_results(monkeypatch):
    from dara.search import core

    puts, sent = [], []
    monkeypatch.setattr(core.ray, "put", lambda value: puts.append(value) or f"ref{len(puts)}")
    monkeypatch.setattr(core._remote_expand_node, "remote", lambda subtree, shared: sent.append((subtree, shared)))

    class Tree:
        all_phases_result = {"phase": "big result"}

    subtrees = []
    monkeypatch.setattr(core.BaseSearchTree, "from_search_tree", classmethod(lambda cls, root_nid, search_tree: subtrees.append(Tree()) or subtrees[-1]))
    tree = Tree()
    core.remote_expand_node(tree, "a")
    core.remote_expand_node(tree, "b")
    assert len(puts) == 1
    assert [shared for _, shared in sent] == ["ref1", "ref1"]
    assert all(subtree.all_phases_result is None for subtree, _ in sent)
    tree.all_phases_result = {"other": 1}
    core.remote_expand_node(tree, "c")
    assert len(puts) == 2
