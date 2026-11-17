"""The three-step wizard (spec, Flujo del asistente). The converter always runs in another process."""

import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QLocale, QObject, QProcess, QProcessEnvironment, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from f3d2solidcraft.tools import SOLIDCRAFT_VERSION
from f3d2solidcraft_app.engines import (
    EngineStatus,
    check,
    prepare_environment,
    save_solidcraft,
    saved_solidcraft,
    solidcraft_in,
)
from f3d2solidcraft_app.events import Feature, parse
from f3d2solidcraft_app.opener import open_design
from f3d2solidcraft_app.outcome import Outcome, outcome_from
from f3d2solidcraft_app.runner import OWN_GROUP, kill_group, kill_tree, partial_paths, worker_command
from f3d2solidcraft_app.stdio import ONE_PIPE
from f3d2solidcraft_app.texts import language, t

if sys.platform == "win32":
    from f3d2solidcraft_app.winjob import JOB_VARIABLE, ProcessTree

FILE, CONVERT, RESULT, ENGINES = range(4)
DESIGN_SUFFIXES = {".f3d", ".f3z"}
DOWNLOAD_URL = "https://github.com/tangency-labs/solidcraft/releases"


class _Checker(QObject):
    """Carries `check()`'s answer from the pool thread back to the UI thread (a queued signal)."""

    done = Signal(object, object)


def _checked() -> tuple[EngineStatus, EngineStatus]:
    """`check()`, but an exception in the pool thread becomes a failed status, not a hung window."""
    try:
        return check()
    except Exception as exc:  # noqa: BLE001 (whatever it is, the window must hear back)
        problem = f"the engine check failed: {exc!r}"
        return EngineStatus("solidcraft-cli", None, None, problem), EngineStatus(
            "cadmpeg", None, None, problem
        )


@dataclass
class _Job:
    """One conversion, fixed when it starts: a drop or a new file afterwards cannot move its paths."""

    process: QProcess
    pid: int
    output: Path
    report: Path
    # Windows: the Job object the worker and its engines live in; closing it ends them all.
    tree: "ProcessTree | None" = None

    @property
    def partials(self) -> list[Path]:
        return partial_paths(self.output, self.report)


