"""Find Music: searching the catalogue and getting songs and albums."""

from types import SimpleNamespace

import pytest

from synclassic import config, update
from synclassic.ipod import itunesdb, sync
from synclassic.library import Library
from synclassic.sources import store
from tests.conftest import make_audio
from tests.test_ipod_sync import ipod, read_db  # noqa: F401

ART = "https://is1-ssl.mzstatic.com/image/thumb/Music/abc/source/100x100bb.jpg"


def song_json(n, title, album="Discovery", album_id=7):
    return {"wrapperType": "track", "kind": "song", "trackId": 100 + n, "trackName": title, "artistName": "Daft Punk",
            "collectionName": album, "collectionId": album_id, "trackNumber": n, "discNumber": 1,
            "trackTimeMillis": 200_000 + n, "releaseDate": "2001-03-07T08:00:00Z", "primaryGenreName": "Electronic",
            "artworkUrl100": ART, "trackExplicitness": "notExplicit"}


ALBUM = {"wrapperType": "collection", "collectionId": 7, "collectionName": "Discovery", "artistName": "Daft Punk",
         "releaseDate": "2001-03-07T08:00:00Z", "primaryGenreName": "Electronic", "trackCount": 2, "artworkUrl100": ART}


class Catalogue:
    def __init__(self):
        self.asked = []

    def get(self, url, params=None, timeout=None):
        self.asked.append((url, dict(params)))
        if url == store.SEARCH_URL and params["entity"] == "song":
            results = [song_json(1, "One More Time"), {"wrapperType": "track", "kind": "music-video", "trackName": "x"}]
        elif url == store.SEARCH_URL:
            results = [ALBUM]
        else:
            results = [ALBUM, song_json(2, "Aerodynamic"), song_json(1, "One More Time")]
        return SimpleNamespace(status_code=200, json=lambda: {"results": results})


def test_search_and_album():
    web = Catalogue()
    (found,) = store.search_songs("one more time", "PH", web)
    assert (found.title, found.album, found.track_number, found.genre) == ("One More Time", "Discovery", 1, "Electronic")
    assert found.artwork.endswith("/1000x1000bb.jpg") and web.asked[0][1]["country"] == "PH"
    (album,) = store.search_albums("discovery", session=web)
    assert (album.name, album.year, album.track_count) == ("Discovery — Daft Punk", "2001", 2)
    album, songs = store.album_tracks(7, session=web)
    assert [s.title for s in songs] == ["One More Time", "Aerodynamic"]  # in track order
    track = store.to_track(songs[0])
    assert (track.artist, track.year, track.genre, track.cover_url) == ("Daft Punk", "2001", "Electronic", songs[0].artwork)


def test_catalogue_errors_are_friendly():
    slow = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=429, json=lambda: {}))
    with pytest.raises(store.StoreError, match="slow down"):
        store.search_songs("x", session=slow)


def test_getting_songs_and_albums(tmp_path, ipod, monkeypatch):
    web = Catalogue()
    library = Library(config.app_dir() / "library.db")
    album, songs = store.album_tracks(7, session=web)
    collection = store.get_album(library, album, songs)
    assert collection.name == "Discovery — Daft Punk" and collection.spec.kind == store.ALBUM_KIND
    assert store.get_album(library, album, songs).id == collection.id  # getting it again: same collection
    single = store.get_song(library, store.search_songs("x", session=web)[0])
    store.get_song(library, store.search_songs("x", session=web)[0])
    assert single.name == store.SONGS_NAME and len(library.playlist_track_ids(single)) == 1
    assert len(library.tracks_in_playlists()) == 2  # the single is the album's song: stored once

    # Refreshing: the album is read again; "Songs I Got" is left as it is.
    monkeypatch.setattr(store, "_get", lambda url, params, session=None: Catalogue().get(url, params).json()["results"])
    assert update.refresh_playlist(library, collection, None).total == 2
    assert update.refresh_playlist(library, library.playlist_by_spec(single.spec), None).total == 1

    # Synced as songs, not as iPod playlists.
    for item in library.tracks_in_playlists():
        path = tmp_path / f"{item.id}.m4a"
        make_audio(path, seconds=1)
        library.update_track(item.id, status="downloaded", file_path=str(path))
    plan = sync.make_plan(library, ipod)
    assert len(plan.copy) == 2 and plan.playlists == []
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert {"One More Time", "Aerodynamic"} <= {s.title for s in itunesdb.songs(db)}
    assert not any(p.name in (collection.name, store.SONGS_NAME) for p in itunesdb.playlists(db, 2))
