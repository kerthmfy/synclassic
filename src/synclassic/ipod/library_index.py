"""The pre-sorted indexes the iPod uses for its Songs / Artists / Albums / Genres menus.

They live in the main (master) playlist as text-field chunks of type 52 (the songs, sorted)
and 53 (a jump table: where each letter starts, for fast scrolling). Whenever songs are
added or removed they must be rebuilt, or the iPod's menus show wrong or missing songs.

Layout (after the 24-byte mhod header):
  type 52: sort_type u32, count u32, 40 bytes zero, then `count` u32 song positions
  type 53: sort_type u32, count u32, 8 bytes zero, then `count` x (letter u16, 0 u16, start u32, count u32)
Song positions are positions in the master playlist (not the song list).

Apple's exact sort rules (punctuation, articles, album grouping for type 36) vary between iTunes
versions, so Synclassic never re-sorts existing songs: it keeps the existing order, drops
removed songs, and inserts new songs where they belong. Existing menus stay exactly as they
were, and an unchanged database stays byte-for-byte identical.
Format from libgpod's mk_mhod52/mk_mhod53 and iOpenPod's research.
"""

from __future__ import annotations

import bisect

import struct
import unicodedata

from synclassic.ipod import itunesdb
from synclassic.ipod.itunesdb import Chunk, SongView

SORT_TITLE, SORT_ALBUM, SORT_ARTIST, SORT_GENRE, SORT_COMPOSER = 0x03, 0x04, 0x05, 0x07, 0x12
SORT_SHOW, SORT_SEASON, SORT_EPISODE, SORT_ALBUM_ARTIST = 0x1D, 0x1E, 0x1F, 0x23

# Sort-name text fields (iTunes "Sort Name" etc.), used in place of the display text when set.
SORT_NAME, SORT_ALBUM_TEXT, SORT_ARTIST_TEXT, SORT_ALBUM_ARTIST_TEXT, SORT_COMPOSER_TEXT = 27, 28, 23, 29, 30


def _strip_article(text: str) -> str:
    lower = text.lower()
    for article in ("the ", "a ", "an "):
        if lower.startswith(article):
            return text[len(article):]
    return text


