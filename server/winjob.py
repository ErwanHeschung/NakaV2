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

    def _create_job():
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
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
        handle = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                                  False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _k32.AssignProcessToJobObject(_job, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _k32.CloseHandle(handle)
        return True
    except OSError as e:
        log.warning("could not tie pid %d to this process's lifetime: %s", pid, e)
        return False
