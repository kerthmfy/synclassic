"""The main Synclassic window, in the style of iTunes 7 on Mac OS X (see gui/theme.py)."""

from __future__ import annotations

import pathlib
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synclassic import __version__, playlist_writer
from synclassic.config import app_dir, load_settings
from synclassic.gui import theme
from synclassic.gui.add_dialog import AddDialog
from synclassic.gui.full_reader_bridge import FullReaderBridge
from synclassic.gui.picker_dialog import fmt_time, open_picker
from synclassic.gui.settings_dialog import SettingsDialog
from synclassic.gui.ipod_views import AlbumsPage, BrowserPage, SongTable
from synclassic.gui.player import Player, PlayItem
from synclassic.gui.podcasts_view import PodcastsPage
from synclassic.gui.widgets import (
    COUNT_ROLE,
    DEPTH_ROLE,
    ICON_ROLE,
    SECTION_ROLE,
    CapacityBar,
    GlossyButton,
    GradientBar,
    IPodGraphic,
    LcdDisplay,
    SourceList,
    SourceListDelegate,
    StatusPillDelegate,
    speaker_pixmap,
)
from synclassic.gui.workers import Task, run_in_background
from synclassic.library import Library, LibraryPlaylist, LibraryTrack
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyClient
from synclassic.update import add_inputs, download_missing, finalize, refresh_playlist

ATTENTION = "__attention__"
IPOD = "__ipod__"
PODCASTS = "__podcasts__"
SPECIAL = (None, ATTENTION, IPOD, PODCASTS)  # sources that aren't Synclassic playlists
ATTENTION_STATUSES = ("review", "unmatched", "failed")

