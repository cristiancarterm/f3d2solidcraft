"""The whole wizard, offscreen, on the public sample with the real engine (spec, Pruebas)."""

import functools
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6")

from f3d2solidcraft_app.engines import saved_solidcraft
from f3d2solidcraft_app.texts import t
from f3d2solidcraft_app.window import CONVERT, ENGINES, FILE, RESULT, Wizard

OLD_ENGINE = Path.home() / ".f3d2solidcraft/engines/0.30.1/solidcraft-cli"


def test_the_wizard_converts_the_sample_end_to_end(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    assert wizard.pages.currentIndex() == FILE
    wizard.load_file(sample_f3d)
    wizard.output = tmp_path / "box.solidcraft"
    qtbot.waitUntil(lambda: "26 operaciones" in wizard.holds_label.text(), timeout=60_000)
    assert wizard.holds_label.text() == "32 parámetros · 8 bocetos · 26 operaciones"
    wizard.start_conversion()
    assert wizard.pages.currentIndex() == CONVERT
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=300_000)
    assert wizard.outcome.kind == "verified"
    assert (tmp_path / "box.solidcraft").is_file()
    assert "Verificado" in wizard.result_label.text()
    assert wizard.save_report_button.isVisibleTo(wizard)


def test_cancelling_leaves_no_half_written_file(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(sample_f3d)
    wizard.output = tmp_path / "box.solidcraft"
    qtbot.waitUntil(lambda: "features" in wizard.holds_label.text(), timeout=60_000)
    wizard.start_conversion()
    qtbot.waitUntil(lambda: wizard.progress_label.text().startswith("Feature"), timeout=120_000)
    wizard.cancel()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == FILE, timeout=30_000)
    assert not (tmp_path / "box.solidcraft").exists()
    assert not (tmp_path / "box.solidcraft.partial").exists()


@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_output_choice_shows_only_with_a_file_loaded(qtbot, tools, sample_f3d, tmp_path, lang):
    # Step ① with nothing loaded has no output to name, so neither «Change…» nor "Save as" shows.
    wizard = Wizard(lang=lang, settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.show()
    qtbot.waitExposed(wizard)
    assert wizard.change_output.text() == t("change_output", lang)
    assert not wizard.change_output.isVisible() and not wizard.save_label.isVisible()
    wizard.load_file(sample_f3d)
    assert wizard.change_output.isVisible() and wizard.save_label.isVisible()
    assert wizard.save_label.text() == t("save_as", lang, name=sample_f3d.with_suffix(".solidcraft").name)
    # A refused file forgets the loaded one: the choice goes with it.
    text = tmp_path / "notes.txt"
    text.write_text("hola")
    wizard.load_file(text)
    assert not wizard.change_output.isVisible() and not wizard.save_label.isVisible()
    # And so does starting over.
    wizard.load_file(sample_f3d)
    assert wizard.change_output.isVisible()
    wizard._reset()
    assert not wizard.change_output.isVisible() and not wizard.save_label.isVisible()


def test_a_file_that_is_not_a_design_is_refused(qtbot, tools, tmp_path):
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    text = tmp_path / "notes.txt"
    text.write_text("hola")
    wizard.load_file(text)
    assert wizard.pages.currentIndex() == FILE
    assert wizard.holds_label.text() == "Ese archivo no es un diseño de Fusion (.f3d o .f3z)."


def test_the_engines_are_checked_without_blocking_the_window(qtbot, tools, tmp_path):
    # `check()` runs every candidate's `--version` (up to 30 s each): the window is built and shows
    # that it is checking before the answer comes back on the UI thread.
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    assert wizard.engines_label.text() == t("checking_engines", "es")
    assert not wizard.convert_button.isEnabled()
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    assert wizard.solidcraft is not None and wizard.solidcraft.ok
    assert wizard.engines_label.text().startswith("✓ SolidCraft")


def _engines_under(root: int) -> list[str]:
    """The command lines of every `solidcraft-cli` running below process `root`, read from `ps`."""
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,ppid=,command="], check=False, capture_output=True, text=True
    ).stdout
    children: dict[int, list[tuple[int, str]]] = {}
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            children.setdefault(int(parts[1]), []).append((int(parts[0]), parts[2]))
    found, todo = [], [root]
    while todo:
        for pid, command in children.get(todo.pop(), []):
            todo.append(pid)
            if "solidcraft-cli" in command:
                found.append(command)
    return found


