"""The worker's output, back on the pipe the window reads (Windows, packaged app).

The packaged Windows app is Briefcase's GUI stub (stub b14 under Briefcase 0.4.7), and it is also its
own worker. Before Python starts, the stub asks for its parent's console (`AttachConsole`). The window
has none, so the stub reopens the C runtime's stdout onto a log file under
`%LOCALAPPDATA%\\<author>\\f3d2solidcraft\\Logs`: that closes the pipe the window passed as stdout and
makes the log the process's standard output handle. Everything the worker printed (the counts line,
the progress events) went into that log, with exit code 0 and nothing on the pipe: «Could not read the
file: exit code 0» in 0.3.1. Measured on the release runner with a parent that has no console.

The stub leaves stderr alone. So the window starts the worker with stdout and stderr merged into one
pipe (`QProcess.MergedChannels`; `stderr=STDOUT` in `runner.check_worker`) and says so in `ONE_PIPE`.
The C runtime does not close a handle that stdout shares with stderr, so the pipe survives the stub's
redirect as stderr, and `attach_std_streams` points `sys.stdout` at it too.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

#: Set by whoever starts the worker with stdout and stderr in one pipe (the window, `check_worker`).
ONE_PIPE = "F3D2SC_WORKER_ONE_PIPE"

STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12
FILE_TYPE_UNKNOWN = 0


def _os_handle(stream) -> int | None:
    """The Windows handle under a Python stream, or None if it has no usable descriptor."""
    import msvcrt

    try:
        return msvcrt.get_osfhandle(stream.fileno())
    except (AttributeError, OSError, ValueError):
        return None


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    return kernel32


def _open_handle(handle: int, errors: str):
    """A UTF-8, line-buffered text stream on a duplicate of `handle`, which stays open on its own.

    A duplicate, so closing this stream (Python does at exit) never closes the handle stderr is on.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = _kernel32()
    duplicate = wintypes.HANDLE()
    me = kernel32.GetCurrentProcess()
    if not kernel32.DuplicateHandle(me, handle, me, ctypes.byref(duplicate), 0, False, 2):  # SAME_ACCESS
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY)
    return open(fd, "w", encoding="utf-8", errors=errors, buffering=1)


def _std_handle(which: int) -> int | None:
    """The process's standard handle `which`, or None when there is none usable (a double click)."""
    kernel32 = _kernel32()
    handle = kernel32.GetStdHandle(which)
    if not handle or handle in (-1, 2**64 - 1):  # NULL, INVALID_HANDLE_VALUE
        return None
    return handle if kernel32.GetFileType(handle) != FILE_TYPE_UNKNOWN else None


def attach_std_streams(environ: MutableMapping[str, str] = os.environ) -> None:
    """Windows: make the worker's `sys.stdout` and `sys.stderr` reach whoever started it.

    - Started with one pipe for both (`ONE_PIPE`) and stdout is somewhere else: stdout joins stderr
      on that pipe. This is the stub's log-file redirect undone.
    - A stream that is None (a GUI program started with no console): onto the process's standard
      handle when there is one, else onto a null sink, so a print never fails.

    `ONE_PIPE` is taken out of the environment, so the engines the worker starts do not inherit it.
    A no-op on macOS and Linux, from a console, and whenever stdout already shares stderr's pipe.
    """
    one_pipe = environ.pop(ONE_PIPE, None) == "1"
    if sys.platform != "win32":
        return
    if one_pipe and sys.stderr is not None:
        err = _os_handle(sys.stderr)
        if err is not None and _os_handle(sys.stdout) != err:
            try:
                sys.stdout = _open_handle(err, "strict")
            except OSError:
                pass  # left where it was: no worse than before
    for name, which, errors in (
        ("stdout", STD_OUTPUT_HANDLE, "strict"),
        ("stderr", STD_ERROR_HANDLE, "backslashreplace"),
    ):
        if getattr(sys, name) is not None:
            continue
        handle = _std_handle(which)
        if handle is not None:
            try:
                setattr(sys, name, _open_handle(handle, errors))
                continue
            except OSError:
                pass
        setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 (lives as long as the process)