STATUS = {
    "downloaded": "Ready",
    "local": "Mac file",
    "review": "Check",
    "pending": "Waiting",
    "unmatched": "Not found",
    "failed": "Failed",
    "missing": "File missing",
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


def _human_size(size: int) -> str:
    return f"{size / 1000**3:.1f} GB" if size >= 1000**3 else f"{size / 1000**2:.0f} MB"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Synclassic")
        self.resize(1180, 740)
        self.setMinimumSize(900, 560)
        self.library_path = app_dir() / "library.db"
        self.library = Library(self.library_path)
        self.job = None  # the running background update, if any
        self.current_key: object = None  # selected source: playlist id, ATTENTION or IPOD
        self.reader_bridge = FullReaderBridge(self)
        self.ipod = None  # the connected iPod, if any
        self._ipod_root = None
        self.ipod_contents = None  # what's on it (synclassic.ipod.browse.Contents), loaded in the background
        self._backed_up: set = set()  # iPods backed up this session (edits back up once per session)
        self._pending_key = None  # a source to select as soon as it appears (e.g. a new iPod playlist)
        self._playing_key = None
        self.player = Player(self)
        self.player.track_changed.connect(self._track_changed)
        self.player.position_changed.connect(self._position_changed)
        self.player.playing_changed.connect(self._playing_changed)
        self.player.error.connect(self.say)
        self.player.finished_item.connect(self._finished_playing)
        self._downloading_episodes: set[int] = set()
        self._cover_cache: dict[int, tuple[float, object]] = {}

        self._build_actions()
        self._build_menus()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_body(), 1)
        layout.addWidget(self._build_bottom_bar())
        self.setCentralWidget(central)

        # Refresh views at most a few times per second while downloads report progress.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(300)
        self._refresh_timer.timeout.connect(self.refresh)
        # Notice when an iPod is plugged in or ejected.
        self._ipod_timer = QTimer(self)
        self._ipod_timer.setInterval(3000)
        self._ipod_timer.timeout.connect(self._detect_ipod)
        self._ipod_timer.start()
        self._detect_ipod()

        self.refresh()

    # --- actions and menus ---------------------------------------------------------------------

    def _build_actions(self) -> None:
        def action(text, slot, shortcut=None, tip=None):
            act = QAction(text, self)
            act.triggered.connect(slot)
            if shortcut:
                act.setShortcut(shortcut)
            if tip:
                act.setToolTip(tip)
            return act

        self.add_action = action("Add Playlists…", self.add_playlists, QKeySequence.StandardKey.New)
        self.subscribe_action = action("Subscribe to Podcast…", self.subscribe_podcast, None,
                                       "Find a podcast, or paste its feed address")
        self.podcasts_update_action = action("Update Podcasts", lambda: self.update_podcasts(), None,
                                             "Check every podcast for new episodes and download them")
        self.update_action = action("Update All", lambda: self.start_update(None), QKeySequence("Ctrl+R"),
                                    "Check every playlist for new songs and download them")
        self.stop_action = action("Stop", self.stop_job, QKeySequence("Ctrl+."))
        self.stop_action.setEnabled(False)
        self.sync_action = action("Sync iPod", self.sync_ipod, QKeySequence("Ctrl+Shift+S"),
                                  "Put your playlists on your iPod (shows what will change first)")
        self.update_sync_action = action("Update && Sync", self.update_and_sync, QKeySequence("Ctrl+Shift+U"),
                                         "Check Spotify, download what's new, then sync your iPod")
        self.ipod_action = action("Check iPod", self.check_ipod, None,
                                  "Read your iPod and check that Synclassic can manage it (changes nothing)")
        self.eject_action = action("Eject iPod", self.eject_ipod, QKeySequence("Ctrl+E"))
        self.new_ipod_playlist_action = action("New Playlist on iPod…", lambda: self.new_ipod_playlist([]),
                                               QKeySequence("Ctrl+Shift+N"))
        self.import_ipod_playlist_action = action("Import Playlist to iPod…", self.import_ipod_playlist)
        self.info_action = action("Get Info", self.get_info, QKeySequence("Ctrl+I"))
        self.backups_action = action("Backups…", self.show_backups)
        self.cleanup_action = action("Clean Up…", self.show_cleanup)
        self.soundcheck_action = action("Sound Check for Other Songs…", self.soundcheck_other_songs, None,
                                        "Measure songs Synclassic didn't add so Sound Check evens them out too")
        self.fix_art_action = action("Fix Album Art…", self.show_fix_art, None,
                                     "Find missing or pixelated covers on the iPod and put sharp ones on")
        self.scrobble_action = action("Scrobble to Last.fm", lambda: self.scrobble_now())
        self.folder_action = action("Show Music Folder", lambda: reveal_in_finder(load_settings().music_path))
        self.settings_action = action("Settings…", self.open_settings, QKeySequence.StandardKey.Preferences)
        self.play_action = action("Play", lambda: self.play_pause(), QKeySequence(Qt.Key.Key_Space))
        self.next_action = action("Next Song", lambda: self.player.next(), QKeySequence("Ctrl+Right"))
        self.previous_action = action("Previous Song", lambda: self.player.previous(), QKeySequence("Ctrl+Left"))
        self.shuffle_action = action("Shuffle", lambda: self._set_shuffle(self.shuffle_action.isChecked()))
        self.shuffle_action.setCheckable(True)
        self.repeat_action = action("Repeat: Off", lambda: self._cycle_repeat())
        self.log_action = action("Show Activity", self._toggle_log, QKeySequence("Ctrl+L"))
        self.log_action.setCheckable(True)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        for act in (self.add_action, self.subscribe_action, None, self.update_action, self.podcasts_update_action, self.stop_action, None, self.folder_action,
                    self.fix_art_action, None, self.settings_action):
            file_menu.addSeparator() if act is None else file_menu.addAction(act)
        ipod_menu = bar.addMenu("iPod")
        for act in (self.update_sync_action, self.sync_action, None, self.new_ipod_playlist_action,
                    self.import_ipod_playlist_action, self.info_action, None, self.scrobble_action, None,
                    self.backups_action, self.cleanup_action, self.fix_art_action, self.soundcheck_action,
                    self.ipod_action, None, self.eject_action):
            ipod_menu.addSeparator() if act is None else ipod_menu.addAction(act)
        controls = bar.addMenu("Controls")
        for act in (self.play_action, self.next_action, self.previous_action, None, self.shuffle_action, self.repeat_action):
            controls.addSeparator() if act is None else controls.addAction(act)
        view_menu = bar.addMenu("View")
        view_menu.addAction(self.log_action)
        help_menu = bar.addMenu("Help")
        about = QAction("About Synclassic", self)
        about.triggered.connect(lambda: QMessageBox.about(
            self, "About Synclassic", f"<b>Synclassic {__version__}</b><br>Your Spotify playlists on your iPod."))
        help_menu.addAction(about)

    # --- layout -----------------------------------------------------------------------------

    def _glossy(self, symbol: str, label: str, action: QAction, diameter: int = 34) -> GlossyButton:
        button = GlossyButton(symbol, label, diameter)
        button.clicked.connect(action.trigger)
        button.setToolTip(action.toolTip() or label)
        button.setEnabled(action.isEnabled())
        action.changed.connect(lambda: button.setEnabled(action.isEnabled()))
        return button

    def _build_toolbar(self) -> QWidget:
        bar = GradientBar(theme.TOOLBAR_TOP, theme.TOOLBAR_BOTTOM, line_at_bottom=True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(4)
        from PySide6.QtWidgets import QSlider

        self.prev_button = self._glossy("prev", "", self.previous_action, 30)
        self.play_button = self._glossy("play", "", self.play_action, 40)
        self.next_button = self._glossy("next", "", self.next_action, 30)
        for button in (self.prev_button, self.play_button, self.next_button):
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        volume = QHBoxLayout()
        volume.setSpacing(3)
        quiet, loud = QLabel(), QLabel()
        quiet.setPixmap(speaker_pixmap(0, 12))
        loud.setPixmap(speaker_pixmap(2, 12))
        for label in (quiet, loud):
            label.setStyleSheet("background: transparent;")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(80)
        self.volume.setStyleSheet(
            "QSlider { background: transparent; }"
            "QSlider::groove:horizontal { height: 4px; border: 1px solid #6e6e6e; border-radius: 2px; background: #f4f4f4; }"
            "QSlider::sub-page:horizontal { border-radius: 2px; background: #8d8d8d; }"
            "QSlider::handle:horizontal { width: 12px; margin: -5px 0; border-radius: 6px; border: 1px solid #5b5b5b;"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #b9b9b9); }"
        )
        self.volume.valueChanged.connect(self.player.set_volume)
        volume.addWidget(quiet)
        volume.addWidget(self.volume)
        volume.addWidget(loud)
        row.addSpacing(6)
        row.addLayout(volume)
        row.addSpacing(18)
        for symbol, label, act in (("plus", "Add", self.add_action), ("refresh", "Update", self.update_action),
                                   ("stop", "Stop", self.stop_action)):
            row.addWidget(self._glossy(symbol, label, act, 30))
        row.addStretch(1)
        self.lcd = LcdDisplay()
        self.lcd.setMinimumWidth(340)
        self.lcd.setMaximumWidth(460)
        self.lcd.seek.sliderReleased.connect(lambda: self.player.seek(self.lcd.seek.value()))
        row.addWidget(self.lcd)
        row.addStretch(1)
        self.search = QLineEdit()
        self.search.setProperty("search", True)
        self.search.setPlaceholderText("Search")
        self.search.setFixedWidth(170)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self._apply_search())
        search_box = QVBoxLayout()
        search_box.setSpacing(2)
        search_box.addStretch(1)
        search_box.addWidget(self.search)
        caption = QLabel("Search")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        small = QFont(caption.font())
        small.setPixelSize(10)
        caption.setFont(small)
        caption.setStyleSheet("background: transparent;")
        search_box.addWidget(caption)
        row.addLayout(search_box)
        row.addSpacing(6)
        row.addWidget(self._glossy("sync", "Update & Sync", self.update_sync_action, 36))
        # The LCD is the status display; these names are what the rest of the window updates.
        self.status_label = self.lcd.primary
        self.progress = self.lcd.bar
        return bar

    def _build_body(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        self.sidebar = SourceList()
        self.sidebar.accepts_drop = self._accepts_song_drop
        self.sidebar.songs_dropped.connect(self._songs_dropped)
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setIndentation(0)
        self.sidebar.setItemDelegate(SourceListDelegate(self.sidebar))
        self.sidebar.setFrameShape(QFrame.Shape.NoFrame)
        self.sidebar.setMinimumWidth(190)
        self.sidebar.setStyleSheet(f"QTreeWidget {{ background: {theme.SOURCE_LIST}; border: none; }}")
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        source_column = QWidget()
        source_column.setStyleSheet(f"background: {theme.SOURCE_LIST};")
        column = QVBoxLayout(source_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.sidebar, 1)
        self.artwork_title = QLabel("  Now Playing")
        self.artwork_title.setFixedHeight(20)
        self.artwork_title.setStyleSheet(
            "color: #3b3b3b; font-size: 11px; border-top: 1px solid #9aa5b3;"
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #eef1f5, stop:1 #c9d1db);"
        )
        self.artwork = QLabel()
        self.artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artwork.setMinimumHeight(150)
        self.artwork.setStyleSheet("background: #1f1f1f;")
        for widget in (self.artwork_title, self.artwork):
            widget.hide()
            column.addWidget(widget)
        splitter.addWidget(source_column)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Thin strip above the song list: playlist name, where it comes from, Update.
        self.header_strip = GradientBar("#f4f4f4", "#dcdcdc", line_at_bottom=True)
        strip = QHBoxLayout(self.header_strip)
        strip.setContentsMargins(12, 5, 10, 5)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel()
        title_font = QFont(self.font())
        title_font.setPixelSize(15)
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet("color: #5b5b5b; font-size: 11px;")
        self.subtitle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for label in (self.title, self.subtitle):
            label.setStyleSheet(label.styleSheet() + "background: transparent;")
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        strip.addLayout(text, 1)
        self.update_one = QPushButton("Update Playlist")
        self.update_one.clicked.connect(self._update_current)
        strip.addWidget(self.update_one)
        self.subscribe_button = QPushButton("Subscribe…")
        self.subscribe_button.clicked.connect(self.subscribe_action.trigger)
        self.podcasts_button = QPushButton("Update Podcasts")
        self.podcasts_button.clicked.connect(self.podcasts_update_action.trigger)
        for button in (self.subscribe_button, self.podcasts_button):
            button.hide()
            strip.addWidget(button)
        right_layout.addWidget(self.header_strip)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("background: #fff6d5; color: #6b4b00; border-bottom: 1px solid #e3cf8a; padding: 4px 12px;")
        right_layout.addWidget(self.note)

        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setHeaderLabels(["#", "Name", "Artist", "Album", "Time", "Status"])
        self.table.setItemDelegateForColumn(5, StatusPillDelegate(self.table))
        header = self.table.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(5, 118)
        self.table.itemDoubleClicked.connect(lambda *_: self._library_double_clicked())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)  # 0
        self.stack.addWidget(self._build_empty_page())  # 1
        self.stack.addWidget(self._build_ipod_page())  # 2
        self.ipod_songs = SongTable()
        self.stack.addWidget(self.ipod_songs)  # 3: Music / Podcasts / an iPod playlist
        self.albums_page = AlbumsPage()
        self.stack.addWidget(self.albums_page)  # 4
        self.artists_page = BrowserPage("Artist", "artist")
        self.stack.addWidget(self.artists_page)  # 5
        self.genres_page = BrowserPage("Genre", "genre")
        self.stack.addWidget(self.genres_page)  # 6
        self.podcasts_page = PodcastsPage()
        self.podcasts_page.episode_activated.connect(self._play_episode)
        self.podcasts_page.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.podcasts_page.customContextMenuRequested.connect(self._podcasts_menu)
        self.stack.addWidget(self.podcasts_page)  # 7
        for table in self._ipod_tables():
            table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            table.customContextMenuRequested.connect(lambda pos, t=table: self._ipod_song_menu(t, pos))
            table.delete_pressed.connect(lambda t=table: self._delete_pressed(t))
        self.ipod_songs.reorder_requested.connect(self._reorder)
        for table in self._ipod_tables():
            table.doubleClicked.connect(lambda index, t=table: self._play_from_table(t, index.row()))
        right_layout.addWidget(self.stack, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("Activity will show here.")
        self.log.setStyleSheet("QPlainTextEdit { font-family: Monaco, Menlo, monospace; font-size: 11px; border: none;"
                               " border-top: 1px solid #a3a3a3; }")
        self.log.setFixedHeight(130)
        self.log.hide()
        right_layout.addWidget(self.log)

        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([210, 970])
        return splitter

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: #ffffff;")
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        welcome = QLabel("Add your first playlist")
        welcome.setStyleSheet("font-size: 20px; font-weight: bold; color: #5b5b5b;")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explain = QLabel("Paste a Spotify link, pick from your Spotify account,\nor import an .m3u8 / .csv file.")
        explain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explain.setStyleSheet("color: #7a7a7a;")
        add_button = QPushButton("Add Playlists…")
        add_button.setProperty("aqua", True)
        add_button.clicked.connect(self.add_playlists)
        layout.addWidget(welcome)
        layout.addWidget(explain)
        layout.addSpacing(10)
        layout.addWidget(add_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(2)
        return page

    def _build_ipod_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("ipodPage")
        page.setStyleSheet("#ipodPage { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #fbfbfb, stop:1 #e6e6e6); }")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 18, 24, 12)
        top = QHBoxLayout()
        self.ipod_graphic = IPodGraphic()
        self.ipod_graphic.setFixedSize(190, 300)
        top.addWidget(self.ipod_graphic)
        top.addSpacing(18)
        info = QVBoxLayout()
        self.ipod_name = QLabel("iPod")
        name_font = QFont(self.font())
        name_font.setPixelSize(20)
        name_font.setBold(True)
        self.ipod_name.setFont(name_font)
        info.addWidget(self.ipod_name)
        summary = QFrame()
        summary.setObjectName("summary")
        summary.setStyleSheet("#summary { background: #ffffff; border: 1px solid #b9b9b9; border-radius: 6px; }")
        form = QVBoxLayout(summary)
        form.setContentsMargins(14, 10, 14, 10)
        self.ipod_details = QLabel("")
        self.ipod_details.setTextFormat(Qt.TextFormat.RichText)
        self.ipod_details.setStyleSheet("background: transparent;")
        form.addWidget(self.ipod_details)
        info.addWidget(summary)
        buttons = QHBoxLayout()
        self.ipod_sync_button = QPushButton("Update && Sync")
        self.ipod_sync_button.setProperty("aqua", True)
        self.ipod_sync_button.setMinimumWidth(130)
        self.ipod_sync_button.clicked.connect(self.update_and_sync)
        sync_only = QPushButton("Sync Only")
        sync_only.clicked.connect(lambda: self.sync_ipod())
        check = QPushButton("Check iPod")
        check.clicked.connect(self.check_ipod)
        eject = QPushButton("Eject")
        eject.clicked.connect(self.eject_ipod)
        buttons.addWidget(self.ipod_sync_button)
        buttons.addWidget(sync_only)
        buttons.addWidget(check)
        buttons.addWidget(eject)
        buttons.addStretch(1)
        info.addLayout(buttons)
        maintenance = QHBoxLayout()
        for text, slot in (("Backups…", self.show_backups), ("Clean Up…", self.show_cleanup),
                           ("Fix Album Art…", self.show_fix_art)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            maintenance.addWidget(button)
        self.scrobble_button = QPushButton("Scrobble Now")
        self.scrobble_button.setToolTip("Send the plays logged on your iPod to Last.fm")
        self.scrobble_button.clicked.connect(lambda: self.scrobble_now())
        maintenance.addWidget(self.scrobble_button)
        maintenance.addStretch(1)
        info.addLayout(maintenance)
        details_box = QFrame()
        details_box.setObjectName("details")
        details_box.setStyleSheet("#details { background: rgba(255,255,255,150); border: 1px solid #c9c9c9; border-radius: 6px; }")
        details_layout = QVBoxLayout(details_box)
        details_layout.setContentsMargins(14, 8, 14, 8)
        details_title = QLabel("<b>Technical Details</b>")
        details_title.setStyleSheet("background: transparent;")
        self.ipod_tech = QLabel("")
        self.ipod_tech.setTextFormat(Qt.TextFormat.RichText)
        self.ipod_tech.setStyleSheet("background: transparent; color: #3b3b3b; font-size: 11px;")
        self.ipod_tech.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(details_title)
        details_layout.addWidget(self.ipod_tech)
        info.addWidget(details_box)
        info.addStretch(1)
        top.addLayout(info, 1)
        layout.addLayout(top, 1)
        self.capacity = CapacityBar()
        layout.addWidget(self.capacity)
        return page

    def _build_bottom_bar(self) -> QWidget:
        bar = GradientBar(theme.BOTTOMBAR_TOP, theme.BOTTOMBAR_BOTTOM, line_at_bottom=False)
        bar.setFixedHeight(30)
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 3, 8, 3)

        def square(text: str, tip: str, slot) -> QPushButton:
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setFixedSize(28, 22)
            button.setStyleSheet(
                "QPushButton { border-radius: 3px; padding: 0; font-weight: bold; border: 1px solid #6e6e6e;"
                " background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #fbfbfb, stop:0.5 #e2e2e2, stop:0.51 #d3d3d3, stop:1 #ececec); }"
                " QPushButton:pressed { background: #b9b9b9; }"
            )
            button.clicked.connect(slot)
            return button

        row.addWidget(square("+", "Add playlists", self.add_playlists))
        row.addWidget(square("⚙", "Settings", self.open_settings))
        row.addWidget(square("▤", "Show music folder", self.folder_action.trigger))
        row.addSpacing(10)
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon

        from synclassic.gui.widgets import control_pixmap

        self.shuffle_button = square("", "Shuffle", lambda: self.shuffle_action.trigger())
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setIcon(QIcon(control_pixmap("shuffle")))
        self.repeat_button = square("", "Repeat: Off", lambda: self.repeat_action.trigger())
        self.repeat_button.setCheckable(True)
        self.repeat_button.setIcon(QIcon(control_pixmap("repeat")))
        for button in (self.shuffle_button, self.repeat_button):
            button.setIconSize(QSize(16, 16))
        for button in (self.shuffle_button, self.repeat_button):
            button.setStyleSheet(button.styleSheet() + " QPushButton:checked { color: #1f5fbf;"
                                 " background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #c9dcf5, stop:1 #a8c4ea); }")
            row.addWidget(button)
        row.addStretch(1)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("background: transparent; color: #1f1f1f; font-size: 11px;")
        row.addWidget(self.summary_label)
        row.addStretch(1)
        self.log_button = square("≡", "Show activity", lambda: self.log_action.trigger())
        row.addWidget(self.log_button)
        return bar

    def _toggle_log(self) -> None:
        self.log.setVisible(self.log_action.isChecked())

    # --- refreshing views ---------------------------------------------------------------------

    def schedule_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def sidebar_entries(self) -> list[str]:
        """Source list entries (not headings), as shown: 'Name' or 'Name (count)'."""
        entries = []
        for i in range(self.sidebar.topLevelItemCount()):
            item = self.sidebar.topLevelItem(i)
            if not item.data(0, SECTION_ROLE):
                count = item.data(0, COUNT_ROLE)
                entries.append(item.text(0) + (f" ({count})" if count else ""))
        return entries

    def _add_section(self, title: str) -> None:
        item = QTreeWidgetItem([title])
        item.setData(0, SECTION_ROLE, True)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.sidebar.addTopLevelItem(item)

    def _add_source(self, text: str, key, icon: str, count=None, tip: str = "", depth: int = 0) -> None:
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setData(0, ICON_ROLE, icon)
        item.setData(0, DEPTH_ROLE, depth)
        if count:
            item.setData(0, COUNT_ROLE, count)
        if tip:
            item.setToolTip(0, tip)
        self.sidebar.addTopLevelItem(item)

    def refresh(self) -> None:
        playlists = self.library.playlists()
        all_tracks = self.library.tracks_in_playlists()
        attention = [i for i in all_tracks if i.status in ATTENTION_STATUSES]

        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        self._add_section("Library")
        self._add_source("Podcasts", PODCASTS, "podcasts", self._podcasts().new_count() or None,
                         "Podcasts you subscribe to (the number: new episodes)")
        if attention:
            self._add_source("Needs Attention", ATTENTION, "attention", len(attention),
                             "Songs that weren't found or might be the wrong version")
        if playlists or self.ipod is None:
            self._add_section("Playlists")
        for playlist in playlists:
            counts = self.library.counts(playlist)
            total = sum(counts.values())
            ready = counts.get("downloaded", 0) + counts.get("review", 0) + counts.get("local", 0)
            waiting = total - ready
            self._add_source(playlist.name, playlist.id, "playlist", waiting or None,
                             f"{ready} of {total} songs ready · {playlist.spec.describe()}")
        if self.ipod is not None:
            self._add_section("Devices")
            contents = self.ipod_contents
            self._add_source(contents.name if contents else self.ipod.name, IPOD, "ipod", None, str(self.ipod.root))
            if contents is not None:
                self._add_source("Music", ("ipod", "music"), "music", depth=1)
                self._add_source("Albums", ("ipod", "albums"), "albums", depth=1)
                self._add_source("Artists", ("ipod", "artists"), "artists", depth=1)
                self._add_source("Genres", ("ipod", "genres"), "genres", depth=1)
                if contents.songs_of_kind("podcast"):
                    self._add_source("Podcasts", ("ipod", "podcasts"), "podcasts", depth=1)
                for playlist in contents.playlists:
                    tip = "Smart playlist" if playlist.smart else ("Managed by Synclassic" if playlist.synclassic else "")
                    self._add_source(playlist.name, ("ipodpl", playlist.id), "smart" if playlist.smart else "playlist",
                                     tip=tip, depth=1)
        # Keep the selection (or select the one asked for, once it exists).
        target = None
        first = None
        pending = None
        for i in range(self.sidebar.topLevelItemCount()):
            item = self.sidebar.topLevelItem(i)
            if item.data(0, SECTION_ROLE):
                continue
            first = first or item
            if item.data(0, Qt.ItemDataRole.UserRole) == self.current_key:
                target = item
            if self._pending_key is not None and item.data(0, Qt.ItemDataRole.UserRole) == self._pending_key:
                pending = item
        if pending is not None:
            target = pending
            self._pending_key = None
        target = target or first
        if target is not None:
            self.sidebar.setCurrentItem(target)
            self.current_key = target.data(0, Qt.ItemDataRole.UserRole)
        else:
            self.current_key = None
        self.sidebar.blockSignals(False)
        self._show_current(attention)

        songs = len(all_tracks)
        hours = sum((i.track.duration_ms or 0) for i in all_tracks) / 3_600_000
        size = sum(pathlib.Path(i.file_path).stat().st_size for i in all_tracks if i.has_file)
        self.summary_label.setText(
            f"{len(playlists)} playlists, {songs} songs, {hours:.1f} hours, {_human_size(size)}" if songs else ""
        )
        if self.job is None:
            self._idle_lcd()
        self._update_scrobble_count()

    def _idle_lcd(self) -> None:
        if self.lcd.bar.isVisible():
            return
        item = self.player.current
        if item is not None:
            self.lcd.show_now_playing(item.title, item.line)
            return
        if not self.status_label.text() or self.status_label.text() in ("Ready", "Synclassic"):
            self.status_label.setText("Synclassic")
        ipod = f"{self.ipod.name} connected" if self.ipod is not None else "No iPod connected"
        self.lcd.secondary.setText(f"{ipod}")

    def _sidebar_changed(self, current, previous) -> None:
        if current is not None and current.data(0, SECTION_ROLE):
            return
        self.current_key = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self._show_current()

    def _current_playlist(self) -> LibraryPlaylist | None:
        if self.current_key in SPECIAL:
            return None
        return next((p for p in self.library.playlists() if p.id == self.current_key), None)

    def _show_current(self, attention: list[LibraryTrack] | None = None) -> None:
        if self.current_key is None:
            self.stack.setCurrentIndex(1)
            self.header_strip.hide()
            self.note.hide()
            return
        if self.current_key == IPOD:
            self.header_strip.hide()
            self.note.hide()
            self.stack.setCurrentIndex(2)
            self._show_ipod()
            return
        if isinstance(self.current_key, tuple):
            self._show_podcast_buttons(False)
            self._show_ipod_view(self.current_key)
            return
        if self.current_key == PODCASTS:
            self._show_podcasts()
            return
        self._show_podcast_buttons(False)
        self.header_strip.show()
        self.stack.setCurrentIndex(0)
        if self.current_key == ATTENTION:
            items = attention if attention is not None else [
                i for i in self.library.tracks_in_playlists() if i.status in ATTENTION_STATUSES
            ]
            self.title.setText("Needs Attention")
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
            label = STATUS.get(state, state)
            if item.manual and state == "downloaded":
                label = "Your pick"
            row = QTreeWidgetItem([str(number), track.title, track.artist, track.album,
                                   fmt_time((track.duration_ms or 0) / 1000), label])
            row.setData(0, Qt.ItemDataRole.UserRole, item.id)
            row.setTextAlignment(0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.setForeground(0, Qt.GlobalColor.gray)
            if self._playing_key == ("lib", item.id):
                row.setIcon(0, self._speaker_icon())
            tip = item.error or item.match_notes or ""
            if tip:
                row.setToolTip(5, tip)
            rows.append(row)
        self.table.addTopLevelItems(rows)
        for row in rows:
            if row.data(0, Qt.ItemDataRole.UserRole) in selected:
                row.setSelected(True)
        self.table.verticalScrollBar().setValue(scroll)
        self._apply_search()

    def _show_ipod_view(self, key: tuple) -> None:
        contents = self.ipod_contents
        if contents is None:
            return
        self.note.hide()
        self.update_one.hide()
        self.header_strip.show()
        kind, which = key
        name = contents.name
        if kind == "ipodpl":
            playlist = next((p for p in contents.playlists if p.id == which), None)
            if playlist is None:
                return
            songs = [contents.songs[i] for i in playlist.song_ids if i in contents.songs]
            self.title.setText(playlist.name)
            what = "smart playlist" if playlist.smart else "playlist"
            managed = " · kept in sync by Synclassic" if playlist.synclassic else ""
            self.subtitle.setText(f"{len(songs)} songs · {self._length(songs)} · {what} on {name}{managed}")
            self.ipod_songs.set_songs(songs, keep_order=True)
            self.ipod_songs.reorderable = self._editable_playlist(playlist)
            self.stack.setCurrentIndex(3)
        elif which in ("music", "podcasts"):
            kind_name = "song" if which == "music" else "podcast"
            songs = [contents.songs[i] for i in contents.songs_of_kind(kind_name)]
            self.title.setText("Music" if which == "music" else "Podcasts")
            self.subtitle.setText(f"{len(songs):,} {'songs' if which == 'music' else 'episodes'} · {self._length(songs)} · on {name}")
            self.ipod_songs.set_songs(songs)
            self.ipod_songs.reorderable = False
            if which == "music":
                self.ipod_songs.sortByColumn(2, Qt.SortOrder.AscendingOrder)
            self.stack.setCurrentIndex(3)
        elif which == "albums":
            self.title.setText("Albums")
            self.subtitle.setText(f"{len(self.albums_page.grid.albums):,} albums on {name} · click a cover to see its songs")
            self.stack.setCurrentIndex(4)
        elif which in ("artists", "genres"):
            page = self.artists_page if which == "artists" else self.genres_page
            self.title.setText("Artists" if which == "artists" else "Genres")
            self.subtitle.setText(f"{len(page.groups):,} {which} on {name}")
            self.stack.setCurrentIndex(5 if which == "artists" else 6)
        self._apply_search()

    @staticmethod
    def _length(songs) -> str:
        minutes = sum(s.length_ms for s in songs) // 60000
        if minutes >= 24 * 60:
            return f"{minutes / 1440:.1f} days"
        return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"

    # --- managing the iPod -------------------------------------------------------------------------

    def _ipod_tables(self):
        return [self.ipod_songs, self.albums_page.songs, self.artists_page.songs, self.genres_page.songs]

    def _editable_playlist(self, playlist) -> bool:
        return not playlist.smart and not playlist.synclassic

    def _ipod_playlist(self, playlist_id):
        contents = self.ipod_contents
        return next((p for p in contents.playlists if p.id == playlist_id), None) if contents else None

    def _current_ipod_playlist(self):
        key = self.current_key
        if isinstance(key, tuple) and key[0] == "ipodpl" and self.stack.currentIndex() == 3:
            return self._ipod_playlist(key[1])
        return None

    def _why_not_editable(self, playlist) -> str:
        if playlist.smart:
            return f"“{playlist.name}” is a smart playlist: its songs come from its rules."
        return (f"“{playlist.name}” is kept in sync from Spotify by Synclassic, so changes here would be replaced "
                "at the next sync. Change it in Spotify, or remove it from Synclassic's PLAYLISTS.")

    def _ipod_edit(self, text: str, action, select=None, done_message: str = "") -> None:
        """Run a change to the iPod in the background, then reload what's on it."""
        ipod = self.ipod
        if ipod is None:
            return
        if self.job is not None:
            QMessageBox.information(self, "Busy", "Wait for the current task to finish first.")
            return
        make_backup = ipod.root not in self._backed_up

        def work(task):
            return action(ipod, make_backup)

        def finished(result):
            self._backed_up.add(ipod.root)
            if callable(select):
                key = select(result)
                if key is not None:
                    self._pending_key = key  # selected once the reloaded contents list it
            if done_message:
                message = done_message(result) if callable(done_message) else done_message
                self.say(message)
                # Shown after the window returns to idle (which would otherwise replace it).
                QTimer.singleShot(0, lambda: self.status_label.setText(message))
            self._load_ipod_contents()

        def failed(error):
            box_title = "Couldn't change the iPod"
            QMessageBox.warning(self, box_title, f"{error}\n\nNothing on the iPod was changed.")

        self._run_job(text, work, finished, verb="Saving")
        self.job.failed.connect(lambda error: failed(error))

    def _accepts_song_drop(self, key) -> bool:
        if isinstance(key, tuple) and key[0] == "ipodpl":
            playlist = self._ipod_playlist(key[1])
            return playlist is not None and self._editable_playlist(playlist)
        return False

    def _songs_dropped(self, key, ids) -> None:
        playlist = self._ipod_playlist(key[1])
        if playlist is not None:
            self.add_to_ipod_playlist(playlist, ids)

    def add_to_ipod_playlist(self, playlist, ids) -> None:
        from synclassic.ipod import manage

        if not self._editable_playlist(playlist):
            QMessageBox.information(self, "Can't change this playlist", self._why_not_editable(playlist))
            return
        self._ipod_edit(
            f"Adding to “{playlist.name}”…",
            lambda ipod, backup: manage.add_to_playlist(ipod, playlist.id, ids, backup),
            done_message=lambda added: f"Added {added} song{'s' if added != 1 else ''} to “{playlist.name}”",
        )

    def new_ipod_playlist(self, ids) -> None:
        from synclassic.ipod import manage

        if self.ipod is None or self.ipod_contents is None:
            QMessageBox.information(self, "No iPod", "Plug in your iPod first.")
            return
        name, ok = QInputDialog.getText(self, "New Playlist", "Name of the new playlist on your iPod:",
                                        text="untitled playlist")
        if not ok or not name.strip():
            return
        self._ipod_edit(
            "Creating playlist…",
            lambda ipod, backup: manage.create_playlist(ipod, name.strip(), list(ids), backup),
            select=lambda pid: ("ipodpl", pid),
            done_message=f"Made “{name.strip()}” on your iPod",
        )

    def import_ipod_playlist(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from synclassic.ipod import manage

        if self.ipod is None:
            QMessageBox.information(self, "No iPod", "Plug in your iPod first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import a playlist to your iPod", "", "Playlists (*.m3u8 *.m3u)")
        if not path:
            return

        def done(result):
            parts = [f"{result.matched} already on the iPod"]
            if result.copied:
                parts.append(f"{result.copied} copied")
            if result.missing:
                parts.append(f"{len(result.missing)} not found")
            if result.missing:
                QMessageBox.information(self, "Playlist imported", "Imported: " + ", ".join(parts) + ".\n\nNot found:\n"
                                        + "\n".join(result.missing[:25]) + ("\n…" if len(result.missing) > 25 else ""))
            return "Imported: " + ", ".join(parts)

        self._ipod_edit(
            "Importing playlist…",
            lambda ipod, backup: manage.import_m3u(ipod, pathlib.Path(path), make_backup=backup),
            select=lambda result: ("ipodpl", result.playlist_id),
            done_message=done,
        )

    def _ipod_source_menu(self, key, global_pos) -> None:
        from PySide6.QtWidgets import QFileDialog

        from synclassic.ipod import manage

        if self.ipod_contents is None:
            return
        menu = QMenu(self)
        new = menu.addAction("New Playlist…")
        imp = menu.addAction("Import Playlist (.m3u8)…")
        rename = delete = export = None
        playlist = self._ipod_playlist(key[1]) if isinstance(key, tuple) and key[0] == "ipodpl" else None
        if playlist is not None:
            menu.addSeparator()
            editable = self._editable_playlist(playlist)
            rename = menu.addAction("Rename…")
            delete = menu.addAction("Delete Playlist…")
            rename.setEnabled(editable)
            delete.setEnabled(editable or playlist.synclassic)  # smart playlists: neither
            if not editable:
                note = menu.addAction("Smart playlist (read-only)" if playlist.smart else "Synced from Spotify by Synclassic")
                note.setEnabled(False)
            menu.addSeparator()
            export = menu.addAction("Export as .m3u8…")
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == new:
            self.new_ipod_playlist([])
        elif chosen == imp:
            self.import_ipod_playlist()
        elif chosen == rename and playlist is not None:
            name, ok = QInputDialog.getText(self, "Rename Playlist", "New name:", text=playlist.name)
            if ok and name.strip() and name.strip() != playlist.name:
                self._ipod_edit("Renaming…", lambda ipod, backup: manage.rename_playlist(ipod, playlist.id, name.strip(), backup),
                                done_message=f"Renamed to “{name.strip()}”")
        elif chosen == delete and playlist is not None:
            self._delete_ipod_playlist(playlist)
        elif chosen == export and playlist is not None:
            target, _ = QFileDialog.getSaveFileName(self, "Export playlist", f"{playlist.name}.m3u8", "Playlists (*.m3u8)")
            if target:
                count = manage.export_m3u(self.ipod, playlist.id, pathlib.Path(target))
                self.status_label.setText(f"Exported {count} songs to {pathlib.Path(target).name}")

    def _delete_ipod_playlist(self, playlist) -> None:
        from synclassic.ipod import manage
        from synclassic.ipod import sync as ipod_sync

        library_playlist = None
        if playlist.synclassic and self.ipod is not None:
            # Is it still one of Synclassic's playlists? Then the next sync would bring it back.
            keys = {k for k, v in ipod_sync.load_state(self.ipod)["playlists"].items() if int(v["id"], 16) == playlist.id}
            library_playlist = next((p for p in self.library.playlists() if ipod_sync.playlist_key(p) in keys), None)
        if library_playlist is not None:
            question = (f"“{playlist.name}” is synced from Spotify by Synclassic. Remove it from Synclassic and delete "
                        "it from your iPod? (Otherwise the next sync would put it back.) Its songs stay on your Mac; "
                        "songs only Synclassic needed for it are removed from the iPod at the next sync.")
        else:
            question = f"Delete the playlist “{playlist.name}” from your iPod? Its songs stay on the iPod."
        if QMessageBox.question(self, "Delete Playlist", question) != QMessageBox.StandardButton.Yes:
            return
        if library_playlist is not None:
            self.library.remove_playlist(library_playlist)
            finalize(self.library)
        self._ipod_edit(
            "Deleting playlist…",
            lambda ipod, backup: manage.delete_playlist(ipod, playlist.id, backup, synced_too=True),
            select=lambda _: ("ipod", "music"),
            done_message=f"Deleted “{playlist.name}”",
        )

    def _ipod_song_menu(self, table, position) -> None:
        from synclassic.ipod import manage

        ids = table.selected_ids()
        if not ids or self.ipod_contents is None:
            return
        menu = QMenu(self)
        info = menu.addAction("Get Info…")
        menu.addSeparator()
        add_menu = menu.addMenu("Add to Playlist")
        new_from = add_menu.addAction("New Playlist from Selection…")
        add_menu.addSeparator()
        targets = {}
        for playlist in self.ipod_contents.playlists:
            if self._editable_playlist(playlist):
                targets[add_menu.addAction(playlist.name)] = playlist
        current = self._current_ipod_playlist() if table is self.ipod_songs else None
        remove = top = bottom = None
        if current is not None and self._editable_playlist(current):
            menu.addSeparator()
            remove = menu.addAction("Remove from Playlist")
            if table.in_own_order():
                top = menu.addAction("Move to Top")
                bottom = menu.addAction("Move to Bottom")
        menu.addSeparator()
        delete = menu.addAction("Delete from iPod…")
        reveal = menu.addAction("Show in Finder")
        reveal.setEnabled(len(ids) == 1)
        chosen = menu.exec(table.viewport().mapToGlobal(position))
        if chosen is None:
            return
        if chosen == info:
            self.get_info(table)
        elif chosen == new_from:
            self.new_ipod_playlist(ids)
        elif chosen in targets:
            self.add_to_ipod_playlist(targets[chosen], ids)
        elif chosen == remove:
            self._remove_from_playlist(current, ids)
        elif chosen in (top, bottom):
            rest = [i for i in current.song_ids if i not in set(ids)]
            order = (ids + rest) if chosen == top else (rest + ids)
            self._ipod_edit("Reordering…", lambda ipod, backup: manage.set_playlist_songs(ipod, current.id, order, backup))
        elif chosen == delete:
            self._delete_from_ipod(ids)
        elif chosen == reveal:
            song = self.ipod_contents.songs.get(ids[0])
            if song and self.ipod:
                reveal_in_finder(self.ipod.root / song.path)

    # --- playback ----------------------------------------------------------------------------------

    def _speaker_icon(self):
        from PySide6.QtGui import QIcon

        from synclassic.gui.widgets import speaker_icon

        if not hasattr(self, "_speaker_cache"):
            self._speaker_cache = speaker_icon()
        return self._speaker_cache

    def play_pause(self) -> None:
        if self.player.current is not None:
            self.player.toggle()
            return
        # Nothing loaded yet: play the selected song (or the list from the top).
        table = self._focused_ipod_table() if self.stack.currentIndex() in (3, 4, 5, 6) else None
        if table is not None:
            rows = sorted(i.row() for i in table.selectionModel().selectedRows())
            self._play_from_table(table, rows[0] if rows else 0)
        elif self.stack.currentIndex() == 0:
            self._library_double_clicked(start_at_selection=True)

    def _library_items(self) -> list[tuple[PlayItem, int]]:
        """The library list's playable songs, as (item, row)."""
        items = []
        for row in range(self.table.topLevelItemCount()):
            tree_item = self.table.topLevelItem(row)
            if tree_item.isHidden():
                continue
            track = self.library.track(tree_item.data(0, Qt.ItemDataRole.UserRole))
            if track is None or not track.has_file:
                continue
            t = track.track
            items.append((PlayItem(pathlib.Path(track.file_path), t.title, t.artist, t.album, t.album_artist,
                                   t.duration_ms or 0, ("lib", track.id)), row))
        return items

    def _library_double_clicked(self, start_at_selection: bool = False) -> None:
        current = self.table.currentItem()
        if current is None and not start_at_selection:
            return
        track = self.library.track(current.data(0, Qt.ItemDataRole.UserRole)) if current else None
        if self.current_key == ATTENTION or (track is not None and not track.has_file):
            self.pick_selected()  # nothing to play: choose a version instead
            return
        items = self._library_items()
        if not items:
            return
        rows = [row for _, row in items]
        start = rows.index(self.table.indexOfTopLevelItem(current)) if current and self.table.indexOfTopLevelItem(current) in rows else 0
        self.player.play_list([item for item, _ in items], start)

    def _play_from_table(self, table, row: int) -> None:
        contents, ipod = self.ipod_contents, self.ipod
        if contents is None or ipod is None:
            return
        ids = table.song_ids_in_view()
        items, start = [], 0
        for n, song_id in enumerate(ids):
            song = contents.songs.get(song_id)
            if song is None or not song.path:
                continue
            if n == row:
                start = len(items)
            items.append(PlayItem(ipod.root / song.path, song.title, song.artist, song.album, song.album_artist,
                                  song.length_ms, ("ipod", song.id), contents.covers.get(song.dbid),
                                  podcast=song.kind == "podcast"))
        self.player.play_list(items, start)

    def _set_shuffle(self, on: bool) -> None:
        self.player.set_shuffle(on)
        self.shuffle_button.setChecked(on)

    def _cycle_repeat(self) -> None:
        mode = self.player.cycle_repeat()
        label = {"off": "Repeat: Off", "all": "Repeat: All", "one": "Repeat: One"}[mode]
        self.repeat_action.setText(label)
        self.repeat_button.setChecked(mode != "off")
        from PySide6.QtGui import QIcon

        from synclassic.gui.widgets import control_pixmap

        self.repeat_button.setIcon(QIcon(control_pixmap("repeat_one" if mode == "one" else "repeat")))
        self.repeat_button.setToolTip(label)

    def _playing_changed(self, playing: bool) -> None:
        self.play_button.symbol = "pause" if playing else "play"
        self.play_button.update()
        self.play_action.setText("Pause" if playing else "Play")

    def _track_changed(self, item) -> None:
        self._playing_key = item.key if item is not None else None
        song_id = item.key[1] if item is not None and item.key and item.key[0] == "ipod" else None
        for table in self._ipod_tables():
            table.source.set_playing(song_id)
        for row in range(self.table.topLevelItemCount()):
            tree_item = self.table.topLevelItem(row)
            playing = self._playing_key == ("lib", tree_item.data(0, Qt.ItemDataRole.UserRole))
            from PySide6.QtGui import QIcon

            tree_item.setIcon(0, self._speaker_icon() if playing else QIcon())
        if item is None:
            self.lcd.hide_now_playing()
            self.artwork.hide()
            self.artwork_title.hide()
            self._playing_changed(False)
            if self.job is None:
                self.status_label.setText("Synclassic")
                self._idle_lcd()
            return
        if self.job is None:
            self.lcd.show_now_playing(item.title, item.line)
        self._load_artwork(item)

    def _position_changed(self, position: int, duration: int) -> None:
        if self.job is None:
            if not self.lcd.seek_row.isVisible() and self.player.current is not None:
                self._idle_lcd()
            self.lcd.set_times(position, duration)

    def _load_artwork(self, item) -> None:
        from PySide6.QtGui import QImage, QPixmap

        from synclassic.ipod import artwork as art
        from synclassic.ipod import browse

        ipod = self.ipod

        def work(task):
            data = art.embedded_cover(item.path)
            if data:
                image = QImage.fromData(data)
                if not image.isNull():
                    return image
            if item.cover_file is not None and item.cover_file.is_file():
                image = QImage(str(item.cover_file))
                if not image.isNull():
                    return image
            if item.cover and ipod is not None:
                picture = browse.cover(ipod, item.cover)
                if picture is not None:
                    raw = picture.tobytes()
                    return QImage(raw, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()
            return None

        def show(image):
            if self.player.current is not item:
                return
            self.artwork_title.show()
            self.artwork.show()
            width = max(120, self.sidebar.width())
            if image is None:
                from synclassic.gui.ipod_views import placeholder_cover

                pixmap = placeholder_cover(width)
            else:
                pixmap = QPixmap.fromImage(image).scaled(width, width, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation)
            self.artwork.setFixedHeight(pixmap.height())
            self.artwork.setPixmap(pixmap)

        run_in_background(self, work, show, lambda error: None)

    def _update_scrobble_count(self) -> None:
        from synclassic import scrobble

        try:
            count = scrobble.waiting()
        except OSError:
            count = 0
        self.scrobble_button.setText(f"Scrobble Now ({count})" if count else "Scrobble Now")
        self.scrobble_action.setText(f"Scrobble to Last.fm ({count} plays)" if count else "Scrobble to Last.fm")

    def scrobble_now(self, quiet: bool = False) -> None:
        """Send waiting plays to Last.fm. quiet=True (after a sync): no dialogs, LCD message only."""
        from synclassic import lastfm, scrobble

        if self.job is not None:
            return
        if not lastfm.connected_user():
            if not quiet:
                QMessageBox.information(self, "Last.fm", "Connect your Last.fm account in Settings first.")
            return
        if scrobble.waiting() == 0:
            if not quiet:
                self.status_label.setText("No plays to scrobble. Plays are picked up at each sync or change.")
            return

        def done(result):
            parts = [f"Scrobbled {result.accepted} play{'s' if result.accepted != 1 else ''} to Last.fm"]
            if result.ignored:
                parts.append(f"{result.ignored} not accepted")
            if result.too_old:
                parts.append(f"{result.too_old} too old (over 2 weeks)")
            message = ", ".join(parts)
            self.say(message + "".join(f"\n    {n} × {reason}" for reason, n in (result.reasons or {}).items()))
            QTimer.singleShot(0, lambda: (self.status_label.setText(message), self._update_scrobble_count()))

        self._run_job("Scrobbling to Last.fm…", lambda task: scrobble.submit(), done, verb="Scrobbling")

    def _focused_ipod_table(self):
        focus = QApplication.focusWidget()
        for table in self._ipod_tables():
            if focus is table:
                return table
        index = self.stack.currentIndex()
        return {3: self.ipod_songs, 4: self.albums_page.songs, 5: self.artists_page.songs,
                6: self.genres_page.songs}.get(index)

    def get_info(self, table=None) -> None:
        from synclassic.gui.maintenance_dialogs import InfoDialog
        from synclassic.ipod import manage

        table = table if table is not None and not isinstance(table, bool) else self._focused_ipod_table()
        if table is None or self.ipod_contents is None:
            return
        songs = [self.ipod_contents.songs[i] for i in table.selected_ids() if i in self.ipod_contents.songs]
        if not songs:
            return
        dialog = InfoDialog(songs, self)
        if dialog.exec() != InfoDialog.DialogCode.Accepted:
            return
        changes = dialog.changes()
        if not changes:
            return
        ids = [s.id for s in songs]
        self._ipod_edit(
            "Saving song info…",
            lambda ipod, backup: manage.edit_songs(ipod, ids, changes, backup),
            done_message=f"Updated {len(ids)} song{'s' if len(ids) != 1 else ''}",
        )

    def show_backups(self) -> None:
        from synclassic.gui.maintenance_dialogs import BackupsDialog

        if self.ipod is None:
            QMessageBox.information(self, "No iPod", "Plug in your iPod first.")
            return
        BackupsDialog(self, self.ipod).exec()

    def show_cleanup(self) -> None:
        from synclassic.gui.maintenance_dialogs import CleanupDialog

        if self.ipod is None:
            QMessageBox.information(self, "No iPod", "Plug in your iPod first.")
            return
        CleanupDialog(self, self.ipod).exec()

    def soundcheck_other_songs(self) -> None:
        from synclassic.ipod import soundcheck

        ipod = self.ipod
        if ipod is None:
            QMessageBox.information(self, "No iPod", "Plug in your iPod first.")
            return
        if self.job is not None:
            QMessageBox.information(self, "Busy", "Wait for the current task to finish first.")
            return
        count = len(soundcheck.missing(ipod))
        if not count:
            QMessageBox.information(self, "Sound Check", "Every song on the iPod already has Sound Check.")
            return
        minutes = max(1, round(count * 0.5 / 60))
        answer = QMessageBox.question(
            self, "Sound Check",
            f"Measure the loudness of {count} songs on the iPod that don't have Sound Check yet? This reads each "
            f"file once (about {minutes} min); you can stop any time and keep what was measured.\n\n"
            "Turn Sound Check on on the iPod too: Settings → Sound Check.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        make_backup = ipod.root not in self._backed_up

        def work(task):
            done = soundcheck.apply(ipod, make_backup, task.say, task.progress.emit, task.should_stop)
            return {"summary": f"Sound Check set for {done} songs."}

        def finished(result):
            self._backed_up.add(ipod.root)
            self._load_ipod_contents()

        self._run_job("Measuring loudness…", work, finished, verb="Measuring")

    def show_fix_art(self) -> None:
        from synclassic.gui.art_dialog import ArtFixDialog

        ArtFixDialog(self, self.ipod, self.library_path).exec()  # without an iPod: songs on this Mac

    def restore_ipod_backup(self, folder) -> None:
        from synclassic.ipod import manage

        ipod = self.ipod
        if ipod is None or self.job is not None:
            return

        def done(result):
            self._backed_up.add(ipod.root)
            QTimer.singleShot(0, lambda: self.status_label.setText("Backup restored"))
            self._load_ipod_contents()
            if self.current_key == IPOD:
                self._show_ipod()

        self._run_job("Restoring backup…", lambda task: manage.restore_backup(ipod, folder), done, verb="Restoring")

    def _remove_from_playlist(self, playlist, ids) -> None:
        from synclassic.ipod import manage

        remaining = [i for i in playlist.song_ids if i not in set(ids)]
        self._ipod_edit(
            "Removing from playlist…",
            lambda ipod, backup: manage.set_playlist_songs(ipod, playlist.id, remaining, backup),
            done_message=f"Removed {len(ids)} song{'s' if len(ids) != 1 else ''} from “{playlist.name}”",
        )

    def _delete_pressed(self, table) -> None:
        ids = table.selected_ids()
        if not ids:
            return
        current = self._current_ipod_playlist() if table is self.ipod_songs else None
        if current is not None:
            if self._editable_playlist(current):
                self._remove_from_playlist(current, ids)
            else:
                QMessageBox.information(self, "Can't change this playlist", self._why_not_editable(current))
        else:
            self._delete_from_ipod(ids)

    def _delete_from_ipod(self, ids) -> None:
        from synclassic.ipod import manage

        songs = [self.ipod_contents.songs[i] for i in ids if i in self.ipod_contents.songs]
        names = "\n".join(f"• {s.artist} – {s.title}" if s.artist else f"• {s.title}" for s in songs[:8])
        more = f"\n…and {len(songs) - 8} more" if len(songs) > 8 else ""
        answer = QMessageBox.question(
            self, "Delete from iPod",
            f"Delete {len(songs)} song{'s' if len(songs) != 1 else ''} from your iPod? They're removed from every "
            f"playlist and their files are deleted.\n\n{names}{more}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._ipod_edit(
            "Deleting from iPod…",
            lambda ipod, backup: manage.delete_songs(ipod, set(ids), backup),
            done_message=lambda count: f"Deleted {count} song{'s' if count != 1 else ''} from your iPod",
        )

    def _reorder(self, ids, row) -> None:
        from synclassic.ipod import manage

        playlist = self._current_ipod_playlist()
        if playlist is None or not self._editable_playlist(playlist):
            return
        shown = self.ipod_songs.song_ids_in_view()
        moving = set(ids)
        before = [i for i in shown[:row] if i not in moving]
        after = [i for i in shown[row:] if i not in moving]
        order = before + [i for i in shown if i in moving] + after
        if order == playlist.song_ids:
            return
        self._ipod_edit("Reordering…", lambda ipod, backup: manage.set_playlist_songs(ipod, playlist.id, order, backup))

    def _load_ipod_contents(self) -> None:
        """Read what's on the iPod (in the background), then show it in the source list."""
        ipod = self.ipod
        if ipod is None:
            self.ipod_contents = None
            return
        from synclassic.ipod import browse

        def done(contents):
            if self.ipod is not ipod:
                return
            self.ipod_contents = contents
            self.albums_page.set_contents(contents)
            self.artists_page.set_contents(contents)
            self.genres_page.set_contents(contents)
            self.refresh()
            self._load_covers(ipod, contents)

        run_in_background(self, lambda task: browse.read(ipod), done,
                          lambda error: self.say(f"Couldn't read the iPod's music: {error}"))

    def _load_covers(self, ipod, contents) -> None:
        from PySide6.QtGui import QImage

        from synclassic.ipod import browse

        albums = self.albums_page.grid.albums

        def work(task):
            images = []
            for row, album in enumerate(albums):
                location = contents.covers.get(album.cover_dbid) if album.cover_dbid else None
                if not location:
                    continue
                try:
                    picture = browse.cover(ipod, location)
                except OSError:
                    continue
                if picture is not None:
                    data = picture.tobytes()
                    image = QImage(data, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()
                    images.append((row, image))
            return images

        def done(images):
            if self.ipod_contents is not contents:
                return
            for row, image in images:
                self.albums_page.grid.set_cover(row, image)

        run_in_background(self, work, done, lambda error: None)

    def _apply_search(self) -> None:
        text = self.search.text()
        index = self.stack.currentIndex()
        if index == 3:
            self.ipod_songs.proxy.set_query(text)
        elif index == 4:
            self.albums_page.grid.filter(text)
            self.albums_page.songs.proxy.set_query(text)
        elif index in (5, 6):
            (self.artists_page if index == 5 else self.genres_page).filter(text)
        elif index == 7:
            self.podcasts_page.apply_search(text)
        words = self.search.text().casefold().split()
        for i in range(self.table.topLevelItemCount()):
            row = self.table.topLevelItem(i)
            haystack = " ".join(row.text(c) for c in (1, 2, 3)).casefold()
            row.setHidden(bool(words) and not all(w in haystack for w in words))

    # --- podcasts --------------------------------------------------------------------------------

    def _podcasts(self):
        from synclassic.podcasts.store import Podcasts

        return Podcasts(self.library)

    def _show_podcast_buttons(self, on: bool) -> None:
        self.subscribe_button.setVisible(on)
        self.podcasts_button.setVisible(on)

    def _podcast_cover(self, store, podcast):
        from PySide6.QtGui import QPixmap

        path = store.cover_path(podcast, load_settings().music_path)
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
        cached = self._cover_cache.get(podcast.id)
        if cached and cached[0] == stamp:
            return cached[1]
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        pixmap = pixmap.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(2)
        self._cover_cache[podcast.id] = (stamp, pixmap)
        return pixmap

    def _show_podcasts(self) -> None:
        store = self._podcasts()
        shows, covers = [], {}
        for podcast in store.podcasts():
            episodes = store.episodes(podcast.id)
            shows.append((podcast, episodes, {e.id for e in store.wanted(podcast)}))
            cover = self._podcast_cover(store, podcast)
            if cover is not None:
                covers[podcast.id] = cover
        self.header_strip.show()
        self.note.hide()
        self.update_one.hide()
        self._show_podcast_buttons(True)
        self.podcasts_button.setEnabled(self.job is None)
        self.title.setText("Podcasts")
        if shows:
            new = store.new_count()
            self.subtitle.setText(
                f"{len(shows)} podcast{'s' if len(shows) != 1 else ''} · {new} new episode{'s' if new != 1 else ''} · "
                "the newest unplayed episodes of each go on your iPod when you sync")
        else:
            self.subtitle.setText("No podcasts yet. Press Subscribe… to find one.")
        self.podcasts_page.fill(shows, covers, self._downloading_episodes)
        self.stack.setCurrentIndex(7)
        self._apply_search()

    def subscribe_podcast(self) -> None:
        from synclassic.gui.podcast_dialog import SubscribeDialog

        dialog = SubscribeDialog(self)
        if not dialog.exec() or not dialog.feed_urls:
            return
        urls = dialog.feed_urls
        path = self.library_path
        if self.job is not None:
            QMessageBox.information(self, "Busy", "Wait for the current update to finish, or press Stop.")
            return

        def work(task: Task):
            from synclassic.podcasts.store import Podcasts

            library = Library(path)
            try:
                store = Podcasts(library)
                titles = []
                for url in urls:
                    task.progress.emit(0, 0, "Reading the podcast…")
                    try:
                        podcast = store.subscribe(url)
                    except Exception as error:  # a bad feed shouldn't stop the others
                        task.say(f"Couldn't subscribe to {url}: {error}")
                        continue
                    titles.append(podcast.title)
                    task.say(f"Subscribed to {podcast.title}")
                    task.changed.emit()
                if titles:
                    _update_podcasts(library, task)
                return {"summary": f"Subscribed to {', '.join(titles)}." if titles else "Couldn't subscribe."}
            finally:
                library.close()

        self.current_key = PODCASTS
        self._run_job("Subscribing…", work)

    def update_podcasts(self, podcast_ids: list[int] | None = None) -> None:
        if self.job is not None:
            return
        if not isinstance(podcast_ids, list):  # e.g. a button's "checked" flag
            podcast_ids = None
        path = self.library_path

        def work(task: Task):
            library = Library(path)
            try:
                return {"summary": _update_podcasts(library, task, podcast_ids)}
            finally:
                library.close()

        self._run_job("Checking podcasts…", work)

    def download_episodes(self, episode_ids: list[int]) -> None:
        if self.job is not None or not episode_ids:
            return
        path = self.library_path
        music = load_settings().music_path
        self._downloading_episodes = set(episode_ids)

        def work(task: Task):
            from synclassic.podcasts.feed import FeedError
            from synclassic.podcasts.store import Podcasts

            library = Library(path)
            try:
                store = Podcasts(library)
                done = 0
                for number, episode_id in enumerate(episode_ids, start=1):
                    episode = store.episode(episode_id)
                    if task.should_stop():
                        break
                    if episode is None or episode.downloaded:
                        continue
                    task.progress.emit(number, len(episode_ids), episode.title)
                    try:
                        store.download(episode, music, should_stop=task.should_stop)
                        done += 1
                    except FeedError as error:
                        task.say(f"{episode.title}: {error}")
                    task.changed.emit()
                return {"summary": f"Downloaded {done} episode{'s' if done != 1 else ''}."}
            finally:
                library.close()

        def finished(result):
            self._downloading_episodes = set()

        self._run_job("Downloading episodes…", work, finished)

    def _episode_item(self, store, episode) -> PlayItem | None:
        podcast = store.podcast(episode.podcast_id)
        if podcast is None or not episode.downloaded:
            return None
        path = pathlib.Path(episode.file_path)
        return PlayItem(path, episode.title, podcast.author or podcast.title, podcast.title, podcast.author,
                        episode.duration_s * 1000, ("pod", episode.id), podcast=True,
                        cover_file=path.parent / "cover.jpg")

    def _play_episode(self, episode_id: int) -> None:
        store = self._podcasts()
        episode = store.episode(episode_id)
        if episode is None:
            return
        if not episode.downloaded:
            self.download_episodes([episode_id])
            return
        items = []
        start = 0
        for other_id in self.podcasts_page.episode_ids_in_view(episode.podcast_id):
            other = store.episode(other_id)
            item = self._episode_item(store, other) if other is not None else None
            if item is None:
                continue
            if other_id == episode_id:
                start = len(items)
            items.append(item)
        self.player.play_list(items, start)

    def _finished_playing(self, item) -> None:
        if item.key and item.key[0] == "pod":
            self._podcasts().set_played(item.key[1], True)
            self.schedule_refresh()

    def _podcasts_menu(self, position) -> None:
        from synclassic.podcasts.store import Podcasts

        store: Podcasts = self._podcasts()
        page = self.podcasts_page
        episodes = [e for e in (store.episode(i) for i in page.selected("episode")) if e is not None]
        shows = [p for p in (store.podcast(i) for i in page.selected("show")) if p is not None]
        if not episodes and not shows:
            menu = QMenu(self)
            menu.addAction(self.subscribe_action)
            menu.addAction(self.podcasts_update_action)
            menu.exec(page.viewport().mapToGlobal(position))
            return
        menu = QMenu(self)
        actions = {}
        if episodes:
            actions["play"] = menu.addAction("Play")
            actions["play"].setEnabled(any(e.downloaded for e in episodes))
            actions["download"] = menu.addAction("Download")
            actions["download"].setEnabled(self.job is None and any(not e.downloaded for e in episodes))
            menu.addSeparator()
            actions["played"] = menu.addAction("Mark as Played")
            actions["unplayed"] = menu.addAction("Mark as New")
            menu.addSeparator()
            actions["remove"] = menu.addAction("Remove Download")
            actions["remove"].setEnabled(any(e.downloaded for e in episodes))
            actions["reveal"] = menu.addAction("Show in Finder")
            actions["reveal"].setEnabled(len(episodes) == 1 and episodes[0].downloaded)
        if shows:
            if episodes:
                menu.addSeparator()
            actions["refresh"] = menu.addAction("Update Podcast")
            actions["refresh"].setEnabled(self.job is None)
            keep_menu = menu.addMenu("Keep on iPod")
            current = shows[0].keep_on_ipod if len(shows) == 1 else None
            for count, label in ((1, "Newest episode"), (3, "3 newest unplayed"), (5, "5 newest unplayed"),
                                 (10, "10 newest unplayed"), (0, "None (don't put on iPod)")):
                act = keep_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(current == count)
                actions[f"keep{count}"] = act
            actions["show_folder"] = menu.addAction("Show in Finder")
            menu.addSeparator()
            actions["unsubscribe"] = menu.addAction("Unsubscribe…")
        chosen = menu.exec(page.viewport().mapToGlobal(position))
        if chosen is None:
            return
        name = next((k for k, v in actions.items() if v is chosen), "")
        music = load_settings().music_path
        if name == "play":
            first = next(e for e in episodes if e.downloaded)
            self._play_episode(first.id)
        elif name == "download":
            self.download_episodes([e.id for e in episodes if not e.downloaded])
        elif name in ("played", "unplayed"):
            for episode in episodes:
                store.set_played(episode.id, name == "played")
            self.refresh()
        elif name == "remove":
            for episode in episodes:
                if episode.downloaded:
                    self.player.stop_if_from(pathlib.Path(episode.file_path))
                    store.remove_download(episode.id)
            self.refresh()
        elif name == "reveal":
            reveal_in_finder(pathlib.Path(episodes[0].file_path))
        elif name == "refresh":
            self.update_podcasts([p.id for p in shows])
        elif name.startswith("keep"):
            for podcast in shows:
                store.set_keep(podcast.id, int(name[4:]))
            self.refresh()
        elif name == "show_folder":
            reveal_in_finder(store.show_folder(shows[0], music))
        elif name == "unsubscribe":
            names = ", ".join(f"“{p.title}”" for p in shows)
            answer = QMessageBox.question(
                self, "Unsubscribe",
                f"Unsubscribe from {names}? Its downloaded episodes are deleted from your Mac. Episodes Synclassic "
                "put on your iPod come off at the next sync.")
            if answer == QMessageBox.StandardButton.Yes:
                for podcast in shows:
                    self.player.stop_if_from(store.show_folder(podcast, music))
                    store.unsubscribe(podcast.id)
                self.refresh()

    # --- iPod ------------------------------------------------------------------------------------

    def _detect_ipod(self) -> None:
        from synclassic.ipod.device import find_ipods

        try:
            ipods = find_ipods()
        except OSError:
            ipods = []
        ipod = ipods[0] if ipods else None
        root = ipod.root if ipod else None
        if root == self._ipod_root:
            return
        if self._ipod_root is not None and root != self._ipod_root:
            self.player.stop_if_from(self._ipod_root)
        self._ipod_root = root
        self.ipod = ipod
        self.ipod_contents = None
        if ipod is None and (self.current_key == IPOD or isinstance(self.current_key, tuple)):
            self.current_key = None
        self.refresh()
        self._load_ipod_contents()

    def _show_ipod(self) -> None:
        ipod = self.ipod
        if ipod is None:
            return
        display = self.ipod_contents.name if self.ipod_contents else ipod.name
        self.ipod_name.setText(display)
        self.ipod_graphic.set_screen(display, "Reading…")
        total, free = ipod.disk_usage()
        rows = [("Name", display), ("Model", ipod.model), ("Software Version", ipod.firmware or "unknown"),
                ("Capacity", f"{total / 1000**3:.1f} GB")]
        self._ipod_rows = rows
        self._render_ipod_details(rows)
        self.capacity.set_values(0, total - free, free)

        self.ipod_tech.setText("Checking…")
        self._load_ipod_details(ipod)

        def read(task):
            from synclassic.ipod import itunesdb

            db = itunesdb.parse(ipod.database_path.read_bytes())
            songs = itunesdb.songs(db)
            audio = sum(s.size for s in songs if s.media_type in (0, 1))
            playlists = [p for p in itunesdb.playlists(db, 2) if not p.is_master]
            return len(songs), audio, len(playlists)

        def show(result):
            count, audio, playlists = result
            self._render_ipod_details(self._ipod_rows + [("Songs", f"{count:,}"), ("Playlists", str(playlists))])
            self.capacity.set_values(audio, max(0, total - free - audio), free)
            self.ipod_graphic.set_screen(display, f"{count:,} songs\n{playlists} playlists")

        run_in_background(self, read, show, lambda error: self.ipod_graphic.set_screen(ipod.name, "Can't read music"))

    def _load_ipod_details(self, ipod) -> None:
        from synclassic.ipod import sync as ipod_sync
        from synclassic.ipod.check import check

        def work(task):
            report = check(ipod)
            state = ipod_sync.load_state(ipod)
            return report, state.get("last_sync"), len(ipod_sync.list_backups(ipod))

        def show(result):
            report, last_sync, backups = result
            yes = "<span style='color:#2e8b2e'>✓</span>"
            no = "<span style='color:#c63a3a'>✗</span>"
            rows = [
                ("Serial number", ipod.sysinfo.get("pszSerialNumber", "unknown")),
                ("Database", f"{report.database_size / 1024:,.0f} KB · sections {', '.join(map(str, report.sections))} · "
                             f"rebuilds exactly {yes if report.round_trip_ok else no}"),
                ("Library", f"{report.songs:,} songs, {report.videos} videos, {report.podcasts} podcast episodes"),
                ("Album artwork", f"{report.artwork_images:,} images, "
                                  + ", ".join(f"{w}×{h}" for _, w, h in report.artwork_sizes) if report.artwork_db else "none"),
                ("Leftover files", f"{report.unlisted_files}" + (" (Clean Up… can fix this)" if report.unlisted_files else "")),
                ("Missing files", f"{len(report.missing_files)}" + (" (Clean Up… can fix this)" if report.missing_files else "")),
                ("Plays not yet saved", "yes (saved at the next change or sync)" if report.play_counts_file else "no"),
                ("Last Synclassic sync", (last_sync or "never").replace("T", " ")),
                ("Backups", f"{backups} (Backups… to restore one)"),
                ("Safe to manage", yes if report.safe_to_manage else f"{no} " + "; ".join(report.problems)),
            ]
            cells = "".join(f"<tr><td style='color:#6b6b6b; padding:1px 10px 1px 0'>{k}</td><td>{v}</td></tr>" for k, v in rows)
            self.ipod_tech.setText(f"<table>{cells}</table>")

        run_in_background(self, work, show, lambda error: self.ipod_tech.setText(f"Couldn't check: {error}"))

    def _render_ipod_details(self, rows) -> None:
        cells = "".join(
            f"<tr><td align='right' style='color:#6b6b6b; padding:2px 10px 2px 0'>{k}:</td><td style='padding:2px 0'>{v}</td></tr>"
            for k, v in rows
        )
        self.ipod_details.setText(f"<table>{cells}</table>")

    def eject_ipod(self) -> None:
        from synclassic.ipod import sync as ipod_sync

        if self.ipod is None:
            QMessageBox.information(self, "No iPod", "There's no iPod connected.")
            return
        if self.job is not None:
            QMessageBox.information(self, "Busy", "Wait for the current task to finish first.")
            return
        self.player.stop_if_from(self.ipod.root)
        ipod = self.ipod
        self.status_label.setText("Ejecting…")
        self.lcd.secondary.setText(ipod.name)

        def finished(result, forced=False):
            self.say(result.message)
            if result.ok:
                self.status_label.setText("Ejected")
                self.lcd.secondary.setText("You can unplug the iPod.")
                self._detect_ipod()
                return
            self.status_label.setText("Couldn't eject the iPod")
            self.lcd.secondary.setText(result.blocked_by or "See Show Activity for details")
            if forced:
                QMessageBox.warning(self, "Couldn't eject", result.message)
                return
            who = (f"macOS's device helper ({result.blocked_by})" if result.blocked_by.startswith("AMP")
                   else (result.blocked_by or "Another app"))
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Couldn't eject")
            box.setText("The iPod is still in use, so it couldn't be ejected.")
            box.setInformativeText(
                f"{who} is holding it. That usually passes in a few seconds. Everything Synclassic "
                "wrote is already saved on the iPod, so forcing the eject is safe.")
            box.setDetailedText(result.message)
            force = box.addButton("Force Eject", QMessageBox.ButtonRole.DestructiveRole)
            again = box.addButton("Try Again", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(again)
            box.exec()
            if box.clickedButton() is again:
                QTimer.singleShot(0, self.eject_ipod)
            elif box.clickedButton() is force:
                self.status_label.setText("Ejecting…")
                run_in_background(self, lambda task: ipod_sync.eject_ipod(ipod, force=True),
                                  lambda r: finished(r, forced=True), lambda error: self.say(error))

        run_in_background(self, lambda task: ipod_sync.eject_ipod(ipod), finished, lambda error: self.say(error))

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
        if row is not None and not row.data(0, SECTION_ROLE):
            key = row.data(0, Qt.ItemDataRole.UserRole)
            if key == PODCASTS:
                menu = QMenu(self)
                menu.addAction(self.podcasts_update_action)
                menu.addAction(self.subscribe_action)
                menu.exec(self.sidebar.viewport().mapToGlobal(position))
                return
            if key == IPOD or isinstance(key, tuple):
                self._ipod_source_menu(key, self.sidebar.viewport().mapToGlobal(position))
                return
        if not row or row.data(0, SECTION_ROLE) or row.data(0, Qt.ItemDataRole.UserRole) in SPECIAL:
            return
        playlist = next((p for p in self.library.playlists() if p.id == row.data(0, Qt.ItemDataRole.UserRole)), None)
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
                finalize(self.library)
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
                finalize(self.library)
                self.current_key = None
                self.refresh()

    def _playlist_ids_for_view(self) -> list[int] | None:
        return None if self.current_key in SPECIAL else [self.current_key]

    # --- actions ------------------------------------------------------------------------------

    def pick_selected(self) -> None:
        ids = self._selected_track_ids()
        if len(ids) != 1:
            return
        if open_picker(self, self.library_path, ids[0]):
            finalize(self.library)
            self.say("Saved your pick.")
        self.refresh()

    def check_ipod(self) -> None:
        from synclassic.ipod.check import check, format_report
        from synclassic.ipod.device import find_ipods

        def work(task):
            ipods = find_ipods()
            if not ipods:
                return None
            report = check(ipods[0])
            return [(format_report(report), report.safe_to_manage)]

        def show(result):
            self.ipod_action.setEnabled(True)
            self.status_label.setText("Ready")
            if not result:
                QMessageBox.information(self, "No iPod found", "Plug in your iPod and wait until it appears in Finder, then try again.")
                return
            lines, safe = result[0]
            box = QMessageBox(self)
            box.setWindowTitle("iPod check")
            box.setIcon(QMessageBox.Icon.Information if safe else QMessageBox.Icon.Warning)
            box.setText("Your iPod looks good. Nothing was changed." if safe else "Synclassic found a problem. Nothing was changed.")
            box.setDetailedText("\n".join(lines))
            box.setInformativeText("\n".join(l for l in lines[:6] + lines[-2:]))
            box.exec()

        self.ipod_action.setEnabled(False)
        self.status_label.setText("Reading your iPod…")
        run_in_background(self, work, show, lambda error: (self.ipod_action.setEnabled(True), self._job_failed(error)))

    def update_and_sync(self) -> None:
        if self.job is not None:
            return

        def then():
            self._detect_ipod()
            if self.ipod is None:
                self.status_label.setText("Updated. Plug in your iPod to sync.")
                return
            self.sync_ipod(quick=True)

        self.start_update(None, after=then)

    def sync_ipod(self, quick: bool = False) -> None:
        """Preview, confirm and sync. quick=True (Update & Sync) skips the confirmation when the
        sync only adds things; removals and playlist take-overs are always shown first."""
        from synclassic.ipod import sync as ipod_sync
        from synclassic.ipod.check import check, format_report
        from synclassic.ipod.device import find_ipods

        if self.job is not None:
            QMessageBox.information(self, "Busy", "Wait for the current update to finish, or press Stop.")
            return
        path = self.library_path

        def prepare(task):
            ipods = find_ipods()
            if not ipods:
                return None
            report = check(ipods[0])
            if not report.safe_to_manage:
                return ("unsafe", format_report(report))
            library = Library(path)
            try:
                return ("plan", ipod_sync.make_plan(library, ipods[0]))
            finally:
                library.close()

        def show(result):
            self._set_idle("Ready")
            if result is None:
                QMessageBox.information(self, "No iPod found", "Plug in your iPod and wait until it appears in Finder, then try again.")
                return
            kind, value = result
            if kind == "unsafe":
                box = QMessageBox(QMessageBox.Icon.Warning, "Can't sync", "The iPod check found a problem, so nothing was changed.", parent=self)
                box.setDetailedText("\n".join(value))
                box.exec()
                return
            plan = value
            lines = plan.summary()
            if not plan.has_changes:
                if quick:
                    self.status_label.setText("Your iPod is up to date")
                    return
                QMessageBox.information(self, "iPod is up to date", "\n".join(lines[1:]))
                return
            if not plan.fits:
                QMessageBox.warning(self, "Not enough space", "\n".join(lines))
                return
            only_adds = not plan.remove_songs and not plan.remove_playlists and all(
                p.action != "take over" for p in plan.playlists
            )
            if quick and only_adds:
                self._apply_sync(plan)
                return
            box = QMessageBox(QMessageBox.Icon.Question, "Sync iPod", "\n".join(lines[:1] + lines[1:12]), parent=self)
            box.setInformativeText("The iPod's database is backed up first. Sync now?")
            if len(lines) > 12:
                box.setDetailedText("\n".join(lines))
            box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
            box.button(QMessageBox.StandardButton.Ok).setText("Sync")
            if box.exec() == QMessageBox.StandardButton.Ok:
                self._apply_sync(plan)

        self._set_busy("Reading your iPod…")
        self.stop_action.setEnabled(False)
        run_in_background(self, prepare, show, self._job_failed)

    def _apply_sync(self, plan) -> None:
        from synclassic.ipod import sync as ipod_sync

        path = self.library_path

        def work(task: Task):
            library = Library(path)
            try:
                ipod_sync.apply_plan(
                    library, plan, task.say, should_stop=task.should_stop,
                    progress=lambda n, total, text: task.progress.emit(n, total, text),
                )
                return {"summary": "iPod synced.", "ipod": plan.ipod}
            finally:
                library.close()

        def done(result):
            self._load_ipod_contents()
            if load_settings().scrobble_after_sync:
                QTimer.singleShot(50, lambda: self.scrobble_now(quiet=True))
            answer = QMessageBox.question(self, "Sync complete", "Your iPod is synced. Eject it now?")
            if answer == QMessageBox.StandardButton.Yes:
                self.say(ipod_sync.eject(result["ipod"]))

        self._run_job("Syncing iPod…", work, done, verb="Copying to iPod")

    def open_settings(self) -> None:
        before = load_settings().folder_layout
        SettingsDialog(self).exec()
        if load_settings().folder_layout != before:
            if self.job is not None:
                self.say("The new folder layout will be applied after the current update.")
                return
            self.status_label.setText("Arranging folders…")
            QApplication.processEvents()
            finalize(self.library)
            self.say(f"Folders arranged by {'playlist' if load_settings().folder_layout == 'playlist' else 'artist and album'}.")
            self.status_label.setText("Ready")
            self.refresh()

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
                added = add_inputs(library, inputs, name, client, task.say, self._full_reader())
                task.changed.emit()
                if added and download:
                    _download(library, task, added, retry_failed=False)
                finalize(library)
                return [p.id for p in added]
            finally:
                library.close()

        def done(ids):
            if ids:
                self.current_key = ids[0]

        self._run_job("Adding…", work, done)

    def _update_current(self) -> None:
        if self.current_key not in SPECIAL:
            self.start_update([self.current_key])

    def start_update(
        self, playlist_ids: list[int] | None, retry_failed: bool = False, refresh_sources: bool = True, after=None
    ) -> None:
        """Check playlists and download. `after()` runs once it finishes (unless it was stopped)."""
        if self.job is not None:
            return
        path = self.library_path

        def work(task: Task):
            library = Library(path)
            try:
                if playlist_ids is None and refresh_sources:  # Update All: podcasts too
                    _update_podcasts(library, task)
                playlists = [p for p in library.playlists() if playlist_ids is None or p.id in playlist_ids]
                if refresh_sources:
                    client = _spotify_client(task)
                    reader = self._full_reader()
                    for number, playlist in enumerate(playlists, start=1):
                        if task.should_stop():
                            return None
                        task.progress.emit(0, 0, f"Checking {playlist.name} ({number}/{len(playlists)})…")
                        report = refresh_playlist(library, playlist, client, reader)
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
                finalize(library)
                return result
            finally:
                library.close()

        def finished(result):
            stopped = isinstance(result, dict) and result.get("summary", "").startswith("Stopped")
            if after is not None and not stopped and result is not None:
                QTimer.singleShot(0, after)  # once this job is fully finished

        self._run_job("Updating…", work, finished)

    def _full_reader(self):
        from synclassic.sources.full_reader import available

        if load_settings().full_playlist_reader and available():
            return self.reader_bridge.read
        return None

    def _run_job(self, text: str, work, on_done=None, verb: str = "Downloading") -> None:
        self._progress_verb = verb
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
        self.lcd.hide_now_playing()
        self.status_label.setText(text)
        self.lcd.secondary.setText("")
        self.progress.setRange(0, 0)
        self.progress.show()
        for action in (self.update_action, self.add_action, self.sync_action, self.update_sync_action,
                       self.podcasts_update_action):
            action.setEnabled(False)
        self.update_one.setEnabled(False)
        self.stop_action.setEnabled(True)

    def _set_idle(self, text: str) -> None:
        self.job = None
        self.progress.hide()
        self.status_label.setText(text)
        for action in (self.update_action, self.add_action, self.sync_action, self.update_sync_action,
                       self.podcasts_update_action):
            action.setEnabled(True)
        self.update_one.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.refresh()

    def _progress(self, number: int, total: int, text: str) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            self.status_label.setText(text)
            self.lcd.secondary.setText("")
            return
        verb = getattr(self, "_progress_verb", "Downloading")
        self.progress.setRange(0, total)
        self.progress.setValue(min(number - 1, total))
        if number <= total:
            self.status_label.setText(text)
            self.lcd.secondary.setText(f"{verb} {number} of {total}")
        else:
            self.status_label.setText("Finishing…")
            self.lcd.secondary.setText("")

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
        self._ipod_timer.stop()
        self._refresh_timer.stop()
        self.player.stop()
        self.library.close()
        event.accept()


def _update_podcasts(library: Library, task: Task, podcast_ids: list[int] | None = None) -> str:
    """Refresh feeds and download the episodes each show keeps. Returns a one-line summary."""
    from synclassic.podcasts.store import Podcasts

    store = Podcasts(library)
    if not store.podcasts():
        return "No podcasts yet."

    def progress(number, total, text):
        task.progress.emit(number, total, text)
        task.changed.emit()

    task.progress.emit(0, 0, "Checking podcasts…")
    new, downloaded = store.update_all(load_settings().music_path, task.say, should_stop=task.should_stop,
                                       progress=progress, only=podcast_ids)
    task.changed.emit()
    summary = (f"Podcasts: {new} new episode{'s' if new != 1 else ''}, {downloaded} downloaded."
               if new or downloaded else "Podcasts are up to date.")
    task.say(summary)
    return summary


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
