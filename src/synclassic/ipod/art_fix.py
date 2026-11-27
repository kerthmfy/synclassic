"""Fix album art: find iPod albums whose cover is missing or low quality, and put sharp ones on.

1. scan(): group the iPod's songs by album and judge each album's cover:
   - "missing": no song of the album has artwork;
   - "low": the cover inside the song file (on this Mac, or on the iPod) is smaller than 300 px,
     e.g. an old 64 px thumbnail that looks pixelated;
   - "unknown" (only when checking every album): there's no cover inside the files to measure
     (often songs from iTunes); a sharp cover is looked for and you compare before replacing.
2. find_covers(): look for a sharp cover, in order: the album's Spotify cover (songs Synclassic
   downloaded), Deezer's catalogue, then Apple's. Only a cover that is clearly better is offered.
3. apply(): put the chosen covers on the iPod (and into the song files on this Mac, so later
   syncs keep them). Like every change, the iPod is checked and backed up first.
"""

from __future__ import annotations

import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from synclassic.artwork import MIN_GOOD_SIZE, best_cover, download_image, image_size
from synclassic.ipod import artwork, itunesdb
from synclassic.ipod.device import IPod
from synclassic.textnorm import core_title, normalize, split_artists

Say = Callable[[str], None]
DEEZER_ALBUMS = "https://api.deezer.com/search/album"
DEEZER_TRACKS = "https://api.deezer.com/search/track"
ITUNES_SEARCH = "https://itunes.apple.com/search"


@dataclass
class AlbumArt:
    artist: str
    album: str
    songs: list[tuple[int, int, str]] = field(default_factory=list)  # (song id, dbid, title) on the iPod
    titles: list[str] = field(default_factory=list)  # songs only on this Mac
    ipod_files: list[pathlib.Path] = field(default_factory=list)
    library_files: list[pathlib.Path] = field(default_factory=list)  # Synclassic's copies on this Mac
    cover_urls: list[str] = field(default_factory=list)  # Spotify covers of Synclassic's songs
    thumbnail: tuple | None = None  # where the iPod's current cover is (for showing it)
    reason: str = ""  # "missing", "low" or "unknown"
    current_px: int = 0  # the current cover's smaller side, when known
    new_cover: bytes | None = None
    new_size: tuple[int, int] = (0, 0)
    source: str = ""

    @property
    def title(self) -> str:
        return self.album or self.first_song

    @property
    def first_song(self) -> str:
        return self.songs[0][2] if self.songs else (self.titles[0] if self.titles else "")

    @property
    def where(self) -> str:
        return "iPod" if self.songs else "Mac"

    @property
    def problem(self) -> str:
        if self.reason == "missing":
            return "No album art"
        if self.reason == "low":
            return f"Low quality ({self.current_px}×{self.current_px})"
        return "Size unknown"

    @property
    def found(self) -> str:
        if not self.new_cover:
            return "No better cover found"
        return f"{self.new_size[0]}×{self.new_size[1]} from {self.source}"


def _album_key(artist: str, album: str, title: str) -> tuple[str, str]:
    artists = split_artists(artist)
    who = normalize(artists[0] if artists else artist)
    return (who, normalize(core_title(album))) if album else (who, "song:" + normalize(core_title(title)))


def _cover_px(path: pathlib.Path) -> int | None:
    """The smaller side of the cover inside a song file (None: no cover inside)."""
    data = artwork.embedded_cover(path) if path.is_file() else None
    if not data:
        return None
    size = image_size(data)
    return min(size) if size else None


