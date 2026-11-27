"""Get Lyrics: fill in lyrics for songs that don't have them, on this Mac and on the iPod.

On the iPod, lyrics live inside the song file, so each file is updated safely (a copy is
changed and then swapped in) and the iPod's library is told the song's new size and that it
has lyrics. Synclassic's own songs reuse the lyrics already found for the copy on this Mac.
"""

from __future__ import annotations

import os
import pathlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass

import requests

from synclassic import lyrics
from synclassic.ipod import itunesdb
from synclassic.ipod.device import IPod

LYRICS_FLAG = 0xB0  # mhit: the song has lyrics


@dataclass
class LyricsResult:
    found: int = 0
    not_found: int = 0
    failed: int = 0
    stopped: bool = False

    def summary(self) -> str:
        parts = [f"lyrics for {self.found} song{'s' if self.found != 1 else ''}"]
        if self.not_found:
            parts.append(f"{self.not_found} not found")
        if self.failed:
            parts.append(f"{self.failed} couldn't be checked")
        return ("Stopped: " if self.stopped else "") + ", ".join(parts) + "."


def library_missing(library) -> list:
    """Synclassic's songs on this Mac without lyrics (not counting ones asked about recently)."""
    lookups = lyrics.LyricsLookups(library)
    return [item for item in library.tracks_in_playlists()
            if item.has_file and not lyrics.read(pathlib.Path(item.file_path))
            and not lookups.recently_missing(item.track.artist, item.track.title)]


def ipod_missing(ipod: IPod, library=None) -> list[itunesdb.SongView]:
    db = itunesdb.parse(ipod.database_path.read_bytes())
    lookups = lyrics.LyricsLookups(library) if library is not None else None
    songs = []
    for song in itunesdb.songs(db):
        if song.media_type not in (0, 1) or not song.location or len(song.chunk.header) <= LYRICS_FLAG:
            continue
        if song.chunk.u8(LYRICS_FLAG) == 1:
            continue
        if lookups is not None and lookups.recently_missing(song.artist, song.title):
            continue
        songs.append(song)
    return songs


def fill_library(library, finder: lyrics.LyricsFinder | None = None, progress=None,
                 should_stop: Callable[[], bool] | None = None, say: Callable[[str], None] = lambda s: None) -> LyricsResult:
    finder = finder or lyrics.LyricsFinder()
    lookups = lyrics.LyricsLookups(library)
    result = LyricsResult()
    items = library_missing(library)
    for number, item in enumerate(items, start=1):
        if should_stop and should_stop():
            result.stopped = True
            break
        track = item.track
        if progress:
            progress(number, len(items), track.display())
        try:
            outcome = lyrics.fetch_and_write(pathlib.Path(item.file_path), track.artist, track.title, track.album or "",
                                             round((track.duration_ms or 0) / 1000), finder, lookups)
        except (requests.RequestException, OSError, ValueError) as error:
            say(f"  {track.display()}: {error}")
            result.failed += 1
            continue
        if outcome == "found":
            result.found += 1
        elif outcome != "skipped":
            result.not_found += 1
    return result


def fill_ipod(ipod: IPod, library=None, finder: lyrics.LyricsFinder | None = None, make_backup: bool = True,
              progress=None, should_stop: Callable[[], bool] | None = None,
              say: Callable[[str], None] = lambda s: None) -> LyricsResult:
    """Lyrics for the iPod's songs. Synclassic's songs use their Mac copy's lyrics when it has them."""
    from synclassic.ipod.manage import EditSession
    from synclassic.ipod.sync import load_state

    finder = finder or lyrics.LyricsFinder()
    lookups = lyrics.LyricsLookups(library) if library is not None else None
    mac_copy: dict[int, pathlib.Path] = {}
    if library is not None:
        by_key = {item.key: item for item in library.tracks_in_playlists() if item.has_file}
        for key, entry in load_state(ipod)["tracks"].items():
            if key in by_key:
                mac_copy[int(entry["dbid"], 16)] = pathlib.Path(by_key[key].file_path)  # type: ignore[arg-type]

    result = LyricsResult()
    changed: dict[int, int] = {}  # dbid -> new file size
    songs = ipod_missing(ipod, library)
    for number, song in enumerate(songs, start=1):
        if should_stop and should_stop():
            result.stopped = True
            break
        if progress:
            progress(number, len(songs), f"{song.artist} - {song.title}")
        path = ipod.root / song.relative_path
        if not path.is_file():
            continue
        text = lyrics.read(path)  # already inside (e.g. added by another app), just not flagged
        if not text and song.dbid in mac_copy:
            text = lyrics.read(mac_copy[song.dbid])
        if not text:
            try:
                found = finder.find(song.artist, song.title, song.album, round(song.length_ms / 1000))
            except requests.RequestException as error:
                say(f"  {song.title}: {error}")
                result.failed += 1
                continue
            outcome = "found" if found and found.text else ("instrumental" if found else "none")
            if lookups is not None:
                lookups.remember(song.artist, song.title, outcome)
            if outcome != "found":
                result.not_found += 1
                continue
            text = found.text
            if song.dbid in mac_copy:  # keep the Mac's copy in step
                try:
                    lyrics.write(mac_copy[song.dbid], text)  # type: ignore[arg-type]
                except (OSError, ValueError):
                    pass
        try:
            if lyrics.read(path) != text:
                _write_safely(path, text)  # type: ignore[arg-type]
        except (OSError, ValueError) as error:
            say(f"  {song.title}: couldn't change the file on the iPod: {error}")
            result.failed += 1
            continue
        changed[song.dbid] = path.stat().st_size
        result.found += 1
    if changed:
        session = EditSession(ipod, say, make_backup)
        for song in itunesdb.songs(session.db):
            if song.dbid in changed:
                mhit = song.chunk
                if len(mhit.header) >= 0x130 and mhit.u32(0x12C) == mhit.u32(0x24):
                    mhit.set_u32(0x12C, changed[song.dbid])
                mhit.set_u32(0x24, changed[song.dbid])
                mhit.set_u8(LYRICS_FLAG, 1)
        session.commit()
    return result


def _write_safely(path: pathlib.Path, text: str) -> None:
    """Change a copy next to the file, then swap it in: an interruption never leaves half a file."""
    tmp = path.with_name(path.stem + ".lyrics-part" + path.suffix)
    shutil.copyfile(path, tmp)
    try:
        lyrics.write(tmp, text)
        with open(tmp, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
