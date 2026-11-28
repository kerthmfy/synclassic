"""Save to: this Mac, this Mac and the iPod, or the iPod only."""

import pathlib
import sqlite3

from synclassic import cli, config, destinations
from synclassic.ipod import itunesdb, rockbox, sync
from synclassic.ipod.device import ipod_at
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from synclassic.update import needs_work
from tests.conftest import make_audio
from tests.test_ipod_read import fake_ipod, load
from tests.test_ipod_sync import ipod, library_with, playlist_titles, read_db, song  # noqa: F401  (fixture)


def test_older_libraries_get_the_new_column(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE playlists (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, source_kind TEXT NOT NULL,"
                     " source_ref TEXT NOT NULL, source_selector TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL,"
                     " refreshed_at TEXT, note TEXT);"
                     "INSERT INTO playlists (name, source_kind, source_ref, added_at) VALUES ('Old', 'm3u', '/x', 'now');")
    db.close()
    library = Library(path)
    (playlist,) = library.playlists()
    assert playlist.destination == "" and destinations.of_playlist(playlist) == "mac"
    library.set_destination(playlist, "ipod")
    assert Library(path).playlists()[0].destination == "ipod"


def test_ipod_only_songs_leave_this_mac_once_safely_on_the_ipod(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1), song(2)], "Chill": [song(2), song(3)]})
    gym = library.playlist_by_name("Gym")
    library.set_destination(gym, "ipod")
    assert destinations.ipod_only_keys(library) == {library.playlist_tracks(gym)[0].key}  # song 2 is in Chill too
    mine = make_audio(tmp_path / "mine.m4a")  # your own file in an iPod-only playlist: never removed
    own = library.add_playlist("Own", SourceSpec("m3u", "/own"))
    from synclassic.update import apply_source

    track = Track("My Own Song", ["Me"], duration_ms=1000)
    track.local_path = str(mine)
    apply_source(library, own, SourcePlaylist("Own", [track]))
    library.set_destination(own, "ipod")

    files = {i.track.title: pathlib.Path(i.file_path) for i in library.tracks_in_playlists()}
    messages = []
    sync.apply_plan(library, sync.make_plan(library, ipod), messages.append)
    after = {i.track.title: i for i in library.tracks_in_playlists()}
    assert after["New Song 1"].status == "ipod" and after["New Song 1"].file_path is None
    assert not files["New Song 1"].exists()
    assert after["New Song 2"].has_file and after["New Song 3"].has_file  # in a playlist kept on this Mac
    assert after["My Own Song"].status == "local" and mine.is_file()
    assert any("now only on the iPod" in m for m in messages)
    assert not needs_work(after["New Song 1"], retry_failed=True)
    assert playlist_titles(read_db(ipod), "Gym") == ["New Song 1", "New Song 2"]
    plan = sync.make_plan(library, ipod)  # the iPod copy counts: nothing to do, nothing to download
    assert not plan.has_changes and not plan.not_ready

    # Another iPod without the song: it's downloaded again for that one.
    other = ipod_at(fake_ipod(tmp_path / "other", load("captured-album-index-36.b64")))
    assert destinations.reclaim_for(library, other) == 1
    assert library.track(after["New Song 1"].id).status == "pending"


def test_rockbox_ipod_only(tmp_path):
    from tests.test_rockbox import rockbox_ipod

    device = rockbox_ipod(tmp_path)
    library = library_with(tmp_path, {"Run": [song(1)]})
    library.set_destination(library.playlist_by_name("Run"), "ipod")
    (item,) = library.playlist_tracks(library.playlist_by_name("Run"))
    rockbox.apply(library, rockbox.make_plan(library, device), lambda s: None)
    assert library.track(item.id).status == "ipod" and not pathlib.Path(item.file_path).exists()
    assert (device.root / "Music" / "Synclassic Artist" / "New Album" / "01 New Song 1.m4a").is_file()
    assert not rockbox.make_plan(library, device).has_changes


def test_terminal_copies_new_songs_to_the_ipod(tmp_path, ipod, monkeypatch, capsys):
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))
    library = library_with(tmp_path, {"Gym": [song(1)]})
    gym = library.playlist_by_name("Gym")
    cli._send_to_ipod(library, [gym])  # "this Mac": nothing happens
    assert playlist_titles(read_db(ipod), "Gym") is None
    library.set_destination(gym, "both")
    cli._send_to_ipod(library, [gym])
    assert playlist_titles(read_db(ipod), "Gym") == ["New Song 1"]
    assert library.playlist_tracks(gym)[0].has_file  # both: the Mac keeps its copy
    out = capsys.readouterr().out
    assert "Copying the new songs" in out

    library.remove_playlist(gym)  # a sync now would remove songs: it waits for you
    keep = library.add_playlist("Keep", SourceSpec("m3u", "/k"))
    library.set_destination(keep, "both")
    cli._send_to_ipod(library, [keep])
    assert "it waits for you" in capsys.readouterr().out
    assert playlist_titles(read_db(ipod), "Gym") == ["New Song 1"]

    config.save_settings(config.Settings(download_to="ipod"))
    assert destinations.of_playlist(keep) == "both" and destinations.default() == "ipod"


def test_app_copies_after_downloading_and_the_choices_show(tmp_path, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from synclassic.gui.add_dialog import AddDialog
    from synclassic.gui.main_window import MainWindow

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    library_with(tmp_path, {"Gym": [song(1)], "Chill": [song(2)]}).close()
    window = MainWindow()
    try:
        syncs = []
        monkeypatch.setattr(window, "sync_ipod", lambda quick=False: syncs.append(quick))
        window._send_new_songs_to_ipod(None)
        assert syncs == []  # everything is "this Mac"
        window.library.set_destination(window.library.playlist_by_name("Chill"), "both")
        window._send_new_songs_to_ipod([window.library.playlist_by_name("Gym").id])
        assert syncs == []
        window._send_new_songs_to_ipod(None)
        assert syncs == [True]
        assert window.find_page.save_to.count() == 3
        dialog = AddDialog(window)
        assert [dialog.save_to.itemData(i) for i in range(3)] == ["mac", "both", "ipod"]
        dialog.reject()
    finally:
        window.close()
        app.processEvents()