def scan(ipod: IPod, library=None, everything: bool = False, progress=None,
         should_stop: Callable[[], bool] | None = None) -> list[AlbumArt]:
    """Albums on the iPod whose cover is missing or low quality. With `everything`, also albums
    whose cover can't be measured."""
    from synclassic.ipod import browse
    from synclassic.ipod.sync import load_state

    db = itunesdb.parse(ipod.database_path.read_bytes())
    covers = browse.read(ipod).covers  # dbid -> where its thumbnail is
    known = {}
    state = load_state(ipod)
    replaced = {int(k, 16): px for k, px in state.get("covers", {}).items()}  # covers put on by apply()
    if library is not None:
        by_key = {item.key: item for item in library.tracks_in_playlists()}
        for key, entry in state["tracks"].items():
            if key in by_key:
                known[int(entry["dbid"], 16)] = by_key[key]

    albums: dict[tuple[str, str], AlbumArt] = {}
    for song in itunesdb.songs(db):
        if song.media_type not in (0, 1):
            continue
        artist = song.album_artist or song.artist
        key = _album_key(artist, song.album, song.title)
        album = albums.setdefault(key, AlbumArt(artist, song.album))
        album.songs.append((song.id, song.dbid, song.title))
        if song.location:
            album.ipod_files.append(ipod.root / song.relative_path)
        item = known.get(song.dbid)
        if item is not None:
            if item.has_file:
                album.library_files.append(pathlib.Path(item.file_path))  # type: ignore[arg-type]
            if item.track.cover_url and item.track.cover_url not in album.cover_urls:
                album.cover_urls.append(item.track.cover_url)
        if album.thumbnail is None and song.dbid in covers:
            album.thumbnail = covers[song.dbid]

    problems = []
    for number, album in enumerate(albums.values(), start=1):
        if should_stop and should_stop():
            break
        if progress:
            progress(number, len(albums), album.title)
        if album.thumbnail is None:
            album.reason = "missing"
            problems.append(album)
            continue
        px = next((replaced[d] for _, d, _ in album.songs if d in replaced), None)
        # The iPod's covers were made from the files on the iPod, so those tell the truth.
        for path in ([] if px is not None else album.ipod_files[:1] + album.library_files[:1]):
            px = _cover_px(path)
            if px is not None:
                break
        if px is not None:
            if px < MIN_GOOD_SIZE:
                album.reason, album.current_px = "low", px
                problems.append(album)
        elif everything:
            album.reason = "unknown"
            problems.append(album)
    return problems


def scan_library(library, skip: set[pathlib.Path] = frozenset(), progress=None,
                 should_stop: Callable[[], bool] | None = None) -> list[AlbumArt]:
    """Albums among Synclassic's songs on this Mac (not on the iPod: `skip` lists those files)
    whose cover is missing or low quality. Fixing them changes the cover inside the files."""
    albums: dict[tuple[str, str], AlbumArt] = {}
    for item in library.tracks_in_playlists():
        if not item.has_file or pathlib.Path(item.file_path) in skip:  # type: ignore[arg-type]
            continue
        track = item.track
        artist = track.album_artist or track.artist
        album = albums.setdefault(_album_key(artist, track.album or "", track.title), AlbumArt(artist, track.album or ""))
        album.library_files.append(pathlib.Path(item.file_path))  # type: ignore[arg-type]
        album.titles.append(track.title)
        if track.cover_url and track.cover_url not in album.cover_urls:
            album.cover_urls.append(track.cover_url)
    problems = []
    for number, album in enumerate(albums.values(), start=1):
        if should_stop and should_stop():
            break
        if progress:
            progress(number, len(albums), album.title)
        px = _cover_px(album.library_files[0])
        if px is None:
            album.reason = "missing"
        elif px < MIN_GOOD_SIZE:
            album.reason, album.current_px = "low", px
        else:
            continue
        problems.append(album)
    return problems


# --- finding sharp covers -------------------------------------------------------------------


def _same(a: str, b: str) -> bool:
    a, b = normalize(core_title(a)), normalize(core_title(b))
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a))


def _same_artist(wanted: str, found: str) -> bool:
    wanted_parts = [normalize(p) for p in split_artists(wanted)] or [normalize(wanted)]
    found_parts = [normalize(p) for p in split_artists(found)] or [normalize(found)]
    return bool(set(wanted_parts) & set(found_parts)) or normalize(wanted) == normalize(found)


