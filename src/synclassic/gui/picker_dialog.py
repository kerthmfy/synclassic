"""Choose the right recording for a song by hand, listening first."""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from synclassic.config import load_settings
from synclassic.gui.workers import mark_closed, run_in_background
from synclassic.library import Library
from synclassic.matcher import Matcher, MatchResult
from synclassic.picker import candidate_from_link, stream_url, video_id_from_text
from synclassic.update import save_candidate

STATUS_TEXT = {
    "downloaded": "Downloaded",
    "review": "Downloaded, but the match is uncertain",
    "unmatched": "No confident match was found",
    "failed": "Failed",
    "pending": "Not downloaded yet",
    "local": "Using a file from your Mac",
    "ipod": "Kept only on the iPod",
}


def fmt_time(seconds: float | None) -> str:
    if not seconds:
        return "–:––"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PickerDialog(QDialog):
    def __init__(self, library_path: pathlib.Path, track_id: int, parent=None):
        super().__init__(parent)
        self.library_path = library_path
        self.track_id = track_id
        self.matcher = Matcher()
        self.choices: list[MatchResult] = []
        self.player = None
        self.audio = None
        self.playing_id: str | None = None

        library = Library(library_path)
        self.item = library.track(track_id)
        library.close()
        if self.item is None:
            raise ValueError(f"No song #{track_id}")
        track = self.item.track
        self.wanted = (track.duration_ms or 0) / 1000 or None

        self.setWindowTitle("Choose version")
        self.resize(860, 520)
        layout = QVBoxLayout(self)

        title = QLabel(track.display())
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        title.setWordWrap(True)
        details = [d for d in (track.album, fmt_time(self.wanted), STATUS_TEXT.get(self.item.status, self.item.status)) if d]
        if self.item.status == "failed" and self.item.error:
            details.append(self.item.error)
        subtitle = QLabel("  ·  ".join(details))
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit(f"{track.primary_artist} {track.title}".strip())
        self.search_box.setPlaceholderText("Search YouTube Music")
        self.search_box.returnPressed.connect(self.search)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.search)
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setHeaderLabels(["Title", "Artist", "Album", "Length", "Type", "Match"])
        header = self.table.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda *_: self.play())
        layout.addWidget(self.table, 1)

        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.notes)

        link_row = QHBoxLayout()
        self.link_box = QLineEdit()
        self.link_box.setPlaceholderText("Or paste a YouTube / YouTube Music link you found yourself")
        self.link_button = QPushButton("Use link")
        self.link_button.clicked.connect(self.use_link)
        link_row.addWidget(self.link_box, 1)
        link_row.addWidget(self.link_button)
        layout.addLayout(link_row)

        buttons = QHBoxLayout()
        self.play_button = QPushButton("▶  Play preview")
        self.play_button.clicked.connect(self.play)
        self.status = QLabel("")
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.status, 1)
        if self.item.has_file:
            keep = QPushButton("Keep current file")
            keep.clicked.connect(self.keep_current)
            buttons.addWidget(keep)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.use_button = QPushButton("Download this version")
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self.use_selected)
        buttons.addWidget(cancel)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)

        self._selection_changed()
        self.search(initial=True)

    # --- searching --------------------------------------------------------------------------

    def _busy(self, text: str) -> None:
        self.status.setText(text)
        for widget in (self.search_button, self.use_button, self.link_button):
            widget.setEnabled(not text)
        if not text:
            self._selection_changed()

    def search(self, initial: bool = False) -> None:
        query = None if initial else self.search_box.text().strip() or None
        self._busy("Searching YouTube Music…")
        track = self.item.track
        run_in_background(
            self,
            lambda task: self.matcher.rank(track, query=query),
            self._show_choices,
            lambda error: (self._busy(""), self.status.setText(f"Search failed: {error}")),
        )

    def _show_choices(self, choices: list[MatchResult]) -> None:
        self._busy("")
        self.choices = choices
        self.table.clear()
        for result in choices:
            cand = result.candidate
            length = fmt_time(cand.duration_s)
            if self.wanted and cand.duration_s:
                diff = int(round(cand.duration_s - self.wanted))
                if diff:
                    length += f"  ({diff:+d}s)"
            row = QTreeWidgetItem(
                [cand.title + ("  🅴" if cand.explicit else ""), ", ".join(cand.artists), cand.album, length,
                 "Video" if cand.kind == "video" else "Song", f"{round(result.score * 100)}%"]
            )
            if cand.video_id == self.item.video_id:
                font = row.font(0)
                font.setBold(True)
                for column in range(6):
                    row.setFont(column, font)
                row.setToolTip(0, "Current pick")
            self.table.addTopLevelItem(row)
        if choices:
            self.table.setCurrentItem(self.table.topLevelItem(0))
        else:
            self.status.setText("No results. Try other words, or paste a link.")

    def _selected(self) -> MatchResult | None:
        index = self.table.indexOfTopLevelItem(self.table.currentItem()) if self.table.currentItem() else -1
        return self.choices[index] if 0 <= index < len(self.choices) else None

    def _selection_changed(self) -> None:
        result = self._selected()
        self.use_button.setEnabled(result is not None)
        self.play_button.setEnabled(result is not None)
        self.notes.setText("; ".join(result.notes) if result and result.notes else "")

    # --- previews ---------------------------------------------------------------------------

    def play(self) -> None:
        result = self._selected()
        if not result:
            return
        video_id = result.candidate.video_id
        if self.playing_id == video_id:
            self.stop()
            return
        self.stop()
        self.play_button.setText("Loading…")
        settings = load_settings()
        run_in_background(
            self,
            lambda task: stream_url(video_id, settings),
            lambda url: self._start_playing(video_id, url),
            lambda error: self._preview_failed(video_id),
        )

    def _start_playing(self, video_id: str, url: str | None) -> None:
        if not url:
            self._preview_failed(video_id)
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            self._preview_failed(video_id)
            return
        if self.player is None:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.errorOccurred.connect(lambda *_: self._preview_failed(self.playing_id))
        self.playing_id = video_id
        self.player.setSource(QUrl(url))
        self.player.play()
        self.play_button.setText("■  Stop")

    def _preview_failed(self, video_id: str | None) -> None:
        self.stop()
        if video_id:
            QDesktopServices.openUrl(QUrl(f"https://music.youtube.com/watch?v={video_id}"))
            self.status.setText("Couldn't play it here, so it opened in your browser.")

    def stop(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.playing_id = None
        self.play_button.setText("▶  Play preview")

    # --- choosing ---------------------------------------------------------------------------

    def use_selected(self) -> None:
        result = self._selected()
        if result:
            self._download(result)

    def use_link(self) -> None:
        video_id = video_id_from_text(self.link_box.text())
        if not video_id:
            QMessageBox.warning(self, "Not a YouTube link", "Paste a link like https://music.youtube.com/watch?v=…")
            return
        self._busy("Looking up the link…")
        run_in_background(
            self,
            lambda task: candidate_from_link(self.matcher, self.item, video_id),
            self._download,
            lambda error: (self._busy(""), self.status.setText(f"Couldn't use the link: {error}")),
        )

    def _download(self, result: MatchResult) -> None:
        self.stop()
        self._busy("Downloading your choice…")
        path, track_id = self.library_path, self.track_id

        def work(task):
            library = Library(path)
            try:
                item = library.track(track_id)
                messages: list[str] = []
                status = save_candidate(library, item, result, load_settings(), messages.append, manual=True)
                return status, messages
            finally:
                library.close()

        run_in_background(self, work, self._downloaded, lambda error: (self._busy(""), self.status.setText(error)))

    def _downloaded(self, outcome) -> None:
        status, messages = outcome
        if status == "downloaded":
            self.accept()
            return
        self._busy("")
        self.status.setText(" ".join(m.strip() for m in messages) or "Download failed")

    def keep_current(self) -> None:
        library = Library(self.library_path)
        library.update_track(self.track_id, status="downloaded", manual=True, match_notes="kept by you")
        library.close()
        self.accept()

    def done(self, result: int) -> None:  # closing the window in any way stops playback
        self.stop()
        mark_closed(self)
        super().done(result)


def open_picker(parent, library_path: pathlib.Path, track_id: int) -> bool:
    dialog = PickerDialog(library_path, track_id, parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    return dialog.exec() == QDialog.DialogCode.Accepted