def test_the_worker_runs_the_engine_the_check_chose(qtbot, tools, sample_f3d, tmp_path, monkeypatch):
    # The converter in the child takes the first `solidcraft-cli` on its PATH; the window picks the
    # first one new enough. With a real old engine first on the PATH and nothing pinned, the engine
    # process the worker starts must still be the one the check chose.
    if not OLD_ENGINE.is_file():
        pytest.skip("no 0.30.1 engine on this machine")
    shadow = tmp_path / "bin"
    shadow.mkdir()
    (shadow / "solidcraft-cli").symlink_to(OLD_ENGINE)
    monkeypatch.delenv("F3D2SC_SOLIDCRAFT_CLI", raising=False)
    monkeypatch.setenv("PATH", f"{shadow}{os.pathsep}{os.environ['PATH']}")
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    qtbot.waitUntil(lambda: wizard.solidcraft is not None, timeout=120_000)
    if wizard.pages.currentIndex() == ENGINES:
        pytest.skip(f"no new enough SolidCraft here besides the shadow: {wizard.solidcraft.problem}")
    chosen = str(wizard.solidcraft.path)
    assert Path(chosen).resolve() != OLD_ENGINE.resolve()
    wizard.load_file(sample_f3d)
    wizard.output = tmp_path / "box.solidcraft"
    qtbot.waitUntil(lambda: "features" in wizard.holds_label.text(), timeout=60_000)
    wizard.start_conversion()
    seen: list[str] = []

    def engine_running() -> bool:
        if wizard.process is not None:
            seen.extend(_engines_under(wizard.process.processId()))
        return bool(seen) or wizard.pages.currentIndex() == RESULT

    qtbot.waitUntil(engine_running, timeout=120_000)
    assert seen, "no engine process was seen under the worker"
    assert all(command.startswith(chosen) for command in seen), (chosen, seen)
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=300_000)
    assert wizard.outcome.kind == "verified"


# --- Fix round 1: the worker's whole tree, crashes, drops mid-run, the engines page ---------------


def _engines_in_group(pgid: int) -> list[int]:
    """The pids of every `solidcraft-cli` whose process group is `pgid`.

    The worker leads its own group; after it dies its children keep that pgid, so this still finds
    an engine the worker left behind, where walking the parent links no longer can.
    """
    listing = subprocess.run(
        ["ps", "-ax", "-o", "pid=,pgid=,command="], check=False, capture_output=True, text=True
    ).stdout
    found = []
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and int(parts[1]) == pgid and "solidcraft-cli" in parts[2]:
            found.append(int(parts[0]))
    return found


def _alive(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], check=False, capture_output=True, text=True)
    return state.returncode == 0 and not state.stdout.strip().startswith("Z")


