"""iPod smart playlists: Recently Added, Recently Played, Top 25 Most Played, My Top Rated, Never
Played, written the way iTunes writes them (rules in mhod 51 "SLst", options in mhod 50) with
live updating on, so the iPod keeps them current as you play. Synclassic also fills in their
songs at every sync. Which ones you chose is kept on the iPod (state.json "smart").

Rule and option layout as documented by libgpod (itdb_itunesdb.c); rule data is big-endian.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

from synclassic.ipod import itunesdb
from synclassic.ipod.edit import Editor, mac_time

SPLPREF, SPLRULES = 50, 51
DATE_IDENTIFIER = 0x2DAE2DAE2DAE2DAE  # "relative date" marker in rule values

# Fields
DATE_ADDED, PLAY_COUNT, LAST_PLAYED, RATING, MEDIA_KIND = 0x10, 0x16, 0x17, 0x19, 0x3C
# Actions
IS_INT, IS_GREATER_THAN, IS_IN_THE_LAST, BINARY_AND = 0x01, 0x10, 0x200, 0x400
MATCH_ALL = 0
LIMIT_SONGS = 3
SORT_MOST_RECENTLY_ADDED, SORT_MOST_OFTEN_PLAYED, SORT_MOST_RECENTLY_PLAYED, SORT_HIGHEST_RATING = 0x10, 0x14, 0x15, 0x17
WEEK = 7 * 24 * 3600


@dataclass(frozen=True)
class Rule:
    field: int
    action: int
    value: int = 0  # a number (play count, rating x 20, media kind bits)
    last: int = 0  # IS_IN_THE_LAST: how many units
    unit: int = 0  # IS_IN_THE_LAST: seconds per unit


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    rules: tuple[Rule, ...]
    sort: int
    limit: int = 0  # songs; 0 = no limit
    description: str = ""


MUSIC = Rule(MEDIA_KIND, BINARY_AND, 1)
PRESETS = [
    Preset("recently_added", "Recently Added", (Rule(DATE_ADDED, IS_IN_THE_LAST, last=2, unit=WEEK), MUSIC),
           SORT_MOST_RECENTLY_ADDED, description="Songs added in the last 2 weeks"),
    Preset("recently_played", "Recently Played", (Rule(LAST_PLAYED, IS_IN_THE_LAST, last=2, unit=WEEK), MUSIC),
           SORT_MOST_RECENTLY_PLAYED, description="Songs played in the last 2 weeks"),
    Preset("top_25", "Top 25 Most Played", (Rule(PLAY_COUNT, IS_GREATER_THAN, 0), MUSIC),
           SORT_MOST_OFTEN_PLAYED, limit=25, description="Your 25 most played songs"),
    Preset("top_rated", "My Top Rated", (Rule(RATING, IS_GREATER_THAN, 60), MUSIC),
           SORT_HIGHEST_RATING, description="Songs rated 4 or 5 stars"),
    Preset("never_played", "Never Played", (Rule(PLAY_COUNT, IS_INT, 0), MUSIC),
           SORT_MOST_RECENTLY_ADDED, description="Songs you haven't played yet"),
]
BY_KEY = {p.key: p for p in PRESETS}


# --- the iPod's format ----------------------------------------------------------------------------


def _mhod(kind: int, body: bytes) -> itunesdb.Chunk:
    header = bytearray(24)
    header[0:4] = b"mhod"
    struct.pack_into("<III", header, 4, 24, 24 + len(body), kind)
    return itunesdb.Chunk(b"mhod", header, body=body)


def pref_mhod(preset: Preset) -> itunesdb.Chunk:
    """Options: live updating, rules on, the limit (songs) and its sort. 72 bytes after the header."""
    sort = preset.sort
    body = bytes([1, 1, 1 if preset.limit else 0, LIMIT_SONGS, sort & 0xFF, 0, 0, 0])
    body += struct.pack("<I", preset.limit or 25)
    body += bytes([0, 1 if sort & 0x80000000 else 0, 0, 0]) + bytes(14 * 4)
    return _mhod(SPLPREF, body)


def rules_mhod(preset: Preset) -> itunesdb.Chunk:
    body = b"SLst" + struct.pack(">III", 0, len(preset.rules), MATCH_ALL) + bytes(120)
    for rule in preset.rules:
        body += struct.pack(">II", rule.field, rule.action) + bytes(44)
        if rule.action == IS_IN_THE_LAST:
            values = (DATE_IDENTIFIER, (-rule.last) & 0xFFFFFFFFFFFFFFFF, rule.unit, DATE_IDENTIFIER, 0, 1)
        else:
            values = (rule.value, 0, 1, rule.value, 0, 1)
        body += struct.pack(">I", 0x44) + struct.pack(">6Q", *values) + bytes(5 * 4)
    return _mhod(SPLRULES, body)


def parse_rules(mhod: itunesdb.Chunk) -> list[tuple[int, int, int]]:
    """(field, action, value) of each rule in an SLst mhod (for checking what was written)."""
    body = mhod.body
    if body[:4] != b"SLst":
        raise ValueError("not a smart playlist rule list")
    _, count, _ = struct.unpack_from(">III", body, 4)
    offset, rules = 4 + 12 + 120, []
    for _ in range(count):
        field, action = struct.unpack_from(">II", body, offset)
        offset += 8 + 44
        length = struct.unpack_from(">I", body, offset)[0]
        offset += 4
        value = struct.unpack_from(">Q", body, offset)[0] if length == 0x44 else 0
        rules.append((field, action, value))
        offset += length
    return rules


# --- choosing the songs (the iPod refreshes them itself too) -----------------------------------------


def members(preset: Preset, songs: list[itunesdb.SongView], now: int | None = None) -> list[itunesdb.SongView]:
    now = mac_time() if now is None else now

    def matches(song: itunesdb.SongView) -> bool:
        mhit = song.chunk
        for rule in preset.rules:
            if rule.field == MEDIA_KIND:
                if not (song.media_type & rule.value or song.media_type == 0):
                    return False
                continue
            value = {DATE_ADDED: mhit.u32(0x68), LAST_PLAYED: mhit.u32(0x58), PLAY_COUNT: song.play_count,
                     RATING: song.rating}[rule.field]
            if rule.action == IS_IN_THE_LAST and not (value and value >= now - rule.last * rule.unit):
                return False
            if rule.action == IS_GREATER_THAN and not value > rule.value:
                return False
            if rule.action == IS_INT and value != rule.value:
                return False
        return True

    chosen = [s for s in songs if matches(s)]
    key = {SORT_MOST_RECENTLY_ADDED: lambda s: s.chunk.u32(0x68), SORT_MOST_OFTEN_PLAYED: lambda s: s.play_count,
           SORT_MOST_RECENTLY_PLAYED: lambda s: s.chunk.u32(0x58), SORT_HIGHEST_RATING: lambda s: s.rating}[preset.sort]
    chosen.sort(key=key, reverse=True)
    return chosen[: preset.limit] if preset.limit else chosen


# --- putting them on ------------------------------------------------------------------------------


def chosen(state: dict) -> dict[str, int]:
    """The smart playlists Synclassic keeps on this iPod: preset key -> playlist id."""
    return {key: int(value, 16) for key, value in state.get("smart", {}).items() if key in BY_KEY}


def apply(editor: Editor, db: itunesdb.Chunk, state: dict, wanted: set[str] | None = None) -> list[str]:
    """Create, refresh or remove Synclassic's smart playlists. `wanted` = the presets to have (None:
    keep the current choice and just refresh them). Returns what changed, for messages."""
    current = chosen(state)
    wanted = set(current) if wanted is None else wanted
    existing = {p.chunk.u64(0x1C) for p in itunesdb.playlists(db, 2)}
    changes = []
    for key, playlist_id in list(current.items()):
        if key not in wanted and playlist_id in existing:
            editor.delete_playlist(playlist_id)
            changes.append(f"removed “{BY_KEY[key].name}”")
        if key not in wanted:
            current.pop(key)
    songs = itunesdb.songs(db)
    now = mac_time(time.time())
    for preset in PRESETS:
        if preset.key not in wanted:
            continue
        playlist_id = current.get(preset.key)
        if playlist_id is None or playlist_id not in existing:
            playlist_id = editor.create_playlist(preset.name)
            for mhyp in editor.playlists(playlist_id):
                title_at = next((i for i, c in enumerate(mhyp.children) if c.tag == b"mhod" and c.mhod_type == 1), -1)
                mhyp.children[title_at + 1:title_at + 1] = [pref_mhod(preset), rules_mhod(preset)]
            current[preset.key] = playlist_id
            changes.append(f"added “{preset.name}”")
        editor.set_playlist_songs(playlist_id, [(s.id, s.dbid) for s in members(preset, songs, now)])
    state["smart"] = {key: f"{pid:016x}" for key, pid in current.items()}
    return changes


def set_smart_playlists(ipod, wanted: set[str], make_backup: bool = True, say=lambda s: None) -> list[str]:
    """Choose which smart playlists the iPod has (from the app's dialog)."""
    from synclassic.ipod.manage import EditSession

    session = EditSession(ipod, say, make_backup)
    changes = apply(session.editor, session.db, session.state, wanted)
    session.state_changed = True
    session.commit()
    return changes
