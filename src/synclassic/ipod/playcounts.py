"""Merge what happened on the iPod since the last sync into the database.

The iPod doesn't change iTunesDB itself. It writes:
- iTunes/Play Counts: one entry per song, *by position* in the song list: plays and skips
  since the last sync, last played/skipped time, bookmark, and current rating;
- iTunes/OTGPlaylistInfo*: On-The-Go playlists, as song positions.

Because both refer to songs by position, they must be merged *before* songs are added or
removed, and then deleted (as iTunes and libgpod do), or plays land on the wrong songs.
"""

from __future__ import annotations

import pathlib
import struct
from dataclasses import dataclass

from synclassic.ipod import itunesdb
from synclassic.ipod.itunesdb import Chunk


@dataclass
class PlayCount:
    plays: int = 0
    last_played: int = 0
    bookmark: int = 0
    rating: int | None = None
    skips: int = 0
    last_skipped: int = 0


def parse_play_counts(data: bytes) -> list[PlayCount]:
    if len(data) < 16 or data[:4] != b"mhdp":
        raise ValueError("Not a Play Counts file")
    header_len, entry_len, count = struct.unpack_from("<III", data, 4)
    entries = []
    for i in range(count):
        offset = header_len + i * entry_len
        if offset + min(entry_len, 12) > len(data):
            break
        plays, last_played, bookmark = struct.unpack_from("<III", data, offset)
        entry = PlayCount(plays, last_played, bookmark)
        if entry_len >= 0x10:
            rating = struct.unpack_from("<I", data, offset + 12)[0]
            entry.rating = None if rating == 0xFFFFFFFF else rating
        if entry_len >= 0x1C:
            entry.skips, entry.last_skipped = struct.unpack_from("<II", data, offset + 20)
        entries.append(entry)
    return entries


def merge_play_counts(db: Chunk, entries: list[PlayCount]) -> int:
    """Apply Play Counts entries to the songs, in song-list order. Returns songs changed."""
    songs = itunesdb.songs(db)
    changed = 0
    for song, entry in zip(songs, entries):
        mhit = song.chunk
        touched = False
        if entry.plays:
            mhit.set_u32(itunesdb.MHIT_PLAY_COUNT, mhit.u32(itunesdb.MHIT_PLAY_COUNT) + entry.plays)
            mhit.set_u32(0x54, entry.plays)  # plays since the last sync
            touched = True
        if entry.last_played > mhit.u32(0x58):
            mhit.set_u32(0x58, entry.last_played)
            touched = True
        if entry.bookmark and entry.bookmark != mhit.u32(0x6C):
            mhit.set_u32(0x6C, entry.bookmark)
            touched = True
        if entry.rating is not None and entry.rating <= 100 and entry.rating != mhit.u8(itunesdb.MHIT_RATING):
            mhit.set_u8(0x79, mhit.u8(itunesdb.MHIT_RATING))  # previous rating
            mhit.set_u8(itunesdb.MHIT_RATING, entry.rating)
            touched = True
        if entry.skips and len(mhit.header) >= 0xA4:
            mhit.set_u32(0x98, mhit.u32(0x98) + entry.skips)
            touched = True
        if entry.last_skipped > mhit.u32(0xA0) and len(mhit.header) >= 0xA4:
            mhit.set_u32(0xA0, entry.last_skipped)
            touched = True
        changed += touched
    return changed


def parse_otg(data: bytes) -> list[int]:
    """Song positions of one On-The-Go playlist file."""
    if len(data) < 0x14:
        return []
    fmt = "<" if data[:4] == b"mhpo" else ">" if data[:4] == b"ohpm" else None
    if fmt is None:
        return []
    header_len, entry_len, count = struct.unpack_from(f"{fmt}III", data, 4)
    if header_len < 0x14 or entry_len < 4:
        return []
    positions = []
    for i in range(count):
        offset = header_len + i * entry_len
        if offset + 4 > len(data):
            break
        positions.append(struct.unpack_from(f"{fmt}I", data, offset)[0])
    return positions


def otg_files(itunes_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in itunes_dir.glob("OTGPlaylistInfo*") if p.is_file())
