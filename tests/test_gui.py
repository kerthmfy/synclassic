"""Drive the real window: add a playlist and let the background update download it."""

import os
import time

import pytest

from tests.test_ipod_sync import ipod  # noqa: F401  (fixture: a fake iPod with its own "Old Mix" playlist)

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

    assert window.sidebar_entries() == ["Find Music", "Podcasts", "Listening Stats", "Needs Attention (1)", "Mix (1)"]  # (count) = songs not ready
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
    from synclassic.gui.sync_plan_dialog import SyncPlanDialog

    def answer(dialog):
        rows = [dialog.table.topLevelItem(i) for i in range(dialog.table.topLevelItemCount())]
        shown.append([(r.text(0), r.text(1), r.text(2)) for r in rows])
        return SyncPlanDialog.DialogCode.Accepted

    monkeypatch.setattr(SyncPlanDialog, "exec", answer)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    window = MainWindow()
    window.sync_ipod()
    assert wait_until(app, lambda: window.job is not None or bool(shown), 20)
    assert wait_until(app, lambda: window.job is None, 30)
    assert shown[0] == [("Gym", "● New on the iPod", "2")]
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
    assert window.sidebar_entries() == ["Find Music", "Podcasts", "Listening Stats", "Kerth's iPod", "Music", "Albums", "Artists", "Genres", "Yeah"]

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
    dialog.lastfm_own.setChecked(True)
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


def test_podcasts_from_the_window(app, monkeypatch, tmp_path):
    import requests

    from synclassic.gui import player as player_module
    from synclassic.gui import podcast_dialog
    from synclassic.gui.main_window import PODCASTS, MainWindow
    from synclassic.podcasts.store import Podcasts
    from tests.test_player import FakeBackend
    from tests.test_podcasts import FEED_URL, Internet

    internet = Internet()
    monkeypatch.setattr(requests, "get", internet.get)
    monkeypatch.setattr(player_module.Player, "_make_backend", lambda self: FakeBackend())

    # The search dialog finds the show.
    dialog = podcast_dialog.SubscribeDialog(None, search=lambda text: podcast_dialog.feeds.search(text, internet))
    dialog.query.setText("sample")
    dialog.run_search()
    assert wait_until(app, lambda: dialog.results.count() == 1)
    dialog._accept_selection()
    assert dialog.feed_urls == [FEED_URL]

    class Chosen:
        feed_urls = [FEED_URL]

        def __init__(self, parent):
            pass

        def exec(self):
            return True

    monkeypatch.setattr(podcast_dialog, "SubscribeDialog", Chosen)
    window = MainWindow()
    window.show()
    window.subscribe_podcast()
    assert wait_until(app, lambda: window.job is None)
    assert window.current_key == PODCASTS and window.stack.currentIndex() == 7
    assert window.sidebar_entries()[1] == "Podcasts (2)"  # two new episodes downloaded
    page = window.podcasts_page
    show = page.topLevelItem(0)
    assert show.text(0) == "Sample Talk Show" and show.childCount() == 2
    assert [show.child(i).text(4) for i in range(2)] == ["New", "New"]

    # Play the newest episode to the end: it's marked played (and not scrobbled).
    from synclassic.gui.podcasts_view import ID_ROLE

    episode_id = show.child(0).data(0, ID_ROLE)
    window._play_episode(episode_id)
    assert window.player.current.podcast and window.player.current.title == "Episode 2: Later"
    window.player.finished_item.emit(window.player.current)
    assert wait_until(app, lambda: page.topLevelItem(0).child(0).text(4) == "Played")
    assert Podcasts(window.library).new_count() == 1

    window.podcasts_update_action.trigger()  # the button: Qt passes its "checked" flag
    assert wait_until(app, lambda: window.job is None)
    log = window.log.toPlainText()
    assert "Error" not in log and "Podcasts are up to date." in log

    page.apply_search("earlier")
    assert page.topLevelItem(0).child(0).isHidden() and not page.topLevelItem(0).child(1).isHidden()
    window.close()


