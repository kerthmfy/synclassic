"""Find Music: search songs and albums, then get them like any playlist song (found on YouTube
Music, downloaded, tagged).

Two free catalogues, no account needed:
- Deezer's, searched first: one catalogue for the whole world;
- Apple's, for anything Deezer lacks, in your own country's store (your Mac's region, unless
  Settings says otherwise).
Results are merged without duplicates. Both give accurate song info (track numbers, release
dates, genres) and 1000 px covers.

Gotten albums become a collection each; single songs go to "Songs I Got". On the iPod they show
under Albums, Artists and Songs; they aren't made into iPod playlists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import requests

from synclassic.models import SourcePlaylist, Track
from synclassic.textnorm import core_title, normalize, split_artists

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
DEEZER = "https://api.deezer.com"
ALBUM_KIND, SONGS_KIND = "store-album", "store-songs"
COLLECTION_KINDS = (ALBUM_KIND, SONGS_KIND)  # library "playlists" that aren't iPod playlists
SONGS_NAME = "Songs I Got"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Synclassic/1.0"


class StoreError(RuntimeError):
    pass


@dataclass
class StoreSong:
    id: str  # "dz:123" (Deezer) or "it:456" (Apple)
    title: str
    artist: str
    album: str
    album_artist: str
    album_id: str
    track_number: int
    disc_number: int
    duration_ms: int
    release_date: str
    genre: str
    artwork: str
    explicit: bool


@dataclass
class StoreAlbum:
    id: str
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


def default_country() -> str:
    """This Mac's region (e.g. PH), for Apple's catalogue."""
    try:
        from Foundation import NSLocale  # pyobjc, on macOS

        code = NSLocale.currentLocale().countryCode()
        if code:
            return str(code).upper()
    except Exception:
        pass
    import locale

    for value in (locale.getlocale()[0], locale.getdefaultlocale()[0] if hasattr(locale, "getdefaultlocale") else None):
        if value and "_" in value:
            return value.split("_")[1][:2].upper()
    return "US"


def country(setting: str | None) -> str:
    return (setting or "").strip().upper() or default_country()


def artwork(url: str | None, size: int = 1000) -> str:
    """A cover link at another size (Apple's and Deezer's links both say their size)."""
    url = url or ""
    url = re.sub(r"/\d+x\d+bb(-\d+)?\.", f"/{size}x{size}bb.", url)  # Apple
    return re.sub(r"/\d+x\d+-000000-80-0-0\.jpg$", f"/{size}x{size}-000000-80-0-0.jpg", url)  # Deezer


def _get(url: str, params: dict, session=None):
    http = session or requests
    try:
        response = http.get(url, params=params, timeout=20, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as error:
        raise StoreError(f"Couldn't reach the music catalogue: {error}") from error
    if response.status_code in (403, 429):
        raise StoreError("The music catalogue asks to slow down; try again in a minute.")
    if response.status_code != 200:
        raise StoreError(f"The music catalogue answered HTTP {response.status_code}.")
    try:
        body = response.json()
    except ValueError as error:
        raise StoreError("The music catalogue sent an unexpected answer.") from error
    if isinstance(body, dict) and body.get("error"):
        message = body["error"].get("message", "error") if isinstance(body["error"], dict) else str(body["error"])
        raise StoreError(f"The music catalogue said: {message}")
    return body


# --- Apple ----------------------------------------------------------------------------------------


def _apple_song(item: dict) -> StoreSong:
    return StoreSong(
        id=f"it:{item.get('trackId') or 0}", title=item.get("trackName", ""), artist=item.get("artistName", ""),
        album=item.get("collectionName", ""), album_artist=item.get("collectionArtistName") or item.get("artistName", ""),
        album_id=f"it:{item.get('collectionId') or 0}", track_number=int(item.get("trackNumber") or 0),
        disc_number=int(item.get("discNumber") or 0), duration_ms=int(item.get("trackTimeMillis") or 0),
        release_date=(item.get("releaseDate") or "")[:10], genre=item.get("primaryGenreName", ""),
        artwork=artwork(item.get("artworkUrl100")), explicit=item.get("trackExplicitness") == "explicit",
    )


def _apple_album(item: dict) -> StoreAlbum:
    return StoreAlbum(
        id=f"it:{item.get('collectionId') or 0}", title=item.get("collectionName", ""), artist=item.get("artistName", ""),
        year=(item.get("releaseDate") or "")[:4], genre=item.get("primaryGenreName", ""),
        track_count=int(item.get("trackCount") or 0), artwork=artwork(item.get("artworkUrl100")),
        explicit=item.get("collectionExplicitness") == "explicit",
    )


def apple_songs(term: str, where: str, session=None, limit: int = 50) -> list[StoreSong]:
    body = _get(SEARCH_URL, {"term": term, "media": "music", "entity": "song", "limit": limit, "country": where}, session)
    return [_apple_song(r) for r in body.get("results", []) if r.get("wrapperType") == "track" and r.get("kind") == "song"]


def apple_albums(term: str, where: str, session=None, limit: int = 50) -> list[StoreAlbum]:
    body = _get(SEARCH_URL, {"term": term, "media": "music", "entity": "album", "limit": limit, "country": where},
                session)
    return [_apple_album(r) for r in body.get("results", []) if r.get("wrapperType") == "collection"]


def apple_album(album_id: str, where: str, session=None) -> tuple[StoreAlbum, list[StoreSong]]:
    number = album_id.split(":")[-1]
    body = _get(LOOKUP_URL, {"id": number, "entity": "song", "country": where, "limit": 200}, session)
    results = body.get("results", [])
    albums = [_apple_album(r) for r in results if r.get("wrapperType") == "collection"]
    if not albums:
        raise StoreError("That album isn't in the catalogue any more.")
    songs = [_apple_song(r) for r in results if r.get("wrapperType") == "track" and r.get("kind") == "song"]
    songs.sort(key=lambda s: (s.disc_number, s.track_number))
    return albums[0], songs


# --- Deezer ---------------------------------------------------------------------------------------


def _deezer_song(item: dict, album: dict | None = None) -> StoreSong:
    album = album or item.get("album") or {}
    return StoreSong(
        id=f"dz:{item.get('id') or 0}", title=item.get("title", ""), artist=(item.get("artist") or {}).get("name", ""),
        album=album.get("title", ""), album_artist=((album.get("artist") or {}).get("name")
                                                    or (item.get("artist") or {}).get("name", "")),
        album_id=f"dz:{album.get('id') or 0}", track_number=int(item.get("track_position") or 0),
        disc_number=int(item.get("disk_number") or 0), duration_ms=int(item.get("duration") or 0) * 1000,
        release_date=item.get("release_date") or album.get("release_date") or "",
        genre=next((g.get("name", "") for g in (album.get("genres") or {}).get("data", [])), ""),
        artwork=album.get("cover_xl") or album.get("cover_big") or "",
        explicit=bool(item.get("explicit_lyrics")),
    )


def _deezer_album(item: dict) -> StoreAlbum:
    return StoreAlbum(
        id=f"dz:{item.get('id') or 0}", title=item.get("title", ""), artist=(item.get("artist") or {}).get("name", ""),
        year=(item.get("release_date") or "")[:4],
        genre=next((g.get("name", "") for g in (item.get("genres") or {}).get("data", [])), ""),
        track_count=int(item.get("nb_tracks") or 0), artwork=item.get("cover_xl") or item.get("cover_big") or "",
        explicit=bool(item.get("explicit_lyrics")),
    )


def deezer_songs(term: str, session=None, limit: int = 50) -> list[StoreSong]:
    body = _get(f"{DEEZER}/search/track", {"q": term, "limit": limit}, session)
    return [_deezer_song(item) for item in body.get("data", []) if item.get("type", "track") == "track"]


def deezer_albums(term: str, session=None, limit: int = 50) -> list[StoreAlbum]:
    body = _get(f"{DEEZER}/search/album", {"q": term, "limit": limit}, session)
    return [_deezer_album(item) for item in body.get("data", [])]


def deezer_album(album_id: str, session=None) -> tuple[StoreAlbum, list[StoreSong]]:
    number = album_id.split(":")[-1]
    info = _get(f"{DEEZER}/album/{number}", {}, session)
    tracks = _get(f"{DEEZER}/album/{number}/tracks", {"limit": 500}, session).get("data", [])
    album = _deezer_album(info)
    songs = [_deezer_song(t, info) for t in tracks]
    for position, song in enumerate(songs, start=1):
        song.track_number = song.track_number or position
        song.disc_number = song.disc_number or 1
        song.release_date = song.release_date or info.get("release_date", "")
    songs.sort(key=lambda s: (s.disc_number, s.track_number))
    return album, songs


# --- both -----------------------------------------------------------------------------------------


def _merge(first: list, second: list, key) -> list:
    seen = {key(item) for item in first}
    return first + [item for item in second if key(item) not in seen]


def _song_key(song: StoreSong) -> tuple[str, str]:
    artists = split_artists(song.artist)
    return normalize(artists[0] if artists else song.artist), core_title(song.title)


def _album_key(album: StoreAlbum) -> tuple[str, str]:
    return normalize(album.artist), core_title(album.title)


def search_songs(term: str, where: str | None = None, session=None, limit: int = 50) -> list[StoreSong]:
    """Songs from Deezer (worldwide), then Apple's (your country) for anything else."""
    found, errors = [], []
    for search in (lambda: deezer_songs(term, session, limit), lambda: apple_songs(term, country(where), session, limit)):
        try:
            found = _merge(found, search(), _song_key)
        except StoreError as error:
            errors.append(error)
    if not found and errors:
        raise errors[0]
    return found


