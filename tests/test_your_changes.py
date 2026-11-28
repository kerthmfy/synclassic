"""Your own changes: songs added to, moved between and taken out of playlists, and song details."""

import os
import pathlib

from mutagen.mp4 import MP4

from synclassic import config, tagging
from synclassic.ipod import itunesdb, sync
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from synclassic.update import apply_source, refresh_playlist
from tests.conftest import make_audio
from tests.test_ipod_sync import ipod, library_with, playlist_titles, read_db, song  # noqa: F401  (fixture)

LINK = "https://open.spotify.com/playlist/"


def titles(library, playlist):
    return [i.track.title for i in library.playlist_tracks(playlist)]


def test_added_and_removed_songs_stay_that_way_after_an_update(tmp_path):
    library = library_with(tmp_path, {"Gym": [song(1), song(2), song(3)], "Chill": [song(4)]})
    gym, chill = library.playlist_by_name("Gym"), library.playlist_by_name("Chill")
    four = library.playlist_tracks(chill)[0].id
    two = library.playlist_tracks(gym)[1].id
    assert library.add_to_playlist(gym, [four, four]) == 1 and library.add_to_playlist(gym, [four]) == 0
    assert library.remove_from_playlist(gym, [two]) == 1
    assert titles(library, gym) == ["New Song 1", "New Song 3", "New Song 4"]

    # Spotify's version changes (song 5 added); yours stay on top of it.
    apply_source(library, gym, SourcePlaylist("Gym", [song(1), song(2), song(3), song(5)]))
    assert titles(library, gym) == ["New Song 1", "New Song 3", "New Song 5", "New Song 4"]

    # Taking out a song you added just forgets it; putting back one you took out undoes that.
    library.remove_from_playlist(gym, [four])
    library.add_to_playlist(gym, [two])
    apply_source(library, gym, SourcePlaylist("Gym", [song(1), song(2), song(3), song(5)]))
    assert titles(library, gym) == ["New Song 1", "New Song 2", "New Song 3", "New Song 5"]


def test_move_and_your_own_playlists(tmp_path):
    library = library_with(tmp_path, {"Gym": [song(1), song(2)], "Chill": [song(3)]})
    gym, chill = library.playlist_by_name("Gym"), library.playlist_by_name("Chill")
    one = library.playlist_tracks(gym)[0].id
    library.add_to_playlist(chill, [one])
    library.remove_from_playlist(gym, [one])  # = Move to Playlist
    assert titles(library, gym) == ["New Song 2"] and titles(library, chill) == ["New Song 3", "New Song 1"]

    mine = library.new_playlist("Favourites", [one, library.playlist_tracks(chill)[0].id])
    assert mine.spec.kind == "mine" and mine.spec.describe() == "Your own playlist"
    report = refresh_playlist(library, mine, None)  # Update leaves it as you made it
    assert not report.error and titles(library, mine) == ["New Song 1", "New Song 3"]
    library.remove_playlist(mine)
    assert library.db.execute("SELECT COUNT(*) FROM playlist_changes WHERE playlist_id = ?", (mine.id,)).fetchone()[0] == 0


def test_details_you_change_win_over_spotify_and_go_in_the_file(tmp_path):
    library = library_with(tmp_path, {"Gym": [song(1)]})
    gym = library.playlist_by_name("Gym")
    (item,) = library.playlist_tracks(gym)
    tagging.tag_file(pathlib.Path(item.file_path), item.track, None)
    from synclassic import lyrics

    lyrics.write(pathlib.Path(item.file_path), "la la")
    track = library.edit_track(item.id, {"title": "Better Name", "artist": "Band, Friend", "year": 1999,
                                         "track_number": 7, "genre": "OPM"})
    assert (track.title, track.artists, track.year, track.track_number, track.genre) == (
        "Better Name", ["Band", "Friend"], "1999", 7, "OPM")
    tagging.update_details(pathlib.Path(item.file_path), track)
    tags = MP4(item.file_path).tags
    assert tags["\xa9nam"] == ["Better Name"] and tags["\xa9ART"] == ["Band, Friend"] and tags["trkn"] == [(7, 0)]
    assert lyrics.read(pathlib.Path(item.file_path)) == "la la"  # everything else inside is kept

    apply_source(library, gym, SourcePlaylist("Gym", [song(1)]))  # Spotify says "New Song 1" again
    again = library.track(item.id).track
    assert again.title == "Better Name" and again.genre == "OPM" and again.album == "New Album"
    assert library.edit_track(item.id, {"title": ""}).title == "Better Name"  # a song keeps a name


def test_changed_details_go_on_the_ipod_at_the_next_sync(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    first = library.playlist_tracks(library.playlist_by_name("Gym"))[0]
    assert not sync.make_plan(library, ipod).has_changes
    library.edit_track(first.id, {"title": "Renamed", "album": "Other Album", "year": 2001})
    plan = sync.make_plan(library, ipod)
    assert len(plan.details) == 1 and plan.only_adds
    assert "Update the details of 1 song you changed" in "\n".join(plan.summary())
    sync.apply_plan(library, plan, lambda s: None)
    song_there = {s.title: s for s in itunesdb.songs(read_db(ipod))}["Renamed"]
    assert song_there.album == "Other Album" and song_there.chunk.u32(0x34) == 2001
    assert playlist_titles(read_db(ipod), "Gym") == ["Renamed", "New Song 2"]
    assert not sync.make_plan(library, ipod).has_changes  # done


def test_from_the_window(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from synclassic.gui.main_window import MainWindow
    from synclassic.gui.maintenance_dialogs import InfoDialog

    library_with(tmp_path, {"Gym": [song(1), song(2)], "Chill": [song(3)]}).close()
    window = MainWindow()
    try:
        gym = window.library.playlist_by_name("Gym")
        chill = window.library.playlist_by_name("Chill")
        ids = [i.id for i in window.library.playlist_tracks(gym)]
        window.put_in_playlist(ids[:1], chill, move_from=gym)
        assert titles(window.library, chill) == ["New Song 3", "New Song 1"]
        assert titles(window.library, gym) == ["New Song 2"]
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Mix", True)))
        window.new_playlist_from(ids)
        assert titles(window.library, window.library.playlist_by_name("Mix")) == ["New Song 1", "New Song 2"]

        def edit(dialog):
            dialog.fields["genre"].setText("Rock")
            dialog.fields["genre"].setProperty("touched", True)
            return InfoDialog.DialogCode.Accepted

        monkeypatch.setattr(InfoDialog, "exec", edit)
        window.library_get_info(ids)
        assert {window.library.track(i).track.genre for i in ids} == {"Rock"}
        assert MP4(window.library.track(ids[0]).file_path).tags["\xa9gen"] == ["Rock"]
    finally:
        window.close()
        app.processEvents()
