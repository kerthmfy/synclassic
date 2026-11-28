"""Views of what's on the iPod: a sortable song list, an album-cover grid, an artist/genre browser."""

from __future__ import annotations

import json

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, QRectF, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPen, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from synclassic.gui import theme
from synclassic.ipod.browse import Album, Contents, Song

COLUMNS = ["Name", "Time", "Artist", "Album", "Genre", "Plays", "Rating", "Date Added"]
SONG_MIME = "application/x-synclassic-ipod-song-ids"
SORT_ROLE = Qt.ItemDataRole.UserRole + 10
SONG_ID_ROLE = Qt.ItemDataRole.UserRole + 11


def _time(ms: int) -> str:
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds < 3600 else f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class SongsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.songs: list[Song] = []
        self.playing_id: int | None = None
        self._speaker = None

    def set_playing(self, song_id: int | None) -> None:
        if song_id == self.playing_id:
            return
        self.playing_id = song_id
        if self.songs:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.songs) - 1, 0))

    def set_songs(self, songs: list[Song]) -> None:
        self.beginResetModel()
        self.songs = songs
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.songs)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(COLUMNS)

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else base | Qt.ItemFlag.ItemIsDropEnabled

    def mimeTypes(self):  # noqa: N802
        return [SONG_MIME]

    def mimeData(self, indexes):  # noqa: N802
        ids = list(dict.fromkeys(self.songs[i.row()].id for i in indexes if i.isValid()))
        data = QMimeData()
        data.setData(SONG_MIME, json.dumps(ids).encode())
        return data

    def supportedDragActions(self):  # noqa: N802
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def supportedDropActions(self):  # noqa: N802
        return Qt.DropAction.MoveAction

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        song = self.songs[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return [
                song.title, _time(song.length_ms), song.artist, song.album, song.genre,
                str(song.plays) if song.plays else "", "★" * song.rating,
                song.added.strftime("%Y-%m-%d %H:%M") if song.added else "",
            ][column]
        if role == SORT_ROLE:
            return [
                song.title.casefold(), song.length_ms, (song.artist.casefold(), song.album.casefold(), song.track_number),
                (song.album.casefold(), song.disc_number, song.track_number), song.genre.casefold(), song.plays,
                song.rating, song.added.timestamp() if song.added else 0,
            ][column]
        if role == SONG_ID_ROLE:
            return song.id
        if role == Qt.ItemDataRole.DecorationRole and column == 0 and song.id == self.playing_id:
            if self._speaker is None:
                from synclassic.gui.widgets import speaker_icon

                self._speaker = speaker_icon()
            return self._speaker
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column == 6:
            return QColor(theme.c("dim"))
        return None


class SongFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.words: list[str] = []
        self.setSortRole(SORT_ROLE)

    def set_query(self, text: str) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.9+
            self.beginFilterChange()
            self.words = text.casefold().split()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.words = text.casefold().split()
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):  # noqa: N802
        if not self.words:
            return True
        song = self.sourceModel().songs[row]
        haystack = f"{song.title} {song.artist} {song.album} {song.genre}".casefold()
        return all(word in haystack for word in self.words)

    def lessThan(self, left, right):  # noqa: N802
        return left.data(SORT_ROLE) < right.data(SORT_ROLE)


def song_ids_from(mime: QMimeData) -> list[int]:
    if not mime.hasFormat(SONG_MIME):
        return []
    try:
        return [int(i) for i in json.loads(bytes(mime.data(SONG_MIME)).decode())]
    except (ValueError, TypeError):
        return []


