"""Drive the real window: add a playlist and let the background update download it."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from synclassic import config, downloader, tagging, update  # noqa: E402
from synclassic.library import Library  # noqa: E402
from synclassic.models import SourcePlaylist, Track  # noqa: E402
from tests.conftest import make_audio  # noqa: E402
from tests.test_end_to_end import search  # noqa: E402


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_until(app, condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_update_from_window(app, monkeypatch, tmp_path):
    from synclassic.gui.main_window import MainWindow
    from synclassic.matcher import Matcher
    from synclassic.sources import SourceSpec

    def fake_download(video_id, destination, settings):
        path = destination.with_suffix(".m4a")
        path.parent.mkdir(parents=True, exist_ok=True)
        make_audio(path)
        return downloader.DownloadResult(path, "mp4a.40.2", 128.0, converted=False)

    monkeypatch.setattr(downloader, "download", fake_download)
    monkeypatch.setattr(downloader, "check_tools", lambda: [])
    monkeypatch.setattr(tagging, "fetch_cover", lambda url: None)
    monkeypatch.setattr(update, "Matcher", lambda: Matcher(search))
    source = SourcePlaylist("Mix", [
        Track("One More Time", ["Daft Punk"], duration_ms=320_000, spotify_id="a" * 22),
        Track("Strange Song", ["Nobody"], duration_ms=200_000, spotify_id="b" * 22),
    ])
    monkeypatch.setattr(update, "read_source", lambda spec, client, full_reader=None: source)

    library = Library(config.app_dir() / "library.db")
    library.add_playlist("Mix", SourceSpec("spotify", "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd"))
    library.close()

    window = MainWindow()
    window.show()
    window.start_update(None)
    assert window.job is not None and not window.update_action.isEnabled()
    assert wait_until(app, lambda: window.job is None)

    titles = [window.sidebar.item(i).text() for i in range(window.sidebar.count())]
    assert titles[0].startswith("⚠  Needs attention  (1)")
    assert any(t.startswith("Mix\n1 of 2 ready") for t in titles)
    assert "1 downloaded" in window.status_label.text()
    assert (config.load_settings().music_path / "Playlists" / "Mix.m3u8").is_file()
    window.close()


def test_stop_button_stops_between_songs(app, monkeypatch):
    from synclassic.gui.main_window import MainWindow
    from synclassic.matcher import Matcher
    from synclassic.sources import SourceSpec

    calls = []

    def slow_download(video_id, destination, settings):
        calls.append(video_id)
        time.sleep(0.3)
        path = destination.with_suffix(".m4a")
        path.parent.mkdir(parents=True, exist_ok=True)
        make_audio(path)
        return downloader.DownloadResult(path, "mp4a.40.2", 128.0, converted=False)

    monkeypatch.setattr(downloader, "download", slow_download)
    monkeypatch.setattr(downloader, "check_tools", lambda: [])
    monkeypatch.setattr(tagging, "fetch_cover", lambda url: None)
    monkeypatch.setattr(update, "Matcher", lambda: Matcher(search))
    many = [Track("One More Time", ["Daft Punk"], duration_ms=320_000, spotify_id=f"{n:022d}") for n in range(10)]
    monkeypatch.setattr(update, "read_source", lambda spec, client, full_reader=None: SourcePlaylist("Many", many))

    library = Library(config.app_dir() / "library.db")
    library.add_playlist("Many", SourceSpec("spotify", "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd"))
    library.close()

    window = MainWindow()
    window.start_update(None)
    assert wait_until(app, lambda: len(calls) >= 1)
    window.stop_job()
    assert wait_until(app, lambda: window.job is None)
    assert len(calls) < 10
    assert "Stopped" in window.status_label.text()
    window.close()
