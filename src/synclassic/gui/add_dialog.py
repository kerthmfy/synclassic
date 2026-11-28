"""Add playlists: a Spotify link, your Spotify account, or an .m3u8/.csv file."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from synclassic.gui.workers import mark_closed, run_in_background
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyClient
from synclassic.sources.spotify_links import parse_spotify_ref


def load_my_spotify(task) -> tuple[str, list[dict]] | None:
    try:
        client = SpotifyClient.from_saved_login()
    except spotify_auth.SpotifyAuthError:
        return None
    if not client:
        return None
    me = client.me()
    playlists = client.my_playlists()
    for item in playlists:
        item["full"] = item["owner_id"] == me.get("id") or item["collaborative"]
    return me.get("display_name") or "you", playlists


class AddDialog(QDialog):
    """After exec(): `inputs` holds what to add, `name` an optional custom name."""

    def __init__(self, parent=None, open_settings=None):
        super().__init__(parent)
        self.setWindowTitle("Add playlists")
        self.resize(620, 480)
        self.inputs: list[str] = []
        self.name: str | None = None
        self.file_path = ""
        self.open_settings = open_settings
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # --- link ---
        link_tab = QWidget()
        link_layout = QVBoxLayout(link_tab)
        link_layout.addWidget(QLabel("Paste a Spotify playlist, album or song link:"))
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://open.spotify.com/playlist/…")
        self.link.textChanged.connect(self._update_ok)
        link_layout.addWidget(self.link)
        hint = QLabel(
            "In Spotify: ••• → Share → Copy link. Without logging in, Spotify shares the first 100 songs "
            "of a playlist; log in (Settings) to read all songs of playlists you own."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        link_layout.addWidget(hint)
        self.link_name = QLineEdit()
        self.link_name.setPlaceholderText("Name on your iPod (optional, defaults to the Spotify name)")
        link_layout.addWidget(self.link_name)
        link_layout.addStretch(1)
        self.tabs.addTab(link_tab, "Spotify link")

        # --- my spotify ---
        mine_tab = QWidget()
        mine_layout = QVBoxLayout(mine_tab)
        self.mine_label = QLabel("Loading your Spotify playlists…")
        self.mine_label.setWordWrap(True)
        mine_layout.addWidget(self.mine_label)
        self.mine_list = QListWidget()
        self.mine_list.itemChanged.connect(self._update_ok)
        mine_layout.addWidget(self.mine_list, 1)
        mine_buttons = QHBoxLayout()
        self.select_all = QPushButton("Select all")
        self.select_all.clicked.connect(lambda: self._check_all(True))
        self.select_none = QPushButton("Select none")
        self.select_none.clicked.connect(lambda: self._check_all(False))
        self.login_button = QPushButton("Log in to Spotify…")
        self.login_button.clicked.connect(self._go_login)
        self.login_button.hide()
        mine_buttons.addWidget(self.select_all)
        mine_buttons.addWidget(self.select_none)
        mine_buttons.addStretch(1)
        mine_buttons.addWidget(self.login_button)
        mine_layout.addLayout(mine_buttons)
        self.tabs.addTab(mine_tab, "From my Spotify")

        # --- file ---
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        file_layout.addWidget(QLabel("Import a playlist file, or add a folder of your own music:"))
        file_row = QHBoxLayout()
        self.file_label = QLabel("Nothing chosen")
        self.file_label.setWordWrap(True)
        choose = QPushButton("Choose File…")
        choose.clicked.connect(self._choose_file)
        choose_folder = QPushButton("Choose Folder…")
        choose_folder.clicked.connect(self._choose_folder)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(choose)
        file_row.addWidget(choose_folder)
        file_layout.addLayout(file_row)
        file_hint = QLabel(
            "• .m3u8 / .m3u: songs that are already on your Mac are used as they are; the rest are downloaded.\n"
            "• .csv from Exportify or TuneMyMusic: a file with several playlists adds each of them.\n"
            "• A folder: all the music in it (subfolders too) as one playlist, FLAC included. Nothing is "
            "downloaded and your files aren't changed; new files in it are picked up at each Update."
        )
        file_hint.setWordWrap(True)
        file_hint.setStyleSheet("color: palette(placeholder-text);")
        file_layout.addWidget(file_hint)
        file_layout.addStretch(1)
        self.tabs.addTab(file_tab, "File or Folder")

        self.download_now = QCheckBox("Download the songs now")
        self.download_now.setChecked(True)
        layout.addWidget(self.download_now)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.tabs.currentChanged.connect(self._update_ok)

        clipboard = QGuiApplication.clipboard().text().strip()
        if parse_spotify_ref(clipboard):
            self.link.setText(clipboard)
        self._update_ok()
        run_in_background(self, load_my_spotify, self._show_mine, lambda error: self._show_mine_error(error))

    # --- my spotify ---

    def _show_mine(self, result) -> None:
        self.mine_list.clear()
        if result is None:
            self.mine_label.setText("Log in to Spotify to pick from your playlists and Liked Songs.")
            self.login_button.show()
            self.select_all.setEnabled(False)
            self.select_none.setEnabled(False)
            return
        name, playlists = result
        self.mine_label.setText(
            f"Playlists in {name}'s Spotify. ✓ = all songs; · = Spotify only shares the first 100 songs of "
            "playlists you follow but don't own."
        )
        liked = QListWidgetItem("❤  Liked Songs")
        liked.setData(Qt.ItemDataRole.UserRole, "liked")
        liked.setCheckState(Qt.CheckState.Unchecked)
        self.mine_list.addItem(liked)
        for item in playlists:
            if not item.get("url"):
                continue
            total = item["total"] if item["total"] is not None else "?"
            row = QListWidgetItem(f"{'✓' if item['full'] else '·'}  {item['name']}  ({total} songs)")
            row.setData(Qt.ItemDataRole.UserRole, item["url"])
            row.setCheckState(Qt.CheckState.Unchecked)
            self.mine_list.addItem(row)

    def _show_mine_error(self, error: str) -> None:
        self.mine_label.setText(f"Couldn't load your Spotify playlists: {error}")

    def _check_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.mine_list.count()):
            self.mine_list.item(index).setCheckState(state)

    def _checked_mine(self) -> list[str]:
        return [
            self.mine_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.mine_list.count())
            if self.mine_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _go_login(self) -> None:
        if self.open_settings:
            self.open_settings()
            run_in_background(self, load_my_spotify, self._show_mine, lambda error: self._show_mine_error(error))

    # --- file ---

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a playlist file", "", "Playlists (*.m3u8 *.m3u *.csv *.tsv)")
        if path:
            self.file_path = path
            self.file_label.setText(path)
        self._update_ok()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder with your music")
        if path:
            self.file_path = path
            self.file_label.setText(path)
        self._update_ok()

    # --- result ---

    def _current_inputs(self) -> list[str]:
        tab = self.tabs.currentIndex()
        if tab == 0:
            text = self.link.text().strip()
            return [text] if text else []
        if tab == 1:
            return self._checked_mine()
        return [self.file_path] if self.file_path else []

    def _update_ok(self, *_) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self._current_inputs()))

    def _accept(self) -> None:
        self.inputs = self._current_inputs()
        if self.tabs.currentIndex() == 0:
            self.name = self.link_name.text().strip() or None
        if self.inputs:
            self.accept()

    def done(self, result: int) -> None:
        mark_closed(self)
        super().done(result)