class SongTable(QTableView):
    """The striped, sortable song list used everywhere on the iPod side.

    Songs can be dragged (onto playlists in the source list). When `reorderable` is on (an
    editable playlist in its own order), dragging within the list emits `reorder_requested`.
    """

    reorder_requested = Signal(list, int)  # song ids being moved, row to put them before
    delete_pressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reorderable = False
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.source = SongsModel(self)
        self.proxy = SongFilter(self)
        self.proxy.setSourceModel(self.source)
        self.setModel(self.proxy)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(19)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        for column, mode in enumerate([QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.Fixed,
                                       QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.Stretch,
                                       QHeaderView.ResizeMode.Interactive, QHeaderView.ResizeMode.Fixed,
                                       QHeaderView.ResizeMode.Fixed, QHeaderView.ResizeMode.Fixed]):
            header.setSectionResizeMode(column, mode)
        for column, width in ((1, 52), (4, 110), (5, 48), (6, 70), (7, 128)):
            header.resizeSection(column, width)
        self.setStyleSheet("QTableView::item { padding: 0 4px; }")

    def set_songs(self, songs: list[Song], keep_order: bool = False) -> None:
        self.source.set_songs(songs)
        if keep_order:  # playlists and albums: show their own order until a column is clicked
            self.proxy.sort(-1)
            self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def song_ids_in_view(self) -> list[int]:
        return [self.model().index(row, 0).data(SONG_ID_ROLE) for row in range(self.model().rowCount())]

    def selected_ids(self) -> list[int]:
        rows = sorted(self.selectionModel().selectedRows(), key=lambda index: index.row())
        return [index.data(SONG_ID_ROLE) for index in rows]

    def in_own_order(self) -> bool:
        return self.horizontalHeader().sortIndicatorSection() == -1 or not self.horizontalHeader().isSortIndicatorShown()

    def _can_reorder(self, event) -> bool:
        return self.reorderable and event.source() is self and bool(song_ids_from(event.mimeData()))

    def dragEnterEvent(self, event):  # noqa: N802
        if self._can_reorder(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        if self._can_reorder(event):
            super().dragMoveEvent(event)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802
        if not self._can_reorder(event):
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else self.model().rowCount()
        if index.isValid() and event.position().y() > self.visualRect(index).center().y():
            row += 1
        event.setDropAction(Qt.DropAction.CopyAction)  # the list itself is rebuilt after saving
        event.accept()
        self.reorder_requested.emit(song_ids_from(event.mimeData()), row)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_pressed.emit()
            return
        super().keyPressEvent(event)


# --- album grid ------------------------------------------------------------------------------------

COVER = 132
COVER_ROLE = Qt.ItemDataRole.UserRole + 20
ALBUM_ROLE = Qt.ItemDataRole.UserRole + 21


def placeholder_cover(size: int) -> QPixmap:
    """Grey gradient with a note, like iTunes' "no artwork" square."""
    pixmap = QPixmap(size, size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0, QColor("#f2f2f2"))
    gradient.setColorAt(1, QColor("#c9c9c9"))
    painter.fillRect(0, 0, size, size, gradient)
    font = QFont()
    font.setPixelSize(int(size * 0.5))
    painter.setFont(font)
    painter.setPen(QColor("#a5a5a5"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "♫")
    painter.end()
    return pixmap


class AlbumDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # noqa: N802
        return QSize(COVER + 28, COVER + 48)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(option.rect)
        cover_rect = QRectF(rect.center().x() - COVER / 2, rect.top() + 8, COVER, COVER)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.SELECT_TOP).lighter(150))
            painter.drawRoundedRect(cover_rect.adjusted(-6, -6, 6, 6), 6, 6)
        # Soft shadow, then the cover with a thin dark edge.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 45))
        painter.drawRect(cover_rect.translated(0, 2))
        pixmap = index.data(COVER_ROLE)
        if isinstance(pixmap, QPixmap):
            painter.drawPixmap(cover_rect.toRect(), pixmap)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(cover_rect)
        album: Album = index.data(ALBUM_ROLE)
        title_font = QFont(option.font)
        title_font.setPixelSize(11)
        title_font.setBold(True)
        artist_font = QFont(option.font)
        artist_font.setPixelSize(11)
        text_width = int(rect.width() - 8)
        painter.setFont(title_font)
        painter.setPen(QColor(theme.c("text")))
        painter.drawText(QRectF(rect.left() + 4, cover_rect.bottom() + 5, text_width, 15), Qt.AlignmentFlag.AlignHCenter,
                         QFontMetrics(title_font).elidedText(album.title, Qt.TextElideMode.ElideRight, text_width))
        painter.setFont(artist_font)
        painter.setPen(QColor(theme.c("dim")))
        painter.drawText(QRectF(rect.left() + 4, cover_rect.bottom() + 20, text_width, 15), Qt.AlignmentFlag.AlignHCenter,
                         QFontMetrics(artist_font).elidedText(album.artist, Qt.TextElideMode.ElideRight, text_width))
        painter.restore()


class AlbumGrid(QListView):
    album_chosen = Signal(object)  # Album

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = QStandardItemModel(self)
        self.setModel(self.model_)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(8)
        self.setItemDelegate(AlbumDelegate(self))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("albumGrid")  # background from the theme
        self.placeholder = placeholder_cover(COVER)
        self.albums: list[Album] = []
        self.clicked.connect(self._chosen)

    def set_albums(self, albums: list[Album]) -> None:
        self.model_.clear()
        self.albums = albums
        for album in albums:
            item = QStandardItem()
            item.setData(album, ALBUM_ROLE)
            item.setData(self.placeholder, COVER_ROLE)
            item.setData(f"{album.title}\n{album.artist}", Qt.ItemDataRole.ToolTipRole)
            item.setEditable(False)
            self.model_.appendRow(item)

    def set_cover(self, row: int, image: QImage) -> None:
        item = self.model_.item(row)
        if item is not None:
            item.setData(QPixmap.fromImage(image).scaled(COVER, COVER, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation), COVER_ROLE)

    def filter(self, text: str) -> None:
        words = text.casefold().split()
        for row, album in enumerate(self.albums):
            haystack = f"{album.title} {album.artist}".casefold()
            self.setRowHidden(row, bool(words) and not all(w in haystack for w in words))

    def _chosen(self, index) -> None:
        self.album_chosen.emit(index.data(ALBUM_ROLE))


