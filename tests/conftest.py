import json
import pathlib
import shutil
import subprocess

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own settings/library folder and music folder."""
    home = tmp_path / "home"
    monkeypatch.setenv("SYNCLASSIC_HOME", str(home))
    from synclassic import config

    settings = config.Settings(music_dir=str(tmp_path / "Music"), download_pause=0, full_playlist_reader=False)
    config.save_settings(settings)
    # Never look up real Spotify covers during tests.
    from synclassic.sources import spotify_public

    monkeypatch.setattr(spotify_public, "track_cover_url", lambda track_id: None)
    # Nor real lyrics: LRCLIB "doesn't know" any song unless a test says otherwise.
    from types import SimpleNamespace

    from synclassic import lyrics

    original = lyrics.LyricsFinder.__init__

    def offline(self, get=None, sleep=lambda s: None):
        original(self, get or (lambda url, params: SimpleNamespace(status_code=404, json=lambda: None)), sleep)

    monkeypatch.setattr(lyrics.LyricsFinder, "__init__", offline)
    return home


@pytest.fixture
def embed_entity():
    def load(name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


def make_audio(path: pathlib.Path, seconds: float = 1.0) -> pathlib.Path:
    """A tiny real audio file, so tagging and playlists are tested on actual files."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    codec = ["-c:a", "aac", "-b:a", "64k"] if path.suffix == ".m4a" else ["-c:a", "libmp3lame", "-q:a", "9"]
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", *codec, str(path)],
        check=True,
    )
    return path
