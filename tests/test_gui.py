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

    assert window.sidebar_entries() == ["Needs Attention (1)", "Mix (1)"]  # (count) = songs not ready
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


def test_sync_ipod_from_window(app, monkeypatch, tmp_path):
    """Preview -> confirm -> sync, through the real window (dialogs answered automatically)."""
    from PySide6.QtWidgets import QMessageBox

    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import itunesdb
    from tests.test_ipod_read import fake_ipod, load
    from tests.test_ipod_sync import library_with, song

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    library_with(tmp_path, {"Gym": [song(1), song(2)]}).close()
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    window = MainWindow()
    window.sync_ipod()
    assert wait_until(app, lambda: window.job is not None or len(shown) > 1, 20)
    assert wait_until(app, lambda: window.job is None, 30)
    assert any("Copy 2 songs" in text for text in shown)
    db = itunesdb.parse((root / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes())
    assert [p.name for p in itunesdb.playlists(db) if p.name == "Gym"] == ["Gym"]
    assert window.status_label.text() == "iPod synced."
    window.close()


def test_update_and_sync_runs_both_without_asking_when_it_only_adds(app, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import itunesdb
    from synclassic.matcher import Matcher
    from synclassic.sources import SourceSpec
    from tests.test_ipod_read import fake_ipod, load

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))

    def fake_download(video_id, destination, settings):
        path = destination.with_suffix(".m4a")
        path.parent.mkdir(parents=True, exist_ok=True)
        make_audio(path)
        return downloader.DownloadResult(path, "mp4a.40.2", 128.0, converted=False)

    monkeypatch.setattr(downloader, "download", fake_download)
    monkeypatch.setattr(downloader, "check_tools", lambda: [])
    monkeypatch.setattr(tagging, "fetch_cover", lambda url: None)
    monkeypatch.setattr(update, "Matcher", lambda: Matcher(search))
    source = SourcePlaylist("Road Trip", [Track("One More Time", ["Daft Punk"], duration_ms=320_000, spotify_id="r" * 22)])
    monkeypatch.setattr(update, "read_source", lambda spec, client, full_reader=None: source)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: dialogs.append(self.text()) or QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: dialogs.append("eject?") or QMessageBox.StandardButton.No))

    library = Library(config.app_dir() / "library.db")
    library.add_playlist("Road Trip", SourceSpec("spotify", "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd"))
    library.close()

    window = MainWindow()
    window.update_sync_action.trigger()
    assert wait_until(app, lambda: window.job is not None)
    # Update, then (after it) the iPod read and the sync.
    assert wait_until(app, lambda: window.status_label.text() == "iPod synced." and window.job is None, 40)
    assert dialogs == ["eject?"]  # no confirmation: the sync only added a song and a playlist
    db = itunesdb.parse((root / "iPod_Control" / "iTunes" / "iTunesDB").read_bytes())
    assert "Road Trip" in [p.name for p in itunesdb.playlists(db)]
    window.close()


def test_browse_the_ipod(app, monkeypatch, tmp_path):
    """Plugging in an iPod lists its music, albums, artists, genres and playlists."""
    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import itunesdb
    from synclassic.ipod.edit import Editor
    from tests.test_ipod_read import fake_ipod, load

    db = itunesdb.parse(load("captured-album-index-36.b64"))
    songs = itunesdb.songs(db)
    editor = Editor(db)
    master = next(p for p in itunesdb.playlists(db) if p.is_master)
    itunesdb.set_text(master.chunk, itunesdb.TITLE, "Kerth's iPod")
    pid = editor.create_playlist("Yeah")
    editor.set_playlist_songs(pid, [(s.id, s.dbid) for s in songs[:5]])
    editor.finish()
    root = fake_ipod(tmp_path, itunesdb.serialize(db))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))

    window = MainWindow()
    assert wait_until(app, lambda: window.ipod_contents is not None)
    assert window.sidebar_entries() == ["Kerth's iPod", "Music", "Albums", "Artists", "Genres", "Yeah"]

    def select(key):
        for i in range(window.sidebar.topLevelItemCount()):
            item = window.sidebar.topLevelItem(i)
            if item.data(0, 256) == key:
                window.sidebar.setCurrentItem(item)

    select(("ipod", "music"))
    assert window.ipod_songs.model().rowCount() == 60
    window.search.setText("Track 001")
    assert window.ipod_songs.model().rowCount() == 1
    window.search.setText("")
    select(("ipodpl", pid))
    assert window.title.text() == "Yeah" and window.ipod_songs.model().rowCount() == 5
    select(("ipod", "albums"))
    assert len(window.albums_page.grid.albums) == 9
    select(("ipod", "artists"))
    assert window.artists_page.list.count() == len(window.artists_page.groups) + 1
    window.close()
