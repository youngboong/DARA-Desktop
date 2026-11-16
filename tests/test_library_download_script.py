"""scripts/library_download.py: the installer's wrapper around the structure-library download."""

import importlib.util
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "library_download.py"
spec = importlib.util.spec_from_file_location("library_download", SCRIPT)
library_download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(library_download)


def _child(lines, sleep_s=0.0):
    code = "import json, time\n"
    for line in lines:
        code += f"print(json.dumps({line!r}), flush=True)\n"
    code += f"time.sleep({sleep_s})\n"
    return [sys.executable, "-c", code]


def _progress(out):
    """Output lines without the TICK heartbeats."""
    return [line for line in out.getvalue().splitlines() if line != "TICK"]


def test_progress_line_is_tab_separated_and_single_line():
    raw = json.dumps({"phase": "cod", "done": 40, "total": 120, "bytes": 5, "eta_s": 31.6,
                      "message": "40 downloaded,\t0 failed\nin this step"})
    assert library_download.progress_line(raw) == "PROGRESS\tcod\t40\t120\t32\t40 downloaded, 0 failed in this step"
    assert library_download.progress_line(json.dumps({"phase": "select", "done": 0, "total": 1, "eta_s": None})) == \
        "PROGRESS\tselect\t0\t1\t\t"
    assert library_download.progress_line("100%|#####| tqdm noise") is None


def test_done_step_gives_exit_0_and_anything_else_2(tmp_path):
    out = io.StringIO()
    done = [{"phase": "cod", "done": 1, "total": 1, "eta_s": 0, "message": ""},
            {"phase": "done", "done": 1, "total": 1, "eta_s": None, "message": "Finished."}]
    assert library_download.run(_child(done), tmp_path / "cancel", out=out) == 0
    assert out.getvalue().splitlines()[-1] == "PROGRESS\tdone\t1\t1\t\tFinished."
    error = [{"phase": "error", "done": 0, "total": 0, "eta_s": None, "message": "ConnectionError: offline"}]
    assert library_download.run(_child(error), tmp_path / "cancel", out=io.StringIO()) == 2


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the installer's wrapper uses taskkill")


@windows_only
def test_cancel_file_kills_a_child_that_does_not_stop(tmp_path):
    cancel = tmp_path / "cancel"
    cancel.touch()
    out = io.StringIO()
    started = time.monotonic()
    code = library_download.run(_child([{"phase": "select", "done": 0, "total": 1}], sleep_s=60), cancel,
                                grace_s=0.5, out=out)
    assert code == 2
    assert time.monotonic() - started < 20
    assert out.getvalue().splitlines()[-1].startswith("PROGRESS\tcancelled\t")


def _running(pid):
    listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in listing.split()


@windows_only
def test_cancel_kills_the_childs_workers_too(tmp_path):
    cancel = tmp_path / "cancel"
    cancel.touch()
    pid_file = tmp_path / "worker.pid"
    code = ("import pathlib, subprocess, sys, time\n"
            "worker = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(worker.pid))\n"
            "time.sleep(60)\n")
    # The grace period starts at once: it must leave the child time to start its worker.
    assert library_download.run([sys.executable, "-c", code], cancel, grace_s=5, out=io.StringIO()) == 2
    worker = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while _running(worker) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _running(worker)
