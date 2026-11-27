"""Find Music: search songs and albums in Apple's music catalogue (the iTunes Search API: free,
no account), then get them like any playlist song (found on YouTube Music, downloaded, tagged).

The catalogue gives accurate song info (track numbers, release dates, genres) and 1000 px covers.
Gotten albums become a collection each; single songs go to "Songs I Got". On the iPod they show
under Albums, Artists and Songs; they aren't made into iPod playlists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

from synclassic.models import SourcePlaylist, Track
from synclassic.textnorm import split_artists

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
ALBUM_KIND, SONGS_KIND = "store-album", "store-songs"
COLLECTION_KINDS = (ALBUM_KIND, SONGS_KIND)  # library "playlists" that aren't iPod playlists
SONGS_NAME = "Songs I Got"


class StoreError(RuntimeError):
    pass


@dataclass
class StoreSong:
    id: int
    title: str
    artist: str
    album: str
    album_artist: str
    album_id: int
    track_number: int
    disc_number: int
    duration_ms: int
    release_date: str
    genre: str
    artwork: str
    explicit: bool


@dataclass
class StoreAlbum:
    id: int
    title: str
    artist: str
    year: str
    genre: str
    track_count: int
    artwork: str
    explicit: bool

    @property
    def name(self) -> str:
        return f"{self.title} — {self.artist}"


def artwork(url: str | None, size: int = 1000) -> str:
    """Apple's 100 px cover link, at another size."""
    return re.sub(r"/\d+x\d+bb\.", f"/{size}x{size}bb.", url or "")


def _song(item: dict) -> StoreSong:
    return StoreSong(
        id=int(item.get("trackId") or 0), title=item.get("trackName", ""), artist=item.get("artistName", ""),
        album=item.get("collectionName", ""), album_artist=item.get("collectionArtistName") or item.get("artistName", ""),
        album_id=int(item.get("collectionId") or 0), track_number=int(item.get("trackNumber") or 0),
        disc_number=int(item.get("discNumber") or 0), duration_ms=int(item.get("trackTimeMillis") or 0),
        release_date=(item.get("releaseDate") or "")[:10], genre=item.get("primaryGenreName", ""),
        artwork=artwork(item.get("artworkUrl100")), explicit=item.get("trackExplicitness") == "explicit",
    )


def _album(item: dict) -> StoreAlbum:
    return StoreAlbum(
        id=int(item.get("collectionId") or 0), title=item.get("collectionName", ""), artist=item.get("artistName", ""),
        year=(item.get("releaseDate") or "")[:4], genre=item.get("primaryGenreName", ""),
        track_count=int(item.get("trackCount") or 0), artwork=artwork(item.get("artworkUrl100")),
        explicit=item.get("collectionExplicitness") == "explicit",
    )


def _get(url: str, params: dict, session=None) -> list[dict]:
    http = session or requests
    try:
        response = http.get(url, params=params, timeout=20)
    except requests.RequestException as error:
        raise StoreError(f"Couldn't reach the music catalogue: {error}") from error
    if response.status_code in (403, 429):
        raise StoreError("The music catalogue asks to slow down; try again in a minute.")
    if response.status_code != 200:
        raise StoreError(f"The music catalogue answered HTTP {response.status_code}.")
    try:
        return response.json().get("results", [])
    except ValueError as error:
        raise StoreError("The music catalogue sent an unexpected answer.") from error


def search_songs(term: str, country: str = "US", session=None, limit: int = 50) -> list[StoreSong]:
    results = _get(SEARCH_URL, {"term": term, "media": "music", "entity": "song", "limit": limit, "country": country},
                   session)
    return [_song(r) for r in results if r.get("wrapperType") == "track" and r.get("kind") == "song"]


def search_albums(term: str, country: str = "US", session=None, limit: int = 50) -> list[StoreAlbum]:
    results = _get(SEARCH_URL, {"term": term, "media": "music", "entity": "album", "limit": limit, "country": country},
                   session)
    return [_album(r) for r in results if r.get("wrapperType") == "collection"]


def album_tracks(album_id: int, country: str = "US", session=None) -> tuple[StoreAlbum, list[StoreSong]]:
    results = _get(LOOKUP_URL, {"id": album_id, "entity": "song", "country": country, "limit": 200}, session)
    albums = [_album(r) for r in results if r.get("wrapperType") == "collection"]
    if not albums:
        raise StoreError("That album isn't in the catalogue any more.")
    songs = [_song(r) for r in results if r.get("wrapperType") == "track" and r.get("kind") == "song"]
    songs.sort(key=lambda s: (s.disc_number, s.track_number))
    return albums[0], songs


def to_track(song: StoreSong) -> Track:
    return Track(
        title=song.title, artists=split_artists(song.artist) or [song.artist], album=song.album,
        album_artist=song.album_artist, duration_ms=song.duration_ms or None, track_number=song.track_number or None,
        disc_number=song.disc_number or None, release_date=song.release_date, explicit=song.explicit,
        cover_url=song.artwork or None, genre=song.genre,
    )


def read_album(ref: str, country: str = "US", session=None) -> SourcePlaylist:
    """A gotten album's songs (ref: "itunes:album:<id>")."""
    album_id = int(ref.rsplit(":", 1)[-1])
    album, songs = album_tracks(album_id, country, session)
    return SourcePlaylist(album.name, [to_track(s) for s in songs], cover_url=album.artwork)


# --- getting them -------------------------------------------------------------------------------------


def get_album(library, album: StoreAlbum, songs: list[StoreSong]):
    """Add an album to the library as its own collection. Returns the library playlist."""
    from synclassic.sources import SourceSpec
    from synclassic.update import apply_source

    spec = SourceSpec(ALBUM_KIND, f"itunes:album:{album.id}")
    playlist = library.playlist_by_spec(spec) or library.add_playlist(library.unique_name(album.name), spec)
    apply_source(library, playlist, SourcePlaylist(album.name, [to_track(s) for s in songs], cover_url=album.artwork))
    return playlist


def get_song(library, song: StoreSong):
    """Add one song to "Songs I Got". Returns the library playlist."""
    from synclassic.sources import SourceSpec

    spec = SourceSpec(SONGS_KIND, "store:songs")
    playlist = library.playlist_by_spec(spec) or library.add_playlist(library.unique_name(SONGS_NAME), spec)
    track_id = library.upsert_track(to_track(song))
    ids = library.playlist_track_ids(playlist)
    if track_id not in ids:
        library.set_playlist_tracks(playlist, ids + [track_id])
    return playlist