def test_fix_album_art_from_the_window(app, monkeypatch, tmp_path):
    from synclassic.gui.art_dialog import ArtFixDialog
    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import art_fix, itunesdb
    from synclassic.ipod.device import ipod_at
    from tests.test_art_fix import Web, small_cover_library
    from tests.test_ipod_artwork import video_ipod_artwork
    from tests.test_ipod_read import fake_ipod, load

    raw = load("captured-album-index-36.b64")
    root = fake_ipod(tmp_path, raw)
    video_ipod_artwork(root, itunesdb.songs(itunesdb.parse(raw))[0].dbid)
    small_cover_library(tmp_path).close()
    web = Web()
    real = art_fix.CoverFinder
    monkeypatch.setattr(art_fix, "CoverFinder", lambda: real(web.get, web.fetch, sleep=lambda s: None))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    window = MainWindow()
    assert wait_until(app, lambda: window.ipod_contents is not None)
    dialog = ArtFixDialog(window, window.ipod, window.library_path)
    assert wait_until(app, lambda: dialog.worker is None and dialog.list.topLevelItemCount() > 0, 30)
    assert "sharp covers found for 3" in dialog.status.text()
    assert len(dialog.chosen()) == 3 and dialog.replace_button.text() == "Replace 3 Covers"
    dialog.replace_button.click()
    assert wait_until(app, lambda: window.job is None, 30)
    assert "New covers for 3 songs" in window.log.toPlainText()
    left = [a for a in art_fix.scan(ipod_at(root)) if a.title.startswith("Album ")]
    assert left == []
    window.close()


