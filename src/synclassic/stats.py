"""Listening stats: your most played songs, artists and albums.

Two sources:
- the iPod's own play counts (all time, for every song on it);
- a play history Synclassic keeps from now on (plays.jsonl): plays read from the iPod at each
  sync and songs played in the app, each with its time, for "last 7 / 30 days".
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field

from synclassic.config import app_dir


def history_file():
    return app_dir() / "plays.jsonl"


def record(plays: list[dict]) -> None:
    """Add plays (artist, track, album, timestamp, duration[, genre, source]) to the history."""
    if not plays:
        return
    path = history_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for play in plays:
            keep = {k: play.get(k) for k in ("artist", "track", "album", "album_artist", "timestamp", "duration",
                                             "genre", "source") if play.get(k) not in (None, "")}
            handle.write(json.dumps(keep, ensure_ascii=False) + "\n")


def history(since: float | None = None) -> list[dict]:
    path = history_file()
    if not path.is_file():
        return []
    plays = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            play = json.loads(line)
        except ValueError:
            continue
        if since is None or play.get("timestamp", 0) >= since:
            plays.append(play)
    return plays


@dataclass
class Stats:
    title: str
    plays: int = 0
    seconds: int = 0
    songs: list[tuple[str, str, int]] = field(default_factory=list)  # (title, artist, plays)
    artists: list[tuple[str, int]] = field(default_factory=list)
    albums: list[tuple[str, str, int]] = field(default_factory=list)  # (album, artist, plays)
    genres: list[tuple[str, int]] = field(default_factory=list)

    @property
    def hours(self) -> float:
        return self.seconds / 3600


def _top(counter: Counter, limit: int) -> list:
    return [(*key, count) if isinstance(key, tuple) else (key, count) for key, count in counter.most_common(limit)]


def from_history(days: int, now: float | None = None, limit: int = 25) -> Stats:
    now = time.time() if now is None else now
    return from_period(now - days * 86400, now, f"Last {days} days", limit)


def for_year(year: int, limit: int = 25) -> Stats:
    import datetime

    start = datetime.datetime(year, 1, 1).timestamp()
    end = datetime.datetime(year + 1, 1, 1).timestamp()
    return from_period(start, end, str(year), limit)


def from_period(start: float, end: float, title: str, limit: int = 25) -> Stats:
    stats = Stats(title)
    songs, artists, albums, genres = Counter(), Counter(), Counter(), Counter()
    for play in history(start):
        if play.get("timestamp", 0) >= end:
            continue
        artist = play.get("album_artist") or play.get("artist", "")
        songs[(play.get("track", ""), play.get("artist", ""))] += 1
        artists[play.get("artist", "")] += 1
        if play.get("album"):
            albums[(play["album"], artist)] += 1
        if play.get("genre"):
            genres[play["genre"]] += 1
        stats.plays += 1
        stats.seconds += int(play.get("duration") or 0)
    stats.songs, stats.artists = _top(songs, limit), _top(artists, limit)
    stats.albums, stats.genres = _top(albums, limit), _top(genres, limit)
    return stats


def from_ipod(songs, limit: int = 25) -> Stats:
    """All-time stats from the iPod's play counts (browse.Song or itunesdb.SongView objects)."""
    stats = Stats("All time on your iPod")
    by_song, artists, albums, genres = Counter(), Counter(), Counter(), Counter()
    for song in songs:
        plays = getattr(song, "plays", None)
        plays = song.play_count if plays is None else plays
        kind = getattr(song, "kind", "song")
        if not plays or kind not in ("song",):
            continue
        by_song[(song.title, song.artist)] += plays
        artists[song.artist] += plays
        if song.album:
            albums[(song.album, song.album_artist or song.artist)] += plays
        if song.genre:
            genres[song.genre] += plays
        stats.plays += plays
        stats.seconds += plays * song.length_ms // 1000
    stats.songs, stats.artists = _top(by_song, limit), _top(artists, limit)
    stats.albums, stats.genres = _top(albums, limit), _top(genres, limit)
    return stats
