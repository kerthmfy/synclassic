"""The Podcasts page: shows with their episodes under a disclosure triangle, like iTunes 7."""

from __future__ import annotations

import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHeaderView, QTreeWidget, QTreeWidgetItem

from synclassic.gui import theme
from synclassic.gui.picker_dialog import fmt_time
from synclassic.gui.widgets import StatusPillDelegate
from synclassic.podcasts.store import Podcast, StoredEpisode

KIND_ROLE = Qt.ItemDataRole.UserRole + 10  # "show" or "episode"
ID_ROLE = Qt.ItemDataRole.UserRole + 11


def episode_status(episode: StoredEpisode, wanted: bool, downloading: set[int] = frozenset()) -> str:
    if episode.id in downloading:
        return "Downloading"
    if episode.status == "failed" and not episode.downloaded:
        return "Failed"
    if episode.downloaded:
        return "Played" if episode.played else "New"
    if episode.played:
        return "Played"
    return "Waiting" if wanted else "Get"


def blue_dot(size: int = 12) -> QIcon:
    """The unplayed dot from iTunes' podcast list."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(2.5, 2.5, size - 5, size - 5)
    gradient = QRadialGradient(QPointF(r.center().x() - 1, r.top() + 2), r.width())
    gradient.setColorAt(0, QColor("#bfe0ff"))
    gradient.setColorAt(0.6, QColor("#3b86e6"))
    gradient.setColorAt(1, QColor("#1f5fbf"))
    painter.setPen(QPen(QColor("#1d4f9c"), 0.7))
    painter.setBrush(gradient)
    painter.drawEllipse(r)
    painter.end()
    return QIcon(pixmap)


def _date(value: datetime.datetime | None) -> str:
    return value.strftime("%-d %b %Y") if value else ""


class PodcastsPage(QTreeWidget):
    episode_activated = Signal(int)  # episode id (double-click)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(5)
        self.setHeaderLabels(["Podcast", "Time", "Release Date", "Description", "Status"])
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setItemDelegateForColumn(4, StatusPillDelegate(self))
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 340)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(4, 110)
        self._dot = blue_dot()
        self._expanded: set[int] = set()
        self._first_fill = True
        self.itemExpanded.connect(lambda item: self._expanded.add(item.data(0, ID_ROLE)))
        self.itemCollapsed.connect(lambda item: self._expanded.discard(item.data(0, ID_ROLE)))
        self.itemDoubleClicked.connect(self._double_clicked)

    def fill(self, shows: list[tuple[Podcast, list[StoredEpisode], set[int]]], covers: dict[int, QPixmap],
             downloading: set[int] = frozenset()) -> None:
        """shows: (podcast, its episodes newest first, ids of the episodes it keeps)."""
        selected = {(i.data(0, KIND_ROLE), i.data(0, ID_ROLE)) for i in self.selectedItems()}
        scroll = self.verticalScrollBar().value()
        self.clear()
        bold = QFont(self.font())
        bold.setBold(True)
        for podcast, episodes, wanted in shows:
            unplayed = sum(1 for e in episodes if e.downloaded and not e.played)
            show = QTreeWidgetItem([podcast.title, "", _date(episodes[0].published) if episodes else "",
                                    podcast.author, f"{unplayed} new" if unplayed else ""])
            show.setData(0, KIND_ROLE, "show")
            show.setData(0, ID_ROLE, podcast.id)
            show.setFont(0, bold)
            show.setToolTip(0, podcast.description[:400])
            show.setForeground(3, QColor(theme.c("dim")))
            if podcast.id in covers:
                show.setIcon(0, QIcon(covers[podcast.id]))
            elif unplayed:
                show.setIcon(0, self._dot)
            for episode in episodes:
                status = episode_status(episode, episode.id in wanted, downloading)
                row = QTreeWidgetItem([episode.title, fmt_time(episode.duration_s) if episode.duration_s else "",
                                       _date(episode.published), " ".join(episode.description.split())[:200], status])
                row.setData(0, KIND_ROLE, "episode")
                row.setData(0, ID_ROLE, episode.id)
                if episode.downloaded and not episode.played:
                    row.setIcon(0, self._dot)
                if not episode.downloaded:
                    for column in range(4):
                        row.setForeground(column, QColor(theme.c("faint")))
                row.setToolTip(3, episode.description[:600])
                if episode.error and status == "Failed":
                    row.setToolTip(4, episode.error)
                show.addChild(row)
            self.addTopLevelItem(show)
            if podcast.id in self._expanded or (self._first_fill and len(shows) == 1):
                show.setExpanded(True)
        self._first_fill = False
        for item in self._all_items():
            if (item.data(0, KIND_ROLE), item.data(0, ID_ROLE)) in selected:
                item.setSelected(True)
        self.verticalScrollBar().setValue(scroll)

    def _all_items(self):
        for i in range(self.topLevelItemCount()):
            show = self.topLevelItem(i)
            yield show
            for j in range(show.childCount()):
                yield show.child(j)

    def selected(self, kind: str) -> list[int]:
        return [i.data(0, ID_ROLE) for i in self.selectedItems() if i.data(0, KIND_ROLE) == kind]

    def episode_ids_in_view(self, show_id: int) -> list[int]:
        for i in range(self.topLevelItemCount()):
            show = self.topLevelItem(i)
            if show.data(0, ID_ROLE) == show_id:
                return [show.child(j).data(0, ID_ROLE) for j in range(show.childCount()) if not show.child(j).isHidden()]
        return []

    def apply_search(self, text: str) -> None:
        needle = text.casefold().strip()
        for i in range(self.topLevelItemCount()):
            show = self.topLevelItem(i)
            show_matches = not needle or needle in show.text(0).casefold() or needle in show.text(3).casefold()
            any_child = False
            for j in range(show.childCount()):
                child = show.child(j)
                hit = show_matches or needle in child.text(0).casefold() or needle in child.text(3).casefold()
                child.setHidden(not hit)
                any_child = any_child or hit
            show.setHidden(not (show_matches or any_child))
            if needle and any_child and not show_matches:
                show.setExpanded(True)

    def _double_clicked(self, item, column) -> None:
        if item.data(0, KIND_ROLE) == "episode":
            self.episode_activated.emit(item.data(0, ID_ROLE))
