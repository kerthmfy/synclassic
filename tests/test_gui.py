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


def test_manage_ipod_playlists_from_the_window(app, monkeypatch, tmp_path):
    """New playlist from a selection, drag songs onto it, reorder by dragging, remove, delete from iPod."""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import itunesdb
    from tests.test_ipod_read import fake_ipod, load

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Road Trip", True)))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    database = root / "iPod_Control" / "iTunes" / "iTunesDB"
    songs = itunesdb.songs(itunesdb.parse(database.read_bytes()))

    def playlist_titles(name):
        db = itunesdb.parse(database.read_bytes())
        by_id = {s.id: s.title for s in itunesdb.songs(db)}
        view = next((p for p in itunesdb.playlists(db, 3) if p.name == name), None)  # the section the iPod reads
        return None if view is None else [by_id[i] for i in view.track_ids]

    window = MainWindow()
    assert wait_until(app, lambda: window.ipod_contents is not None)

    def settle():
        assert wait_until(app, lambda: window.job is None and window.ipod_contents is not None, 20)
        wait_until(app, lambda: False, 0.3)

    window.new_ipod_playlist([songs[0].id, songs[1].id])
    settle()
    assert playlist_titles("Road Trip") == [songs[0].title, songs[1].title]
    assert window.current_key[0] == "ipodpl" and window.title.text() == "Road Trip"  # it opened
    pid = window.current_key[1]

    assert window.sidebar.accepts_drop(("ipodpl", pid))
    window._songs_dropped(("ipodpl", pid), [songs[5].id, songs[0].id])  # drag onto it (songs[0] already there)
    settle()
    assert playlist_titles("Road Trip") == [songs[0].title, songs[1].title, songs[5].title]
    assert "Added 1 song" in window.status_label.text()

    window._reorder([songs[5].id], 0)  # drag the last song to the top
    settle()
    assert playlist_titles("Road Trip") == [songs[5].title, songs[0].title, songs[1].title]

    window._remove_from_playlist(window._ipod_playlist(pid), [songs[0].id])
    settle()
    assert playlist_titles("Road Trip") == [songs[5].title, songs[1].title]

    window._delete_from_ipod([songs[1].id])
    settle()
    assert playlist_titles("Road Trip") == [songs[5].title]
    assert len(itunesdb.songs(itunesdb.parse(database.read_bytes()))) == 59
    backups = list((window.library_path.parent / "ipod-backups").rglob("iTunesDB"))
    assert len(backups) == 1  # backed up once for the whole session
    window.close()


def test_maintenance_from_the_window(app, monkeypatch, tmp_path):
    """Get Info on several songs, Clean Up a leftover file, then restore a backup."""
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QMessageBox

    from synclassic.gui import maintenance_dialogs
    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import itunesdb

    from tests.test_ipod_read import fake_ipod, load

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    database = root / "iPod_Control" / "iTunes" / "iTunesDB"
    original = database.read_bytes()

    window = MainWindow()
    assert wait_until(app, lambda: window.ipod_contents is not None)

    def settle():
        assert wait_until(app, lambda: window.job is None, 20)
        wait_until(app, lambda: False, 0.4)

    # Get Info on the first three songs of Music: set the genre only.
    for i in range(window.sidebar.topLevelItemCount()):
        item = window.sidebar.topLevelItem(i)
        if item.data(0, 256) == ("ipod", "music"):
            window.sidebar.setCurrentItem(item)
    table = window.ipod_songs
    for row in range(3):
        table.selectionModel().select(table.model().index(row, 0),
                                      QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    chosen = table.selected_ids()

    def accept_with_genre(dialog):
        dialog.fields["genre"].setText("Jazz")
        dialog.fields["genre"].setProperty("touched", True)
        return maintenance_dialogs.InfoDialog.DialogCode.Accepted

    monkeypatch.setattr(maintenance_dialogs.InfoDialog, "exec", accept_with_genre)
    window.get_info(table)
    settle()
    songs = {s.id: s for s in itunesdb.songs(itunesdb.parse(database.read_bytes()))}
    assert [songs[i].genre for i in chosen] == ["Jazz"] * 3
    assert "Updated 3 songs" in window.status_label.text()

    # Clean Up: a leftover file is added back to the library.
    leftover = root / "iPod_Control" / "Music" / "F00" / "LOST.m4a"
    make_audio(leftover, seconds=2)
    dialog = maintenance_dialogs.CleanupDialog(window, window.ipod)
    assert wait_until(app, lambda: dialog.report is not None)
    assert dialog.report.leftovers == [leftover]
    dialog._add()
    settle()
    assert len(itunesdb.songs(itunesdb.parse(database.read_bytes()))) == 61

    # Backups: restore the one made before the first change.
    from synclassic.ipod import manage

    backups = manage.list_backups(window.ipod)
    assert len(backups) == 1
    window.restore_ipod_backup(backups[0].folder)
    settle()
    assert database.read_bytes() == original
    window.close()


def test_delete_a_synced_playlist_from_the_window(app, monkeypatch, tmp_path):
    """The case from the real iPod: a Synclassic playlist is deleted from the iPod (and from Synclassic)."""
    from PySide6.QtWidgets import QMessageBox

    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import itunesdb, manage
    from synclassic.ipod import sync as ipod_sync
    from synclassic.sources import SourceSpec
    from tests.test_ipod_read import fake_ipod, load

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes))
    from synclassic.ipod.device import ipod_at

    ipod = ipod_at(root)
    songs = itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes()))
    pid = manage.create_playlist(ipod, "OUT OF MY MIND", [songs[0].id])
    library = Library(config.app_dir() / "library.db")
    synced = library.add_playlist("OUT OF MY MIND", SourceSpec("spotify", "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd"))
    state = ipod_sync.load_state(ipod)
    state["playlists"][ipod_sync.playlist_key(synced)] = {"id": f"{pid:016x}", "name": "OUT OF MY MIND"}
    ipod_sync.save_state(ipod, state)
    library.close()

    window = MainWindow()
    assert wait_until(app, lambda: window.ipod_contents is not None)
    playlist = window._ipod_playlist(pid)
    assert playlist.synclassic and not window._editable_playlist(playlist)
    window._delete_ipod_playlist(playlist)
    assert wait_until(app, lambda: window.job is None, 20)
    wait_until(app, lambda: False, 0.3)
    assert "Remove it from Synclassic" in asked[0]
    names = [p.name for p in itunesdb.playlists(itunesdb.parse(ipod.database_path.read_bytes()))]
    assert "OUT OF MY MIND" not in names
    assert [p.name for p in window.library.playlists()] == []
    window.close()