def test_cover_flow_moves_and_shows_the_album(app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QKeyEvent
    from PySide6.QtCore import QEvent

    from synclassic.gui.ipod_views import AlbumsPage
    from synclassic.ipod.browse import Album, Contents, Song

    songs = {n: Song(id=n, dbid=n, title=f"Song {n}", artist="A", album=f"Album {n}", album_artist="A", genre="",
                     length_ms=1000, track_number=1, disc_number=1, year=2000, plays=0, rating=0, added=None,
                     size=1, kind="song", path="") for n in range(3)}
    albums = [Album(f"Album {n}", "A", [n]) for n in range(3)]
    contents = Contents("iPod", songs, [], {})
    contents.albums = lambda: albums
    page = AlbumsPage()
    page.resize(800, 600)
    page.set_contents(contents)
    page.show_view("flow")
    page.set_cover(1, QImage(50, 50, QImage.Format.Format_RGB32))
    page.flow.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
    assert page.flow.view.current() == 1 and page.songs.song_ids_in_view() == [1]
    page.filter("Album 2")
    assert page.flow.view.visible == [2]
    page.grab()  # draws without errors


def test_mini_player_and_menu_bar_follow_playback(app, monkeypatch):
    import pathlib

    from synclassic.gui import player as player_module
    from synclassic.gui.main_window import MainWindow
    from synclassic.gui.player import PlayItem
    from tests.test_player import FakeBackend

    monkeypatch.setattr(player_module.Player, "_make_backend", lambda self: FakeBackend())
    window = MainWindow()
    assert window.menu_bar_player is not None  # on by default
    window.show_mini_player()
    window.player.play_list([PlayItem(pathlib.Path("/x.m4a"), "Song", "Artist", "Album", length_ms=200_000, key=("lib", 1))])
    assert window.mini_player.lcd.primary.text() == "Song"
    assert window.menu_bar_player.now.text() == "Song" and "Artist" in window.menu_bar_player.line.text()
    window.close()
    assert not window.mini_player.isVisible()


def test_find_music_from_the_window(app, monkeypatch):
    from PySide6.QtGui import QColor, QImage

    from synclassic.gui import store_view
    from synclassic.gui.main_window import FIND, MainWindow
    from synclassic.sources import store
    from tests.test_store import Catalogue

    web = Catalogue()
    real_songs, real_albums, real_tracks = store.search_songs, store.search_albums, store.album_tracks
    real_details = store.song_details
    monkeypatch.setattr(store, "search_songs", lambda term, country=None: real_songs(term, "US", web))
    monkeypatch.setattr(store, "search_albums", lambda term, country=None: real_albums(term, "US", web))
    monkeypatch.setattr(store, "album_tracks", lambda album_id, country=None: real_tracks(album_id, "US", web))
    monkeypatch.setattr(store, "song_details", lambda song, session=None: real_details(song, web))

    def covers(urls, size):
        image = QImage(size, size, QImage.Format.Format_RGB32)
        image.fill(QColor("#c03"))
        return [(i, image) for i, url in enumerate(urls) if url]

    monkeypatch.setattr(store_view, "_fetch_images", covers)
    window = MainWindow()
    started = []
    monkeypatch.setattr(window, "start_update", lambda ids, **kw: started.append(ids))
    window.current_key = FIND
    window._show_current()
    page = window.find_page
    page.query.setText("daft punk")
    page.search()
    assert wait_until(app, lambda: page.song_list.topLevelItemCount() == 2)
    get = page.song_list.itemWidget(page.song_list.topLevelItem(0), 5)
    assert get.text() == "Get"
    get.click()
    assert wait_until(app, lambda: bool(started))
    assert window.library.playlists()[0].name == store.SONGS_NAME
    item = window.library.tracks_in_playlists()[0]
    assert (item.track.track_number, item.track.genre) == (1, "Electro")  # details looked up before adding

    page.set_kind("albums")
    assert wait_until(app, lambda: page.album_grid.count() == 2)
    page.open_album(page.albums[0])
    assert wait_until(app, lambda: page.track_list.topLevelItemCount() == 2)
    page.get_album_button.click()
    names = [p.name for p in window.library.playlists()]
    assert "Discovery — Daft Punk" in names and len(started) == 2
    window.close()


def test_sync_animation_shows_and_stops(app):
    from synclassic.gui.main_window import MainWindow
    from synclassic.gui.sounds import chime_file

    window = MainWindow()
    window._sync_animation(True)
    assert window._spin_timer.isActive() and window.ipod_graphic.sync_progress == 0.0
    window.ipod_graphic.grab()  # the "Do not disconnect." screen draws
    window._sync_animation(False)
    assert not window._spin_timer.isActive() and window.ipod_graphic.sync_progress is None
    assert chime_file().stat().st_size > 50_000
    window.close()


def test_virtual_ipod_menus_wheel_and_play(app, tmp_path):
    import math

    from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
    from PySide6.QtGui import QMouseEvent

    from synclassic.gui.virtual_ipod import VirtualIPod
    from synclassic.gui.widgets import ipod_geometry
    from synclassic.ipod import browse
    from synclassic.ipod.device import ipod_at
    from tests.test_ipod_read import fake_ipod, load

    contents = browse.read(ipod_at(fake_ipod(tmp_path, load("captured-album-index-36.b64"))))
    ipod = VirtualIPod()
    ipod.resize(240, 392)
    ipod.set_contents(contents)
    played = []
    ipod.play_requested.connect(lambda ids, index: played.append((ids, index)))
    assert [i.label for i in ipod.stack[-1].items][:1] == ["Music"]

    _, center, radius, inner = ipod_geometry(QRectF(ipod.rect()).adjusted(10, 10, -10, -10))

    def mouse(kind, point):
        event_type = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                      "release": QEvent.Type.MouseButtonRelease}[kind]
        buttons = Qt.MouseButton.NoButton if kind == "release" else Qt.MouseButton.LeftButton
        event = QMouseEvent(event_type, point, point, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        {"press": ipod.mousePressEvent, "move": ipod.mouseMoveEvent, "release": ipod.mouseReleaseEvent}[kind](event)

    def click_center():
        mouse("press", center)
        mouse("release", center)

    click_center()  # Music
    assert ipod.stack[-1].title == "Music"
    ring = (radius + inner) / 2
    # Circle the wheel clockwise about 60 degrees: down 3 rows (Playlists -> Songs).
    for degrees in range(-90, -25, 5):
        point = QPointF(center.x() + ring * math.cos(math.radians(degrees)),
                        center.y() + ring * math.sin(math.radians(degrees)))
        mouse("press" if degrees == -90 else "move", point)
    mouse("release", point)
    assert ipod.stack[-1].items[ipod.stack[-1].selected].label == "Songs"
    click_center()
    assert ipod.stack[-1].title == "Songs"
    ipod.scroll(2)
    click_center()
    ids, index = played[0]
    assert index == 2 and len(ids) == len([s for s in contents.songs.values() if s.kind == "song"])
    assert ipod.now_playing
    top = QPointF(center.x(), center.y() - ring)  # MENU
    mouse("press", top)
    mouse("release", top)
    assert not ipod.now_playing and ipod.stack[-1].title == "Songs"
    ipod.back()
    ipod.back()
    assert ipod.stack[-1].title == contents.name
    ipod.grab()


def test_find_music_covers_survive_switching_views(app, monkeypatch):
    import io

    from PIL import Image

    from synclassic.gui import store_view
    from synclassic.sources import store

    webp = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 30, 30)).save(webp, "WEBP")

    class Session:
        headers = {}

        def get(self, url, timeout=None):
            from types import SimpleNamespace

            return SimpleNamespace(status_code=200, content=webp.getvalue())

    import requests

    monkeypatch.setattr(requests, "Session", Session)
    assert not store_view._decode(webp.getvalue()).isNull()  # Pillow reads what Qt might not

    albums = [store.StoreAlbum(f"dz:{n}", f"Album {n}", "A", "2001", "", 1, f"https://x/{n}/1000x1000-000000-80-0-0.jpg",
                               False) for n in range(3)]
    monkeypatch.setattr(store, "search_albums", lambda term, country=None: albums)
    monkeypatch.setattr(store, "album_tracks", lambda album_id, country=None: (albums[0], []))
    page = store_view.FindMusicPage(lambda: "", lambda song: False)
    page.query.setText("x")
    page.set_kind("albums")
    assert wait_until(app, lambda: page.album_grid.count() == 3)
    page.open_album(albums[0])  # opening an album right away must not lose the grid's covers
    from PySide6.QtGui import QColor

    def reddish(i):
        color = QColor(page.album_grid.item(i).icon().pixmap(150, 150).toImage().pixel(75, 75))
        return color.red() > 150 and color.green() < 80

    assert wait_until(app, lambda: all(reddish(i) for i in range(3)))


