"""iPod maintenance windows: Get Info (edit song info), Backups, Clean Up."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
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

from synclassic.gui.workers import mark_closed, run_in_background
from synclassic.ipod.browse import Song

TEXT_FIELDS = [("title", "Name"), ("artist", "Artist"), ("album_artist", "Album Artist"), ("album", "Album"),
               ("genre", "Genre")]
NUMBER_FIELDS = [("year", "Year"), ("track_number", "Track Number"), ("disc_number", "Disc Number")]


def _size(size: int) -> str:
    return f"{size / 1000**2:.1f} MB" if size < 1000**3 else f"{size / 1000**3:.2f} GB"


class InfoDialog(QDialog):
    """iTunes' Get Info: edit one song, or several at once (only the fields you change are applied)."""

    def __init__(self, songs: list[Song], parent=None):
        super().__init__(parent)
        self.songs = songs
        many = len(songs) > 1
        self.setWindowTitle(f"{len(songs)} songs" if many else songs[0].title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        heading = QLabel(f"<b>{len(songs)} songs selected</b><br><span style='color:#6b6b6b'>Fields left as “Mixed” "
                         "aren't changed.</span>" if many else f"<b>{songs[0].title}</b><br>"
                         f"<span style='color:#6b6b6b'>{songs[0].artist}</span>")
        layout.addWidget(heading)
        box = QGroupBox("Info")
        form = QFormLayout(box)
        self.fields: dict[str, QLineEdit] = {}
        for name, label in TEXT_FIELDS + NUMBER_FIELDS:
            values = {getattr(song, name) for song in songs}
            edit = QLineEdit()
            if name in dict(NUMBER_FIELDS):
                edit.setValidator(QIntValidator(0, 9999, edit))
                edit.setMaximumWidth(90)
            if len(values) == 1:
                value = next(iter(values))
                edit.setText("" if value in (0, None) else str(value))
            else:
                edit.setPlaceholderText("Mixed")
            edit.setProperty("touched", False)
            edit.textEdited.connect(lambda _text, e=edit: e.setProperty("touched", True))
            self.fields[name] = edit
            form.addRow(label, edit)
        layout.addWidget(box)
        if not many:
            song = songs[0]
            details = QGroupBox("Summary")
            summary = QFormLayout(details)
            for label, value in (
                ("Kind", song.kind.capitalize()), ("Size", _size(song.size)),
                ("Plays", str(song.plays)), ("Rating", "★" * song.rating or "None"),
                ("Date Added", song.added.strftime("%Y-%m-%d %H:%M") if song.added else "Unknown"),
                ("Where", song.path),
            ):
                text = QLabel(value)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                summary.addRow(label, text)
            layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def changes(self) -> dict[str, str | int]:
        """Only the fields the user actually edited."""
        result: dict[str, str | int] = {}
        for name, edit in self.fields.items():
            if not edit.property("touched"):
                continue
            text = edit.text().strip()
            result[name] = int(text or 0) if name in dict(NUMBER_FIELDS) else text
        if "title" in result and not result["title"]:
            del result["title"]  # a song always keeps a name
        return result


class BackupsDialog(QDialog):
    """The iPod's backups, newest first, with Restore."""

    def __init__(self, window, ipod, parent=None):
        super().__init__(parent or window)
        self.window_ = window
        self.ipod = ipod
        self.setWindowTitle("iPod Backups")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        explain = QLabel(
            "Synclassic backs up your iPod's music and artwork databases before every sync, and before the first "
            "change each session. The last 10 are kept. Restoring puts that version back (and backs up the "
            "current one first, so it can be undone). Songs whose files were deleted since can't come back."
        )
        explain.setWordWrap(True)
        layout.addWidget(explain)
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setHeaderLabels(["Date", "Songs", "Size", "Artwork"])
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.reveal = QPushButton("Show in Finder")
        self.reveal.clicked.connect(self._reveal)
        row.addWidget(self.reveal)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        self.restore_button = QPushButton("Restore…")
        self.restore_button.setProperty("aqua", True)
        self.restore_button.clicked.connect(self._restore)
        row.addWidget(close)
        row.addWidget(self.restore_button)
        layout.addLayout(row)
        self.backups = []
        self._selected()
        self.load()

    def load(self) -> None:
        from synclassic.ipod import manage

        self.list.clear()
        self.list.addTopLevelItem(QTreeWidgetItem(["Reading backups…"]))
        run_in_background(self, lambda task: manage.list_backups(self.ipod), self._show,
                          lambda error: self.list.topLevelItem(0).setText(0, f"Couldn't read backups: {error}"))

    def _show(self, backups) -> None:
        self.backups = backups
        self.list.clear()
        for info in backups:
            songs = f"{info.songs:,}" if info.songs is not None else "unreadable"
            self.list.addTopLevelItem(QTreeWidgetItem([info.when, songs, _size(info.size), "yes" if info.has_artwork else "—"]))
        if not backups:
            self.list.addTopLevelItem(QTreeWidgetItem(["No backups yet (one is made before every sync)."]))
        self._selected()

    def _current(self):
        item = self.list.currentItem()
        index = self.list.indexOfTopLevelItem(item) if item else -1
        return self.backups[index] if 0 <= index < len(self.backups) else None

    def _selected(self) -> None:
        enabled = self._current() is not None
        self.restore_button.setEnabled(enabled)
        self.reveal.setEnabled(enabled)

    def _reveal(self) -> None:
        from synclassic.gui.main_window import reveal_in_finder

        info = self._current()
        if info:
            reveal_in_finder(info.folder / "iTunesDB")

    def _restore(self) -> None:
        info = self._current()
        if info is None:
            return
        answer = QMessageBox.question(
            self, "Restore Backup",
            f"Put the backup from {info.when} back on your iPod? The iPod's current library is backed up first.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.window_.restore_ipod_backup(info.folder)
            self.accept()

    def done(self, result: int) -> None:
        mark_closed(self)
        super().done(result)


class CleanupDialog(QDialog):
    """Leftover files (on the iPod but not in its library) and songs whose file is missing."""

    def __init__(self, window, ipod, parent=None):
        super().__init__(parent or window)
        self.window_ = window
        self.ipod = ipod
        self.report = None
        self.setWindowTitle("Clean Up iPod")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.status = QLabel("Looking for leftovers…")
        layout.addWidget(self.status)

        self.left_box = QGroupBox("Files the iPod's library doesn't list")
        left = QVBoxLayout(self.left_box)
        self.left_text = QLabel("")
        self.left_text.setWordWrap(True)
        left.addWidget(self.left_text)
        self.left_list = QTreeWidget()
        self.left_list.setHeaderHidden(True)
        self.left_list.setRootIsDecorated(False)
        self.left_list.setMaximumHeight(120)
        left.addWidget(self.left_list)
        row = QHBoxLayout()
        row.addStretch(1)
        self.delete_button = QPushButton("Delete Files…")
        self.delete_button.clicked.connect(self._delete)
        self.add_button = QPushButton("Add to Library")
        self.add_button.setProperty("aqua", True)
        self.add_button.clicked.connect(self._add)
        row.addWidget(self.delete_button)
        row.addWidget(self.add_button)
        left.addLayout(row)
        layout.addWidget(self.left_box)

        self.missing_box = QGroupBox("Songs whose file is missing")
        missing = QVBoxLayout(self.missing_box)
        self.missing_text = QLabel("")
        self.missing_text.setWordWrap(True)
        missing.addWidget(self.missing_text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.remove_button = QPushButton("Remove from Library")
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.remove_button)
        missing.addLayout(row)
        layout.addWidget(self.missing_box)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        for box in (self.left_box, self.missing_box):
            box.hide()
        self.load()

    def load(self) -> None:
        from synclassic.ipod import manage

        run_in_background(self, lambda task: manage.find_cleanup(self.ipod), self._show,
                          lambda error: self.status.setText(f"Couldn't check the iPod: {error}"))

    def _show(self, report) -> None:
        self.report = report
        if not report.leftovers and not report.missing:
            self.status.setText("✓ Nothing to clean up: every file on the iPod is in its library, and every song has its file.")
            return
        self.status.setText("")
        if report.leftovers:
            self.left_box.show()
            self.left_text.setText(
                f"{len(report.leftovers)} music file{'s' if len(report.leftovers) != 1 else ''} "
                f"({_size(report.leftover_bytes)}) {'are' if len(report.leftovers) != 1 else 'is'} on the iPod but not in its "
                "library, so the iPod can't play them. Often they're from an interrupted sync. Add them back "
                "(their own song info is used), or delete them to free the space."
            )
            self.left_list.clear()
            for path in report.leftovers[:200]:
                self.left_list.addTopLevelItem(QTreeWidgetItem([path.relative_to(self.ipod.root).as_posix()]))
        if report.missing:
            self.missing_box.show()
            names = ", ".join(name for _, name in report.missing[:5]) + ("…" if len(report.missing) > 5 else "")
            count = len(report.missing)
            self.missing_text.setText(f"{count} song{'s are' if count != 1 else ' is'} listed but the file is gone: {names}")

    def _add(self) -> None:
        from synclassic.ipod import manage

        paths = list(self.report.leftovers)
        self.window_._ipod_edit("Adding files to the library…",
                                lambda ipod, backup: manage.add_leftovers(ipod, paths, backup),
                                done_message=lambda n: f"Added {n} songs back to your iPod's library")
        self.accept()

    def _delete(self) -> None:
        from synclassic.ipod import manage

        paths = list(self.report.leftovers)
        answer = QMessageBox.question(self, "Delete Files", f"Delete {len(paths)} files that aren't in the iPod's library?")
        if answer == QMessageBox.StandardButton.Yes:
            self.window_._ipod_edit("Deleting leftover files…",
                                    lambda ipod, backup: manage.delete_leftovers(ipod, paths),
                                    done_message=lambda n: f"Deleted {n} leftover files")
            self.accept()

    def _remove(self) -> None:
        from synclassic.ipod import manage

        ids = [song_id for song_id, _ in self.report.missing]
        self.window_._ipod_edit("Removing missing songs…",
                                lambda ipod, backup: manage.remove_missing(ipod, ids, backup),
                                done_message=lambda n: f"Removed {n} songs whose file was missing")
        self.accept()

    def done(self, result: int) -> None:
        mark_closed(self)
        super().done(result)
