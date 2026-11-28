"""The sync preview: what will happen to each playlist, before anything changes on the iPod.

One row per playlist, with what happens to it (no changes, new, update, replace the iPod's own
playlist, or waiting) and how many of its songs aren't ready. A playlist that's waiting can be
replaced anyway with a tick; songs that weren't found have a button that shows them so you can
pick a match. Everything else the sync does (songs removed, album art, podcasts…) is listed
underneath.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# action -> (what it says, colour)
LOOKS = {
    "unchanged": ("No changes", "#8c8c8c"),
    "create": ("New on the iPod", "#3d8b3d"),
    "update": ("Updated", "#2f66c1"),
    "take over": ("Replaces the iPod's playlist", "#d48a0b"),
    "wait": ("Waiting: left as it is", "#c63a3a"),
}


def _look(planned) -> tuple[str, str]:
    return LOOKS["unchanged" if planned.unchanged and planned.action == "update" else planned.action]


class SyncPlanDialog(QDialog):
    """Result: Accepted = sync; `self.replace` = playlist keys you ticked to replace anyway (the
    window then asks again with a new plan); `self.show_not_found` = you asked to see the songs
    that weren't found."""

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        from synclassic.ipod.sync import playlist_key

        self.plan = plan
        self.replace: list[str] = []
        self.show_not_found = False
        self.setWindowTitle(f"Sync “{plan.ipod.name}”")
        self.setMinimumSize(720, 460)
        layout = QVBoxLayout(self)

        gb = 1000**3
        copy = (f"Copy {len(plan.copy)} song{'s' if len(plan.copy) != 1 else ''} ({plan.bytes_to_copy / gb:.2f} GB)"
                if plan.copy else "Nothing new to copy")
        free = (plan.free_bytes + plan.total_to_free - plan.total_to_copy) / gb
        heading = QLabel(f"<b>{copy}</b> · {len(plan.reuse):,} songs already on the iPod · "
                         f"{free:.1f} GB free after the sync")
        heading.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(heading)

        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels(["Playlist", "What happens", "Songs", "Not ready", "Replace anyway"])
        header = self.table.header()
        for column in (1, 2, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 200)
        self.ticks: dict[str, QCheckBox] = {}
        not_found = 0
        for planned in plan.playlists:
            text, colour = _look(planned)
            not_ready = []
            if planned.not_found:
                not_ready.append(f"{planned.not_found} not found")
            if planned.missing - planned.not_found:
                not_ready.append(f"{planned.missing - planned.not_found} downloading")
            row = QTreeWidgetItem([planned.library.name, f"● {text}", f"{len(planned.items)}", ", ".join(not_ready), ""])
            row.setForeground(1, QColor(colour))
            tip = planned.wait_reason() if planned.action == "wait" else (
                f"Not ready: {planned.missing_text()}" if planned.missing else "")
            if planned.action == "take over":
                tip = (f"The iPod already had its own “{planned.library.name}” ({planned.ipod_count} songs). It becomes "
                       f"Synclassic's version, in order; songs taken out of it stay on the iPod.")
            for column in range(5):
                row.setToolTip(column, tip)
            self.table.addTopLevelItem(row)
            not_found += planned.not_found
            if planned.action == "wait":
                tick = QCheckBox()
                tick.setToolTip("Replace the iPod's playlist with Synclassic's version now (songs not ready join "
                                "it once they are; songs taken out of it stay on the iPod)")
                holder = QWidget()
                box = QHBoxLayout(holder)
                box.setContentsMargins(0, 0, 0, 0)
                box.addWidget(tick, 0, Qt.AlignmentFlag.AlignCenter)
                self.table.setItemWidget(row, 4, holder)
                self.ticks[playlist_key(planned.library)] = tick
                tick.toggled.connect(self._ticked)
        layout.addWidget(self.table, 1)

        others = [line.strip() for line in plan.summary()[1:]
                  if not line.startswith(("  · ", "  ⏸ ", "  Copy ", "  Reuse ", "  Free space after sync"))]
        if others:
            more = QLabel("\n".join(others))
            more.setTextFormat(Qt.TextFormat.PlainText)
            more.setWordWrap(True)
            layout.addWidget(more)
        if plan.waiting:
            note = QLabel("A waiting playlist stays as it is on the iPod, because Synclassic's version would be "
                          "smaller and isn't complete yet. Tick Replace anyway to replace it now.")
            note.setWordWrap(True)
            note.setProperty("role", "dim")
            layout.addWidget(note)

        buttons = QHBoxLayout()
        if not_found:
            fix = QPushButton(f"Show the {not_found} Song{'s' if not_found != 1 else ''} Not Found…")
            fix.setToolTip("Double-click a song there to listen to the choices and pick the right one")
            fix.clicked.connect(self._fix_not_found)
            buttons.addWidget(fix)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.sync = QPushButton("Sync")
        self.sync.setDefault(True)
        self.sync.setProperty("aqua", True)
        self.sync.clicked.connect(self.accept)
        self.sync.setEnabled(plan.has_changes and plan.fits)
        if not plan.fits:
            self.sync.setToolTip("There isn't enough space on the iPod for this sync")
        buttons.addWidget(cancel)
        buttons.addWidget(self.sync)
        layout.addLayout(buttons)

    def _ticked(self) -> None:
        self.replace = [key for key, tick in self.ticks.items() if tick.isChecked()]
        self.sync.setText("Update the Plan" if self.replace else "Sync")
        self.sync.setEnabled(bool(self.replace) or (self.plan.has_changes and self.plan.fits))

    def _fix_not_found(self) -> None:
        self.show_not_found = True
        self.reject()