def test_choose_playlists_for_this_ipod_and_switch_between_ipods(app, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt

    from synclassic.gui.ipod_choices import PlaylistChoiceDialog
    from synclassic.gui.main_window import MainWindow
    from synclassic.ipod import prefs
    from synclassic.ipod.device import ipod_at
    from synclassic.ipod.sync import playlist_key
    from tests.test_ipod_read import fake_ipod, load
    from tests.test_ipod_sync import library_with, song

    first = ipod_at(fake_ipod(tmp_path / "a", load("captured-album-index-36.b64")))
    other = fake_ipod(tmp_path / "b", load("captured-album-index-36.b64"))
    second = ipod_at(other.rename(other.with_name("Gym iPod")))
    connected = [first]
    monkeypatch.setattr("synclassic.ipod.device.find_ipods", lambda: list(connected))
    library_with(tmp_path, {"Gym": [song(1), song(2)], "Chill": [song(3)]}).close()

    window = MainWindow()
    assert window.ipod.root == first.root and window.ipod_picker.isHidden()
    assert window.ipod_choice_label.text() == "Syncing: All playlists · songs as they are"

    dialog = PlaylistChoiceDialog(window.library, window.ipod, window)
    assert dialog.every.isChecked() and not dialog.list.isEnabled() and "3 songs" in dialog.total.text()
    dialog.only.setChecked(True)
    for row in range(dialog.list.count()):
        item = dialog.list.item(row)
        if not item.text().startswith("Gym"):
            item.setCheckState(Qt.CheckState.Unchecked)
    dialog.size.setCurrentIndex(3)  # 128 kbps
    assert "2 songs" in dialog.total.text()
    dialog._save()
    saved = prefs.load(first)
    assert saved.playlists == [playlist_key(window.library.playlist_by_name("Gym"))] and saved.bitrate == 128

    connected.append(second)  # a second iPod is plugged in: it's shown, and the picker appears
    window._detect_ipod()
    assert window.ipod.root == second.root and not window.ipod_picker.isHidden() and window.ipod_picker.count() == 2
    assert window.ipod_choice_label.text() == "Syncing: All playlists · songs as they are"  # its own choice
    window.ipod_picker.setCurrentIndex(0)
    window._ipod_picked(0)
    assert window.ipod.root == first.root
    assert window.ipod_choice_label.text() == "Syncing: 1 chosen playlist · new songs as 128 kbps AAC"
    window._detect_ipod()  # nothing changed: stays on the one you picked
    assert window.ipod.root == first.root
    window.close()


def test_appearance_switches_live(app):
    from synclassic import config
    from synclassic.gui import theme
    from synclassic.gui.main_window import MainWindow

    window = MainWindow()
    try:
        assert theme.apply(app, "dark") == "dark" and theme.is_dark()
        assert theme.c("window") in app.styleSheet() and app.palette().window().color().name() == "#2b2b2d"
        assert theme.SOURCE_LIST == theme.MIDNIGHT["source_list"]  # the old names follow the palette
        assert theme.apply(app, "graphite") == "graphite" and theme.c("select_bottom") == "#6c7a90"
        assert theme.resolve("auto", system_dark=True) == "dark" and theme.resolve("auto") == "aqua"
        assert theme.resolve("nonsense") == "aqua"
        settings = config.Settings()
        config.set_setting(settings, "appearance", "Dark")
        assert settings.appearance == "dark"
    finally:
        theme.apply(app, "aqua")
        window.close()


def test_a_waiting_playlist_is_mentioned_after_an_automatic_sync(app, monkeypatch, tmp_path, ipod):
    from PySide6.QtWidgets import QMessageBox

    from synclassic import config
    from synclassic.gui.main_window import MainWindow
    from synclassic.library import Library
    from synclassic.models import SourcePlaylist, Track
    from synclassic.sources import SourceSpec
    from synclassic.update import apply_source
    from tests.conftest import make_audio
    from tests.test_ipod_sync import song

    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))  # has its own "Old Mix" playlist of 3 songs
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("old mix", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "5" * 22))
    apply_source(library, playlist, SourcePlaylist("old mix", [song(1), Track("Lost", ["Nobody"], spotify_id="l" * 22)]))
    first, lost = library.playlist_tracks(playlist)
    make_audio(tmp_path / "a.m4a", seconds=2)
    library.update_track(first.id, status="downloaded", file_path=str(tmp_path / "a.m4a"))
    library.update_track(lost.id, status="unmatched")
    library.close()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, *a, **k: asked.append(text) or QMessageBox.StandardButton.No))
    window = MainWindow()
    try:
        window.sync_ipod(quick=True)
        assert wait_until(app, lambda: bool(asked), 30)
        assert "“old mix” was left as it is" in asked[0] and "1 not found" in asked[0]
        assert "⏸ “old mix”" in window.log.toPlainText()
    finally:
        window.close()


