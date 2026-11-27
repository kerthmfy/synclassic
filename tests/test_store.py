"""Find Music: searching the catalogues (Deezer worldwide, Apple by country) and getting songs and albums."""

from types import SimpleNamespace

import pytest

from synclassic import config, update
from synclassic.ipod import itunesdb, sync
from synclassic.library import Library
from synclassic.sources import store
from tests.conftest import make_audio
from tests.test_ipod_sync import ipod, read_db  # noqa: F401

ART = "https://is1-ssl.mzstatic.com/image/thumb/Music/abc/source/100x100bb.jpg"
DZ_ART = "https://e-cdns-images.dzcdn.net/images/cover/f1/1000x1000-000000-80-0-0.jpg"


def song_json(n, title, album="Discovery", album_id=7):
    return {"wrapperType": "track", "kind": "song", "trackId": 100 + n, "trackName": title, "artistName": "Daft Punk",
            "collectionName": album, "collectionId": album_id, "trackNumber": n, "discNumber": 1,
            "trackTimeMillis": 200_000 + n, "releaseDate": "2001-03-07T08:00:00Z", "primaryGenreName": "Electronic",
            "artworkUrl100": ART, "trackExplicitness": "notExplicit"}


ALBUM = {"wrapperType": "collection", "collectionId": 7, "collectionName": "Discovery", "artistName": "Daft Punk",
         "releaseDate": "2001-03-07T08:00:00Z", "primaryGenreName": "Electronic", "trackCount": 2, "artworkUrl100": ART}
DZ_ALBUM = {"id": 302127, "title": "Discovery", "artist": {"name": "Daft Punk"}, "cover_xl": DZ_ART, "nb_tracks": 2,
            "release_date": "2001-03-07", "genres": {"data": [{"name": "Electro"}]}}


def dz_track(n, title):
    return {"id": 3135550 + n, "title": title, "duration": 200 + n, "artist": {"name": "Daft Punk"},
            "album": {"id": 302127, "title": "Discovery", "cover_xl": DZ_ART}, "type": "track"}


class Catalogue:
    """Apple's and Deezer's catalogues, simulated. `deezer=False`: Deezer is unreachable."""

    def __init__(self, deezer=True):
        self.asked = []
        self.deezer = deezer

    def get(self, url, params=None, timeout=None, headers=None):
        self.asked.append((url, dict(params or {})))
        if url.startswith(store.DEEZER):
            if not self.deezer:
                return SimpleNamespace(status_code=503, json=lambda: {})
            path = url[len(store.DEEZER):]
            body = {"/search/track": {"data": [dz_track(1, "One More Time")]},
                    "/search/album": {"data": [DZ_ALBUM]},
                    "/album/302127": DZ_ALBUM,
                    "/album/302127/tracks": {"data": [dict(dz_track(2, "Aerodynamic"), track_position=2, disk_number=1),
                                                      dict(dz_track(1, "One More Time"), track_position=1, disk_number=1)]},
                    "/track/3135551": dict(dz_track(1, "One More Time"), track_position=1, disk_number=1,
                                           release_date="2001-03-07")}.get(path, {"error": {"message": "no data"}})
            return SimpleNamespace(status_code=200, json=lambda: body)
        if url == store.SEARCH_URL and params["entity"] == "song":
            results = [song_json(1, "One More Time"), song_json(3, "Harder, Better, Faster, Stronger"),
                       {"wrapperType": "track", "kind": "music-video", "trackName": "x"}]
        elif url == store.SEARCH_URL:
            results = [ALBUM, dict(ALBUM, collectionId=8, collectionName="Homework")]
        else:
            results = [ALBUM, song_json(2, "Aerodynamic"), song_json(1, "One More Time")]
        return SimpleNamespace(status_code=200, json=lambda: {"results": results})


def test_search_merges_deezer_and_apple():
    web = Catalogue()
    songs = store.search_songs("daft punk", "PH", web)
    # Deezer's first (worldwide); Apple's adds what Deezer didn't have; no duplicates.
    assert [(s.id[:3], s.title) for s in songs] == [("dz:", "One More Time"), ("it:", "Harder, Better, Faster, Stronger")]
    assert any(url == store.SEARCH_URL and params["country"] == "PH" for url, params in web.asked)
    albums = store.search_albums("daft punk", "PH", web)
    assert [(a.id, a.title) for a in albums] == [("dz:302127", "Discovery"), ("it:8", "Homework")]
    assert albums[0].artwork == DZ_ART and albums[1].artwork.endswith("/1000x1000bb.jpg")


def test_country_is_automatic_unless_set(monkeypatch):
    monkeypatch.setattr(store, "default_country", lambda: "JP")
    assert store.country("") == "JP" and store.country("gb") == "GB"
    settings = config.load_settings()
    config.set_setting(settings, "store_country", "auto")
    assert settings.store_country == ""
    with pytest.raises(ValueError):
        config.set_setting(settings, "store_country", "Philippines")


def test_albums_from_either_catalogue():
    web = Catalogue()
    album, songs = store.album_tracks("dz:302127", session=web)
    assert [(s.track_number, s.title) for s in songs] == [(1, "One More Time"), (2, "Aerodynamic")]
    assert (album.year, songs[0].genre, songs[0].release_date) == ("2001", "Electro", "2001-03-07")
    album, songs = store.album_tracks("it:7", "US", web)
    assert [s.title for s in songs] == ["One More Time", "Aerodynamic"]
    assert store.read_album("deezer:album:302127", session=web).name == "Discovery — Daft Punk"
    assert store.read_album("itunes:album:7", "US", web).tracks[0].genre == "Electronic"


def test_deezer_song_details_are_filled_in():
    web = Catalogue()
    (song,) = [s for s in store.search_songs("one more time", "US", web) if s.id.startswith("dz:")]
    assert song.track_number == 0
    detailed = store.song_details(song, web)
    assert (detailed.track_number, detailed.release_date, detailed.genre) == (1, "2001-03-07", "Electro")


def test_apple_alone_when_deezer_is_down():
    songs = store.search_songs("daft punk", "US", Catalogue(deezer=False))
    assert songs and all(s.id.startswith("it:") for s in songs)


def test_artwork_sizes():
    assert store.artwork(ART, 300).endswith("/300x300bb.jpg")
    assert store.artwork(DZ_ART, 250).endswith("/250x250-000000-80-0-0.jpg")


def test_catalogue_errors_are_friendly():
    slow = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=429, json=lambda: {}))
    with pytest.raises(store.StoreError, match="slow down"):
        store.search_songs("x", "US", session=slow)


def test_getting_songs_and_albums(tmp_path, ipod, monkeypatch):
    web = Catalogue()
    library = Library(config.app_dir() / "library.db")
    album, songs = store.album_tracks("dz:302127", session=web)
    collection = store.get_album(library, album, songs)
    assert collection.name == "Discovery — Daft Punk" and collection.spec.ref == "deezer:album:302127"
    assert store.get_album(library, album, songs).id == collection.id  # getting it again: same collection
    one_more = store.song_details(store.search_songs("x", "US", web)[0], web)
    single = store.get_song(library, one_more)
    store.get_song(library, one_more)
    assert single.name == store.SONGS_NAME and len(library.playlist_track_ids(single)) == 1
    assert len(library.tracks_in_playlists()) == 2  # the single is the album's song: stored once

    # Refreshing: the album is read again; "Songs I Got" is left as it is.
    monkeypatch.setattr(store, "_get", lambda url, params, session=None: Catalogue().get(url, params).json())
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