def sort_key(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize("NFKD", _strip_article(text)).casefold()


def jump_letter(text: str) -> int:
    for ch in text or "":
        if ch.isalnum():
            if ch.isdigit():
                return ord("0")
            upper = ord(ch.upper()[0])
            if upper <= 0xFFFF:
                return upper
    return ord("0")


class _Song:
    def __init__(self, view: SongView):
        texts = itunesdb.texts(view.chunk)
        self.title = texts.get(SORT_NAME) or view.title
        self.album = texts.get(SORT_ALBUM_TEXT) or view.album
        self.artist = texts.get(SORT_ARTIST_TEXT) or view.artist
        self.album_artist = texts.get(SORT_ALBUM_ARTIST_TEXT) or view.album_artist or self.artist
        self.genre = view.genre
        self.composer = texts.get(SORT_COMPOSER_TEXT) or texts.get(itunesdb.COMPOSER, "")
        self.track = view.track_number
        self.disc = view.chunk.u32(itunesdb.MHIT_DISC_NUMBER)
        self.show = texts.get(31) or texts.get(19, "")
        self.season = view.chunk.u32(0xD4)
        self.episode = view.chunk.u32(0xD8)

    def key(self, sort_type: int) -> tuple:
        title, album, artist = sort_key(self.title), sort_key(self.album), sort_key(self.artist)
        if sort_type == SORT_ALBUM:
            return (album, self.disc, self.track, title)
        if sort_type == SORT_ARTIST:
            return (artist, album, self.disc, self.track, title)
        if sort_type == SORT_GENRE:
            return (sort_key(self.genre), artist, album, self.disc, self.track, title)
        if sort_type == SORT_COMPOSER:
            return (sort_key(self.composer), album, self.disc, self.track, title)
        if sort_type == SORT_ALBUM_ARTIST:
            return (sort_key(self.album_artist), album, self.disc, self.track, title)
        if sort_type == SORT_SHOW:
            return (sort_key(self.show), self.season, self.episode, title)
        if sort_type == SORT_SEASON:
            return (self.season, self.episode, sort_key(self.show), title)
        if sort_type == SORT_EPISODE:
            return (self.episode, self.season, sort_key(self.show), title)
        return (title,)

    def letter_text(self, sort_type: int) -> str:
        return {
            SORT_ALBUM: self.album, SORT_ARTIST: self.artist, SORT_GENRE: self.genre,
            SORT_COMPOSER: self.composer, SORT_ALBUM_ARTIST: self.album_artist, SORT_SHOW: self.show,
        }.get(sort_type, self.title)


SORT_ALBUM_INDEX = 0x24  # album groups (newer iPods); approximated by album artist/album/disc/track


def _key(song: _Song, sort_type: int) -> tuple:
    if sort_type == SORT_ALBUM_INDEX:
        return (sort_key(song.album_artist), sort_key(song.album), song.disc, song.track, sort_key(song.title))
    return song.key(sort_type)


def _letter_text(song: _Song, sort_type: int) -> str:
    if sort_type == SORT_ALBUM_INDEX:
        return song.album_artist
    return song.letter_text(sort_type)


def full_order(ids: list[int], songs: dict[int, SongView], sort_type: int) -> list[int]:
    """Song ids fully sorted by our rules (used only when there's no existing index)."""
    wrapped = {i: _Song(songs[i]) for i in ids}
    return [i for _, i in sorted(((_key(wrapped[i], sort_type), n), i) for n, i in enumerate(ids))]


def merged_order(existing: list[int], wanted: list[int], songs: dict[int, SongView], sort_type: int) -> list[int]:
    """Keep `existing` order for songs still wanted; insert the others where they sort."""
    wanted_set = set(wanted)
    order = [i for i in existing if i in wanted_set]
    present = set(order)
    new = [i for i in wanted if i not in present]
    if not new:
        return order
    keys = [_key(_Song(songs[i]), sort_type) for i in order]
    for song_id in new:
        key = _key(_Song(songs[song_id]), sort_type)
        at = bisect.bisect_right(keys, key)
        keys.insert(at, key)
        order.insert(at, song_id)
    return order


def jump_table(
    order: list[int], songs: dict[int, SongView], sort_type: int, known: dict[int, int] | None = None
) -> list[tuple[int, int, int]]:
    """Letter groups for this order. `known` = letters songs already have on the iPod (kept as-is)."""
    groups: list[list[int]] = []
    for position, song_id in enumerate(order):
        if known and song_id in known:
            letter = known[song_id]
        else:
            letter = jump_letter(_strip_article(_letter_text(_Song(songs[song_id]), sort_type)))
        if groups and groups[-1][0] == letter:
            groups[-1][2] += 1
        else:
            groups.append([letter, position, 1])
    return [tuple(g) for g in groups]  # type: ignore[misc]


def _body52(sort_type: int, positions: list[int]) -> bytes:
    return struct.pack("<II", sort_type, len(positions)) + bytes(40) + struct.pack(f"<{len(positions)}I", *positions)


def _body53(sort_type: int, groups: list[tuple[int, int, int]]) -> bytes:
    entries = b"".join(struct.pack("<HHII", letter, 0, start, count) for letter, start, count in groups)
    return struct.pack("<II", sort_type, len(groups)) + bytes(8) + entries


def _positions(body: bytes) -> tuple[int, list[int]]:
    sort_type, count = struct.unpack_from("<II", body, 0)
    return sort_type, list(struct.unpack_from(f"<{count}I", body, 48))


def index_sort_types(master: Chunk) -> tuple[list[int], list[int]]:
    t52 = [struct.unpack_from("<I", c.body, 0)[0] for c in master.children if c.tag == b"mhod" and c.mhod_type == 52]
    t53 = [struct.unpack_from("<I", c.body, 0)[0] for c in master.children if c.tag == b"mhod" and c.mhod_type == 53]
    return t52, t53


def update(master: Chunk, old_ids: list[int], new_ids: list[int], songs: dict[int, SongView]) -> None:
    """Update the master playlist's indexes after its song list changed from old_ids to new_ids.

    `old_ids` / `new_ids` are the master playlist's song ids in order, before and after.
    `songs` must contain every id in new_ids.
    """
    if old_ids == new_ids:
        return
    new_position = {}
    for position, song_id in enumerate(new_ids):
        new_position.setdefault(song_id, position)
    orders: dict[int, list[int]] = {}
    old_orders: dict[int, list[int]] = {}
    for child in master.children:
        if child.tag == b"mhod" and child.mhod_type == 52 and len(child.body) >= 48:
            sort_type, positions = _positions(child.body)
            existing = [old_ids[p] for p in positions if p < len(old_ids)]
            old_orders[sort_type] = existing
            order = merged_order(existing, list(dict.fromkeys(new_ids)), songs, sort_type)
            orders[sort_type] = order
            child.body = _body52(sort_type, [new_position[i] for i in order])
    for child in master.children:
        if child.tag == b"mhod" and child.mhod_type == 53 and len(child.body) >= 16:
            sort_type = struct.unpack_from("<I", child.body, 0)[0]
            known = _known_letters(child.body, old_orders.get(sort_type, []))
            order = orders.get(sort_type) or full_order(list(dict.fromkeys(new_ids)), songs, sort_type)
            child.body = _body53(sort_type, jump_table(order, songs, sort_type, known))


def _known_letters(body: bytes, old_order: list[int]) -> dict[int, int]:
    """The letter each existing song has in an existing jump table (so it can be kept)."""
    count = struct.unpack_from("<I", body, 4)[0]
    letters: dict[int, int] = {}
    for n in range(count):
        offset = 16 + n * 12
        if offset + 12 > len(body):
            break
        letter, _, start, size = struct.unpack_from("<HHII", body, offset)
        for position in range(start, min(start + size, len(old_order))):
            letters[old_order[position]] = letter
    return letters


def jump_tables_match(master: Chunk, ids: list[int], songs: dict[int, SongView]) -> dict[int, bool]:
    """Check (changes nothing): do our jump tables reproduce the existing ones for this order?"""
    orders = {}
    for child in master.children:
        if child.tag == b"mhod" and child.mhod_type == 52 and len(child.body) >= 48:
            sort_type, positions = _positions(child.body)
            orders[sort_type] = [ids[p] for p in positions if p < len(ids)]
    result = {}
    for child in master.children:
        if child.tag == b"mhod" and child.mhod_type == 53 and len(child.body) >= 16:
            sort_type = struct.unpack_from("<I", child.body, 0)[0]
            if sort_type in orders and all(i in songs for i in orders[sort_type]):
                result[sort_type] = child.body == _body53(sort_type, jump_table(orders[sort_type], songs, sort_type))
    return result