class _Windows:
    """Native Windows processes through kernel32, where `ps` (Git Bash's, msys) sees none of them.

    A process is held by a handle from the moment it is found: the handle keeps its pid from being
    reused, so "this engine is dead" is asked of that very process, not of whatever has its pid now.
    """

    SNAPPROCESS, SNAPTHREAD = 0x2, 0x4
    SYNCHRONIZE, TERMINATE, QUERY_LIMITED = 0x00100000, 0x0001, 0x1000
    THREAD_SUSPEND_RESUME = 0x0002
    WAIT_TIMEOUT = 0x102

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes

        class Process(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        class Thread(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        self.Process, self.Thread = Process, Thread
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name, kind in (("Process32FirstW", Process), ("Process32NextW", Process)):
            getattr(k, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(kind)]
            getattr(k, name).restype = wintypes.BOOL
        for name in ("Thread32First", "Thread32Next"):
            getattr(k, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Thread)]
            getattr(k, name).restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenThread.restype = wintypes.HANDLE
        k.SuspendThread.argtypes = [wintypes.HANDLE]
        k.SuspendThread.restype = wintypes.DWORD
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        self.k = k

    def _snapshot(self, kind: int, entry_type, first: str, following: str) -> list:
        snapshot = self.k.CreateToolhelp32Snapshot(kind, 0)
        if snapshot in (None, 0, -1, self.ctypes.c_void_p(-1).value):
            raise OSError(self.ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        entries = []
        try:
            entry = entry_type()
            entry.dwSize = self.ctypes.sizeof(entry_type)
            more = getattr(self.k, first)(snapshot, self.ctypes.byref(entry))
            while more:
                copy = entry_type()
                self.ctypes.pointer(copy)[0] = entry
                entries.append(copy)
                more = getattr(self.k, following)(snapshot, self.ctypes.byref(entry))
        finally:
            self.k.CloseHandle(snapshot)
        return entries

    def engines_under(self, root: int) -> list[int]:
        """The pids of every `solidcraft-cli.exe` below `root`, by the parent pid each one recorded.

        The link survives the parent: an engine whose worker died still names the worker's pid.
        """
        processes = self._snapshot(self.SNAPPROCESS, self.Process, "Process32FirstW", "Process32NextW")
        children: dict[int, list] = {}
        for p in processes:
            if p.th32ProcessID != p.th32ParentProcessID:
                children.setdefault(p.th32ParentProcessID, []).append(p)
        found, todo, seen = [], [root], set()
        while todo:
            parent = todo.pop()
            for p in children.get(parent, []):
                if p.th32ProcessID in seen:
                    continue
                seen.add(p.th32ProcessID)
                todo.append(p.th32ProcessID)
                if p.szExeFile.lower().startswith("solidcraft-cli"):
                    found.append(p.th32ProcessID)
        return found

    def hold(self, pid: int) -> int:
        handle = self.k.OpenProcess(self.SYNCHRONIZE | self.TERMINATE | self.QUERY_LIMITED, False, pid)
        if not handle:
            raise OSError(self.ctypes.get_last_error(), f"cannot open process {pid}")
        return handle

    def alive(self, handle: int) -> bool:
        return self.k.WaitForSingleObject(handle, 0) == self.WAIT_TIMEOUT

    def suspend(self, pid: int) -> int:
        """Suspend every thread of `pid`; how many there were."""
        threads = self._snapshot(self.SNAPTHREAD, self.Thread, "Thread32First", "Thread32Next")
        count = 0
        for thread in threads:
            if thread.th32OwnerProcessID != pid:
                continue
            handle = self.k.OpenThread(self.THREAD_SUSPEND_RESUME, False, thread.th32ThreadID)
            if handle:
                if self.k.SuspendThread(handle) != 0xFFFFFFFF:
                    count += 1
                self.k.CloseHandle(handle)
        return count

    def terminate(self, handle: int, code: int) -> None:
        if not self.k.TerminateProcess(handle, code):
            raise OSError(self.ctypes.get_last_error(), "TerminateProcess failed")

    def release(self, handle: int) -> None:
        self.k.CloseHandle(handle)


@functools.cache
def _windows() -> _Windows:
    return _Windows()


def _engines_below(worker: int) -> list[int]:
    """The pids of the `solidcraft-cli` processes a worker started, alive or left behind.

    On POSIX by process group, which the engines keep after the worker dies; on Windows by the parent
    pid each one recorded, which outlives the parent too.
    """
    if os.name == "nt":
        return _windows().engines_under(worker)
    return _engines_in_group(worker)


def _engine_alive(engine: int) -> bool:
    """An engine as `stall` returned it: a pid on POSIX, a handle held on that process on Windows."""
    return _windows().alive(engine) if os.name == "nt" else _alive(engine)


def _no_engine_left(worker: int, engines: list[int]) -> bool:
    return not _engines_below(worker) and not any(_engine_alive(e) for e in engines)


def _converting(qtbot, wizard, sample_f3d, output: Path, answer=None) -> int:
    """Start converting the sample into `output` and return the worker's pid once it is under way.

    Under way is the wizard's own progress (its bar has a total once the first feature is reported)
    and an engine running below the worker, which the kill tests stall. `answer` clicks that button
    on the replace question, if one comes up.
    """
    wizard.load_file(sample_f3d)
    wizard.output = output
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    if answer is not None:
        _answer_question(answer)
    wizard.start_conversion()
    pid = wizard.process.processId()
    qtbot.waitUntil(lambda: bool(_engines_below(pid)) and wizard.bar.maximum() > 0, timeout=120_000)
    return pid


def _answer_question(button) -> list[str]:
    """Click `button` on the next modal question the window asks; returns the texts it asked.

    `QMessageBox.question` runs its own event loop, so the click is queued before the call that asks.
    It gives up after a few seconds, so a question that never comes leaves no timer behind.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    asked: list[str] = []

    def click(tries: int = 200) -> None:
        box = QApplication.activeModalWidget()
        if isinstance(box, QMessageBox):
            asked.append(box.text())
            box.button(button).click()
        elif tries:
            QTimer.singleShot(25, lambda: click(tries - 1))

    QTimer.singleShot(0, click)
    return asked


@pytest.fixture
def stall():
    """Stop the worker's engines, as a busy engine that is not reading its stdin.

    Measured on this Mac: an engine whose worker is killed alone exits by itself 15-45 ms later,
    when its stdin closes, so an idle engine cannot tell a tree kill from a worker-only kill. A
    stopped one does not exit by itself; only a kill that reaches it ends it. On POSIX that is
    SIGSTOP; Windows has no such signal, so every thread of the engine is suspended (`SuspendThread`),
    which a process cannot undo from inside either. Whatever is left is killed at teardown so a
    failing run does not leave stopped engines behind.

    Returns what `_engine_alive` takes: pids on POSIX, handles held on the engines on Windows.
    """
    import signal

    stopped: list[int] = []

    def stall_engines(worker: int) -> list[int]:
        if os.name == "nt":
            windows = _windows()
            pids = _engines_below(worker)
            engines = [windows.hold(pid) for pid in pids]
            stopped.extend(engines)
            for pid in pids:
                assert windows.suspend(pid) > 0, f"no thread of engine {pid} was suspended"
            return engines
        engines = _engines_in_group(worker)
        for engine in engines:
            os.kill(engine, signal.SIGSTOP)
        stopped.extend(engines)
        return engines

    yield stall_engines
    for engine in stopped:
        if os.name == "nt":
            if _windows().alive(engine):
                _windows().terminate(engine, 1)
            _windows().release(engine)
            continue
        try:
            os.kill(engine, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_cancelling_kills_the_engine_too(qtbot, tools, sample_f3d, tmp_path, stall):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    output = tmp_path / "box.solidcraft"
    pid = _converting(qtbot, wizard, sample_f3d, output)
    if os.name == "posix":
        assert os.getpgid(pid) == pid, "the worker leads its own process group"
    else:
        assert wizard.job.tree is not None, "the worker is in a job object"
    engines = stall(pid)
    assert engines
    wizard.cancel()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == FILE, timeout=30_000)
    qtbot.waitUntil(lambda: _no_engine_left(pid, engines), timeout=5_000)
    partials = [output.with_name(output.name + ".partial"), tmp_path / "box.report.json.partial"]
    with pytest.raises(qtbot.TimeoutError):
        qtbot.waitUntil(lambda: any(p.exists() for p in partials), timeout=2_000)
    assert not output.exists()


def test_closing_the_window_mid_conversion_kills_the_tree_and_the_partials(
    qtbot, tools, sample_f3d, tmp_path, stall
):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    output = tmp_path / "box.solidcraft"
    pid = _converting(qtbot, wizard, sample_f3d, output)
    worker = _windows().hold(pid) if os.name == "nt" else pid
    engines = stall(pid)
    assert engines
    try:
        wizard.close()
        qtbot.waitUntil(
            lambda: _no_engine_left(pid, engines) and not _engine_alive(worker),
            timeout=5_000,
        )
    finally:
        if os.name == "nt":
            _windows().release(worker)
    partials = [output.with_name(output.name + ".partial"), tmp_path / "box.report.json.partial"]
    with pytest.raises(qtbot.TimeoutError):
        qtbot.waitUntil(lambda: any(p.exists() for p in partials), timeout=2_000)
    assert not output.exists()


#: How the worker dies from outside. On Windows, Task Manager's «End task» (TerminateProcess with exit
#: code 1, which Qt reports as a normal exit) and a crash (an access violation's code, which Qt reports
#: as one); elsewhere SIGKILL.
KILLS = {"end task": 1, "crash": 0xC0000005} if os.name == "nt" else {"SIGKILL": None}


@pytest.mark.parametrize("how", list(KILLS))
def test_a_worker_killed_from_outside_is_an_error_and_the_window_lives(
    qtbot, tools, sample_f3d, tmp_path, stall, how
):
    # Spec ③: «Si el proceso del motor muere a mitad de camino, se trata como error (1)». A real report
    # from an earlier conversion sits where this run writes its own: it must not be read as this run's.
    import signal

    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    first = tmp_path / "first.solidcraft"
    wizard.load_file(sample_f3d)
    wizard.output = first
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    wizard.start_conversion()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=300_000)
    assert wizard.outcome.kind == "verified"
    stale = tmp_path / "second.report.json"
    stale.write_text((tmp_path / "first.report.json").read_text(encoding="utf-8"), encoding="utf-8")
    wizard._reset()
    from PySide6.QtWidgets import QMessageBox

    # The stale report is named in the replace question; replacing it is the user's answer.
    pid = _converting(
        qtbot, wizard, sample_f3d, tmp_path / "second.solidcraft", answer=QMessageBox.StandardButton.Yes
    )
    engines = stall(pid)  # the worker dies alone; its engine is left behind, busy
    assert engines
    if os.name == "nt":
        worker = _windows().hold(pid)
        try:
            _windows().terminate(worker, KILLS[how])
        finally:
            _windows().release(worker)
    else:
        os.kill(pid, signal.SIGKILL)
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=30_000)
    assert wizard.outcome.kind == "error"
    assert wizard.copy_error.isVisibleTo(wizard)
    assert not stale.exists(), "the earlier run's report was left where this run's goes"
    qtbot.waitUntil(lambda: _no_engine_left(pid, engines), timeout=5_000)
    wizard._reset()
    wizard.load_file(sample_f3d)
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=60_000)


def test_a_worker_that_does_not_start_is_an_error(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(sample_f3d)
    wizard.output = tmp_path / "box.solidcraft"
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    wizard.executable = str(tmp_path / "no-such-app")  # a packaged app whose binary is gone
    wizard.start_conversion()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=10_000)
    assert wizard.outcome.kind == "error"
    assert wizard.process is None


def test_an_inspection_that_does_not_start_says_so(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    wizard.executable = str(tmp_path / "no-such-app")
    wizard.load_file(sample_f3d)
    qtbot.waitUntil(lambda: wizard.holds_label.text() != "", timeout=10_000)
    assert "features" not in wizard.holds_label.text()
    assert wizard.input is None and not wizard.convert_button.isEnabled()


def _drop(wizard, path: Path) -> None:
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    wizard.dropEvent(event)


def test_a_drop_during_a_conversion_changes_nothing(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    output = tmp_path / "box.solidcraft"
    _converting(qtbot, wizard, sample_f3d, output)
    other = tmp_path / "other.f3d"
    other.write_bytes(sample_f3d.read_bytes())
    _drop(wizard, other)
    wizard.load_file(other)
    assert wizard.input == sample_f3d and wizard.output == output
    assert wizard.pages.currentIndex() == CONVERT
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=300_000)
    assert wizard.outcome.kind == "verified"
    assert output.is_file() and (tmp_path / "box.report.json").is_file()
    assert not (tmp_path / "other.solidcraft").exists()


def test_a_drop_on_the_result_page_starts_over_with_that_file(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(sample_f3d)
    wizard.output = tmp_path / "box.solidcraft"
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    wizard.start_conversion()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=300_000)
    other = tmp_path / "other.f3d"
    other.write_bytes(sample_f3d.read_bytes())
    _drop(wizard, other)
    assert wizard.pages.currentIndex() == FILE
    assert wizard.input == other and wizard.outcome is None
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=60_000)


def test_a_refused_file_after_a_good_one_cannot_be_converted(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(sample_f3d)
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    text = tmp_path / "notes.txt"
    text.write_text("hola")
    _drop(wizard, text)
    assert wizard.input is None and not wizard.inspected
    assert not wizard.convert_button.isEnabled()
    assert wizard.drop_label.text() == t("drop_here", "es")


def test_a_design_that_does_not_inspect_cannot_be_converted(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(sample_f3d)
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    broken = tmp_path / "broken.f3d"
    broken.write_bytes(sample_f3d.read_bytes()[:4096])  # the real file, cut short
    wizard.load_file(broken)
    qtbot.waitUntil(lambda: wizard.inspecting is None and wizard.holds_label.text() != "", timeout=60_000)
    assert wizard.input is None and not wizard.inspected
    assert not wizard.convert_button.isEnabled()


def test_locating_solidcraft_is_off_while_a_check_runs(qtbot, tools, tmp_path):
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    assert wizard.checking
    assert not wizard.locate_button.isEnabled()
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    assert wizard.locate_button.isEnabled()


def test_a_missing_cadmpeg_is_named_as_cadmpeg(qtbot, tools, tmp_path, monkeypatch):
    # An explicit F3D2SC_CADMPEG wins over the bundled one, so a real path that does not exist is a
    # missing cadmpeg whether or not `scripts/fetch_cadmpeg.py` has run in this tree.
    monkeypatch.setenv("F3D2SC_CADMPEG", str(tmp_path / "no-cadmpeg"))
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    assert wizard.pages.currentIndex() == ENGINES
    assert wizard.engines_title.text() == f"<b>{t('missing_cadmpeg', 'es')}</b>"
    assert "SolidCraft" not in wizard.engines_problem.text()
    assert "no-cadmpeg" in wizard.engines_problem.text()
    assert not wizard.download_button.isVisibleTo(wizard)


# --- Final review: the output is never the input, a report is only replaced when asked, labels show
# names as text, a broken settings file, the app bundle, and no report to save ----------------------


def _shown(label) -> str:
    """What a label displays: its text as is when plain, the rendered text when Qt reads it as HTML."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import Qt as GuiQt  # `mightBeRichText` lives in QtGui's namespace
    from PySide6.QtGui import QTextDocument

    text = label.text()
    plain = label.textFormat() == Qt.TextFormat.PlainText or (
        label.textFormat() == Qt.TextFormat.AutoText and not GuiQt.mightBeRichText(text)
    )
    if plain:
        return text
    document = QTextDocument()
    document.setHtml(text)
    return document.toPlainText()


def _copy(sample_f3d: Path, path: Path) -> Path:
    path.write_bytes(sample_f3d.read_bytes())
    return path


def test_the_output_cannot_be_the_input(qtbot, tools, sample_f3d, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    design = _copy(sample_f3d, tmp_path / "box.f3d")
    original = design.read_bytes()
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(design)
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    # Chosen in «Cambiar…» by another spelling of the same file: refused, the old choice stays.
    assert not wizard.set_output(tmp_path / "sub" / ".." / "box.f3d")
    assert wizard.output == tmp_path / "box.solidcraft"
    assert wizard.save_label.text() == t("same_as_input", "es")
    # And if it gets there anyway, converting refuses before asking anything.
    wizard.output = design
    asked = _answer_question(QMessageBox.StandardButton.Yes)
    wizard.start_conversion()
    qtbot.wait(300)
    assert asked == []
    assert wizard.job is None and wizard.pages.currentIndex() == FILE
    assert wizard.save_label.text() == t("same_as_input", "es")
    assert design.read_bytes() == original


def test_a_design_named_with_accents_converts_through_the_window(qtbot, tools, sample_f3d, tmp_path):
    # The same names as the command's test: accents, an ñ, an en dash and a letter outside Windows'
    # ANSI code page, in the design and in the folder the result goes to. The worker's paths, its
    # progress lines and its report all cross a process boundary.
    design = _copy(sample_f3d, tmp_path / "caja ñandú – pieza Ω.f3d")
    folder = tmp_path / "salida ñandú – Ω"
    folder.mkdir()
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(design)
    assert wizard.drop_label.text() == "caja ñandú – pieza Ω.f3d"
    qtbot.waitUntil(lambda: "26 operaciones" in wizard.holds_label.text(), timeout=120_000)
    output = folder / "caja ñandú – pieza.solidcraft"
    assert wizard.set_output(output)
    assert wizard.save_label.text() == t("save_as", "es", name="caja ñandú – pieza.solidcraft")
    wizard.start_conversion()
    assert _shown(wizard.converting_label) == "caja ñandú – pieza Ω.f3d"
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=300_000)
    assert wizard.outcome.kind == "verified", wizard.outcome.message
    assert output.is_file()
    report = folder / "caja ñandú – pieza.report.json"
    # Qt hands a process its arguments decomposed (NFD) on macOS, as file names are spelt there; the
    # file system takes either spelling, so the report names the same file in another normal form.
    source = unicodedata.normalize("NFC", json.loads(report.read_text(encoding="utf-8"))["source"])
    assert source.endswith("caja ñandú – pieza Ω.f3d"), source
    assert wizard.report == report and wizard.save_report_button.isVisibleTo(wizard)
    assert not list(folder.glob("*.partial"))


def test_an_existing_report_is_named_before_it_is_replaced(qtbot, tools, sample_f3d, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    design = _copy(sample_f3d, tmp_path / "box.f3d")
    report = tmp_path / "box.report.json"
    report.write_text('{"mine": true}', encoding="utf-8")  # the user's own file, not one of ours
    wizard = Wizard(lang="es", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(design)
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    asked = _answer_question(QMessageBox.StandardButton.No)
    wizard.start_conversion()
    qtbot.waitUntil(lambda: bool(asked), timeout=5_000)
    assert "box.report.json" in asked[0]
    assert wizard.job is None and wizard.pages.currentIndex() == FILE
    assert report.read_text(encoding="utf-8") == '{"mine": true}'


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold < or >")
def test_names_and_messages_are_shown_as_text(qtbot, tools, sample_f3d, tmp_path):
    # A file name cannot hold `</b>` (the slash), but an unclosed `<b>` is still bold to Qt.
    design = _copy(sample_f3d, tmp_path / "<b>x.f3d")
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(design)
    assert _shown(wizard.drop_label) == "<b>x.f3d"
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    # The real converter refuses an output inside a file, and its error names that file.
    wizard.output = design / "y.solidcraft"
    wizard.start_conversion()
    assert _shown(wizard.converting_label) == "<b>x.f3d"
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=120_000)
    assert wizard.outcome.kind == "error"
    assert "<b>x.f3d" in wizard.outcome.message
    assert wizard.outcome.message in _shown(wizard.result_label)


@pytest.mark.parametrize("content", ["[]", '"x"', '{"solidcraft_cli": 5}'])
def test_a_settings_file_that_is_not_an_object_does_not_stop_the_window(qtbot, tools, tmp_path, content):
    settings = tmp_path / "settings.json"
    settings.write_text(content, encoding="utf-8")
    wizard = Wizard(lang="en", settings_path=settings)
    qtbot.addWidget(wizard)
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    assert wizard.pages.currentIndex() == FILE


def test_locating_a_folder_that_is_not_solidcraft_is_refused(qtbot, tools, tmp_path):
    settings = tmp_path / "settings.json"
    wizard = Wizard(lang="es", settings_path=settings)
    qtbot.addWidget(wizard)
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    other = tmp_path / "Other.app"
    other.mkdir()
    assert not wizard.use_solidcraft(other)
    assert wizard.engines_problem.text() == t("not_solidcraft", "es")
    assert not settings.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="app bundles are macOS")
def test_locating_the_app_bundle_saves_the_cli_inside_it(qtbot, tools, tmp_path):
    bundle = Path("/Applications/SolidCraft.app")
    if not (bundle / "Contents/MacOS/solidcraft-cli").is_file():
        pytest.skip("SolidCraft.app is not installed on this machine")
    settings = tmp_path / "settings.json"
    wizard = Wizard(lang="es", settings_path=settings)
    qtbot.addWidget(wizard)
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        pass
    with qtbot.waitSignal(wizard.engines_checked, timeout=120_000):
        assert wizard.use_solidcraft(bundle)
    assert saved_solidcraft(settings) == bundle / "Contents/MacOS/solidcraft-cli"


def test_there_is_no_report_to_save_after_an_error_without_one(qtbot, tools, sample_f3d, tmp_path):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    wizard.load_file(sample_f3d)
    wizard.output = tmp_path / "box.solidcraft"
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    wizard.executable = str(tmp_path / "no-such-app")
    wizard.start_conversion()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=10_000)
    assert wizard.outcome.kind == "error"
    assert not wizard.save_report_button.isVisibleTo(wizard)
