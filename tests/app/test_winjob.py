"""The Windows Job object that holds a worker and everything it starts (`f3d2solidcraft_app.winjob`).

Real processes only: a Python child that joins the job, as the worker does, and then starts a
grandchild of its own, as the worker starts an engine. The child reports its own pid: in a virtual
environment on Windows `sys.executable` is a launcher that runs the interpreter as a child of its own,
so `Popen.pid` is the launcher's, not the interpreter's.
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Job objects are Windows")

CHILD = (
    "import os, subprocess, sys, time\n"
    "from f3d2solidcraft_app.winjob import join\n"
    "print(join(sys.argv[1]), os.getpid(), flush=True)\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import os; print(os.getpid(), flush=True); "
    "import time; time.sleep(600)'])\n"
    "time.sleep(600)\n"
)


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


SYNCHRONIZE, PROCESS_TERMINATE, WAIT_OBJECT_0, WAIT_TIMEOUT = 0x00100000, 0x0001, 0, 0x102


def test_closing_the_job_ends_a_grandchild_whose_parent_already_died():
    from f3d2solidcraft_app.winjob import ProcessTree

    k = _kernel32()
    tree = ProcessTree()
    child = subprocess.Popen([sys.executable, "-c", CHILD, tree.name], stdout=subprocess.PIPE, text=True)
    handles = []
    try:
        tree.add(child.pid)  # what the window does with the process QProcess started
        joined, parent = child.stdout.readline().split()
        assert joined == "True", "the child could not join the job"
        # The grandchild's own pid: the interpreter's, below its launcher in a virtual environment.
        pid = int(child.stdout.readline())
        parent_handle = k.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, int(parent))
        grandchild = k.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, pid)
        handles += [h for h in (parent_handle, grandchild) if h]
        assert parent_handle and grandchild, (parent, pid)
        # The parent dies alone, as a crashed worker does: its child is left running, an orphan.
        k.TerminateProcess(parent_handle, 0xC0000005)
        assert k.WaitForSingleObject(parent_handle, 10_000) == WAIT_OBJECT_0
        assert k.WaitForSingleObject(grandchild, 1_000) == WAIT_TIMEOUT, "the grandchild died with its parent"
        tree.close()
        assert k.WaitForSingleObject(grandchild, 10_000) == WAIT_OBJECT_0, "closing the job left it running"
        assert tree.closed
        tree.close()  # a second close does nothing
    finally:
        for handle in handles:
            k.TerminateProcess(handle, 1)
            k.CloseHandle(handle)
        child.kill()
        tree.close()


def test_a_worker_goes_on_when_its_job_is_not_there():
    import time

    from f3d2solidcraft_app.winjob import join

    start = time.monotonic()
    assert join("Local\\f3d2solidcraft-no-such-job") is False
    assert time.monotonic() - start < 1