def test_scrobble_now_from_the_window(app, monkeypatch):
    from synclassic import lastfm, scrobble
    from synclassic.gui.main_window import MainWindow
    from tests.test_lastfm import KEY, SECRET, FakeLastfm, entry, song

    now = time.time()
    scrobble.capture([song(1), song(2)], [entry(2, now - 900), entry(1, now - 60)])
    lastfm.save_credentials({"api_key": KEY, "secret": SECRET, "session_key": "SESSION", "user": "kerth"})
    server = FakeLastfm()
    monkeypatch.setattr(lastfm.Client, "from_saved", classmethod(lambda cls: cls(KEY, SECRET, "SESSION", session=server)))

    window = MainWindow()
    assert window.scrobble_button.text() == "Scrobble Now (3)"
    window.scrobble_now()
    assert wait_until(app, lambda: window.job is None, 20)
    wait_until(app, lambda: False, 0.2)
    assert window.status_label.text() == "Scrobbled 3 plays to Last.fm"
    assert len(server.scrobbled) == 3 and window.scrobble_button.text() == "Scrobble Now"
    window.close()


def test_settings_has_tabs_and_saves_lastfm_keys(app):
    from synclassic import lastfm
    from synclassic.gui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["General", "Spotify", "Last.fm"]
    dialog.lastfm_key.setText("a" * 32)
    dialog.lastfm_secret.setText("b" * 32)
    dialog._save()
    assert lastfm.load_credentials() == {"api_key": "a" * 32, "secret": "b" * 32}


def test_double_click_plays_from_the_ipod(app, monkeypatch, tmp_path):
    from synclassic.gui import player as player_module
    from synclassic.gui.main_window import MainWindow
    from tests.test_ipod_read import fake_ipod, load
    from tests.test_player import FakeBackend

    monkeypatch.setattr(player_module.Player, "_make_backend", lambda self: FakeBackend())
    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    window = MainWindow()
    assert wait_until(app, lambda: window.ipod_contents is not None)
    for i in range(window.sidebar.topLevelItemCount()):
        item = window.sidebar.topLevelItem(i)
        if item.data(0, 256) == ("ipod", "music"):
            window.sidebar.setCurrentItem(item)
    # The sample database has no file paths; give every song one so it can be "played".
    for song in window.ipod_contents.songs.values():
        song.path = song.path or f"iPod_Control/Music/F00/{song.id}.m4a"
    index = window.ipod_songs.model().index(2, 0)
    window.ipod_songs.doubleClicked.emit(index)
    expected = index.data()
    assert window.player.current.title == expected
    assert window.status_label.text() == expected and window.lcd.seek_row.isVisibleTo(window)
    assert window.ipod_songs.source.playing_id == window.player.current.key[1]
    window.play_pause()
    assert window.play_action.text() == "Play"
    window.player.next()
    assert window.player.current.title == window.ipod_songs.model().index(3, 0).data()
    window.close()