def search_albums(term: str, where: str | None = None, session=None, limit: int = 50) -> list[StoreAlbum]:
    found, errors = [], []
    for search in (lambda: deezer_albums(term, session, limit), lambda: apple_albums(term, country(where), session, limit)):
        try:
            found = _merge(found, search(), _album_key)
        except StoreError as error:
            errors.append(error)
    if not found and errors:
        raise errors[0]
    return found


def album_tracks(album_id, where: str | None = None, session=None) -> tuple[StoreAlbum, list[StoreSong]]:
    album_id = str(album_id)
    if album_id.startswith("dz:"):
        return deezer_album(album_id, session)
    return apple_album(album_id if album_id.startswith("it:") else f"it:{album_id}", country(where), session)


def song_details(song: StoreSong, session=None) -> StoreSong:
    """A searched Deezer song's track number, disc, release date and genre (search leaves them out)."""
    if not song.id.startswith("dz:") or (song.track_number and song.genre):
        return song
    try:
        track = _get(f"{DEEZER}/track/{song.id[3:]}", {}, session)
        album = _get(f"{DEEZER}/album/{song.album_id[3:]}", {}, session) if song.album_id != "dz:0" else {}
    except StoreError:
        return song
    detailed = _deezer_song(track, album or track.get("album") or {})
    return replace(detailed, artwork=detailed.artwork or song.artwork)


