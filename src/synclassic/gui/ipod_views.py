"""Views of what's on the iPod: a sortable song list, an album-cover grid, an artist/genre browser."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, QSortFilterProxyModel, Qt, Signal
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
SORT_ROLE = Qt.ItemDataRole.UserRole + 10
SONG_ID_ROLE = Qt.ItemDataRole.UserRole + 11


def _time(ms: int) -> str:
    seconds = round(ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds < 3600 else f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class SongsModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.songs: list[Song] = []

    def set_songs(self, songs: list[Song]) -> None:
        self.beginResetModel()
        self.songs = songs
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.songs)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(COLUMNS)

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
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column == 6:
            return QColor("#4a4a4a")
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


class SongTable(QTableView):
    """The striped, sortable song list used everywhere on the iPod side."""

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.setStyleSheet("QTableView::item { border-right: 1px solid %s; padding: 0 4px; }" % theme.GRID)

    def set_songs(self, songs: list[Song], keep_order: bool = False) -> None:
        self.source.set_songs(songs)
        if keep_order:  # playlists and albums: show their own order until a column is clicked
            self.proxy.sort(-1)
            self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def selected_ids(self) -> list[int]:
        return [index.data(SONG_ID_ROLE) for index in self.selectionModel().selectedRows()]


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
        painter.setPen(QColor("#000000"))
        painter.drawText(QRectF(rect.left() + 4, cover_rect.bottom() + 5, text_width, 15), Qt.AlignmentFlag.AlignHCenter,
                         QFontMetrics(title_font).elidedText(album.title, Qt.TextElideMode.ElideRight, text_width))
        painter.setFont(artist_font)
        painter.setPen(QColor("#5e5e5e"))
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
        self.setStyleSheet("QListView { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #eef1f5); }")
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
    """Cover grid on top, the chosen album's songs underneath."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.grid = AlbumGrid()
        self.songs = SongTable()
        self.addWidget(self.grid)
        self.addWidget(self.songs)
        self.setStretchFactor(0, 3)
        self.setStretchFactor(1, 2)
        self.contents: Contents | None = None
        self.grid.album_chosen.connect(self._show_album)

    def set_contents(self, contents: Contents) -> None:
        self.contents = contents
        self.grid.set_albums(contents.albums())
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
        header.setStyleSheet(
            "border-bottom: 1px solid #8d8d8d; border-right: 1px solid #b9b9b9;"
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:0.5 #f1f1f1, stop:0.51 #e4e4e4, stop:1 #eeeeee);"
        )
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
