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
    QVBoxLayout,
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
        self.setMinimumWidth(560)
        self.settings: Settings = load_settings()
        layout = QVBoxLayout(self)

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
        self.full_reader = QCheckBox("Read playlists over 100 songs in full from Spotify's web page")
        self.full_reader.setChecked(self.settings.full_playlist_reader)
        self.full_reader.setToolTip(
            "Without logging in (or for playlists you only follow), Spotify shares just 100 songs.\n"
            "This opens Spotify's page in a small window and scrolls through it to read every song."
        )
        form.addRow("", self.full_reader)
        layout.addWidget(music)

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
        layout.addWidget(spotify)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_account()

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
        self.settings.spotify_client_id = self.client_id.text().strip() or self.settings.spotify_client_id
        save_settings(self.settings)
        self.accept()

    def done(self, result: int) -> None:
        mark_closed(self)
        super().done(result)
