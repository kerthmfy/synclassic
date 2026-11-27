"""Lyrics from LRCLIB, inside song files on this Mac and on the iPod."""

import pathlib
from types import SimpleNamespace

from synclassic import config, lyrics
from synclassic.ipod import itunesdb, lyrics_sync, sync
from synclassic.ipod.check import check
from synclassic.library import Library
from tests.conftest import make_audio
from tests.test_ipod_sync import ipod, library_with, read_db, song  # noqa: F401

WORDS = "First line\nSecond line"


class Lrclib:
    def __init__(self, known=None):
        self.known = known or {}
        self.asked = []

    def get(self, url, params):
        self.asked.append((url, dict(params)))
        title = params.get("track_name")
        if url == lyrics.GET_URL:
            if title in self.known:
                return SimpleNamespace(status_code=200, json=lambda: self.known[title])
            return SimpleNamespace(status_code=404, json=lambda: None)
        items = [dict(item, trackName=t, artistName=item.get("artistName", "Synclassic Artist"))
                 for t, item in self.known.items() if t == title]
        return SimpleNamespace(status_code=200, json=lambda: items)


def finder(known):
    web = Lrclib(known)
    return lyrics.LyricsFinder(web.get, sleep=lambda s: None), web


def test_find_plain_synced_and_instrumental():
    found, web = finder({"A": {"plainLyrics": WORDS}, "B": {"syncedLyrics": "[00:01.00] Hello\n[00:02.50] there"},
                         "C": {"instrumental": True}})
    assert found.find("Synclassic Artist", "A", "Album", 200).text == WORDS
    assert web.asked[0][1] == {"artist_name": "Synclassic Artist", "track_name": "A", "duration": 200, "album_name": "Album"}
    assert found.find("Synclassic Artist", "B", "", 200).text == "Hello\nthere"
    assert found.find("Synclassic Artist", "C", "", 200).instrumental
    assert found.find("Synclassic Artist", "Unknown", "", 200) is None


def test_search_rejects_other_versions():
    def get(url, params):
        if url == lyrics.GET_URL:
            return SimpleNamespace(status_code=404, json=lambda: None)
        return SimpleNamespace(status_code=200, json=lambda: [
            {"trackName": "Song", "artistName": "Band", "duration": 400, "plainLyrics": "live version"},
            {"trackName": "Song", "artistName": "Band", "duration": 201, "plainLyrics": "the one"}])

    assert lyrics.LyricsFinder(get, sleep=lambda s: None).find("Band", "Song", "", 200).text == "the one"


def test_files_on_this_mac_get_lyrics_once(tmp_path):
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    found, web = finder({"New Song 1": {"plainLyrics": WORDS}})
    result = lyrics_sync.fill_library(library, found)
    assert (result.found, result.not_found) == (1, 1)
    items = {i.track.title: pathlib.Path(i.file_path) for i in library.tracks_in_playlists()}
    assert lyrics.read(items["New Song 1"]) == WORDS and lyrics.read(items["New Song 2"]) is None
    asked = len(web.asked)
    assert lyrics_sync.fill_library(library, found).found == 0 and len(web.asked) == asked  # not asked again soon
    assert lyrics_sync.library_missing(library) == []


def test_mp3_lyrics(tmp_path):
    path = make_audio(tmp_path / "a.mp3", seconds=1)
    lyrics.write(path, WORDS)
    assert lyrics.read(path) == WORDS


def test_synced_songs_with_lyrics_are_flagged(tmp_path, ipod):
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    lyrics.write(pathlib.Path(next(i for i in library.tracks_in_playlists() if i.track.title == "New Song 1").file_path), WORDS)
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    flags = {s.title: s.chunk.u8(lyrics_sync.LYRICS_FLAG) for s in itunesdb.songs(read_db(ipod))}
    assert flags["New Song 1"] == 1 and flags["New Song 2"] != 1


def test_songs_on_the_ipod_get_lyrics(tmp_path, ipod):
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    # Song 1's Mac copy got lyrics after it was synced; song 2 is found online.
    mac = {i.track.title: pathlib.Path(i.file_path) for i in library.tracks_in_playlists()}
    lyrics.write(mac["New Song 1"], WORDS)
    found, web = finder({"New Song 2": {"plainLyrics": "Online words"}})
    result = lyrics_sync.fill_ipod(ipod, library, found)
    assert result.found == 2
    assert not any(params.get("track_name") == "New Song 1" for _, params in web.asked)  # reused the Mac's
    songs = {s.title: s for s in itunesdb.songs(read_db(ipod))}
    for title, words in (("New Song 1", WORDS), ("New Song 2", "Online words")):
        on_ipod = ipod.root / songs[title].relative_path
        assert lyrics.read(on_ipod) == words
        assert songs[title].chunk.u8(lyrics_sync.LYRICS_FLAG) == 1
        assert songs[title].size == on_ipod.stat().st_size
    assert lyrics.read(mac["New Song 2"]) == "Online words"  # the Mac copy keeps in step
    assert not list(ipod.root.rglob("*lyrics-part*"))
    assert check(ipod).safe_to_manage
    assert lyrics_sync.ipod_missing(ipod, library) == [] or all(
        s.title not in ("New Song 1", "New Song 2") for s in lyrics_sync.ipod_missing(ipod, library))


def test_lyrics_setting_off(tmp_path):
    settings = config.load_settings()
    config.set_setting(settings, "lyrics", "off")
    assert settings.lyrics is False
    Library(config.app_dir() / "library.db").close()
