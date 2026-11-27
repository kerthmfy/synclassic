"""Genres and years from Deezer (and Apple Music), for downloads and songs already there."""

import pathlib
from types import SimpleNamespace

from synclassic import song_info
from synclassic.ipod import info_fill, itunesdb, sync
from synclassic.ipod.check import check
from synclassic.models import Track
from tests.test_ipod_sync import ipod, library_with, read_db, song  # noqa: F401


class Catalogue:
    def __init__(self):
        self.asked = []

    def get(self, url, params):
        self.asked.append(url)
        if url == song_info.DEEZER_SEARCH:
            if 'track:"New Song 1"' in params["q"]:
                return SimpleNamespace(status_code=200, json=lambda: {"data": [
                    {"title": "New Song 1", "artist": {"name": "Synclassic Artist"}, "duration": 2, "album": {"id": 7}}]})
            return SimpleNamespace(status_code=200, json=lambda: {"data": []})
        if url == song_info.DEEZER_ALBUM + "7":
            return SimpleNamespace(status_code=200, json=lambda: {"genres": {"data": [{"name": "Electro"}]},
                                                                  "release_date": "2011-10-18"})
        if url == song_info.ITUNES_SEARCH and "New Song 2" in params["term"]:
            return SimpleNamespace(status_code=200, json=lambda: {"results": [
                {"trackName": "New Song 2", "artistName": "Synclassic Artist", "trackTimeMillis": 2000,
                 "primaryGenreName": "Alternative", "releaseDate": "2007-01-01T08:00:00Z"}]})
        return SimpleNamespace(status_code=200, json=lambda: {"data": [], "results": []})


def finder():
    web = Catalogue()
    return song_info.InfoFinder(web.get, sleep=lambda s: None), web


def test_deezer_then_apple():
    found, web = finder()
    assert found.find("Synclassic Artist", "New Song 1", 2) == song_info.Info("Electro", 2011, "Deezer")
    assert found.find("Synclassic Artist", "New Song 2", 2) == song_info.Info("Alternative", 2007, "Apple Music")
    assert found.find("Synclassic Artist", "Unknown", 2) is None
    found.find("Synclassic Artist", "New Song 1", 2)
    assert web.asked.count(song_info.DEEZER_ALBUM + "7") == 1  # albums are looked up once


def test_complete_keeps_what_is_there():
    found, _ = finder()
    track = Track("New Song 1", ["Synclassic Artist"], duration_ms=2000, release_date="1999-05-05")
    assert song_info.complete(track, found) and (track.genre, track.year) == ("Electro", "1999")


def test_library_and_ipod_get_genres_and_years(tmp_path, ipod):
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    found, _ = finder()
    result = info_fill.fill_library(library, found)
    assert result.updated == 2
    items = {i.track.title: i for i in library.tracks_in_playlists()}
    assert (items["New Song 1"].track.genre, items["New Song 1"].track.year) == ("Electro", "2011")
    from mutagen.mp4 import MP4

    tags = MP4(pathlib.Path(items["New Song 2"].file_path)).tags
    assert tags["\xa9gen"] == ["Alternative"] and tags["\xa9day"] == ["2007"] and "\xa9too" in tags  # rest kept

    result = info_fill.fill_ipod(ipod, library, found)
    songs = {s.title: s for s in itunesdb.songs(read_db(ipod))}
    assert (songs["New Song 1"].genre, songs["New Song 1"].year) == ("Electro", 2011)
    assert (songs["New Song 2"].genre, songs["New Song 2"].year) == ("Alternative", 2007)
    assert check(ipod).safe_to_manage


def test_new_syncs_carry_the_genre(tmp_path, ipod):
    library = library_with(tmp_path, {"Mix": [song(1)]})
    item = library.tracks_in_playlists()[0]
    item.track.genre = "Electro"
    library.update_track(item.id, track=item.track)
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    assert {s.title: s.genre for s in itunesdb.songs(read_db(ipod))}["New Song 1"] == "Electro"
