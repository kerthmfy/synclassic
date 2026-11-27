"""Listening Stats: top songs, artists, albums and genres, for the last 7 / 30 days or all time."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synclassic import stats

PERIODS = (("week", "Last 7 Days"), ("month", "Last 30 Days"), ("ipod", "All Time on iPod"))


class StatsPage(QWidget):
    rewind_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ipod_songs = None  # browse.Song values of the connected iPod, if any
        self.period = "month"
        self.setStyleSheet("StatsPage { background: #ffffff; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        row = QHBoxLayout()
        self.buttons = {}
        for key, label in PERIODS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.show_period(k))
            row.addWidget(button)
            self.buttons[key] = button
        row.addStretch(1)
        rewind = QPushButton("iPod Rewind…")
        rewind.setProperty("aqua", True)
        rewind.setToolTip("Your recap as a picture to share")
        rewind.clicked.connect(self.rewind_requested)
        row.addWidget(rewind)
        layout.addLayout(row)
        self.summary = QLabel("")
        self.summary.setStyleSheet("font-size: 13px; color: #333; padding: 6px 2px;")
        layout.addWidget(self.summary)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.lists = {}
        for index, (key, title, columns) in enumerate((("songs", "Top Songs", ["#", "Song", "Artist", "Plays"]),
                                                       ("artists", "Top Artists", ["#", "Artist", "Plays"]),
                                                       ("albums", "Top Albums", ["#", "Album", "Artist", "Plays"]),
                                                       ("genres", "Top Genres", ["#", "Genre", "Plays"]))):
            box = QVBoxLayout()
            heading = QLabel(f"<b>{title}</b>")
            box.addWidget(heading)
            table = QTreeWidget()
            table.setHeaderLabels(columns)
            table.setRootIsDecorated(False)
            table.setAlternatingRowColors(True)
            table.setFrameShape(QFrame.Shape.StyledPanel)
            header = table.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            for column in range(1, len(columns) - 1):
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            box.addWidget(table)
            grid.addLayout(box, index // 2, index % 2)
            self.lists[key] = table
        layout.addLayout(grid, 1)
        self.show_period(self.period)

    def set_ipod_songs(self, songs) -> None:
        self.ipod_songs = songs
        self.buttons["ipod"].setEnabled(songs is not None)
        if songs is None and self.period == "ipod":
            self.period = "month"
        self.show_period(self.period)

    def show_period(self, key: str) -> None:
        self.period = key
        for name, button in self.buttons.items():
            button.setChecked(name == key)
        if key == "ipod":
            data = stats.from_ipod(self.ipod_songs or [])
        else:
            data = stats.from_history(7 if key == "week" else 30)
        self.fill(data)

    def fill(self, data: stats.Stats) -> None:
        if data.plays:
            hours = f"{data.hours:.1f} hours" if data.hours >= 1 else f"{round(data.seconds / 60)} minutes"
            self.summary.setText(f"{data.title}: <b>{data.plays:,}</b> plays · <b>{hours}</b> of music")
        elif self.period == "ipod":
            self.summary.setText("No plays on this iPod yet.")
        else:
            self.summary.setText(f"{data.title}: no plays recorded yet. Plays are recorded from now on: the iPod's at "
                                 "each sync, and songs you play in Synclassic.")
        rows = {"songs": data.songs, "artists": data.artists, "albums": data.albums, "genres": data.genres}
        for key, table in self.lists.items():
            table.clear()
            for rank, row in enumerate(rows[key], start=1):
                item = QTreeWidgetItem([str(rank), *[str(v) for v in row[:-1]], f"{row[-1]:,}"])
                item.setTextAlignment(0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setTextAlignment(table.columnCount() - 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.addTopLevelItem(item)