def to_track(song: StoreSong) -> Track:
    return Track(
        title=song.title, artists=split_artists(song.artist) or [song.artist], album=song.album,
        album_artist=song.album_artist, duration_ms=song.duration_ms or None, track_number=song.track_number or None,
        disc_number=song.disc_number or None, release_date=song.release_date, explicit=song.explicit,
        cover_url=song.artwork or None, genre=song.genre,
    )


def read_album(ref: str, where: str | None = None, session=None) -> SourcePlaylist:
    """A gotten album's songs (ref: "deezer:album:<id>" or "itunes:album:<id>")."""
    source, _, number = ref.split(":", 2)
    album, songs = album_tracks(f"{'dz' if source == 'deezer' else 'it'}:{number}", where, session)
    return SourcePlaylist(album.name, [to_track(s) for s in songs], cover_url=album.artwork)


def album_ref(album: StoreAlbum) -> str:
    source, number = album.id.split(":", 1)
    return f"{'deezer' if source == 'dz' else 'itunes'}:album:{number}"


# --- getting them -------------------------------------------------------------------------------------


def get_album(library, album: StoreAlbum, songs: list[StoreSong]):
    """Add an album to the library as its own collection. Returns the library playlist."""
    from synclassic.sources import SourceSpec
    from synclassic.update import apply_source

    spec = SourceSpec(ALBUM_KIND, album_ref(album))
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
