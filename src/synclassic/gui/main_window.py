"""The main Synclassic window."""

from __future__ import annotations

import pathlib
import subprocess
import sys

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synclassic import __version__, playlist_writer
from synclassic.config import app_dir, load_settings
from synclassic.gui.add_dialog import AddDialog
from synclassic.gui.picker_dialog import fmt_time, open_picker
from synclassic.gui.settings_dialog import SettingsDialog
from synclassic.gui.workers import Task, run_in_background
from synclassic.library import Library, LibraryPlaylist, LibraryTrack
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyClient
from synclassic.update import add_inputs, download_missing, refresh_playlist

ATTENTION = "__attention__"
ATTENTION_STATUSES = ("review", "unmatched", "failed")

STATUS = {
    # status: (label, color)
    "downloaded": ("Ready", "#2e9d4a"),
    "local": ("Mac file", "#2f7bd8"),
    "review": ("Check", "#e08a00"),
    "pending": ("Waiting", "#8a8a8a"),
    "unmatched": ("Not found", "#d93b3b"),
    "failed": ("Failed", "#d93b3b"),
    "missing": ("File missing", "#d93b3b"),
}


def status_of(item: LibraryTrack) -> str:
    if item.status in ("downloaded", "review", "local") and not item.has_file:
        return "missing"
    return item.status