class CoverFinder:
    """Looks covers up on Deezer and Apple (both free and keyless). Pass `get` to simulate them."""

    def __init__(self, get=None, fetch_image: Callable[[str], bytes | None] = download_image,
                 sleep: Callable[[float], None] = time.sleep):
        self.get = get or (lambda url, params: requests.get(url, params=params, timeout=20))
        self.fetch_image = fetch_image
        self.sleep = sleep
        self.apple_ok = True

    def _json(self, url: str, params: dict) -> dict:
        try:
            response = self.get(url, params)
        except requests.RequestException:
            return {}
        if response.status_code in (403, 429) and url == ITUNES_SEARCH:
            self.apple_ok = False  # Apple allows about 20 searches a minute; stop asking
            return {}
        if response.status_code != 200:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    def deezer(self, artist: str, album: str, title: str) -> str | None:
        main = (split_artists(artist) or [artist])[0]
        if album:
            data = self._json(DEEZER_ALBUMS, {"q": f'artist:"{main}" album:"{album}"', "limit": 10})
            self.sleep(0.12)  # Deezer allows 50 requests every 5 seconds
            for item in data.get("data", []):
                if _same(album, item.get("title", "")) and _same_artist(artist, (item.get("artist") or {}).get("name", "")):
                    return item.get("cover_xl") or item.get("cover_big")
        if title:
            data = self._json(DEEZER_TRACKS, {"q": f'artist:"{main}" track:"{title}"', "limit": 10})
            self.sleep(0.12)
            for item in data.get("data", []):
                found_album = item.get("album") or {}
                if (_same(title, item.get("title", "")) and _same_artist(artist, (item.get("artist") or {}).get("name", ""))
                        and (not album or _same(album, found_album.get("title", "")))):
                    return found_album.get("cover_xl") or found_album.get("cover_big")
        return None

    def apple(self, artist: str, album: str, title: str) -> str | None:
        if not self.apple_ok:
            return None
        main = (split_artists(artist) or [artist])[0]
        if album:
            data = self._json(ITUNES_SEARCH, {"term": f"{main} {album}", "entity": "album", "limit": 10})
            for item in data.get("results", []):
                if _same(album, item.get("collectionName", "")) and _same_artist(artist, item.get("artistName", "")):
                    return _apple_size(item.get("artworkUrl100"))
        elif title:
            data = self._json(ITUNES_SEARCH, {"term": f"{main} {title}", "entity": "song", "limit": 10})
            for item in data.get("results", []):
                if _same(title, item.get("trackName", "")) and _same_artist(artist, item.get("artistName", "")):
                    return _apple_size(item.get("artworkUrl100"))
        return None

    def find(self, album: AlbumArt) -> None:
        """Fill in album.new_cover with something clearly better than what's there, if found."""
        title = album.first_song
        # Clearly better: sharp (300 px+), bigger than now, and for a cover we can't measure, 600 px+.
        needed = max(MIN_GOOD_SIZE * (2 if album.reason == "unknown" else 1), album.current_px + 1)
        attempts: list[tuple[str, Callable[[], bytes | None]]] = []
        if album.cover_urls:
            attempts.append(("Spotify", lambda: best_cover(album.cover_urls, self.fetch_image)))
        attempts.append(("Deezer", lambda: self._image(self.deezer(album.artist, album.album, title))))
        attempts.append(("Apple Music", lambda: self._image(self.apple(album.artist, album.album, title))))
        for source, attempt in attempts:
            data = attempt()
            size = image_size(data) if data else None
            if data and size and min(size) >= needed:
                album.new_cover, album.new_size, album.source = data, size, source
                return

    def _image(self, url: str | None) -> bytes | None:
        return self.fetch_image(url) if url else None


def _apple_size(url: str | None) -> str | None:
    """Apple's 100 px artwork link, asking for 1000 px instead."""
    if not url:
        return None
    return url.replace("100x100bb", "1000x1000bb")


def find_covers(albums: list[AlbumArt], finder: CoverFinder | None = None, progress=None,
                should_stop: Callable[[], bool] | None = None) -> None:
    finder = finder or CoverFinder()
    for number, album in enumerate(albums, start=1):
        if should_stop and should_stop():
            return
        if progress:
            progress(number, len(albums), album.title)
        finder.find(album)


# --- putting them on ----------------------------------------------------------------------------


def apply(ipod: IPod | None, albums: list[AlbumArt], make_backup: bool = True, say: Say = lambda s: None) -> int:
    """Put the new covers on the iPod (and into Synclassic's files on this Mac). Returns how many
    songs got a new cover."""
    from synclassic.tagging import embed_cover

    chosen = [a for a in albums if a.new_cover]
    count = 0
    on_ipod = [a for a in chosen if a.songs]
    if on_ipod and ipod is not None:
        from synclassic.ipod.manage import EditSession

        session = EditSession(ipod, say, make_backup)
        covers = session.state.setdefault("covers", {})  # remembered: the files on the iPod keep their old one
        for album in on_ipod:
            for _, dbid, _ in album.songs:
                session.set_cover(dbid, album.new_cover)  # type: ignore[arg-type]
                covers[f"{dbid:016x}"] = min(album.new_size)
                count += 1
        session.state_changed = True
        session.commit()
    for album in chosen:
        for path in dict.fromkeys(album.library_files):
            try:
                embed_cover(path, album.new_cover)  # type: ignore[arg-type]
                if not album.songs:
                    count += 1
            except Exception as error:  # a file that can't be changed keeps its old cover
                say(f"Couldn't update the cover inside {path.name}: {error}")
    return count
