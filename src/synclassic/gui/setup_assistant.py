"""The first time Synclassic opens: a few pages to get going (like iTunes' setup assistant).

1. Welcome.
2. The tools for downloading (ffmpeg and deno): already inside the standalone app; otherwise it
   shows the one Homebrew command, with a button to copy it and one to check again.
3. Where your music goes, and the file format.
4. The look, and checking for new versions.
Everything can be changed later in Settings. People who already use Synclassic don't see it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from synclassic.config import load_settings, save_settings

BREW = "brew install ffmpeg deno"


def should_show(library) -> bool:
    """Only the very first time (and not for people who already have playlists)."""
    settings = load_settings()
    if settings.setup_done:
        return False
    if library.playlists():
        settings.setup_done = True
        save_settings(settings)
        return False
    return True


def _page(title: str, text: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    heading = QLabel(title)
    font = heading.font()
    font.setPixelSize(20)
    font.setBold(True)
    heading.setFont(font)
    layout.addWidget(heading)
    body = QLabel(text)
    body.setWordWrap(True)
    body.setTextFormat(Qt.TextFormat.RichText)
    body.setOpenExternalLinks(True)
    layout.addWidget(body)
    return page, layout


class SetupAssistant(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Synclassic")
        self.setMinimumSize(560, 420)
        self.settings = load_settings()
        outer = QVBoxLayout(self)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)

        page, layout = _page("Welcome to Synclassic",
                             "Synclassic puts your playlists on your iPod: from Spotify links, your Spotify "
                             "account, playlist files, or folders of your own music. It finds each song, downloads "
                             "it with its album art and lyrics, and syncs everything to the iPod, which it can "
                             "also browse, fix up and back up.<br><br>A few quick questions, then you're set.")
        layout.addStretch(1)
        self.pages.addWidget(page)

        page, layout = _page("Tools for downloading",
                             "Synclassic uses two free tools: <b>ffmpeg</b> (for audio) and <b>deno</b> (which "
                             "YouTube needs).")
        self.tools_status = QLabel()
        self.tools_status.setTextFormat(Qt.TextFormat.RichText)
        self.tools_status.setWordWrap(True)
        layout.addWidget(self.tools_status)
        self.brew_row = QWidget()
        row = QHBoxLayout(self.brew_row)
        row.setContentsMargins(0, 0, 0, 0)
        command = QLineEdit(BREW)
        command.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(BREW))
        again = QPushButton("Check Again")
        again.clicked.connect(self._check_tools)
        row.addWidget(command, 1)
        row.addWidget(copy)
        row.addWidget(again)
        layout.addWidget(self.brew_row)
        layout.addStretch(1)
        self.pages.addWidget(page)

        page, layout = _page("Your music", "Where should Synclassic keep the songs it downloads?")
        form = QFormLayout()
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(str(self.settings.music_path))
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(choose)
        form.addRow("Folder", folder_row)
        self.format = QComboBox()
        self.format.addItem("AAC (.m4a): best for the iPod", "m4a")
        self.format.addItem("MP3", "mp3")
        self.format.setCurrentIndex(max(0, self.format.findData(self.settings.format)))
        form.addRow("Format", self.format)
        layout.addLayout(form)
        layout.addStretch(1)
        self.pages.addWidget(page)

        from synclassic.gui import theme

        page, layout = _page("Almost done", "How should Synclassic look?")
        self.appearance = QComboBox()
        for value, text in theme.APPEARANCES.items():
            self.appearance.addItem(text, value)
        self.appearance.setCurrentIndex(max(0, self.appearance.findData(self.settings.appearance)))
        self.appearance.currentIndexChanged.connect(self._preview_look)
        layout.addWidget(self.appearance)
        self.updates = QCheckBox("Tell me when there's a new version of Synclassic (checks GitHub once a day)")
        self.updates.setChecked(self.settings.check_updates)
        layout.addWidget(self.updates)
        tips = QLabel("Next: press <b>+</b> to add a playlist, and plug in your iPod. Last.fm, your Spotify login "
                      "and more are in Settings.")
        tips.setWordWrap(True)
        tips.setTextFormat(Qt.TextFormat.RichText)
        layout.addSpacing(12)
        layout.addWidget(tips)
        layout.addStretch(1)
        self.pages.addWidget(page)

        buttons = QHBoxLayout()
        self.back = QPushButton("Go Back")
        self.back.clicked.connect(lambda: self._go(-1))
        self.next = QPushButton("Continue")
        self.next.setDefault(True)
        self.next.clicked.connect(lambda: self._go(1))
        buttons.addWidget(self.back)
        buttons.addStretch(1)
        buttons.addWidget(self.next)
        outer.addLayout(buttons)
        self._check_tools()
        self._go(0)

    def _check_tools(self) -> None:
        import shutil

        from synclassic import tools

        tools.add_to_path()
        found = {name: tools.where(name) for name in ("ffmpeg", "deno")}
        ok = all(shutil.which(name) for name in found)
        lines = [f"{'✓' if where != 'missing' else '✗'} {name}: {where}" for name, where in found.items()]
        if ok:
            lines.append("<br>All set.")
        else:
            lines.append("<br>Install them with <a href='https://brew.sh'>Homebrew</a>: open Terminal, paste this "
                         "command, press Return, then come back and press Check Again. (You can also do this "
                         "later; downloads wait until they're there.)")
        self.tools_status.setText("<br>".join(lines))
        self.brew_row.setVisible(not ok)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Where Synclassic keeps your music", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _preview_look(self) -> None:
        from PySide6.QtWidgets import QApplication

        from synclassic.gui import theme

        theme.apply(QApplication.instance(), self.appearance.currentData())

    def _go(self, step: int) -> None:
        index = self.pages.currentIndex() + step
        if index >= self.pages.count():
            self._finish()
            return
        self.pages.setCurrentIndex(max(0, index))
        self.back.setVisible(self.pages.currentIndex() > 0)
        self.next.setText("Start Using Synclassic" if self.pages.currentIndex() == self.pages.count() - 1 else "Continue")

    def _finish(self) -> None:
        settings = load_settings()
        settings.music_dir = self.folder.text().strip()
        settings.format = self.format.currentData()
        settings.appearance = self.appearance.currentData()
        settings.check_updates = self.updates.isChecked()
        settings.setup_done = True
        save_settings(settings)
        self.accept()