class Wizard(QMainWindow):
    #: Emitted on the UI thread once an engine check has been answered and shown.
    engines_checked = Signal()

    def __init__(self, lang: str | None = None, settings_path: Path | None = None) -> None:
        super().__init__()
        self.lang = lang or language(QLocale.system().name())
        self.settings_path = settings_path or Path.home() / ".f3d2solidcraft" / "app.json"
        self.input: Path | None = None
        self.output: Path | None = None
        self.report: Path | None = None
        self.outcome: Outcome | None = None
        self.job: _Job | None = None
        # Python in development; packaged, the app itself (`worker_command`).
        self.executable = sys.executable
        self.inspecting: QProcess | None = None
        self.inspecting_tree: ProcessTree | None = None  # Windows: the inspection's job
        # The SolidCraft the check chose; None until a check has been answered.
        self.solidcraft: EngineStatus | None = None
        self.checking = False
        self.engines_ready = False
        self.inspected = False
        self._checker = _Checker()
        self._checker.done.connect(self._engines_checked)
        self.buffer = ""
        self.stderr = ""  # the worker's text: every line of its one pipe that is not a progress event
        self.cancelled = False
        self.setWindowTitle(t("title", self.lang))
        self.setAcceptDrops(True)
        self.pages = QStackedWidget()
        self.steps = QLabel()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.steps)
        layout.addWidget(self.pages)
        self.setCentralWidget(root)
        self._build_file_page()
        self._build_convert_page()
        self._build_result_page()
        self._build_engines_page()
        prepare_environment(saved_solidcraft(self.settings_path))
        self._show(FILE)
        self.check_engines()

    @property
    def process(self) -> QProcess | None:
        """The running conversion's worker, if any."""
        return self.job.process if self.job is not None else None

    # --- pages ------------------------------------------------------------------------------
    def _show(self, page: int) -> None:
        names = [t("step_file", self.lang), t("step_convert", self.lang), t("step_result", self.lang)]
        current = FILE if page == ENGINES else page
        self.steps.setText(" — ".join(f"<b>{n}</b>" if i == current else n for i, n in enumerate(names)))
        self.pages.setCurrentIndex(page)

    def _build_file_page(self) -> None:
        page = QWidget()
        box = QVBoxLayout(page)
        # Labels that show a file name, a feature name or an engine's message take it as text, never
        # as HTML: a name holding `<b>` is shown as written.
        self.drop_label = _plain(QLabel(t("drop_here", self.lang)))
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_label.setStyleSheet("border: 2px dashed #999; border-radius: 10px; padding: 30px;")
        choose = QPushButton(t("choose_file", self.lang))
        choose.clicked.connect(self._choose_file)
        self.holds_label = _plain(QLabel(""))
        self.save_label = _plain(QLabel(""))
        self.change_output = QPushButton(t("change_output", self.lang))
        self.change_output.clicked.connect(self._choose_output)
        self.engines_label = _plain(QLabel(""))  # versions as the engines print them
        self.convert_button = QPushButton(t("convert", self.lang))
        self.convert_button.setEnabled(False)
        self.convert_button.clicked.connect(self.start_conversion)
        for widget in (
            self.drop_label,
            choose,
            self.holds_label,
            self.save_label,
            self.change_output,
            self.engines_label,
        ):
            box.addWidget(widget)
        self._show_output_choice(False)  # nothing to save, nor to change, before a file is loaded
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.convert_button)
        box.addLayout(row)
        self.pages.addWidget(page)

    def _build_convert_page(self) -> None:
        page = QWidget()
        box = QVBoxLayout(page)
        self.converting_label = QLabel("")
        self.bar = QProgressBar()
        self.progress_label = _plain(QLabel(""))
        note = QLabel(t("slow_note", self.lang))
        note.setWordWrap(True)
        cancel = QPushButton(t("cancel", self.lang))
        cancel.clicked.connect(self.cancel)
        for widget in (self.converting_label, self.bar, self.progress_label, note, cancel):
            box.addWidget(widget)
        self.pages.addWidget(page)

    def _build_result_page(self) -> None:
        page = QWidget()
        box = QVBoxLayout(page)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.counts_label = QLabel("")
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.hide()
        show_details = QPushButton(t("details", self.lang))
        show_details.clicked.connect(lambda: self.details.setVisible(not self.details.isVisible()))
        self.save_report_button = QPushButton(t("save_report", self.lang))
        self.save_report_button.clicked.connect(self._save_report)
        self.copy_error = QPushButton(t("copy_error", self.lang))
        self.copy_error.clicked.connect(self._copy_error)
        another = QPushButton(t("convert_another", self.lang))
        another.clicked.connect(self._reset)
        self.open_button = QPushButton(t("open_in_solidcraft", self.lang))
        self.open_button.clicked.connect(self._open_result)
        for widget in (
            self.result_label,
            self.counts_label,
            show_details,
            self.details,
            self.save_report_button,
            self.copy_error,
        ):
            box.addWidget(widget)
        row = QHBoxLayout()
        row.addWidget(another)
        row.addStretch()
        row.addWidget(self.open_button)
        box.addLayout(row)
        self.pages.addWidget(page)

    def _build_engines_page(self) -> None:
        page = QWidget()
        box = QVBoxLayout(page)
        self.engines_title = QLabel(f"<b>{t('missing_solidcraft', self.lang)}</b>")
        self.engines_problem = _plain(QLabel(""))
        self.engines_problem.setWordWrap(True)
        self.download_button = QPushButton(t("download_solidcraft", self.lang))
        self.download_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOWNLOAD_URL)))
        self.locate_button = QPushButton(t("locate_solidcraft", self.lang))
        self.locate_button.clicked.connect(self._locate_solidcraft)
        self.check_again = QPushButton(t("check_again", self.lang))
        self.check_again.clicked.connect(self.check_engines)
        for widget in (
            self.engines_title,
            self.engines_problem,
            self.download_button,
            self.locate_button,
            self.check_again,
        ):
            box.addWidget(widget)
        self.pages.addWidget(page)

    # --- engines ----------------------------------------------------------------------------
    def check_engines(self) -> None:
        """Run `check()` in the thread pool: it asks each candidate for its version, up to 30 s each."""
        if self.checking:
            return
        self.checking = True
        self.engines_ready = False
        self.engines_label.setText(t("checking_engines", self.lang))
        self.engines_problem.setText(t("checking_engines", self.lang))
        # The pool thread reads `os.environ`; locating a SolidCraft writes it, so not while it runs.
        self.check_again.setEnabled(False)
        self.locate_button.setEnabled(False)
        self._update_convert_button()
        checker = self._checker  # not `self`: the pool thread only touches the messenger
        QThreadPool.globalInstance().start(lambda: checker.done.emit(*_checked()))

    def _engines_checked(self, solidcraft: EngineStatus, cadmpeg: EngineStatus) -> None:
        self.checking = False
        self.solidcraft = solidcraft
        self.check_again.setEnabled(True)
        self.locate_button.setEnabled(True)
        if not solidcraft.ok or not cadmpeg.ok:
            # Each failing engine speaks for itself; SolidCraft's buttons only when SolidCraft fails.
            details = []
            if not solidcraft.ok:
                details.append(
                    t(
                        "missing_solidcraft_detail",
                        self.lang,
                        minimum=SOLIDCRAFT_VERSION,
                        why=solidcraft.problem,
                    )
                )
            if not cadmpeg.ok:
                details.append(t("missing_cadmpeg_detail", self.lang, why=cadmpeg.problem))
            title = "missing_solidcraft" if not solidcraft.ok else "missing_cadmpeg"
            self.engines_title.setText(f"<b>{t(title, self.lang)}</b>")
            self.download_button.setVisible(not solidcraft.ok)
            self.locate_button.setVisible(not solidcraft.ok)
            self.engines_label.setText("")
            self.engines_problem.setText("\n\n".join(details))
            self._show(ENGINES)
        else:
            self.engines_ready = True
            self.engines_label.setText(
                t("engines_ok", self.lang, solidcraft=solidcraft.version, cadmpeg=cadmpeg.version)
            )
            if self.pages.currentIndex() == ENGINES:
                self._show(FILE)
            if self.input is not None and not self.inspected and self.inspecting is None:
                self._inspect()
        self._update_convert_button()
        self.engines_checked.emit()

    def _new_process(self) -> tuple[QProcess, "ProcessTree | None"]:
        """A converter process pinned to the SolidCraft the check chose, and on Windows its job.

        The converter resolves SolidCraft on its own with `locate_solidcraft()`, the first copy on
        the PATH; the check picks the first one new enough. Passing the choice in
        `F3D2SC_SOLIDCRAFT_CLI` makes both the same engine.

        On Windows the job is made here, before the worker starts, so its name can go in the
        worker's environment (`winjob`); `_start` puts the worker in it. None where there are process
        groups instead, or if Windows refused to make one (then `kill_tree` falls back to taskkill).
        """
        process = QProcess(self)
        # stdout and stderr in one pipe: packaged on Windows, the worker's launcher moves its stdout
        # off the pipe it was given, and only a pipe shared with stderr survives that (`stdio`). The
        # window tells the two apart by line: JSON lines are the converter's, the rest is its text.
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert(ONE_PIPE, "1")
        environment.insert(OWN_GROUP, "1")  # so `kill_tree` reaches the engines too
        if self.solidcraft is not None and self.solidcraft.ok and self.solidcraft.path is not None:
            environment.insert("F3D2SC_SOLIDCRAFT_CLI", str(self.solidcraft.path))
        tree = None
        if sys.platform == "win32":
            try:
                tree = ProcessTree()
            except OSError as exc:
                print(f"f3d2solidcraft: no job object for the worker: {exc}", file=sys.stderr)
            else:
                environment.insert(JOB_VARIABLE, tree.name)
        process.setProcessEnvironment(environment)
        return process, tree

    @staticmethod
    def _start(process: QProcess, tree: "ProcessTree | None", command: list[str]) -> "ProcessTree | None":
        """Start `process` and put it in `tree` at once; the tree, or None if there is none to keep.

        A worker the window could not assign still joins the job itself (`winjob.join`), so the
        tree is kept: closing it is still what ends whatever is inside.
        """
        process.start(command[0], command[1:])
        if tree is None:
            return None
        pid = process.processId()
        if not pid:  # it did not start: nothing can be inside
            tree.close()
            return None
        try:
            tree.add(pid)
        except OSError as exc:
            print(f"f3d2solidcraft: could not put the worker in its job object: {exc}", file=sys.stderr)
        return tree

    @staticmethod
    def _end_tree(tree: "ProcessTree | None", pid: int) -> None:
        """Kill a running worker with every process under it: its job on Windows, its group elsewhere."""
        if tree is not None:
            tree.close()
        else:
            kill_tree(pid)

    def _update_convert_button(self) -> None:
        self.convert_button.setEnabled(self.engines_ready and self.inspected and self.input is not None)

    def _locate_solidcraft(self) -> None:
        if self.checking:
            return  # the button is off meanwhile; this guards a click queued before it went off
        chosen, _ = QFileDialog.getOpenFileName(self, t("locate_solidcraft", self.lang))
        if chosen:
            self.use_solidcraft(Path(chosen))

    def use_solidcraft(self, chosen: Path) -> bool:
        """Save the SolidCraft the user pointed at and check again; refuse what is not one.

        On macOS the dialog hands back `SolidCraft.app`, a folder: the engine is the CLI inside it.
        """
        if self.checking:
            return False
        engine = solidcraft_in(chosen)
        if engine is None:
            self.engines_problem.setText(t("not_solidcraft", self.lang))
            return False
        save_solidcraft(engine, self.settings_path)
        # The window's own environment: `check()` reads the saved choice from it.
        prepare_environment(engine)
        self.check_engines()
        return True

    # --- step 1 -----------------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        # Not while converting: the running job's file and paths stay as they are.
        if event.mimeData().hasUrls() and self.job is None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls or self.job is not None:
            event.ignore()
            return
        if self.pages.currentIndex() == RESULT:
            self._reset()  # a file dropped on the result is "convert another" with that file
        self.load_file(Path(urls[0].toLocalFile()))

    def _choose_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, t("choose_file", self.lang), filter="Fusion (*.f3d *.f3z)"
        )
        if chosen:
            self.load_file(Path(chosen))

    def _choose_output(self) -> None:
        if self.output is None:
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, t("change_output", self.lang), str(self.output), "SolidCraft (*.solidcraft)"
        )
        if chosen:
            self.set_output(Path(chosen))

    def _is_input(self, path: Path) -> bool:
        return self.input is not None and path.resolve() == self.input.resolve()

    def set_output(self, path: Path) -> bool:
        """Where the result goes; never over the design being converted, which would be lost."""
        if self._is_input(path):
            self.save_label.setText(t("same_as_input", self.lang))
            return False
        self.output = path
        self.save_label.setText(t("save_as", self.lang, name=path.name))
        return True

    def _show_output_choice(self, shown: bool) -> None:
        """The "Save as" line and its «Change…» button: only with a file loaded, which gives them a name."""
        self.save_label.setVisible(shown)
        self.change_output.setVisible(shown)

    def _forget_file(self) -> None:
        """No file loaded: nothing the card no longer shows can be converted."""
        self.input = self.output = None
        self.inspecting = None  # a pending inspection's answer is ignored
        self.inspected = False
        self.drop_label.setText(t("drop_here", self.lang))
        self.save_label.setText("")
        self._show_output_choice(False)
        self._update_convert_button()

    def load_file(self, path: Path) -> None:
        if self.job is not None:
            return  # converting: the file is chosen again after it ends
        if path.suffix.lower() not in DESIGN_SUFFIXES or not path.is_file():
            self._forget_file()
            self.holds_label.setText(t("not_a_design", self.lang))
            return
        self.input = path
        self.output = path.with_suffix(".solidcraft")
        self.inspected = False
        self._update_convert_button()
        self.save_label.setText(t("save_as", self.lang, name=self.output.name))
        self._show_output_choice(True)
        self.drop_label.setText(path.name)
        self.holds_label.setText("")
        # Before the check answers there is no engine to pin yet: `_engines_checked` inspects then.
        if self.engines_ready:
            self._inspect()

    def _inspect(self) -> None:
        inspect, tree = self._new_process()
        self.inspecting = inspect
        command = worker_command(["inspect", str(self.input), "--json"], self.executable)

        def finished(code: int, _status: QProcess.ExitStatus) -> None:
            if tree is not None:
                tree.close()  # Windows: nothing the inspection started outlives it
            self._inspected(inspect, code)

        inspect.finished.connect(finished)
        inspect.errorOccurred.connect(lambda error: self._inspect_error(inspect, error))
        self.inspecting_tree = self._start(inspect, tree, command)

    def _inspect_error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        # Only a start failure: anything later also ends in `finished`, which answers it.
        if error != QProcess.ProcessError.FailedToStart:
            return
        process.deleteLater()
        if process is not self.inspecting:
            return
        self._forget_file()
        self.holds_label.setText(t("inspect_failed", self.lang, why=process.errorString()))

    def _inspected(self, process: QProcess, code: int) -> None:
        process.deleteLater()
        if process is not self.inspecting:
            return  # another file was chosen meanwhile; its own inspection answers
        self.inspecting = None
        out = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        counts = None
        for line in out.splitlines():
            if line.startswith("{"):
                try:
                    counts = json.loads(line)
                except ValueError:
                    continue
                break
        if code != 0 or not isinstance(counts, dict):
            err = "\n".join(line for line in out.splitlines() if not line.startswith("{")).strip()
            why = (
                _error_line(err).strip().splitlines()[-1].removeprefix("error: ")
                if err
                else f"exit code {code}"
            )
            self._forget_file()
            self.holds_label.setText(t("inspect_failed", self.lang, why=why))
            return
        self.holds_label.setText(t("holds", self.lang, **counts))
        self.inspected = True
        self._update_convert_button()

    # --- step 2 -----------------------------------------------------------------------------
    def start_conversion(self) -> None:
        if self.input is None or self.output is None or not self.engines_ready or self.job is not None:
            return
        if self._is_input(self.output):
            self.save_label.setText(t("same_as_input", self.lang))
            return
        report = self.output.with_suffix(".report.json")
        # The report next to the result is replaced too: when it is already there, the question names it.
        existing = [path for path in (self.output, report) if path.exists()]
        if existing:
            if len(existing) == 2:
                question = t("replace_question_both", self.lang, name=self.output.name, report=report.name)
            else:
                question = t("replace_question", self.lang, name=existing[0].name)
            answer = QMessageBox.question(self, t("title", self.lang), question)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.report = report
        # An earlier run's report (or its leftovers) must never be read as this run's.
        for stale in (self.report, *partial_paths(self.output, self.report)):
            _remove(stale)
        self.buffer, self.stderr, self.cancelled = "", "", False
        self.converting_label.setText(f"<b>{html.escape(self.input.name)}</b>")
        self.progress_label.setText(t("starting", self.lang))
        self.bar.setRange(0, 0)
        self._show(CONVERT)
        process, tree = self._new_process()
        job = _Job(process, 0, self.output, self.report)
        self.job = job
        process.readyReadStandardOutput.connect(self._read_progress)
        process.finished.connect(lambda code, status: self._finished(job, code, status))
        process.errorOccurred.connect(lambda error: self._failed_to_start(job, error))
        args = [
            "convert",
            str(self.input),
            "-o",
            str(job.output),
            "--report",
            str(job.report),
            "--force",
            "--progress",
        ]
        command = worker_command(args, self.executable)
        job.tree = self._start(process, tree, command)
        job.pid = process.processId()  # the fork happens in start(); 0 if it failed

    def _read_progress(self) -> None:
        if self.process is None:
            return
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self.buffer = self.buffer.split("\n")
        for line in lines:
            event = parse(line)
            if event is None:  # the converter's own text (stderr and stdout share the pipe)
                self.stderr += line.rstrip("\r") + "\n"
            if isinstance(event, Feature):
                self.bar.setRange(0, event.total)
                self.bar.setValue(event.index)
                self.progress_label.setText(
                    t("progress", self.lang, index=event.index, total=event.total, name=event.name)
                )

    def _kill(self, job: _Job) -> None:
        """The worker and the engines under it: `QProcess.kill()` alone reaches only the worker."""
        pid = job.process.processId() or job.pid
        if job.tree is not None:
            job.tree.close()  # whatever state the worker is in: its engines may outlive it
        elif job.process.state() != QProcess.ProcessState.NotRunning:
            kill_tree(pid)
        job.process.kill()

    def cancel(self) -> None:
        if self.job is None:
            return
        self.cancelled = True
        self._kill(self.job)

    def _failed_to_start(self, job: _Job, error: QProcess.ProcessError) -> None:
        # A crash also emits `finished` (with CrashExit), which handles it; a failed start does not.
        if error != QProcess.ProcessError.FailedToStart or job is not self.job:
            return
        self.job = None
        job.process.deleteLater()
        self._show_result(
            outcome_from(1, None, f"the converter could not start: {job.process.errorString()}")
        )

    def _finished(self, job: _Job, code: int, status: QProcess.ExitStatus) -> None:
        if job is not self.job:
            return
        self._read_progress()
        if self.buffer.strip() and parse(self.buffer) is None:
            self.stderr += self.buffer  # a last line without its newline
        self.buffer = ""
        crashed = status == QProcess.ExitStatus.CrashExit
        if self.cancelled or crashed:
            kill_group(job.pid)  # whatever the worker left behind keeps its group id
        if job.tree is not None:
            # Windows: whatever the worker left behind is still in its job, however it ended (a worker
            # ended from outside exits with the code it was given, not as a crash).
            job.tree.close()
        self.job = None
        job.process.deleteLater()
        self.output, self.report = job.output, job.report
        if self.cancelled:
            for partial in job.partials:
                _remove(partial)
            self._show(FILE)
            return
        if crashed:
            # Spec ③: an engine (or worker) that dies halfway is an error; any report there is not this run's.
            self._show_result(
                outcome_from(1, None, "the converter stopped unexpectedly (it crashed or was killed)")
            )
            return
        report = None
        if job.report.is_file():
            try:
                report = json.loads(job.report.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = None
        self._show_result(outcome_from(code, report, _error_line(self.stderr)))

    def closeEvent(self, event) -> None:
        """Closing mid-conversion is a cancel: no engine left running, no half-written file left."""
        job = self.job
        if job is not None:
            self.cancelled = True
            self._kill(job)
            job.process.waitForFinished(5_000)  # delivers `finished`, which removes the partials
            kill_group(job.pid)  # Windows: `_kill` has closed the job, which ended the whole tree
            for partial in job.partials:
                _remove(partial)
        inspecting = self.inspecting
        if inspecting is not None and inspecting.state() != QProcess.ProcessState.NotRunning:
            self.inspecting = None
            self._end_tree(self.inspecting_tree, inspecting.processId())
            inspecting.kill()
            inspecting.waitForFinished(2_000)
        super().closeEvent(event)

    # --- step 3 -----------------------------------------------------------------------------
    def _show_result(self, outcome: Outcome) -> None:
        self.outcome = outcome
        if outcome.kind == "verified":
            self.result_label.setText(f"<span style='color:#1a7f37'><b>{t('verified', self.lang)}</b></span>")
        elif outcome.kind == "differs":
            self.result_label.setText(
                f"<span style='color:#b54708'><b>{t('differs', self.lang)}</b></span><br>{_html(outcome.message)}"
            )
        else:
            self.result_label.setText(
                f"<span style='color:#b42318'><b>{t('failed', self.lang)}</b></span><br>{_html(outcome.message)}"
            )
        if outcome.kind == "error":
            self.counts_label.setText("")
        else:
            self.counts_label.setText(
                t("editable", self.lang, editable=outcome.editable, total=outcome.total)
                + "<br>"
                + t("imported", self.lang, imported=outcome.imported)
            )
        self.details.setPlainText(
            "\n".join(
                f"{f['ordinal']:>3}  {f['status']:<8} {f['name']}  {f.get('detail', '')}"
                for f in outcome.features
            )
        )
        self.open_button.setVisible(outcome.kind != "error")
        self.copy_error.setVisible(outcome.kind == "error")
        # An error before the converter wrote anything leaves no report to save.
        self.save_report_button.setVisible(self.report is not None and self.report.is_file())
        self._show(RESULT)

    def _open_result(self) -> None:
        if self.output is not None:
            open_design(self.output)

    def _copy_error(self) -> None:
        QGuiApplication.clipboard().setText(self.outcome.message if self.outcome else "")

    def _save_report(self) -> None:
        if self.report is None or not self.report.is_file():
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, t("save_report", self.lang), str(self.report), "JSON (*.json)"
        )
        if chosen:
            Path(chosen).write_text(self.report.read_text(encoding="utf-8"), encoding="utf-8")

    def _reset(self) -> None:
        self.input = self.output = self.report = None
        self.outcome = None
        self.inspecting = None
        self.inspected = False
        self.drop_label.setText(t("drop_here", self.lang))
        self.holds_label.setText("")
        self.save_label.setText("")
        self._show_output_choice(False)
        self.convert_button.setEnabled(False)
        self._show(FILE)


def _remove(path: Path) -> None:
    """Remove a file if it is there; a path below a file (not a folder) holds nothing either."""
    try:
        path.unlink()
    except (FileNotFoundError, NotADirectoryError):
        pass


def _plain(label: QLabel) -> QLabel:
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _html(text: str) -> str:
    """Raw text (an engine's message, a path) inside a label that is HTML."""
    return html.escape(text).replace("\n", "<br>")


def _error_line(stderr: str) -> str:
    """The converter's own `error: ...` line when it wrote one, else all of stderr.

    `outcome_from` keeps the last stderr line; anything written after the converter's message (a
    warning at interpreter exit, say) would otherwise take its place.
    """
    lines = [line for line in stderr.splitlines() if line.startswith("error: ")]
    return lines[-1] if lines else stderr


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # The packaged app carries its icon in the bundle; this one is for the window itself, on every system.
    app.setWindowIcon(QIcon(str(Path(__file__).parent / "resources" / "icon-256.png")))
    wizard = Wizard()
    wizard.resize(560, 420)
    wizard.show()
    return app.exec()
