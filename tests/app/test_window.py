"""The whole wizard, offscreen, on the public sample with the real engine (spec, Pruebas)."""

import os
import subprocess
import sys
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


def _converting(
    qtbot, wizard, sample_f3d, output: Path, wait_for_engine: bool = os.name == "posix", answer=None
) -> int:
    """Start converting the sample into `output` and return the worker's pid once it is under way.

    Under way is the wizard's own progress: its bar has a total once the first feature is reported.
    On POSIX it also waits for an engine below the worker, which the process-group tests stall; `ps`
    on Windows (Git Bash's msys one) knows nothing of native pids, so there the bar alone tells.
    `answer` clicks that button on the replace question, if one comes up.
    """
    wizard.load_file(sample_f3d)
    wizard.output = output
    qtbot.waitUntil(lambda: wizard.convert_button.isEnabled(), timeout=120_000)
    if answer is not None:
        _answer_question(answer)
    wizard.start_conversion()
    pid = wizard.process.processId()
    if wait_for_engine:
        qtbot.waitUntil(lambda: bool(_engines_under(pid)) and wizard.bar.maximum() > 0, timeout=120_000)
    else:
        qtbot.waitUntil(lambda: wizard.bar.maximum() > 0, timeout=120_000)
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
def stall(request):
    """Stop the worker's engines with SIGSTOP, as a busy engine that is not reading its stdin.

    Measured on this Mac: an engine whose worker is killed alone exits by itself 15-45 ms later,
    when its stdin closes, so an idle engine cannot tell a tree kill from a worker-only kill. A
    stopped one does not exit by itself; only a kill that reaches it ends it. Whatever is left is
    killed at teardown so a failing run does not leave stopped engines behind.
    """
    import signal

    stopped: list[int] = []

    def stall_engines(pgid: int) -> list[int]:
        engines = _engines_in_group(pgid)
        for engine in engines:
            os.kill(engine, signal.SIGSTOP)
        stopped.extend(engines)
        return engines

    yield stall_engines
    for engine in stopped:
        try:
            os.kill(engine, signal.SIGKILL)
        except ProcessLookupError:
            pass


unix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are Unix; Windows uses taskkill")


@unix_only
def test_cancelling_kills_the_engine_too(qtbot, tools, sample_f3d, tmp_path, stall):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    output = tmp_path / "box.solidcraft"
    pid = _converting(qtbot, wizard, sample_f3d, output)
    assert os.getpgid(pid) == pid, "the worker leads its own process group"
    engines = stall(pid)
    assert engines
    wizard.cancel()
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == FILE, timeout=30_000)
    qtbot.waitUntil(lambda: not _engines_in_group(pid) and not any(_alive(e) for e in engines), timeout=5_000)
    partials = [output.with_name(output.name + ".partial"), tmp_path / "box.report.json.partial"]
    with pytest.raises(qtbot.TimeoutError):
        qtbot.waitUntil(lambda: any(p.exists() for p in partials), timeout=2_000)
    assert not output.exists()


@unix_only
def test_closing_the_window_mid_conversion_kills_the_tree_and_the_partials(
    qtbot, tools, sample_f3d, tmp_path, stall
):
    wizard = Wizard(lang="en", settings_path=tmp_path / "settings.json")
    qtbot.addWidget(wizard)
    output = tmp_path / "box.solidcraft"
    pid = _converting(qtbot, wizard, sample_f3d, output)
    engines = stall(pid)
    assert engines
    wizard.close()
    qtbot.waitUntil(
        lambda: not _engines_in_group(pid) and not _alive(pid) and not any(_alive(e) for e in engines),
        timeout=5_000,
    )
    partials = [output.with_name(output.name + ".partial"), tmp_path / "box.report.json.partial"]
    with pytest.raises(qtbot.TimeoutError):
        qtbot.waitUntil(lambda: any(p.exists() for p in partials), timeout=2_000)
    assert not output.exists()


@unix_only
def test_a_worker_killed_from_outside_is_an_error_and_the_window_lives(
    qtbot, tools, sample_f3d, tmp_path, stall
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
    os.kill(pid, signal.SIGKILL)
    qtbot.waitUntil(lambda: wizard.pages.currentIndex() == RESULT, timeout=30_000)
    assert wizard.outcome.kind == "error"
    assert wizard.copy_error.isVisibleTo(wizard)
    assert not stale.exists(), "the earlier run's report was left where this run's goes"
    qtbot.waitUntil(lambda: not any(_alive(e) for e in engines), timeout=5_000)
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
