"""Genre and year for songs, from Deezer's catalogue (with Apple's as a fallback).

Spotify doesn't give songs a genre, and its release year isn't always passed on, so the iPod's
Genres menu would otherwise be empty. Both services are free and need no account. Each album
is looked up once per run; songs nothing is found for are remembered for a month.
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from synclassic.textnorm import core_title, normalize, split_artists

DEEZER_SEARCH = "https://api.deezer.com/search/track"
DEEZER_ALBUM = "https://api.deezer.com/album/"
ITUNES_SEARCH = "https://itunes.apple.com/search"
RETRY_AFTER_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS info_lookups (
    song TEXT PRIMARY KEY,          -- artist|title, normalised
    found INTEGER NOT NULL,
    checked_at TEXT NOT NULL
);
"""


@dataclass
class Info:
    genre: str = ""
    year: int = 0
    source: str = ""


def _same_title(a: str, b: str) -> bool:
    return core_title(a) == core_title(b)


def _same_artist(wanted: str, found: str) -> bool:
    wanted_parts = {normalize(p) for p in split_artists(wanted)} | {normalize(wanted)}
    found_parts = {normalize(p) for p in split_artists(found)} | {normalize(found)}
    return bool(wanted_parts & found_parts)


def _year(text: str | None) -> int:
    if text and len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        return year if 1900 <= year <= 2100 else 0
    return 0


class InfoFinder:
    def __init__(self, get=None, sleep: Callable[[float], None] = time.sleep):
        self.get = get or (lambda url, params: requests.get(url, params=params, timeout=20))
        self.sleep = sleep
        self.albums: dict[int, Info] = {}  # Deezer album id -> its genre and year
        self.apple_ok = True

    def _json(self, url: str, params: dict | None = None):
        response = self.get(url, params or {})
        if response.status_code in (403, 429) and url == ITUNES_SEARCH:
            self.apple_ok = False
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def find(self, artist: str, title: str, duration_s: int = 0) -> Info | None:
        """Genre and year for a song, or None. Network errors raise."""
        return self._deezer(artist, title, duration_s) or self._apple(artist, title, duration_s)

    def _deezer(self, artist: str, title: str, duration_s: int) -> Info | None:
        main = (split_artists(artist) or [artist])[0]
        data = self._json(DEEZER_SEARCH, {"q": f'artist:"{main}" track:"{title}"', "limit": 10}) or {}
        self.sleep(0.12)  # Deezer allows 50 requests every 5 seconds
        for item in data.get("data", []):
            if not (_same_title(title, item.get("title", "")) and _same_artist(artist, (item.get("artist") or {}).get("name", ""))):
                continue
            if duration_s and item.get("duration") and abs(item["duration"] - duration_s) > 5:
                continue
            album_id = (item.get("album") or {}).get("id")
            if not album_id:
                continue
            if album_id not in self.albums:
                album = self._json(f"{DEEZER_ALBUM}{album_id}") or {}
                self.sleep(0.12)
                genres = [g.get("name", "") for g in (album.get("genres") or {}).get("data", [])]
                self.albums[album_id] = Info(next((g for g in genres if g), ""), _year(album.get("release_date")), "Deezer")
            info = self.albums[album_id]
            if info.genre or info.year:
                return info
        return None

    def _apple(self, artist: str, title: str, duration_s: int) -> Info | None:
        if not self.apple_ok:
            return None
        main = (split_artists(artist) or [artist])[0]
        data = self._json(ITUNES_SEARCH, {"term": f"{main} {title}", "entity": "song", "limit": 10}) or {}
        for item in data.get("results", []):
            if not (_same_title(title, item.get("trackName", "")) and _same_artist(artist, item.get("artistName", ""))):
                continue
            seconds = (item.get("trackTimeMillis") or 0) / 1000
            if duration_s and seconds and abs(seconds - duration_s) > 5:
                continue
            info = Info(item.get("primaryGenreName", "") or "", _year(item.get("releaseDate")), "Apple Music")
            if info.genre or info.year:
                return info
        return None


class InfoLookups:
    def __init__(self, library):
        self.db = library.db
        self.db.executescript(SCHEMA)

    @staticmethod
    def _key(artist: str, title: str) -> str:
        main = (split_artists(artist) or [artist])[0]
        return f"{normalize(main)}|{core_title(title)}"

    def recently_missing(self, artist: str, title: str) -> bool:
        row = self.db.execute("SELECT found, checked_at FROM info_lookups WHERE song = ?",
                              (self._key(artist, title),)).fetchone()
        if not row or row["found"]:
            return False
        checked = datetime.datetime.fromisoformat(row["checked_at"])
        return datetime.datetime.now(datetime.timezone.utc) - checked < datetime.timedelta(days=RETRY_AFTER_DAYS)

    def remember(self, artist: str, title: str, found: bool) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO info_lookups (song, found, checked_at) VALUES (?, ?, ?)",
                            (self._key(artist, title), int(found),
                             datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))


def complete(track, finder: InfoFinder, lookups: InfoLookups | None = None) -> bool:
    """Fill in a Track's missing genre and year. Returns True if anything changed."""
    if track.genre and track.year:
        return False
    if lookups is not None and lookups.recently_missing(track.artist, track.title):
        return False
    info = finder.find(track.artist, track.title, round((track.duration_ms or 0) / 1000))
    if lookups is not None:
        lookups.remember(track.artist, track.title, info is not None)
    if info is None:
        return False
    changed = False
    if not track.genre and info.genre:
        track.genre = info.genre
        changed = True
    if not track.year and info.year:
        track.release_date = str(info.year)
        changed = True
    return changed
