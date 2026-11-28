"""Which playlists go on this iPod, and how big its songs are (like iTunes' Music tab)."""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from synclassic import convert
from synclassic.ipod import prefs
from synclassic.ipod.sync import playlist_key

SIZES = [(0, "As they are"), (256, "256 kbps AAC"), (192, "192 kbps AAC"), (128, "128 kbps AAC (about twice as many songs)")]
NOT_DOWNLOADED_KBPS = 130  # what a song not downloaded yet will probably take


def song_size(item, bitrate: int) -> int:
    """About how much room one song takes on the iPod with this size setting."""
    duration = item.track.duration_ms or 0
    if item.has_file:
        path = pathlib.Path(item.file_path)
        try:
            target = convert.target_for(path, duration, bitrate)
            return convert.estimated_size(path, duration, target)
        except OSError:
            pass
    return round(duration * min(bitrate or NOT_DOWNLOADED_KBPS, NOT_DOWNLOADED_KBPS) / 8)


class PlaylistChoiceDialog(QDialog):
    def __init__(self, library, ipod, parent=None):
        super().__init__(parent)
        self.library, self.ipod = library, ipod
        self.current = prefs.load(ipod)
        self.setWindowTitle(f"Music on “{ipod.name}”")
        self.setMinimumSize(480, 520)
        layout = QVBoxLayout(self)
        intro = QLabel(f"<b>Choose what goes on “{ipod.name}”.</b><br><span style='color:#6b6b6b'>"
                       "Every iPod keeps its own choice. Playlists you untick are taken off it at the "
                       "next sync (songs you put on it yourself stay).</span>")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.every = QRadioButton("All playlists (new ones too)")
        self.only = QRadioButton("Only the playlists ticked below:")
        group = QButtonGroup(self)
        group.addButton(self.every)
        group.addButton(self.only)
        layout.addWidget(self.every)
        layout.addWidget(self.only)

        self.list = QListWidget()
        self.playlists = library.playlists()
        chosen = {playlist_key(p) for p in prefs.chosen_playlists(library, ipod, self.current)}
        self.songs: dict[str, set[str]] = {}  # playlist key -> song keys
        self.items: dict[str, object] = {}  # song key -> library song
        for playlist in self.playlists:
            key = playlist_key(playlist)
            tracks = library.playlist_tracks(playlist)
            self.songs[key] = {t.key for t in tracks}
            for track in tracks:
                self.items.setdefault(track.key, track)
            row = QListWidgetItem(f"{playlist.name}  ({len(tracks)} song{'s' if len(tracks) != 1 else ''})")
            row.setData(Qt.ItemDataRole.UserRole, key)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Checked if key in chosen else Qt.CheckState.Unchecked)
            self.list.addItem(row)
        layout.addWidget(self.list, 1)
        quick = QHBoxLayout()
        for text, state in (("Tick All", Qt.CheckState.Checked), ("Untick All", Qt.CheckState.Unchecked)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, s=state: self._tick_all(s))
            quick.addWidget(button)
        quick.addStretch(1)
        layout.addLayout(quick)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Songs copied to this iPod:"))
        self.size = QComboBox()
        for value, text in SIZES:
            self.size.addItem(text, value)
        self.size.setCurrentIndex(max(0, [v for v, _ in SIZES].index(self.current.bitrate)))
        self.size.setToolTip("Smaller files fit more songs. Songs bigger than this are converted on their way to "
                             "the iPod (your own files aren't changed). Songs already on the iPod stay as they are.")
        size_row.addWidget(self.size, 1)
        layout.addLayout(size_row)

        self.total = QLabel("")
        self.total.setTextFormat(Qt.TextFormat.RichText)
        self.total.setWordWrap(True)
        layout.addWidget(self.total)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        (self.every if self.current.all_playlists else self.only).setChecked(True)
        self.every.toggled.connect(self._update)
        self.list.itemChanged.connect(lambda _: self._update())
        self.size.currentIndexChanged.connect(lambda _: self._update(sizes_changed=True))
        self._sizes: dict[str, int] = {}
        self._update(sizes_changed=True)

    def _tick_all(self, state) -> None:
        self.only.setChecked(True)
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            self.list.item(row).setCheckState(state)
        self.list.blockSignals(False)
        self._update()

    def chosen_keys(self) -> list[str] | None:
        if self.every.isChecked():
            return None
        return [self.list.item(r).data(Qt.ItemDataRole.UserRole) for r in range(self.list.count())
                if self.list.item(r).checkState() == Qt.CheckState.Checked]

    def _update(self, sizes_changed: bool = False) -> None:
        self.list.setEnabled(self.only.isChecked())
        bitrate = self.size.currentData()
        if sizes_changed:
            self._sizes = {key: song_size(item, bitrate) for key, item in self.items.items()}
        keys = self.chosen_keys()
        songs: set[str] = set()
        for key in (keys if keys is not None else self.songs):
            songs |= self.songs.get(key, set())
        need = sum(self._sizes.get(k, 0) for k in songs)
        capacity = self.ipod.disk_usage()[0]
        gb = 1000**3
        colour = "#2e8b2e" if need < capacity * 0.97 else "#c63a3a"
        amount = f"{need / gb:.1f} GB" if need >= gb / 10 else f"{need / 1000**2:.0f} MB"
        self.total.setText(f"<span style='color:{colour}'>About <b>{amount}</b> for {len(songs):,} "
                           f"song{'s' if len(songs) != 1 else ''}</span>"
                           f" · this iPod holds {capacity / gb:.1f} GB"
                           + ("" if need < capacity * 0.97 else "<br>That's more than fits: untick some playlists or "
                              "choose smaller files."))

    def _save(self) -> None:
        prefs.update(self.ipod, playlists=self.chosen_keys(), bitrate=self.size.currentData())
        self.accept()
