"""Tie child processes' lives to ours, on Windows.

llama-server.exe holds about 7.4 GB of VRAM. If this process dies without
stopping it — killed from Task Manager, crashed, its parent tray killed — the
child keeps running and keeps the memory, with nothing left that knows it
exists. On a machine shared with games, that is the GPU gone until someone
finds it in the process list.

A job object with KILL_ON_JOB_CLOSE fixes that at the operating system level:
every process assigned to the job is terminated the moment the last handle to
the job closes, and the last handle is ours, held for our whole lifetime. So
however this process ends, cleanly or not, its children end with it.

A no-op elsewhere, so importing this never breaks a non-Windows checkout.
"""

import logging
import sys

log = logging.getLogger("naka.winjob")

_job = None

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _k32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _create_job(memory_bytes: int = 0):
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_bytes:
            info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_bytes
        if not _k32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return job


def contain(pid: int) -> bool:
    """Put a running process in our job. Returns whether it worked.

    Failure is logged rather than raised: a child that is not contained is a
    leak risk, not a reason to refuse to start it.
    """
    global _job
    if sys.platform != "win32":
        return False
    try:
        if _job is None:
            # Created once and deliberately never closed: closing it is what
            # kills the children, and that should happen only when we exit.
            _job = _create_job()
        _assign(_job, pid)
        return True
    except OSError as e:
        log.warning("could not tie pid %d to this process's lifetime: %s", pid, e)
        return False


def _assign(job, pid: int) -> None:
    handle = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                              False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _k32.AssignProcessToJobObject(job, handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _k32.CloseHandle(handle)


def command_job(pid: int, memory_mb: int):
    """A job of its own for one command, capped in memory. Returns its handle.

    Separate from the lifetime job above because it has to be closable on its
    own: closing this handle is how a command that ran too long, or that the
    kill switch caught, is ended — along with everything it started, which
    killing the PowerShell process alone would leave running.

    Raises on failure. A command that cannot be contained is not run.
    """
    if sys.platform != "win32":
        raise OSError("commands can only be contained on Windows")
    job = _create_job(memory_mb * 1024 * 1024)
    try:
        _assign(job, pid)
    except OSError:
        _k32.CloseHandle(job)
        raise
    return job


def close_job(job) -> None:
    """End every process in a job made by command_job."""
    if job is not None and sys.platform == "win32":
        _k32.CloseHandle(job)


def release_job(job) -> None:
    """Let go of a command's job without ending what is still in it.

    A command that finished may have launched something meant to outlive it —
    an editor, a folder window. Lifting the kill-on-close limit before closing
    the handle leaves those alone.
    """
    if job is None or sys.platform != "win32":
        return
    info = _ExtendedLimits()
    _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                 ctypes.byref(info), ctypes.sizeof(info))
    _k32.CloseHandle(job)
