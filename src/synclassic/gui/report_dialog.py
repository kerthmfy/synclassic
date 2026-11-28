"""Help → Report a Problem: describe it, see exactly what the report says, then copy it, save it,
or open a new GitHub issue. Nothing is sent by itself."""

from __future__ import annotations

import pathlib

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from synclassic.gui.workers import mark_closed, run_in_background


class ReportDialog(QDialog):
    def __init__(self, activity: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report a Problem")
        self.setMinimumSize(640, 560)
        self.activity = activity
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>What happened?</b> What did you do, and what did you expect?"))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("e.g. I pressed Sync and it stopped at song 12 with an error…")
        self.description.setFixedHeight(90)
        layout.addWidget(self.description)
        note = QLabel("The report below has the versions, settings, iPod details and the end of the activity log, "
                      "with passwords, keys, tokens, e-mail addresses and your user name taken out. Read it "
                      "first: nothing is sent unless you send it.")
        note.setWordWrap(True)
        note.setProperty("role", "dim")
        layout.addWidget(note)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("Gathering the details…")
        self.report.setObjectName("log")
        layout.addWidget(self.report, 1)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Update Report")
        self.refresh_button.clicked.connect(self.gather)
        copy = QPushButton("Copy Report")
        copy.clicked.connect(self.copy)
        save = QPushButton("Save Report…")
        save.clicked.connect(self.save)
        issue = QPushButton("Open GitHub Issue…")
        issue.setToolTip("Opens a new issue on GitHub in your browser; paste or attach the report there")
        issue.clicked.connect(self.open_issue)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (self.refresh_button, copy, save, issue):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.finished.connect(lambda *_: mark_closed(self))
        self.gather()

    def gather(self) -> None:
        from synclassic import diagnostics

        description, activity = self.description.toPlainText(), self.activity
        self.refresh_button.setEnabled(False)

        def show(text: str) -> None:
            self.report.setPlainText(text)
            self.refresh_button.setEnabled(True)

        def failed(error: str) -> None:
            self.report.setPlainText(f"Couldn't gather the report: {error}")
            self.refresh_button.setEnabled(True)

        run_in_background(self, lambda task: diagnostics.collect(description, activity), show, failed)

    def _text(self) -> str:
        from synclassic import diagnostics

        text = self.report.toPlainText()
        described = self.description.toPlainText().strip()
        if described and described not in text:  # typed after the report was made
            text = text.replace("(not described)", diagnostics.redact(described), 1)
        return text

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self._text())

    def save(self) -> None:
        import time

        default = pathlib.Path.home() / "Desktop" / f"Synclassic report {time.strftime('%Y-%m-%d %H.%M')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save the report", str(default), "Text (*.txt)")
        if path:
            pathlib.Path(path).write_text(self._text(), encoding="utf-8")

    def open_issue(self) -> None:
        from synclassic import diagnostics

        self.copy()  # ready to paste into the issue
        QDesktopServices.openUrl(QUrl(diagnostics.issue_link(self.description.toPlainText() or "Problem")))
