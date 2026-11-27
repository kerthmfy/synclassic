"""Lyrics from LRCLIB (lrclib.net, a free open lyrics database), put inside song files.

The iPod 5G/5.5G shows a song's lyrics (press the center button while it plays) when they are
inside the file (M4A ©lyr, MP3 USLT) and the iPod's library says the song has lyrics.

Songs LRCLIB doesn't know (and instrumentals) are remembered for a while so they aren't asked
about at every update.
"""

from __future__ import annotations

import datetime
import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from synclassic.textnorm import core_title, normalize, split_artists

GET_URL = "https://lrclib.net/api/get"
SEARCH_URL = "https://lrclib.net/api/search"
USER_AGENT = "Synclassic/1.0 (iPod manager)"
RETRY_AFTER_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS lyrics_lookups (
    song TEXT PRIMARY KEY,          -- artist|title, normalised
    result TEXT NOT NULL,           -- found, none, instrumental
    checked_at TEXT NOT NULL
);
"""


@dataclass
class Found:
    text: str | None  # plain lyrics; None for an instrumental
    instrumental: bool = False


def _song_key(artist: str, title: str) -> str:
    main = (split_artists(artist) or [artist])[0]
    return f"{normalize(main)}|{core_title(title)}"


def _plain(item: dict) -> Found | None:
    if item.get("instrumental"):
        return Found(None, instrumental=True)
    text = (item.get("plainLyrics") or "").strip()
    if not text and item.get("syncedLyrics"):  # only timed lyrics: drop the [mm:ss.xx] stamps
        lines = [line.split("]", 1)[-1].strip() for line in item["syncedLyrics"].splitlines()]
        text = "\n".join(lines).strip()
    return Found(text) if text else None


class LyricsFinder:
    def __init__(self, get=None, sleep: Callable[[float], None] = time.sleep):
        self.get = get or (lambda url, params: requests.get(url, params=params, timeout=20,
                                                            headers={"User-Agent": USER_AGENT}))
        self.sleep = sleep

    def _json(self, url: str, params: dict):
        try:
            response = self.get(url, params)
        except requests.RequestException:
            raise
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise requests.RequestException(f"LRCLIB answered HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            return None

    def find(self, artist: str, title: str, album: str = "", duration_s: int = 0) -> Found | None:
        """Lyrics for a song, or None if LRCLIB doesn't have them. Network errors raise."""
        main = (split_artists(artist) or [artist])[0]
        if duration_s:
            params = {"artist_name": main, "track_name": title, "duration": duration_s}
            if album:
                params["album_name"] = album
            item = self._json(GET_URL, params)
            self.sleep(0.15)
            if isinstance(item, dict):
                found = _plain(item)
                if found:
                    return found
        results = self._json(SEARCH_URL, {"track_name": title, "artist_name": main}) or []
        self.sleep(0.15)
        wanted_title, wanted_artist = core_title(title), normalize(main)
        best = None
        for item in results if isinstance(results, list) else []:
            if core_title(item.get("trackName", "")) != wanted_title:
                continue
            if wanted_artist not in {normalize(a) for a in split_artists(item.get("artistName", ""))} | {
                    normalize(item.get("artistName", ""))}:
                continue
            off = abs((item.get("duration") or 0) - duration_s) if duration_s else 0
            if duration_s and off > 4:
                continue  # probably another version
            found = _plain(item)
            if found and (best is None or off < best[0]):
                best = (off, found)
        return best[1] if best else None


class LyricsLookups:
    """Remembers songs LRCLIB had nothing for, so they're only asked about again after a while."""

    def __init__(self, library):
        self.db = library.db
        self.db.executescript(SCHEMA)

    def recently_missing(self, artist: str, title: str) -> bool:
        row = self.db.execute("SELECT result, checked_at FROM lyrics_lookups WHERE song = ?",
                              (_song_key(artist, title),)).fetchone()
        if not row or row["result"] == "found":
            return False
        checked = datetime.datetime.fromisoformat(row["checked_at"])
        return datetime.datetime.now(datetime.timezone.utc) - checked < datetime.timedelta(days=RETRY_AFTER_DAYS)

    def remember(self, artist: str, title: str, result: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO lyrics_lookups (song, result, checked_at) VALUES (?, ?, ?)",
                            (_song_key(artist, title), result,
                             datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))


# --- inside song files ----------------------------------------------------------------------


def read(path: pathlib.Path) -> str | None:
    """The lyrics inside a song file, if any."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".m4a", ".m4b", ".mp4", ".aac"):
            from mutagen.mp4 import MP4

            tags = MP4(path).tags
            values = tags.get("\xa9lyr") if tags else None
            return values[0] if values else None
        if suffix == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError

            try:
                frames = ID3(path).getall("USLT")
            except ID3NoHeaderError:
                return None
            return frames[0].text if frames else None
    except Exception:
        return None
    return None


def write(path: pathlib.Path, text: str) -> None:
    """Put lyrics inside a song file (replacing any)."""
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".m4b", ".mp4", ".aac"):
        from mutagen.mp4 import MP4

        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9lyr"] = [text]
        audio.save()
    elif suffix == ".mp3":
        from mutagen.id3 import ID3, USLT, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
        tags.save(path)
    else:
        raise ValueError(f"Can't put lyrics inside {suffix} files")


def fetch_and_write(path: pathlib.Path, artist: str, title: str, album: str, duration_s: int,
                    finder: LyricsFinder, lookups: LyricsLookups | None = None) -> str:
    """Look lyrics up and put them in the file. Returns "found", "none", "instrumental" or "skipped"
    (asked recently, nothing then). Network errors raise."""
    if lookups is not None and lookups.recently_missing(artist, title):
        return "skipped"
    found = finder.find(artist, title, album, duration_s)
    result = "found" if found and found.text else ("instrumental" if found else "none")
    if found and found.text:
        write(path, found.text)
    if lookups is not None:
        lookups.remember(artist, title, result)
    return result
