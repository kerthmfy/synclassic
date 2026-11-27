"""Where Synclassic keeps its files, and the user's settings."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from dataclasses import asdict, dataclass, fields

APP_NAME = "Synclassic"


def app_dir() -> pathlib.Path:
    """Folder for settings, the library database and the Spotify login."""
    override = os.environ.get("SYNCLASSIC_HOME")
    if override:
        path = pathlib.Path(override).expanduser()
    elif sys.platform == "darwin":
        path = pathlib.Path.home() / "Library" / "Application Support" / APP_NAME
    elif os.name == "nt":
        path = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home())) / APP_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")
        path = pathlib.Path(base) / "synclassic"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_music_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Music" / APP_NAME


@dataclass
class Settings:
    music_dir: str = ""
    # "m4a" (AAC, copied straight from YouTube Music when possible) or "mp3".
    format: str = "m4a"
    # MP3 only: "v0" (best VBR) or "320" (constant 320 kbps).
    mp3_quality: str = "v0"
    # Optional: read YouTube cookies from a browser ("safari", "chrome", "firefox", ...)
    # so YouTube Music Premium members get the 256 kbps AAC stream.
    cookies_from_browser: str = ""
    spotify_client_id: str = ""
    # Seconds to wait between downloads, to stay polite to YouTube.
    download_pause: float = 2.0
    # Read public playlists over 100 songs in full by scrolling Spotify's web page.
    full_playlist_reader: bool = True
    # "artist": Music/Artist/Album/Song.m4a   "playlist": <Playlist>/Artist - Song.m4a
    folder_layout: str = "artist"
    # Send the iPod's plays to Last.fm after each sync (when connected).
    scrobble_after_sync: bool = True
    # Measure each song's loudness so the iPod's Sound Check plays them at a similar volume.
    sound_check: bool = True

    @property
    def music_path(self) -> pathlib.Path:
        return pathlib.Path(self.music_dir).expanduser() if self.music_dir else default_music_dir()


SETTING_HELP = {
    "music_dir": "Folder where songs and playlists are saved (default ~/Music/Synclassic)",
    "format": "m4a or mp3",
    "mp3_quality": "v0 or 320 (only used when format is mp3)",
    "cookies_from_browser": "safari, chrome, firefox, brave, edge... or empty to turn off",
    "spotify_client_id": "Client ID of your Spotify developer app (set by 'synclassic login')",
    "download_pause": "Seconds to wait between downloads",
    "full_playlist_reader": "on/off: read public playlists over 100 songs from Spotify's web page",
    "folder_layout": "artist (Artist/Album/Song) or playlist (one folder per playlist)",
    "sound_check": "on/off: measure loudness so the iPod's Sound Check evens out volume",
}


def settings_file() -> pathlib.Path:
    return app_dir() / "settings.json"


def load_settings() -> Settings:
    path = settings_file()
    if not path.is_file():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save_settings(settings: Settings) -> None:
    path = settings_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    tmp.replace(path)


def set_setting(settings: Settings, key: str, value: str) -> None:
    """Validate and apply one setting from text (used by 'synclassic config')."""
    if key not in SETTING_HELP:
        raise ValueError(f"Unknown setting '{key}'. Known settings: {', '.join(SETTING_HELP)}")
    value = value.strip()
    if key == "format":
        value = value.lower()
        if value not in ("m4a", "mp3"):
            raise ValueError("format must be m4a or mp3")
    elif key == "mp3_quality":
        value = value.lower()
        if value not in ("v0", "320"):
            raise ValueError("mp3_quality must be v0 or 320")
    elif key == "download_pause":
        try:
            number = float(value)
        except ValueError:
            raise ValueError("download_pause must be a number of seconds") from None
        if number < 0:
            raise ValueError("download_pause can't be negative")
        setattr(settings, key, number)
        return
    elif key == "folder_layout":
        value = value.lower()
        if value not in ("artist", "playlist"):
            raise ValueError("folder_layout must be artist or playlist")
    elif key in ("full_playlist_reader", "sound_check"):
        lowered = value.lower()
        if lowered not in ("on", "off", "true", "false", "yes", "no", "1", "0"):
            raise ValueError(f"{key} must be on or off")
        setattr(settings, key, lowered in ("on", "true", "yes", "1"))
        return
    elif key == "music_dir" and value:
        value = str(pathlib.Path(value).expanduser().resolve())
    setattr(settings, key, value)