class AlbumsPage(QSplitter):
    """Covers on top (a grid, or Cover Flow), the chosen album's songs underneath."""

    def __init__(self, parent=None):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget

        from synclassic.gui.coverflow import CoverFlow

        super().__init__(Qt.Orientation.Vertical, parent)
        top = QWidget()
        column = QVBoxLayout(top)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        switch = QHBoxLayout()
        switch.setContentsMargins(8, 4, 8, 4)
        switch.addStretch(1)
        self.view_buttons = {}
        for name, label in (("grid", "Grid"), ("flow", "Cover Flow")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setFixedHeight(22)
            button.clicked.connect(lambda _=False, n=name: self.show_view(n))
            switch.addWidget(button)
            self.view_buttons[name] = button
        column.addLayout(switch)
        self.stack = QStackedWidget()
        self.grid = AlbumGrid()
        self.flow = CoverFlow()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.flow)
        column.addWidget(self.stack, 1)
        self.songs = SongTable()
        self.addWidget(top)
        self.addWidget(self.songs)
        self.setStretchFactor(0, 3)
        self.setStretchFactor(1, 2)
        self.contents: Contents | None = None
        self.grid.album_chosen.connect(self._show_album)
        self.flow.current_changed.connect(lambda index: self._show_album(self.grid.albums[index]))
        self.show_view("grid")

    def show_view(self, name: str) -> None:
        self.stack.setCurrentWidget(self.flow if name == "flow" else self.grid)
        for key, button in self.view_buttons.items():
            button.setChecked(key == name)
        if name == "flow":
            self.flow.view.setFocus()
            current = self.flow.view.current()
            if current is not None and self.grid.albums:
                self._show_album(self.grid.albums[current])

    def set_cover(self, row: int, image: QImage) -> None:
        self.grid.set_cover(row, image)
        self.flow.set_image(row, image)

    def filter(self, text: str) -> None:
        self.grid.filter(text)
        self.flow.filter(text)

    def set_contents(self, contents: Contents) -> None:
        self.contents = contents
        albums = contents.albums()
        self.grid.set_albums(albums)
        self.flow.set_items([(album.title, album.artist) for album in albums])
        self.songs.set_songs([])

    def _show_album(self, album: Album) -> None:
        if self.contents:
            self.songs.set_songs([self.contents.songs[i] for i in album.song_ids], keep_order=True)


# --- artist / genre browser --------------------------------------------------------------------------


class BrowserPage(QSplitter):
    """iTunes' browser: a list of artists (or genres) on the left, their songs on the right."""

    def __init__(self, title: str, attribute: str, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.attribute = attribute
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setAlternatingRowColors(True)
        self.list.setStyleSheet("QListWidget::item { padding: 2px 6px; }")
        self.list.currentRowChanged.connect(self._chosen)
        from PySide6.QtWidgets import QLabel

        header = QLabel(f"  {title}")
        header.setFixedHeight(20)
        header.setProperty("role", "listHeader")  # looks like a column header (theme)
        layout.addWidget(header)
        layout.addWidget(self.list)
        self.songs = SongTable()
        self.addWidget(left)
        self.addWidget(self.songs)
        self.setSizes([230, 800])
        self.setStretchFactor(1, 1)
        self.groups: dict[str, list[int]] = {}
        self.contents: Contents | None = None

    def set_contents(self, contents: Contents) -> None:
        self.contents = contents
        self.groups = contents.grouped(self.attribute)
        self.list.clear()
        all_item = QListWidgetItem(f"All ({len(self.groups)} {'Artists' if self.attribute == 'artist' else 'Genres'})")
        self.list.addItem(all_item)
        for name in self.groups:
            self.list.addItem(QListWidgetItem(name))
        self.list.setCurrentRow(0)

    def filter(self, text: str) -> None:
        self.songs.proxy.set_query(text)

    def _chosen(self, row: int) -> None:
        if self.contents is None or row < 0:
            return
        if row == 0:
            ids = [i for ids in self.groups.values() for i in ids]
        else:
            ids = self.groups[self.list.item(row).text()]
        songs = [self.contents.songs[i] for i in ids]
        songs.sort(key=lambda s: (s.album.casefold(), s.disc_number, s.track_number))
        self.songs.set_songs(songs, keep_order=True)
