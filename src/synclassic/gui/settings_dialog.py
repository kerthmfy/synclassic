"""Settings: where music goes, the file format, and the Spotify login."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from synclassic.config import Settings, load_settings, save_settings
from synclassic.gui.workers import mark_closed, run_in_background
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyClient

BROWSERS = [("Off", ""), ("Safari", "safari"), ("Chrome", "chrome"), ("Firefox", "firefox"), ("Brave", "brave"),
            ("Edge", "edge"), ("Arc / Chromium", "chromium")]
DASHBOARD = "https://developer.spotify.com/dashboard"


def spotify_account_name() -> str | None:
    """Display name of the logged-in Spotify account, or None when not logged in."""
    try:
        client = SpotifyClient.from_saved_login()
    except spotify_auth.SpotifyAuthError:
        return None
    if not client:
        return None
    try:
        return client.me().get("display_name") or "your account"
    except Exception:
        return "your account"


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(680)
        self.settings: Settings = load_settings()
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # --- music ---
        music = QGroupBox("Music")
        form = QFormLayout(music)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(str(self.settings.music_path))
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(choose)
        form.addRow("Save music in", folder_row)

        self.format = QComboBox()
        self.format.addItem("M4A (AAC) — best for iPod, saved without re-encoding", "m4a")
        self.format.addItem("MP3", "mp3")
        self.format.setCurrentIndex(0 if self.settings.format == "m4a" else 1)
        form.addRow("Format", self.format)

        self.mp3_quality = QComboBox()
        self.mp3_quality.addItem("V0 (variable, best)", "v0")
        self.mp3_quality.addItem("320 kbps constant", "320")
        self.mp3_quality.setCurrentIndex(0 if self.settings.mp3_quality == "v0" else 1)
        form.addRow("MP3 quality", self.mp3_quality)
        self.format.currentIndexChanged.connect(self._format_changed)
        self._format_changed()

        self.layout_choice = QComboBox()
        self.layout_choice.addItem("Artist / Album / Song", "artist")
        self.layout_choice.addItem("One folder per playlist", "playlist")
        self.layout_choice.setCurrentIndex(1 if self.settings.folder_layout == "playlist" else 0)
        self.layout_choice.setToolTip(
            "One folder per playlist: each playlist's folder has its songs and its .m3u8.\n"
            "A song in several playlists is stored once (shown in each folder)."
        )
        form.addRow("Organize files", self.layout_choice)

        self.cookies = QComboBox()
        for label, value in BROWSERS:
            self.cookies.addItem(label, value)
        index = max(0, self.cookies.findData(self.settings.cookies_from_browser))
        self.cookies.setCurrentIndex(index)
        self.cookies.setToolTip(
            "Uses your YouTube sign-in from this browser. YouTube Music Premium members then get 256 kbps AAC.\n"
            "Also helps with age-restricted songs."
        )
        form.addRow("YouTube sign-in from", self.cookies)

        self.pause = QDoubleSpinBox()
        self.pause.setRange(0, 60)
        self.pause.setSuffix(" s")
        self.pause.setValue(self.settings.download_pause)
        form.addRow("Pause between downloads", self.pause)

        self.sound_check = QCheckBox("Sound Check: measure each song's loudness so the iPod plays them evenly")
        self.sound_check.setChecked(self.settings.sound_check)
        self.sound_check.setToolTip("Turn Sound Check on on the iPod too: Settings → Sound Check.")
        form.addRow("", self.sound_check)
        self.lyrics = QCheckBox("Lyrics: put each song's lyrics inside it (from lrclib.net); the iPod shows them")
        self.lyrics.setChecked(self.settings.lyrics)
        form.addRow("", self.lyrics)
        self.song_info = QCheckBox("Fill in genre and year (from Deezer / Apple Music) when Spotify doesn't give them")
        self.song_info.setChecked(self.settings.song_info)
        form.addRow("", self.song_info)
        self.full_reader = QCheckBox("Read playlists over 100 songs in full from Spotify's web page")
        self.full_reader.setChecked(self.settings.full_playlist_reader)
        self.full_reader.setToolTip(
            "Without logging in (or for playlists you only follow), Spotify shares just 100 songs.\n"
            "This opens Spotify's page in a small window and scrolls through it to read every song."
        )
        form.addRow("", self.full_reader)
        self.tabs.addTab(music, "General")

        # --- spotify ---
        spotify = QGroupBox("Spotify login (for Liked Songs and playlists over 100 songs)")
        box = QVBoxLayout(spotify)
        self.account = QLabel("Checking…")
        box.addWidget(self.account)
        steps = QLabel(
            f"1. Open the <a href='{DASHBOARD}'>Spotify developer dashboard</a> and click <b>Create app</b>.<br>"
            f"2. Add this Redirect URI: <code>{spotify_auth.REDIRECT_URI}</code> and tick <b>Web API</b>.<br>"
            "3. Paste the app's <b>Client ID</b> below and click Log in.<br>"
            "<span style='color: gray'>Spotify requires Premium on the developer account.</span>"
        )
        steps.setTextFormat(Qt.TextFormat.RichText)
        steps.setOpenExternalLinks(True)
        steps.setWordWrap(True)
        box.addWidget(steps)
        id_row = QHBoxLayout()
        self.client_id = QLineEdit(self.settings.spotify_client_id)
        self.client_id.setPlaceholderText("Client ID")
        copy_uri = QPushButton("Copy Redirect URI")
        copy_uri.clicked.connect(lambda: QGuiApplication.clipboard().setText(spotify_auth.REDIRECT_URI))
        id_row.addWidget(self.client_id, 1)
        id_row.addWidget(copy_uri)
        box.addLayout(id_row)
        login_row = QHBoxLayout()
        self.login_button = QPushButton("Log in with Spotify")
        self.login_button.clicked.connect(self._login)
        self.logout_button = QPushButton("Log out")
        self.logout_button.clicked.connect(self._logout)
        login_row.addWidget(self.login_button)
        login_row.addWidget(self.logout_button)
        login_row.addStretch(1)
        box.addLayout(login_row)
        self.tabs.addTab(spotify, "Spotify")

        # --- last.fm ---
        from synclassic import lastfm

        saved = lastfm.load_credentials()
        lastfm_box = QGroupBox("Last.fm (scrobble what you play on your iPod)")
        lbox = QVBoxLayout(lastfm_box)
        self.lastfm_status = QLabel("")
        lbox.addWidget(self.lastfm_status)
        builtin = all(lastfm.builtin_key())
        own = bool(saved.get("api_key") and saved.get("secret"))
        self.lastfm_own = QCheckBox("Advanced: use my own Last.fm API account")
        self.lastfm_own.setChecked(own or not builtin)
        self.lastfm_own.setVisible(builtin)  # without a built-in key, your own is the only way
        lbox.addWidget(self.lastfm_own)
        self.lastfm_advanced = QWidget()
        advanced = QVBoxLayout(self.lastfm_advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        lsteps = QLabel(
            f"<a href='{lastfm.CREATE_ACCOUNT_PAGE}'>Create a Last.fm API account</a> (any name; leave the "
            "callback URL empty), then paste its <b>API key</b> and <b>shared secret</b>."
        )
        lsteps.setTextFormat(Qt.TextFormat.RichText)
        lsteps.setOpenExternalLinks(True)
        lsteps.setWordWrap(True)
        advanced.addWidget(lsteps)
        lform = QFormLayout()
        self.lastfm_key = QLineEdit(saved.get("api_key", ""))
        self.lastfm_key.setPlaceholderText("API key")
        self.lastfm_secret = QLineEdit(saved.get("secret", ""))
        self.lastfm_secret.setPlaceholderText("Shared secret")
        self.lastfm_secret.setEchoMode(QLineEdit.EchoMode.Password)
        lform.addRow("API key", self.lastfm_key)
        lform.addRow("Shared secret", self.lastfm_secret)
        advanced.addLayout(lform)
        lbox.addWidget(self.lastfm_advanced)
        self.lastfm_advanced.setVisible(self.lastfm_own.isChecked())
        self.lastfm_own.toggled.connect(self.lastfm_advanced.setVisible)
        lrow = QHBoxLayout()
        self.lastfm_connect = QPushButton("Connect to Last.fm")
        self.lastfm_connect.clicked.connect(self._lastfm_connect)
        self.lastfm_disconnect = QPushButton("Disconnect")
        self.lastfm_disconnect.clicked.connect(self._lastfm_disconnect)
        lrow.addWidget(self.lastfm_connect)
        lrow.addWidget(self.lastfm_disconnect)
        lrow.addStretch(1)
        lbox.addLayout(lrow)
        self.scrobble_after = QCheckBox("Scrobble automatically after each sync")
        self.scrobble_after.setChecked(self.settings.scrobble_after_sync)
        lbox.addWidget(self.scrobble_after)
        self.tabs.addTab(lastfm_box, "Last.fm")
        self._show_lastfm()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_account()

    def _show_lastfm(self) -> None:
        from synclassic import lastfm

        user = lastfm.connected_user()
        self.lastfm_status.setText(f"✓ Connected as <b>{user}</b>" if user else "Not connected.")
        self.lastfm_disconnect.setEnabled(bool(user))

    def _lastfm_connect(self) -> None:
        import webbrowser

        from synclassic import lastfm

        if self.lastfm_own.isChecked():
            key, secret = self.lastfm_key.text().strip(), self.lastfm_secret.text().strip()
            if not key or not secret:
                QMessageBox.information(self, "Last.fm", "Paste your Last.fm API key and shared secret first.")
                return
        else:
            key, secret = lastfm.builtin_key()
        self.lastfm_connect.setEnabled(False)
        self.lastfm_status.setText("Approve Synclassic on the Last.fm page that just opened…")

        def done(user):
            self.lastfm_connect.setEnabled(True)
            self._show_lastfm()

        def failed(error):
            self.lastfm_connect.setEnabled(True)
            self.lastfm_status.setText(f"Couldn't connect: {error}")

        run_in_background(self, lambda task: lastfm.Client(key, secret).connect(webbrowser.open), done, failed)

    def _lastfm_disconnect(self) -> None:
        from synclassic import lastfm

        lastfm.disconnect()
        self._show_lastfm()

    def _format_changed(self) -> None:
        self.mp3_quality.setEnabled(self.format.currentData() == "mp3")

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save music in", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _refresh_account(self) -> None:
        self.account.setText("Checking…")
        run_in_background(self, lambda task: spotify_account_name(), self._show_account,
                          lambda error: self._show_account(None))

    def _show_account(self, name: str | None) -> None:
        self.account.setText(f"✓ Logged in as <b>{name}</b>" if name else "Not logged in. Links still work (first 100 songs).")
        self.logout_button.setEnabled(bool(name))

    def _login(self) -> None:
        client_id = self.client_id.text().strip()
        if not client_id:
            QMessageBox.information(self, "Client ID needed", "Paste your Spotify app's Client ID first.")
            QDesktopServices.openUrl(QUrl(DASHBOARD))
            return
        self.login_button.setEnabled(False)
        self.account.setText("Finish logging in in your browser…")

        def done(_):
            settings = load_settings()
            settings.spotify_client_id = client_id
            save_settings(settings)
            self.settings.spotify_client_id = client_id
            self.login_button.setEnabled(True)
            self._refresh_account()

        def failed(error):
            self.login_button.setEnabled(True)
            self.account.setText(f"Login failed: {error}")

        run_in_background(self, lambda task: spotify_auth.login(client_id), done, failed)

    def _logout(self) -> None:
        spotify_auth.forget_token()
        self._show_account(None)

    def _save(self) -> None:
        self.settings.music_dir = self.folder.text().strip()
        self.settings.format = self.format.currentData()
        self.settings.mp3_quality = self.mp3_quality.currentData()
        self.settings.cookies_from_browser = self.cookies.currentData()
        self.settings.download_pause = float(self.pause.value())
        self.settings.full_playlist_reader = self.full_reader.isChecked()
        self.settings.sound_check = self.sound_check.isChecked()
        self.settings.lyrics = self.lyrics.isChecked()
        self.settings.song_info = self.song_info.isChecked()
        self.settings.folder_layout = self.layout_choice.currentData()
        self.settings.scrobble_after_sync = self.scrobble_after.isChecked()
        from synclassic import lastfm

        credentials = lastfm.load_credentials()
        if self.lastfm_own.isChecked():
            key, secret = self.lastfm_key.text().strip(), self.lastfm_secret.text().strip()
        else:
            key, secret = "", ""  # the built-in account
        if (key, secret) != (credentials.get("api_key", ""), credentials.get("secret", "")):
            # A different API account: the old connection no longer applies.
            lastfm.save_credentials({"api_key": key, "secret": secret} if key else {})
        self.settings.spotify_client_id = self.client_id.text().strip() or self.settings.spotify_client_id
        save_settings(self.settings)
        self.accept()

    def done(self, result: int) -> None:
        mark_closed(self)
        super().done(result)
