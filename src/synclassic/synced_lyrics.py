"""Timed lyrics for karaoke (View → Lyrics while a song plays in Synclassic).

Timed lyrics are in LRC form: "[01:02.50]a line" (a line can carry several times). They come
from, in this order:
- inside the song file, when its lyrics have times in them (some FLAC/M4A files do);
- Synclassic's cache (Lyrics folder in Synclassic's own folder), filled whenever LRCLIB is
  asked for a song's lyrics, also while downloading;
- LRCLIB (lrclib.net), asked when the song plays.
Songs LRCLIB has no timed lyrics for are remembered for 30 days. Without timed lyrics, the
plain lyrics are shown without following along.
"""

from __future__ import annotations

import bisect
import hashlib
import pathlib
import re
import time
from dataclasses import dataclass

from synclassic.config import app_dir

_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\]", re.IGNORECASE)
RETRY_AFTER_S = 30 * 24 * 3600


@dataclass
class Lyrics:
    lines: list[tuple[int, str]]  # (start in ms, text), in order; empty when not timed
    plain: str = ""  # the words without times (always set when there are lyrics)
    source: str = ""  # "file", "cache" or "LRCLIB"

    @property
    def timed(self) -> bool:
        return bool(self.lines)

    def line_at(self, ms: int) -> int:
        """Index of the line being sung at `ms` (-1 before the first)."""
        return bisect.bisect_right([start for start, _ in self.lines], ms) - 1


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """LRC text -> [(ms, line)], sorted. Lines without a time are left out."""
    offset = 0
    found = _OFFSET.search(text or "")
    if found:
        offset = int(found.group(1))
    lines: list[tuple[int, str]] = []
    for raw in (text or "").splitlines():
        stamps = list(_STAMP.finditer(raw))
        if not stamps or stamps[0].start() != 0:
            continue
        words = raw[stamps[-1].end():].strip()
        for stamp in stamps:
            minutes, seconds, fraction = stamp.group(1), stamp.group(2), stamp.group(3) or "0"
            ms = (int(minutes) * 60 + int(seconds)) * 1000 + int(fraction.ljust(3, "0")[:3])
            lines.append((max(0, ms - offset), words))
    lines.sort(key=lambda pair: pair[0])
    return lines


def plain_of(text: str) -> str:
    """The words of LRC text without the times and tags."""
    out = []
    for raw in (text or "").splitlines():
        stamps = list(_STAMP.finditer(raw))
        if stamps and stamps[0].start() == 0:
            out.append(raw[stamps[-1].end():].strip())
        elif not re.match(r"^\[[a-z]+:.*\]$", raw.strip(), re.IGNORECASE):
            out.append(raw.strip())
    return "\n".join(out).strip()


# --- the cache ----------------------------------------------------------------------------------


def _folder() -> pathlib.Path:
    return app_dir() / "Lyrics"


def _key(artist: str, title: str) -> str:
    from synclassic.lyrics import _song_key

    return hashlib.sha1(_song_key(artist, title).encode("utf-8")).hexdigest()


def remember(artist: str, title: str, synced: str | None) -> None:
    """Keep LRCLIB's timed lyrics (or that it had none)."""
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    key = _key(artist, title)
    if synced and parse_lrc(synced):
        (folder / f"{key}.lrc").write_text(synced, encoding="utf-8")
        (folder / f"{key}.none").unlink(missing_ok=True)
    else:
        (folder / f"{key}.none").write_text("", encoding="utf-8")


def cached(artist: str, title: str, now: float | None = None) -> tuple[str | None, bool]:
    """(timed lyrics, asked recently and LRCLIB had none)."""
    folder, key = _folder(), _key(artist, title)
    try:
        return (folder / f"{key}.lrc").read_text(encoding="utf-8"), False
    except OSError:
        pass
    marker = folder / f"{key}.none"
    try:
        age = (now or time.time()) - marker.stat().st_mtime
    except OSError:
        return None, False
    return None, age < RETRY_AFTER_S


# --- finding them -------------------------------------------------------------------------------


def find(path: pathlib.Path | None, artist: str, title: str, album: str = "", duration_s: int = 0,
         finder=None, network: bool = True) -> Lyrics | None:
    """Lyrics for a song, timed when possible (network errors fall back to what's on this Mac)."""
    from synclassic import lyrics as plain_lyrics

    inside = plain_lyrics.read(path) if path is not None and path.is_file() else None
    if inside and parse_lrc(inside):
        return Lyrics(parse_lrc(inside), plain_of(inside), "file")
    if artist and title:
        synced, none_recently = cached(artist, title)
        if synced:
            return Lyrics(parse_lrc(synced), plain_of(synced), "cache")
        if network and not none_recently:
            try:
                found = (finder or plain_lyrics.LyricsFinder()).find(artist, title, album, duration_s)
            except Exception:
                found = None  # offline: what's in the file will do
            else:
                remember(artist, title, found.synced if found else None)
                if found and found.synced and parse_lrc(found.synced):
                    return Lyrics(parse_lrc(found.synced), found.text or plain_of(found.synced), "LRCLIB")
                if found and found.text and not inside:
                    return Lyrics([], found.text, "LRCLIB")
    if inside:
        return Lyrics([], inside.strip(), "file")
    return None
