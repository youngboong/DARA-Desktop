"""Runtime CPU-core detection, used to size worker/thread counts portably."""

from __future__ import annotations

import os


def detect_cpu_count() -> int:
    """Detect the number of CPU cores usable by this process.

    Checked in priority order:
        1. SLURM_CPUS_PER_TASK, then SLURM_CPUS_ON_NODE (SLURM-managed jobs).
        2. ``os.sched_getaffinity(0)`` (Linux only; reflects taskset/affinity
           restrictions that ``os.cpu_count()`` ignores).
        3. ``os.cpu_count()``.

    Returns
    -------
        A positive integer. Falls back to 1 if nothing above yields a usable count.
    """
    for env_var in ("SLURM_CPUS_PER_TASK", "SLURM_CPUS_ON_NODE"):
        value = os.environ.get(env_var)
        if value:
            try:
                count = int(value)
            except ValueError:
                count = 0
            if count > 0:
                return count

    if hasattr(os, "sched_getaffinity"):
        count = len(os.sched_getaffinity(0))
        if count > 0:
            return count

    count = os.cpu_count()
    if count:
        return count

    return 1


#: Commit memory one Ray worker needs (measured 0.33-0.56 GB per worker on Windows).
WORKER_MEMORY_GB = 0.6
_MEMORY_RESERVE_GB = 0.5


def available_memory_gb() -> float | None:
    """Memory a new process can still commit, in GB; ``None`` when unknown.

    On Windows this is the free commit charge (RAM plus page file), which is what runs out
    when Ray's plasma store or a worker fails with GetLastError 1450/1455.
    """
    try:
        if os.name == "nt":
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.ullAvailPageFile / 1024**3
        import psutil

        return psutil.virtual_memory().available / 1024**3
    except Exception:  # noqa: BLE001 - sizing is best effort
        return None


def memory_capped_workers(cpus: int, store_gb: float, available_gb: float | None = None) -> int:
    """``cpus`` reduced so the workers and the object store fit in the free memory (at least 2)."""
    if available_gb is None:
        available_gb = available_memory_gb()
    if available_gb is None:
        return cpus
    fit = int((available_gb - store_gb - _MEMORY_RESERVE_GB) / WORKER_MEMORY_GB)
    return max(1, min(cpus, max(2, fit)))