def reveal_in_finder(path: pathlib.Path) -> None:
    if sys.platform == "darwin" and path.exists():
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Synclassic")
        self.resize(1180, 740)
        self.library_path = app_dir() / "library.db"
        self.library = Library(self.library_path)
        self.job = None  # the running background update, if any
        self.current_key: object = None  # selected sidebar entry: playlist id or ATTENTION

        self._build_toolbar()
        self._build_body()
        self._build_status_bar()

        # Refresh views at most a few times per second while downloads report progress.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self.refresh)

        self.refresh()

    # --- layout -----------------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = QToolBar("Main")
        bar.setMovable(False)
        bar.setIconSize(QSize(18, 18))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        self.add_action = QAction("＋  Add playlists", self)
        self.add_action.setShortcut(QKeySequence.StandardKey.New)
        self.add_action.triggered.connect(self.add_playlists)
        self.update_action = QAction("⟳  Update all", self)
        self.update_action.setShortcut(QKeySequence("Ctrl+R"))
        self.update_action.setToolTip("Check every playlist for new songs and download them")
        self.update_action.triggered.connect(lambda: self.start_update(None))
        self.stop_action = QAction("■  Stop", self)
        self.stop_action.setEnabled(False)
        self.stop_action.triggered.connect(self.stop_job)
        self.folder_action = QAction("Show music folder", self)
        self.folder_action.triggered.connect(lambda: reveal_in_finder(load_settings().music_path))
        self.settings_action = QAction("Settings", self)
        self.settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
        self.settings_action.triggered.connect(self.open_settings)
        for action in (self.add_action, self.update_action, self.stop_action):
            bar.addAction(action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        bar.addAction(self.folder_action)
        bar.addAction(self.settings_action)

    def _build_body(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.sidebar = QListWidget()
        self.sidebar.setMinimumWidth(220)
        self.sidebar.setSpacing(2)
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        splitter.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 8, 12, 0)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet("color: palette(placeholder-text);")
        self.subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #e08a00;")
        header_row = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.addWidget(self.title)
        header_text.addWidget(self.subtitle)
        header_row.addLayout(header_text, 1)
        self.update_one = QPushButton("⟳  Update")
        self.update_one.clicked.connect(self._update_current)
        header_row.addWidget(self.update_one, 0, Qt.AlignmentFlag.AlignTop)
        right_layout.addLayout(header_row)
        right_layout.addWidget(self.note)

        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setHeaderLabels(["#", "Title", "Artist", "Album", "Time", "Status"])
        header = self.table.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda *_: self.pick_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        welcome = QLabel("Add your first playlist")
        welcome.setStyleSheet("font-size: 20px; font-weight: 600;")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explain = QLabel("Paste a Spotify link, pick from your Spotify account, or import an .m3u8 / .csv file.")
        explain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explain.setStyleSheet("color: palette(placeholder-text);")
        add_button = QPushButton("＋  Add playlists")
        add_button.clicked.connect(self.add_playlists)
        empty_layout.addWidget(welcome)
        empty_layout.addWidget(explain)
        empty_layout.addWidget(add_button, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(2)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(empty)
        right_layout.addWidget(self.stack, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 920])

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("Activity will show here.")

        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.addWidget(splitter)
        vertical.addWidget(self.log)
        vertical.setStretchFactor(0, 1)
        vertical.setSizes([600, 120])
        self.setCentralWidget(vertical)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.status_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(True)
        self.progress.hide()
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.progress)

    # --- refreshing views ---------------------------------------------------------------------

    def schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def refresh(self) -> None:
        playlists = self.library.playlists()
        attention = [i for i in self.library.tracks_in_playlists() if i.status in ATTENTION_STATUSES]

        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        if attention:
            item = QListWidgetItem(f"⚠  Needs attention  ({len(attention)})")
            item.setData(Qt.ItemDataRole.UserRole, ATTENTION)
            item.setForeground(QColor("#e08a00"))
            item.setToolTip("Songs that weren't found or might be the wrong version")
            self.sidebar.addItem(item)
        for playlist in playlists:
            counts = self.library.counts(playlist)
            total = sum(counts.values())
            ready = counts.get("downloaded", 0) + counts.get("review", 0) + counts.get("local", 0)
            item = QListWidgetItem(f"{playlist.name}\n{ready} of {total} ready")
            item.setData(Qt.ItemDataRole.UserRole, playlist.id)
            item.setToolTip(playlist.spec.describe())
            self.sidebar.addItem(item)
        # Keep the selection.
        target = 0 if self.sidebar.count() else -1
        for row in range(self.sidebar.count()):
            if self.sidebar.item(row).data(Qt.ItemDataRole.UserRole) == self.current_key:
                target = row
                break
        if target >= 0:
            self.sidebar.setCurrentRow(target)
            self.current_key = self.sidebar.item(target).data(Qt.ItemDataRole.UserRole)
        else:
            self.current_key = None
        self.sidebar.blockSignals(False)
        self._show_current(attention)

    def _sidebar_changed(self, current, previous) -> None:
        self.current_key = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._show_current()

    def _current_playlist(self) -> LibraryPlaylist | None:
        if self.current_key in (None, ATTENTION):
            return None
        return next((p for p in self.library.playlists() if p.id == self.current_key), None)

    def _show_current(self, attention: list[LibraryTrack] | None = None) -> None:
        if self.current_key is None:
            self.stack.setCurrentIndex(1)
            self.title.setText("Synclassic")
            self.subtitle.setText(f"Version {__version__}")
            self.note.hide()
            self.update_one.hide()
            return
        self.stack.setCurrentIndex(0)
        if self.current_key == ATTENTION:
            items = attention if attention is not None else [
                i for i in self.library.tracks_in_playlists() if i.status in ATTENTION_STATUSES
            ]
            self.title.setText("Needs attention")
            self.subtitle.setText("Double-click a song to listen to the choices and pick the right version.")
            self.note.hide()
            self.update_one.hide()
        else:
            playlist = self._current_playlist()
            if playlist is None:
                return
            items = self.library.playlist_tracks(playlist)
            self.title.setText(playlist.name)
            minutes = sum((i.track.duration_ms or 0) for i in items) // 60000
            length = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
            self.subtitle.setText(f"{len(items)} songs · {length} · {playlist.spec.describe()}")
            self.note.setText(playlist.note or "")
            self.note.setVisible(bool(playlist.note))
            self.update_one.show()
            self.update_one.setEnabled(self.job is None)
        self._fill_table(items)

    def _fill_table(self, items: list[LibraryTrack]) -> None:
        selected = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.table.selectedItems()}
        scroll = self.table.verticalScrollBar().value()
        self.table.clear()
        rows = []
        for number, item in enumerate(items, start=1):
            track = item.track
            state = status_of(item)
            label, color = STATUS.get(state, (state, "#8a8a8a"))
            if item.manual and state == "downloaded":
                label = "Ready (your pick)"
            row = QTreeWidgetItem([str(number), track.title, track.artist, track.album, fmt_time((track.duration_ms or 0) / 1000), label])
            row.setData(0, Qt.ItemDataRole.UserRole, item.id)
            row.setForeground(5, QColor(color))
            row.setTextAlignment(0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            tip = item.error or item.match_notes or ""
            if tip:
                row.setToolTip(5, tip)
            rows.append(row)
        self.table.addTopLevelItems(rows)
        for row in rows:
            if row.data(0, Qt.ItemDataRole.UserRole) in selected:
                row.setSelected(True)
        self.table.verticalScrollBar().setValue(scroll)

    # --- menus --------------------------------------------------------------------------------

    def _selected_track_ids(self) -> list[int]:
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in self.table.selectedItems()]

    def _table_menu(self, position) -> None:
        ids = self._selected_track_ids()
        if not ids:
            return
        menu = QMenu(self)
        pick = menu.addAction("Choose version…")
        pick.setEnabled(len(ids) == 1)
        retry = menu.addAction("Search again automatically")
        menu.addSeparator()
        reveal = menu.addAction("Show in Finder")
        item = self.library.track(ids[0])
        reveal.setEnabled(bool(item and item.has_file))
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen == pick:
            self.pick_selected()
        elif chosen == retry:
            for track_id in ids:
                self.library.update_track(track_id, status="pending", manual=False, error=None)
            self.refresh()
            self.start_update(self._playlist_ids_for_view(), refresh_sources=False)
        elif chosen == reveal and item and item.file_path:
            reveal_in_finder(pathlib.Path(item.file_path))

    def _sidebar_menu(self, position) -> None:
        row = self.sidebar.itemAt(position)
        if not row or row.data(Qt.ItemDataRole.UserRole) == ATTENTION:
            return
        playlist = next((p for p in self.library.playlists() if p.id == row.data(Qt.ItemDataRole.UserRole)), None)
        if not playlist:
            return
        menu = QMenu(self)
        update = menu.addAction("Update")
        update.setEnabled(self.job is None)
        retry = menu.addAction("Retry songs that failed or weren't found")
        retry.setEnabled(self.job is None)
        menu.addSeparator()
        rename = menu.addAction("Rename…")
        reveal = menu.addAction("Show playlist file in Finder")
        menu.addSeparator()
        remove = menu.addAction("Remove playlist…")
        chosen = menu.exec(self.sidebar.viewport().mapToGlobal(position))
        if chosen == update:
            self.start_update([playlist.id])
        elif chosen == retry:
            self.start_update([playlist.id], retry_failed=True, refresh_sources=False)
        elif chosen == rename:
            name, ok = QInputDialog.getText(self, "Rename playlist", "New name:", text=playlist.name)
            if ok and name.strip():
                self.library.rename_playlist(playlist, name.strip())
                playlist_writer.write_all(self.library, load_settings().music_path)
                self.refresh()
        elif chosen == reveal:
            folder = playlist_writer.playlists_dir(load_settings().music_path)
            files = list(folder.glob("*.m3u8"))
            match = next((f for f in files if f.stem.lower() == playlist.name.lower()), None)
            reveal_in_finder(match or folder)
        elif chosen == remove:
            answer = QMessageBox.question(
                self, "Remove playlist",
                f"Stop syncing “{playlist.name}”? Its downloaded songs stay on your Mac.",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.library.remove_playlist(playlist)
                playlist_writer.write_all(self.library, load_settings().music_path)
                self.current_key = None
                self.refresh()

    def _playlist_ids_for_view(self) -> list[int] | None:
        return None if self.current_key in (None, ATTENTION) else [self.current_key]

    # --- actions ------------------------------------------------------------------------------

    def pick_selected(self) -> None:
        ids = self._selected_track_ids()
        if len(ids) != 1:
            return
        if open_picker(self, self.library_path, ids[0]):
            playlist_writer.write_all(self.library, load_settings().music_path)
            self.say("Saved your pick.")
        self.refresh()

    def open_settings(self) -> None:
        SettingsDialog(self).exec()

    def add_playlists(self) -> None:
        if self.job is not None:
            QMessageBox.information(self, "Busy", "Wait for the current update to finish, or press Stop.")
            return
        dialog = AddDialog(self, open_settings=self.open_settings)
        if dialog.exec() != AddDialog.DialogCode.Accepted:
            return
        inputs, name, download = dialog.inputs, dialog.name, dialog.download_now.isChecked()
        path = self.library_path

        def work(task: Task):
            library = Library(path)
            try:
                client = _spotify_client(task)
                added = add_inputs(library, inputs, name, client, task.say)
                task.changed.emit()
                if added and download:
                    _download(library, task, added, retry_failed=False)
                playlist_writer.write_all(library, load_settings().music_path)
                return [p.id for p in added]
            finally:
                library.close()

        def done(ids):
            if ids:
                self.current_key = ids[0]

        self._run_job("Adding…", work, done)

    def _update_current(self) -> None:
        if self.current_key not in (None, ATTENTION):
            self.start_update([self.current_key])

    def start_update(self, playlist_ids: list[int] | None, retry_failed: bool = False, refresh_sources: bool = True) -> None:
        if self.job is not None:
            return
        path = self.library_path

        def work(task: Task):
            library = Library(path)
            try:
                playlists = [p for p in library.playlists() if playlist_ids is None or p.id in playlist_ids]
                if refresh_sources:
                    client = _spotify_client(task)
                    for number, playlist in enumerate(playlists, start=1):
                        if task.should_stop():
                            return None
                        task.progress.emit(0, 0, f"Checking {playlist.name} ({number}/{len(playlists)})…")
                        report = refresh_playlist(library, playlist, client)
                        if report.error:
                            task.say(f"{playlist.name}: couldn't read it: {report.error}")
                        else:
                            change = []
                            if report.added:
                                change.append(f"{len(report.added)} new")
                            if report.removed:
                                change.append(f"{len(report.removed)} removed")
                            task.say(f"{playlist.name}: {report.total} songs" + (f" ({', '.join(change)})" if change else ", no changes"))
                            for title in report.removed[:20]:
                                task.say(f"    − {title}")
                        task.changed.emit()
                result = _download(library, task, playlists, retry_failed)
                playlist_writer.write_all(library, load_settings().music_path)
                return result
            finally:
                library.close()

        self._run_job("Updating…", work)

    def _run_job(self, text: str, work, on_done=None) -> None:
        self._set_busy(text)
        self.job = run_in_background(
            self, work,
            lambda result: self._job_finished(result, on_done),
            self._job_failed,
        )
        self.job.task.message.connect(self.say)
        self.job.task.progress.connect(self._progress)
        self.job.task.changed.connect(self.schedule_refresh)

    def stop_job(self) -> None:
        if self.job is not None:
            self.job.task.stop()
            self.status_label.setText("Stopping after the current song…")
            self.stop_action.setEnabled(False)

    def _set_busy(self, text: str) -> None:
        self.status_label.setText(text)
        self.progress.setRange(0, 0)
        self.progress.show()
        for action in (self.update_action, self.add_action):
            action.setEnabled(False)
        self.update_one.setEnabled(False)
        self.stop_action.setEnabled(True)

    def _set_idle(self, text: str) -> None:
        self.job = None
        self.progress.hide()
        self.status_label.setText(text)
        for action in (self.update_action, self.add_action):
            action.setEnabled(True)
        self.update_one.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.refresh()

    def _progress(self, number: int, total: int, text: str) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            self.status_label.setText(text)
            return
        self.progress.setRange(0, total)
        self.progress.setValue(min(number - 1, total))
        self.progress.setFormat(f"{min(number, total)} / {total}")
        self.status_label.setText(f"Downloading: {text}" if number <= total else "Finishing…")

    def _job_finished(self, result, on_done) -> None:
        summary = "Done."
        if isinstance(result, dict):
            summary = result.get("summary", summary)
        if on_done:
            on_done(result)
        self._set_idle(summary)

    def _job_failed(self, error: str) -> None:
        self.say(f"Error: {error}")
        self._set_idle(f"Something went wrong: {error}")

    def say(self, text: str) -> None:
        self.log.appendPlainText(text)

    def closeEvent(self, event) -> None:
        if self.job is not None:
            answer = QMessageBox.question(self, "Quit Synclassic", "Downloads are still running. Stop them and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.job.task.stop()
            self.job.wait(15000)
        self.library.close()
        event.accept()


def _spotify_client(task: Task) -> SpotifyClient | None:
    try:
        return SpotifyClient.from_saved_login()
    except spotify_auth.SpotifyAuthError as error:
        task.say(f"Spotify: {error} Continuing without login.")
        return None


def _download(library: Library, task: Task, playlists: list[LibraryPlaylist], retry_failed: bool) -> dict:
    settings = load_settings()

    def progress(number, total, item):
        task.progress.emit(number, total, item.track.display())
        task.changed.emit()

    report = download_missing(
        library, settings, task.say, retry_failed=retry_failed, only=playlists,
        progress=progress, should_stop=task.should_stop,
    )
    parts = []
    if report.downloaded:
        parts.append(f"{report.downloaded} downloaded")
    if report.review:
        parts.append(f"{report.review} to check")
    if report.unmatched:
        parts.append(f"{report.unmatched} not found")
    if report.failed:
        parts.append(f"{report.failed} failed")
    if report.stopped_early:
        task.say(report.stopped_early)
    summary = ("Done: " + ", ".join(parts) + ".") if parts else "Everything is up to date."
    if report.stopped_early:
        summary = report.stopped_early.splitlines()[0]
    if report.review or report.unmatched or report.failed:
        summary += "  See “Needs attention”."
    task.say(summary)
    return {"summary": summary}
