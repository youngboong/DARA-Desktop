"""Run the structure-library download for the installer (packaging/DARA.iss).

    python -I scripts/library_download.py --cancel-file PATH [--log PATH] -- <download arguments>

Starts ``python -m dara.structure_library download --progress-json --cancel-file PATH <arguments>``
and turns each JSON progress line into one tab-separated line that Inno Setup's Pascal script
can split without a JSON parser::

    PROGRESS<TAB>phase<TAB>done<TAB>total<TAB>eta_s (empty if unknown)<TAB>message

It also prints ``TICK`` about every 0.3 s: Inno Setup only runs script code (and so only handles
the Finish later button) when a line of output arrives.

The download itself stops cleanly at the next chunk once the cancel file exists. A chunk can
take minutes (the Materials Project listing, a slow network), so the child is killed when it
has not stopped ``GRACE_S`` seconds after the cancel file appeared; every file it writes is
atomic, so that is safe and a later run resumes.

Exit code: 0 when the download reached its "done" step (some structures may still be missing
and are retried by the next run), 2 otherwise (cancelled, network error, killed).

Standard library only, and it never imports dara (that would load ray).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

GRACE_S = 15.0


def progress_line(raw: str) -> str | None:
    """One JSON progress line of the CLI as ``PROGRESS\\tphase\\tdone\\ttotal\\teta\\tmessage``."""
    try:
        item = json.loads(raw)
        eta = item.get("eta_s")
        fields = [str(item["phase"]), str(int(item.get("done", 0))), str(int(item.get("total", 0))),
                  "" if eta is None else str(int(round(float(eta)))), str(item.get("message", ""))]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return "\t".join(["PROGRESS", *(" ".join(f.split()) for f in fields)])


def run(command: list[str], cancel_file: Path, *, log=None, grace_s: float = GRACE_S, out=sys.stdout) -> int:
    """Run ``command``, forward its progress lines to ``out`` and return the exit code above."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log or subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    last = {"phase": None}
    lock = threading.Lock()

    def write(line):
        with lock:
            out.write(line + "\n")
            out.flush()

    def forward():
        for raw in process.stdout:
            line = progress_line(raw)
            if line is not None:
                last["phase"] = line.split("\t")[1]
                write(line)

    reader = threading.Thread(target=forward, daemon=True)
    reader.start()
    cancel_seen = None
    while process.poll() is None:
        if cancel_seen is None and cancel_file.exists():
            cancel_seen = time.monotonic()
        if cancel_seen is not None and time.monotonic() - cancel_seen > grace_s:
            # The whole tree: the sticks step's worker processes would keep {app}\python in use.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
            process.kill()  # in case taskkill failed; a no-op when the child is already gone
            process.wait()
            write("PROGRESS\tcancelled\t0\t0\t\tStopped; the download resumes later.")
            return 2
        write("TICK")
        time.sleep(0.3)
    reader.join(timeout=5)
    return 0 if last["phase"] == "done" else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cancel-file", type=Path, required=True)
    parser.add_argument("--log", type=Path, default=None, help="append the download's stderr here")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="-- then the download arguments")
    args = parser.parse_args(argv)
    extra = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    command = [sys.executable, "-m", "dara.structure_library", "download", "--progress-json",
               "--cancel-file", str(args.cancel_file), *extra]
    if args.log is None:
        return run(command, args.cancel_file)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with open(args.log, "ab") as log:
        return run(command, args.cancel_file, log=log)


if __name__ == "__main__":
    sys.exit(main())
