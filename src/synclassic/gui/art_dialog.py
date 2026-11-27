"""Fix Album Art: albums on the iPod with missing or low-quality covers, the sharp covers found
for them side by side, and a button to put the chosen ones on."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from synclassic.gui.workers import mark_closed, run_in_background

THUMB = 56


def _pil_to_qimage(picture) -> QImage:
    picture = picture.convert("RGB")
    raw = picture.tobytes()
    return QImage(raw, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()


def _icon(image: QImage | None) -> QIcon:
    if image is None or image.isNull():
        from synclassic.gui.ipod_views import placeholder_cover

        return QIcon(placeholder_cover(THUMB * 2))
    pixmap = QPixmap.fromImage(image.scaled(THUMB * 2, THUMB * 2, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ArtFixDialog(QDialog):
    def __init__(self, window, ipod, library_path, parent=None):
        super().__init__(parent or window)
        self.window_ = window
        self.ipod = ipod
        self.library_path = library_path
        self.albums = []
        self.worker = None
        self.setWindowTitle("Fix Album Art")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        intro = QLabel("Albums on your iPod (and songs on this Mac) with no cover, or a small one that looks "
                       "pixelated. Sharp covers "
                       "are looked up (the album's Spotify cover, then Deezer, then Apple Music); compare and "
                       "choose which to put on.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        self.everything = QCheckBox("Also check covers that can't be measured (often songs from iTunes)")
        self.everything.setToolTip("Their cover isn't inside the song file, so its size is unknown. A cover is only "
                                   "offered when it's at least 600 px; compare before replacing.")
        row.addWidget(self.everything, 1)
        self.check_button = QPushButton("Check Again")
        self.check_button.clicked.connect(self.start)
        row.addWidget(self.check_button)
        layout.addLayout(row)
        self.status = QLabel("")
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        layout.addWidget(self.bar)

        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Album", "Now", "New", "Details"])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(True)
        self.list.setIconSize(QSize(THUMB, THUMB))
        header = self.list.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 260)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, THUMB + 14)
        self.list.itemChanged.connect(lambda *_: self._update_button())
        layout.addWidget(self.list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.replace_button = buttons.addButton("Replace Covers", QDialogButtonBox.ButtonRole.AcceptRole)
        self.replace_button.setProperty("aqua", True)
        self.replace_button.clicked.connect(self._replace)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.start()

    # --- checking ------------------------------------------------------------------------------

    def start(self) -> None:
        if self.worker is not None:
            return
        from synclassic.ipod import art_fix, browse
        from synclassic.library import Library

        ipod, library_path, everything = self.ipod, self.library_path, self.everything.isChecked()
        self.list.clear()
        self.check_button.setEnabled(False)
        self.replace_button.setEnabled(False)
        self.status.setText("Checking the covers…")
        self.bar.setRange(0, 0)
        self.bar.show()

        def work(task):
            library = Library(library_path)
            try:
                def scanning(number, total, text):
                    task.progress.emit(number, total, f"Checking covers… {number} of {total} albums")

                albums = art_fix.scan(ipod, library, everything, scanning, task.should_stop) if ipod else []
                on_ipod = {path for album in albums for path in album.library_files}
                albums += art_fix.scan_library(library, on_ipod, scanning, task.should_stop)

                def finding(number, total, text):
                    task.progress.emit(number, total, f"Looking for sharp covers… {number} of {total}: {text}")

                art_fix.find_covers(albums, progress=finding, should_stop=task.should_stop)
            finally:
                library.close()
            images = []
            for album in albums:
                old = None
                if album.thumbnail is not None:
                    try:
                        picture = browse.cover(ipod, album.thumbnail)
                        old = _pil_to_qimage(picture) if picture is not None else None
                    except OSError:
                        old = None
                new = QImage.fromData(album.new_cover) if album.new_cover else None
                images.append((old, new))
            return albums, images

        self.worker = run_in_background(self, work, self._show, self._failed)
        self.worker.task.progress.connect(self._progress)

    def _progress(self, number: int, total: int, text: str) -> None:
        self.status.setText(text)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(number)

    def _failed(self, error: str) -> None:
        self.worker = None
        self.bar.hide()
        self.check_button.setEnabled(True)
        self.status.setText(f"Couldn't check the covers: {error}")

    def _show(self, result) -> None:
        self.worker = None
        self.bar.hide()
        self.check_button.setEnabled(True)
        albums, images = result
        # Covers found first, then the rest; alphabetical within each.
        order = sorted(range(len(albums)), key=lambda i: (albums[i].new_cover is None, albums[i].title.casefold()))
        albums, images = [albums[i] for i in order], [images[i] for i in order]
        self.albums = albums
        self.list.blockSignals(True)
        for index, (album, (old, new)) in enumerate(zip(albums, images)):
            name = album.title or "(no name)"
            row = QTreeWidgetItem([f"{name}\n{album.artist}", "", "", f"{album.problem} · on your {album.where}\n{album.found}"])
            row.setData(0, Qt.ItemDataRole.UserRole, index)
            row.setIcon(1, _icon(old))
            row.setIcon(2, _icon(new) if new is not None else QIcon())
            row.setSizeHint(0, QSize(0, THUMB + 8))
            songs = len(album.songs) or len(album.titles)
            row.setToolTip(3, f"{songs} song{'s' if songs != 1 else ''}")
            if album.new_cover:
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(0, Qt.CheckState.Checked)
            else:
                row.setFlags(row.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                for column in range(4):
                    row.setForeground(column, Qt.GlobalColor.gray)
            self.list.addTopLevelItem(row)
        self.list.blockSignals(False)
        found = sum(1 for a in albums if a.new_cover)
        if not albums:
            self.status.setText("✓ Every album has a sharp cover.")
        else:
            self.status.setText(f"{len(albums)} album{'s' if len(albums) != 1 else ''} could look better; "
                                f"sharp covers found for {found}.")
        self._update_button()

    def chosen(self) -> list:
        picked = []
        for i in range(self.list.topLevelItemCount()):
            row = self.list.topLevelItem(i)
            if row.flags() & Qt.ItemFlag.ItemIsUserCheckable and row.checkState(0) == Qt.CheckState.Checked:
                picked.append(self.albums[row.data(0, Qt.ItemDataRole.UserRole)])
        return picked

    def _update_button(self) -> None:
        count = len(self.chosen())
        self.replace_button.setEnabled(count > 0 and self.worker is None)
        self.replace_button.setText(f"Replace {count} Cover{'s' if count != 1 else ''}" if count else "Replace Covers")

    # --- replacing -----------------------------------------------------------------------------

    def _replace(self) -> None:
        from synclassic.ipod import art_fix

        albums = self.chosen()
        if not albums:
            return
        songs = sum(len(a.songs) or len(a.titles) for a in albums)
        if not any(a.songs for a in albums) or self.ipod is None:  # only files on this Mac
            from synclassic.ipod import art_fix as fix

            count = fix.apply(None, albums, say=self.window_.say)
            self.window_.say(f"New covers for {count} songs on this Mac")
            self.window_.status_label.setText(f"New covers for {count} songs")
            self.accept()
            return
        self.window_._ipod_edit(
            "Putting sharp covers on the iPod…",
            lambda ipod, backup: art_fix.apply(ipod, albums, backup),
            done_message=lambda count: f"New covers for {count} song{'s' if count != 1 else ''}",
        )
        self.window_.say(f"Replacing the covers of {len(albums)} albums ({songs} songs)…")
        self.accept()

    def done(self, result: int) -> None:
        if self.worker is not None:
            self.worker.task.stop()
        mark_closed(self)
        super().done(result)