def test_sync_preview_window_replace_anyway_and_not_found(app, monkeypatch, tmp_path, ipod):
    from synclassic import config
    from synclassic.gui.main_window import ATTENTION, MainWindow
    from synclassic.gui.sync_plan_dialog import SyncPlanDialog
    from synclassic.ipod import prefs
    from synclassic.ipod import sync as ipod_sync
    from synclassic.library import Library
    from synclassic.models import SourcePlaylist, Track
    from synclassic.sources import SourceSpec
    from synclassic.update import apply_source
    from tests.conftest import make_audio
    from tests.test_ipod_sync import song

    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("old mix", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "2" * 22))
    apply_source(library, playlist, SourcePlaylist("old mix", [song(1), Track("Lost", ["Nobody"], spotify_id="l" * 22)]))
    first, lost = library.playlist_tracks(playlist)
    make_audio(tmp_path / "a.m4a", seconds=2)
    library.update_track(first.id, status="downloaded", file_path=str(tmp_path / "a.m4a"))
    library.update_track(lost.id, status="unmatched")
    plan = ipod_sync.make_plan(library, ipod)

    dialog = SyncPlanDialog(plan)
    row = dialog.table.topLevelItem(0)
    assert (row.text(0), row.text(1)) == ("old mix", "● Waiting: left as it is")
    assert "1 not found" in row.text(3) and "would have only 1 song" in row.toolTip(1)
    tick = next(iter(dialog.ticks.values()))
    tick.setChecked(True)
    assert dialog.replace == [ipod_sync.playlist_key(playlist)] and dialog.sync.text() == "Update the Plan"
    dialog.reject()

    window = MainWindow()
    try:
        resyncs = []
        monkeypatch.setattr(window, "sync_ipod", lambda quick=False: resyncs.append(quick))

        def tick_and_close(self):
            next(iter(self.ticks.values())).setChecked(True)
            return SyncPlanDialog.DialogCode.Accepted

        monkeypatch.setattr(SyncPlanDialog, "exec", tick_and_close)
        window._review_sync(plan)
        assert prefs.load(ipod).replace == [ipod_sync.playlist_key(playlist)]
        assert wait_until(app, lambda: resyncs == [False], 5)  # the plan again, now replacing it
        replanned = ipod_sync.make_plan(window.library, ipod)
        assert replanned.playlists[0].action == "take over" and not replanned.waiting

        def show_songs(self):
            self._fix_not_found()
            return SyncPlanDialog.DialogCode.Rejected

        monkeypatch.setattr(SyncPlanDialog, "exec", show_songs)
        window._review_sync(replanned)
        assert window.current_key == ATTENTION
    finally:
        window.close()
