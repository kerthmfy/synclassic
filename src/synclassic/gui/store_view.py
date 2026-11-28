"""Find Music: search songs and albums, Get them (they download like playlist songs)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synclassic.gui.picker_dialog import fmt_time
from synclassic.gui.workers import run_in_background
from synclassic.sources import store

THUMB = 40
TILE = 150


LAST_IMAGE_PROBLEM: list[str] = []  # why the last cover couldn't be shown (for the status line)


def _decode(data: bytes) -> QImage:
    """Qt's decoder first; Pillow for anything it can't read (WebP, unusual JPEGs)."""
    image = QImage.fromData(data)
    if not image.isNull():
        return image
    try:
        import io

        from PIL import Image

        picture = Image.open(io.BytesIO(data)).convert("RGB")
        raw = picture.tobytes()
        return QImage(raw, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()
    except Exception:
        return QImage()


def _fetch_images(urls: list[str], size: int) -> list[tuple[int, QImage]]:
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = store.USER_AGENT
    images = []
    LAST_IMAGE_PROBLEM.clear()
    for index, url in enumerate(urls):
        if not url:
            continue
        for candidate in dict.fromkeys((store.artwork(url, size), url)):  # the sized link, else the original
            try:
                response = session.get(candidate, timeout=15)
            except requests.RequestException as error:
                LAST_IMAGE_PROBLEM[:] = [f"covers couldn't be downloaded ({error.__class__.__name__})"]
                continue
            if response.status_code != 200:
                LAST_IMAGE_PROBLEM[:] = [f"the cover server answered HTTP {response.status_code}"]
                continue
            image = _decode(response.content)
            if image.isNull():
                LAST_IMAGE_PROBLEM[:] = ["a cover couldn't be read"]
                continue
            images.append((index, image))
            break
    return images


def _icon(image: QImage, size: int) -> QIcon:
    pixmap = QPixmap.fromImage(image.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class FindMusicPage(QWidget):
    get_song = Signal(object)  # StoreSong
    get_album = Signal(object, list)  # StoreAlbum, [StoreSong]

    def __init__(self, country: Callable[[], str], in_library: Callable[[object], bool], parent=None):
        super().__init__(parent)
        self.country = country
        self.in_library = in_library  # StoreSong -> already in the library?
        self.songs: list[store.StoreSong] = []
        self.albums: list[store.StoreAlbum] = []
        self.album: store.StoreAlbum | None = None
        self.album_songs: list[store.StoreSong] = []
        self._generation = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 6)

        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setProperty("search", True)
        self.query.setPlaceholderText("Search for a song, album or artist")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        self.kind_buttons = {}
        for kind, label in (("songs", "Songs"), ("albums", "Albums")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=kind: self.set_kind(k))
            row.addWidget(button)
            self.kind_buttons[kind] = button
        from synclassic import destinations
        from synclassic.gui.add_dialog import destination_box

        row.addSpacing(10)
        row.addWidget(QLabel("Save to:"))
        self.save_to = destination_box()  # for what you Get here (songs share one list, albums each have their own)
        self.save_to.setCurrentIndex(max(0, self.save_to.findData(destinations.default())))
        row.addWidget(self.save_to)
        layout.addLayout(row)
        self.status = QLabel("Search the music catalogue, then Get a song or a whole album. It downloads like your "
                             "playlists' songs, to this Mac or straight to your iPod (Save to).")
        self.status.setWordWrap(True)
        self.status.setProperty("role", "dim")
        self.status.setStyleSheet("padding: 4px 2px;")
        layout.addWidget(self.status)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        # Songs.
        self.song_list = QTreeWidget()
        self.song_list.setHeaderLabels(["", "Name", "Artist", "Album", "Time", ""])
        self.song_list.setRootIsDecorated(False)
        self.song_list.setAlternatingRowColors(True)
        self.song_list.setIconSize(QSize(THUMB, THUMB))
        self.song_list.setFrameShape(QFrame.Shape.NoFrame)
        header = self.song_list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, THUMB + 12)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(5, 96)
        self.stack.addWidget(self.song_list)
        # Albums.
        self.album_grid = QListWidget()
        self.album_grid.setViewMode(QListView.ViewMode.IconMode)
        self.album_grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.album_grid.setMovement(QListView.Movement.Static)
        self.album_grid.setIconSize(QSize(TILE, TILE))
        self.album_grid.setGridSize(QSize(TILE + 30, TILE + 56))
        self.album_grid.setWordWrap(True)
        self.album_grid.setFrameShape(QFrame.Shape.NoFrame)
        self.album_grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.album_grid.itemClicked.connect(lambda item: self.open_album(self.albums[item.data(Qt.ItemDataRole.UserRole)]))
        self.stack.addWidget(self.album_grid)
        # One album.
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.album_cover = QLabel()
        self.album_cover.setFixedSize(170, 170)
        self.album_cover.setStyleSheet("background: #ddd; border: 1px solid #aaa;")
        top.addWidget(self.album_cover)
        info = QVBoxLayout()
        self.album_title = QLabel()
        font = QFont(self.font())
        font.setPixelSize(20)
        font.setBold(True)
        self.album_title.setFont(font)
        self.album_title.setWordWrap(True)
        self.album_info = QLabel()
        self.album_info.setProperty("role", "dim")
        info.addWidget(self.album_title)
        info.addWidget(self.album_info)
        buttons = QHBoxLayout()
        self.get_album_button = QPushButton("Get Album")
        self.get_album_button.setProperty("aqua", True)
        self.get_album_button.clicked.connect(self._get_album)
        back = QPushButton("‹ Back to Albums")
        back.clicked.connect(lambda: self.set_kind("albums"))
        buttons.addWidget(self.get_album_button)
        buttons.addWidget(back)
        buttons.addStretch(1)
        info.addLayout(buttons)
        info.addStretch(1)
        top.addLayout(info, 1)
        column.addLayout(top)
        self.track_list = QTreeWidget()
        self.track_list.setHeaderLabels(["#", "Name", "Artist", "Time", ""])
        self.track_list.setRootIsDecorated(False)
        self.track_list.setAlternatingRowColors(True)
        header = self.track_list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(4, 96)
        column.addWidget(self.track_list, 1)
        self.stack.addWidget(page)
        self.set_kind("songs")

    # --- searching ------------------------------------------------------------------------------

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        for name, button in self.kind_buttons.items():
            button.setChecked(name == kind)
        self.stack.setCurrentIndex(0 if kind == "songs" else 1)
        if self.query.text().strip() and ((kind == "songs" and not self.songs) or (kind == "albums" and not self.albums)):
            self.search()

    def search(self) -> None:
        term = self.query.text().strip()
        if not term:
            return
        self._generation += 1
        generation, kind, country = self._generation, self.kind, self.country()
        self.status.setText(f"Searching for “{term}”…")

        def work(task):
            if kind == "songs":
                found = store.search_songs(term, country)
            else:
                found = store.search_albums(term, country)
            return found

        def done(found):
            if generation != self._generation:
                return
            if kind == "songs":
                self.show_songs(found)
            else:
                self.show_albums(found)
            self.status.setText(f"{len(found)} {kind} for “{term}”" if found else f"Nothing found for “{term}”. "
                                "Try fewer or other words.")

        run_in_background(self, work, done, lambda error: self.status.setText(str(error)))

    def _load_images(self, urls: list[str], size: int, show, still_current) -> None:
        """Load covers in the background; `still_current()` says whether the list they're for is
        still the one shown (a newer search replaces it; switching tabs or opening an album doesn't)."""

        def done(images):
            if not still_current():
                return
            for index, image in images:
                show(index, image)
            if LAST_IMAGE_PROBLEM and len(images) < sum(1 for u in urls if u):
                self.status.setText(self.status.text().split("  (")[0] + f"  ({LAST_IMAGE_PROBLEM[0]})")

        run_in_background(self, lambda task: _fetch_images(urls, size), done,
                          lambda error: self.status.setText(f"Covers couldn't load: {error}"))

    def _get_button(self, song: store.StoreSong) -> QPushButton:
        button = QPushButton("In Library" if self.in_library(song) else "Get")
        button.setFixedHeight(22)
        button.setEnabled(button.text() == "Get")

        def clicked():
            button.setText("Getting…")
            button.setEnabled(False)
            self.get_song.emit(song)

        button.clicked.connect(clicked)
        return button

    def show_songs(self, songs: list[store.StoreSong]) -> None:
        self.songs = songs
        self.song_list.clear()
        for song in songs:
            row = QTreeWidgetItem(["", song.title + (" (Explicit)" if song.explicit else ""), song.artist, song.album,
                                   fmt_time(song.duration_ms / 1000) if song.duration_ms else "", ""])
            row.setSizeHint(0, QSize(THUMB, THUMB + 6))
            self.song_list.addTopLevelItem(row)
            self.song_list.setItemWidget(row, 5, self._get_button(song))
        self._load_images([s.artwork for s in songs], 100,
                          lambda i, image: self.song_list.topLevelItem(i).setIcon(0, _icon(image, THUMB)),
                          lambda: self.songs is songs)

    def show_albums(self, albums: list[store.StoreAlbum]) -> None:
        self.albums = albums
        self.album_grid.clear()
        for index, album in enumerate(albums):
            item = QListWidgetItem(f"{album.title}\n{album.artist}" + (f" · {album.year}" if album.year else ""))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            from synclassic.gui.ipod_views import placeholder_cover

            item.setIcon(QIcon(placeholder_cover(TILE)))
            self.album_grid.addItem(item)
        self._load_images([a.artwork for a in albums], 300,
                          lambda i, image: self.album_grid.item(i).setIcon(_icon(image, TILE)),
                          lambda: self.albums is albums)

    # --- one album ------------------------------------------------------------------------------

    def open_album(self, album: store.StoreAlbum) -> None:
        self.album, self.album_songs = album, []
        self.album_title.setText(album.title)
        self.album_info.setText(" · ".join(p for p in (album.artist, album.year, album.genre,
                                                        f"{album.track_count} songs") if p))
        self.track_list.clear()
        self.get_album_button.setEnabled(False)
        self.album_cover.setPixmap(QPixmap())
        self.stack.setCurrentIndex(2)
        country = self.country()
        self._generation += 1
        generation = self._generation

        def done(result):
            if generation != self._generation:
                return
            _, songs = result
            self.album_songs = songs
            for song in songs:
                row = QTreeWidgetItem([str(song.track_number), song.title, song.artist,
                                       fmt_time(song.duration_ms / 1000) if song.duration_ms else "", ""])
                self.track_list.addTopLevelItem(row)
                self.track_list.setItemWidget(row, 4, self._get_button(song))
            missing = [s for s in songs if not self.in_library(s)]
            self.get_album_button.setEnabled(bool(missing))
            self.get_album_button.setText("Get Album" if missing else "In Library")

        run_in_background(self, lambda task: store.album_tracks(album.id, country), done,
                          lambda error: self.status.setText(str(error)))
        self._load_images([album.artwork], 600, lambda i, image: self.album_cover.setPixmap(
            QPixmap.fromImage(image.scaled(170, 170, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))),
            lambda: self.album is album)

    def _get_album(self) -> None:
        if self.album is None or not self.album_songs:
            return
        self.get_album_button.setText("Getting…")
        self.get_album_button.setEnabled(False)
        self.get_album.emit(self.album, list(self.album_songs))
