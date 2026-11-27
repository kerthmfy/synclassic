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
from synclassic.gui.widgets import (
    COUNT_ROLE,
    ICON_ROLE,
    SECTION_ROLE,
    CapacityBar,
    GlossyButton,
    GradientBar,
    IPodGraphic,
    LcdDisplay,
    SourceListDelegate,
    StatusPillDelegate,
)
from synclassic.gui.workers import Task, run_in_background
from synclassic.library import Library, LibraryPlaylist, LibraryTrack
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyClient
from synclassic.update import add_inputs, download_missing, finalize, refresh_playlist

ATTENTION = "__attention__"
IPOD = "__ipod__"
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
        self.folder_action = action("Show Music Folder", lambda: reveal_in_finder(load_settings().music_path))
        self.settings_action = action("Settings…", self.open_settings, QKeySequence.StandardKey.Preferences)
        self.log_action = action("Show Activity", self._toggle_log, QKeySequence("Ctrl+L"))
        self.log_action.setCheckable(True)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        for act in (self.add_action, None, self.update_action, self.stop_action, None, self.folder_action, None,
                    self.settings_action):
            file_menu.addSeparator() if act is None else file_menu.addAction(act)
        ipod_menu = bar.addMenu("iPod")
        for act in (self.update_sync_action, self.sync_action, self.ipod_action, None, self.eject_action):
            ipod_menu.addSeparator() if act is None else ipod_menu.addAction(act)
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
        for symbol, label, act in (("plus", "Add", self.add_action), ("refresh", "Update", self.update_action),
                                   ("stop", "Stop", self.stop_action)):
            row.addWidget(self._glossy(symbol, label, act))
        row.addStretch(1)
        self.lcd = LcdDisplay()
        self.lcd.setFixedWidth(440)
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

        self.sidebar = QTreeWidget()
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
        splitter.addWidget(self.sidebar)

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
        self.table.itemDoubleClicked.connect(lambda *_: self.pick_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)  # 0
        self.stack.addWidget(self._build_empty_page())  # 1
        self.stack.addWidget(self._build_ipod_page())  # 2
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

    def _add_source(self, text: str, key, icon: str, count=None, tip: str = "") -> None:
        item = QTreeWidgetItem([text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setData(0, ICON_ROLE, icon)
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
        if attention:
            self._add_section("Library")
            self._add_source("Needs Attention", ATTENTION, "attention", len(attention),
                             "Songs that weren't found or might be the wrong version")
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
            self._add_source(self.ipod.name, IPOD, "ipod", None, str(self.ipod.root))
        # Keep the selection.
        target = None
        first = None
        for i in range(self.sidebar.topLevelItemCount()):
            item = self.sidebar.topLevelItem(i)
            if item.data(0, SECTION_ROLE):
                continue
            first = first or item
            if item.data(0, Qt.ItemDataRole.UserRole) == self.current_key:
                target = item
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

    def _idle_lcd(self) -> None:
        if self.lcd.bar.isVisible():
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
        if self.current_key in (None, ATTENTION, IPOD):
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

    def _apply_search(self) -> None:
        words = self.search.text().casefold().split()
        for i in range(self.table.topLevelItemCount()):
            row = self.table.topLevelItem(i)
            haystack = " ".join(row.text(c) for c in (1, 2, 3)).casefold()
            row.setHidden(bool(words) and not all(w in haystack for w in words))

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
        self._ipod_root = root
        self.ipod = ipod
        if ipod is None and self.current_key == IPOD:
            self.current_key = None
        self.refresh()

    def _show_ipod(self) -> None:
        ipod = self.ipod
        if ipod is None:
            return
        self.ipod_name.setText(ipod.name)
        self.ipod_graphic.set_screen(ipod.name, "Reading…")
        total, free = ipod.disk_usage()
        rows = [("Name", ipod.name), ("Model", ipod.model), ("Software Version", ipod.firmware or "unknown"),
                ("Capacity", f"{total / 1000**3:.1f} GB")]
        self._ipod_rows = rows
        self._render_ipod_details(rows)
        self.capacity.set_values(0, total - free, free)

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
            self.ipod_graphic.set_screen(ipod.name, f"{count:,} songs\n{playlists} playlists")

        run_in_background(self, read, show, lambda error: self.ipod_graphic.set_screen(ipod.name, "Can't read music"))

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
        message = ipod_sync.eject(self.ipod)
        self.say(message)
        self.status_label.setText(message)
        self._detect_ipod()

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
        if not row or row.data(0, SECTION_ROLE) or row.data(0, Qt.ItemDataRole.UserRole) in (ATTENTION, IPOD):
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
        return None if self.current_key in (None, ATTENTION, IPOD) else [self.current_key]

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
        if self.current_key not in (None, ATTENTION, IPOD):
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
        self.status_label.setText(text)
        self.lcd.secondary.setText("")
        self.progress.setRange(0, 0)
        self.progress.show()
        for action in (self.update_action, self.add_action, self.sync_action, self.update_sync_action):
            action.setEnabled(False)
        self.update_one.setEnabled(False)
        self.stop_action.setEnabled(True)

    def _set_idle(self, text: str) -> None:
        self.job = None
        self.progress.hide()
        self.status_label.setText(text)
        for action in (self.update_action, self.add_action, self.sync_action, self.update_sync_action):
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
        self.library.close()
        event.accept()


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
