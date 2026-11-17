"""A Windows Job object around a worker, so ending the job ends every process it started.

Windows has no process groups to kill. `taskkill /T` walks the parent links of processes that are still
running, so an engine whose worker crashed is no longer reachable that way. A Job object holds every
process started inside it, children included, and with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` closing its
last handle terminates all of them: on cancel, on a worker that crashed, and on a window that died,
since Windows closes a dead process's handles too.

The window creates the job and assigns the worker as soon as `QProcess` has started it. That alone
leaves two gaps: the worker could start an engine before the window gets to it, and in development the
process `QProcess` starts is the virtual environment's `python.exe`, a launcher that runs the real
interpreter as a child of its own, possibly before the launcher is assigned. So the window also passes
the job's name in `JOB_VARIABLE`, and the worker, first thing, joins the job itself if it is not inside
yet (`join`). From then on whatever the worker starts is born inside the job.
"""

import ctypes
import os
import uuid
from ctypes import wintypes

#: The job's name, set by the window in the worker's environment.
JOB_VARIABLE = "F3D2SC_WORKER_JOB"

_JOB_OBJECT_ASSIGN_PROCESS = 0x0001
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


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


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
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


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _fail(what: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{what} failed: {ctypes.FormatError(code).strip()}")


class ProcessTree:
    """A Job object that kills every process inside it when it is closed (Windows only)."""

    def __init__(self) -> None:
        self._kernel32 = _kernel32()
        self.name = f"Local\\f3d2solidcraft-{os.getpid()}-{uuid.uuid4().hex}"
        handle = self._kernel32.CreateJobObjectW(None, self.name)
        if not handle:
            raise _fail("CreateJobObjectW")
        self._handle: int | None = handle
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = _fail("SetInformationJobObject")
            self.close()
            raise error

    def add(self, pid: int) -> None:
        """Put a running process in the job; what it starts from then on is born inside it.

        A process that is inside already (it joined on its own, `join`) is left as it is.
        """
        if self._handle is None:
            raise OSError(f"the job {self.name} is closed")
        access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION
        process = self._kernel32.OpenProcess(access, False, pid)
        if not process:
            raise _fail(f"OpenProcess({pid})")
        try:
            _assign(self._kernel32, self._handle, process, f"AssignProcessToJobObject({pid})")
        finally:
            self._kernel32.CloseHandle(process)

    def close(self) -> None:
        """Close the job: every process still inside it is terminated. Closing twice does nothing."""
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)

    @property
    def closed(self) -> bool:
        return self._handle is None


def _inside(kernel32, job: int, process: int) -> bool:
    inside = wintypes.BOOL(False)
    return bool(kernel32.IsProcessInJob(process, job, ctypes.byref(inside)) and inside)


def _assign(kernel32, job: int, process: int, what: str) -> None:
    """Put `process` in `job` unless it is inside already; raise when it ends up outside."""
    if _inside(kernel32, job, process):
        return
    # A failure may be the other side having just put it there: then it is where it should be.
    if not kernel32.AssignProcessToJobObject(job, process) and not _inside(kernel32, job, process):
        raise _fail(what)


def join(name: str) -> bool:
    """Called first thing in a worker: be inside job `name`, the window's, before starting anything.

    True once inside. False when the job cannot be opened (the window has closed it already, which
    it only does to end the worker) or Windows refuses; the worker then goes on outside it.
    """
    kernel32 = _kernel32()
    job = kernel32.OpenJobObjectW(_JOB_OBJECT_ASSIGN_PROCESS | _JOB_OBJECT_QUERY, False, name)
    if not job:
        return False
    try:
        _assign(kernel32, job, kernel32.GetCurrentProcess(), "joining the window's job")
    except OSError:
        return False
    finally:
        # Only the window's handle may keep the job open: closing it must be what ends the tree.
        kernel32.CloseHandle(job)
    return True
