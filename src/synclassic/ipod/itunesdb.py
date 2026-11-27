"""Read and write the iPod's music database (iPod_Control/iTunes/iTunesDB).

The file is a tree of "chunks". Every chunk starts with a 4-letter tag and its header length:

    mhbd  the database
      mhsd  a section (1 = songs, 2 = playlists, 3 = podcast playlists, 4+ = newer extras)
        mhlt  song list        -> mhit (one song)      -> mhod (a text field: title, artist...)
        mhlp  playlist list    -> mhyp (one playlist)  -> mhod (name...), mhip (one entry) -> mhod
        mhla  album list (newer iTunes; old iPods ignore it)

Design rule: Synclassic keeps every byte it reads. Chunks it doesn't change are written back
exactly as they were, so an unchanged database round-trips byte-for-byte. Only the length and
count fields of chunks whose children changed are recalculated. This protects data Synclassic
doesn't understand (play counts, ratings, smart playlists, fields from other tools).

Format knowledge: the iPodLinux wiki "ITunesDB" page, libgpod, and iOpenPod's field
definitions (GPL-3.0-or-later), checked against real databases.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Chunks whose third header word is a child count rather than the chunk's total length.
LIST_TAGS = {b"mhlt", b"mhlp", b"mhla", b"mhli", b"mhlf"}
# Chunks whose body is data, not more chunks.
LEAF_TAGS = {b"mhod"}


class DatabaseError(ValueError):
    pass


@dataclass
class Chunk:
    tag: bytes
    header: bytearray  # the full header, kept verbatim
    children: list["Chunk"] = field(default_factory=list)
    body: bytes = b""  # leaf chunks (mhod): everything after the header
    trailing: bytes = b""  # bytes after the last child inside this chunk (rare padding)
    after: bytes = b""  # root only: bytes after the end of the database

    # --- header access ----------------------------------------------------------------------

    def u8(self, offset: int) -> int:
        return self.header[offset] if offset < len(self.header) else 0

    def u16(self, offset: int) -> int:
        return struct.unpack_from("<H", self.header, offset)[0] if offset + 2 <= len(self.header) else 0

    def u32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.header, offset)[0] if offset + 4 <= len(self.header) else 0

    def u64(self, offset: int) -> int:
        return struct.unpack_from("<Q", self.header, offset)[0] if offset + 8 <= len(self.header) else 0

    def set_u8(self, offset: int, value: int) -> None:
        self.header[offset] = value

    def set_u16(self, offset: int, value: int) -> None:
        struct.pack_into("<H", self.header, offset, value)

    def set_u32(self, offset: int, value: int) -> None:
        struct.pack_into("<I", self.header, offset, value)

    def set_u64(self, offset: int, value: int) -> None:
        struct.pack_into("<Q", self.header, offset, value)

    # --- tree helpers -----------------------------------------------------------------------

    def find(self, tag: bytes) -> list["Chunk"]:
        return [c for c in self.children if c.tag == tag]

    @property
    def mhod_type(self) -> int:
        return self.u32(0x0C) if self.tag == b"mhod" else -1


# --- parsing -------------------------------------------------------------------------------


def parse(data: bytes) -> Chunk:
    if data[:4] != b"mhbd":
        raise DatabaseError("This isn't an iTunesDB file (it doesn't start with 'mhbd').")
    chunk, end = _parse_chunk(data, 0)
    chunk.after = bytes(data[end:])
    return chunk


def _parse_chunk(data: bytes, offset: int) -> tuple[Chunk, int]:
    if offset + 12 > len(data):
        raise DatabaseError(f"Database ends unexpectedly at byte {offset}.")
    tag = bytes(data[offset : offset + 4])
    header_len, third = struct.unpack_from("<II", data, offset + 4)
    if header_len < 12 or offset + header_len > len(data):
        raise DatabaseError(f"Damaged chunk '{tag.decode(errors='replace')}' at byte {offset}.")
    header = bytearray(data[offset : offset + header_len])
    chunk = Chunk(tag, header)

    if tag in LIST_TAGS:
        position = offset + header_len
        for _ in range(third):
            child, position = _parse_chunk(data, position)
            chunk.children.append(child)
        return chunk, position

    total = third
    end = offset + total
    if total < header_len or end > len(data):
        raise DatabaseError(f"Damaged chunk '{tag.decode(errors='replace')}' at byte {offset}.")
    if tag in LEAF_TAGS or not tag.startswith(b"mh"):
        chunk.body = bytes(data[offset + header_len : end])
        return chunk, end

    position = offset + header_len
    while position + 12 <= end and data[position : position + 2] == b"mh":
        child, position = _parse_chunk(data, position)
        chunk.children.append(child)
    if position < end:
        chunk.trailing = bytes(data[position:end])
    elif position > end:
        raise DatabaseError(f"Chunk '{tag.decode(errors='replace')}' at byte {offset} overruns its length.")
    return chunk, end


# --- writing -------------------------------------------------------------------------------


def serialize(chunk: Chunk) -> bytes:
    parts: list[bytes] = []
    _write(chunk, parts)
    parts.append(chunk.after)
    return b"".join(parts)


def _write(chunk: Chunk, out: list[bytes]) -> int:
    header = chunk.header
    struct.pack_into("<I", header, 4, len(header))
    if chunk.tag in LIST_TAGS:
        struct.pack_into("<I", header, 8, len(chunk.children))
        out.append(bytes(header))
        size = len(header)
        for child in chunk.children:
            size += _write(child, out)
        return size
    index = len(out)
    out.append(b"")  # the header goes here once the total size is known
    size = len(header)
    if chunk.tag in LEAF_TAGS or not chunk.tag.startswith(b"mh"):
        out.append(chunk.body)
        size += len(chunk.body)
    else:
        for child in chunk.children:
            size += _write(child, out)
        _update_child_counts(chunk)
    out.append(chunk.trailing)
    size += len(chunk.trailing)
    struct.pack_into("<I", header, 8, size)
    out[index] = bytes(header)
    return size


def _update_child_counts(chunk: Chunk) -> None:
    """Child-count fields that must match the children (only rewritten when they changed)."""
    if chunk.tag == b"mhbd":
        chunk.set_u32(0x14, len(chunk.children))
    elif chunk.tag == b"mhit" or chunk.tag == b"mhip":
        chunk.set_u32(0x0C, len(chunk.find(b"mhod")))
    elif chunk.tag == b"mhyp":
        chunk.set_u32(0x0C, len(chunk.find(b"mhod")))
        chunk.set_u32(0x10, len(chunk.find(b"mhip")))


# --- text fields (mhod) --------------------------------------------------------------------

TITLE, LOCATION, ALBUM, ARTIST, GENRE, FILETYPE, COMMENT = 1, 2, 3, 4, 5, 6, 8
COMPOSER, ALBUM_ARTIST = 12, 22
_URL_TYPES = {15, 16}
_STRING_TYPES = set(range(1, 15)) | set(range(18, 32)) | set(range(200, 205))


def mhod_text(chunk: Chunk) -> str | None:
    kind = chunk.mhod_type
    body = chunk.body
    if kind in _URL_TYPES:
        return body.decode("utf-8", errors="replace")
    if kind not in _STRING_TYPES or len(body) < 16:
        return None
    encoding, length = struct.unpack_from("<II", body, 0)
    raw = body[16 : 16 + length]
    if encoding == 2:
        return raw.decode("utf-8", errors="replace")
    return raw.decode("utf-16-le", errors="replace")


def make_string_mhod(kind: int, text: str) -> Chunk:
    """A new text field, encoded the way iTunes does (UTF-16LE)."""
    encoded = text.encode("utf-16-le")
    header = bytearray(24)
    header[0:4] = b"mhod"
    struct.pack_into("<III", header, 4, 24, 24 + 16 + len(encoded), kind)
    body = struct.pack("<IIII", 1, len(encoded), 1, 0) + encoded
    return Chunk(b"mhod", header, body=body)


ENCLOSURE_URL, RSS_URL, DESCRIPTION, CATEGORY = 15, 16, 14, 9


def make_url_mhod(kind: int, url: str) -> Chunk:
    """Podcast URL fields (types 15/16): plain UTF-8 right after the header, as iTunes writes them."""
    body = url.encode("utf-8")
    header = bytearray(24)
    header[0:4] = b"mhod"
    struct.pack_into("<III", header, 4, 24, 24 + len(body), kind)
    return Chunk(b"mhod", header, body=body)


def set_text(owner: Chunk, kind: int, text: str | None) -> None:
    """Set (or remove, with None/"") one text field of a song or playlist."""
    owner.children = [c for c in owner.children if not (c.tag == b"mhod" and c.mhod_type == kind)]
    if text:
        # Text fields come before any playlist entries (mhip).
        position = next((i for i, c in enumerate(owner.children) if c.tag != b"mhod"), len(owner.children))
        owner.children.insert(position, make_string_mhod(kind, text))


def texts(owner: Chunk) -> dict[int, str]:
    result = {}
    for child in owner.children:
        if child.tag == b"mhod":
            value = mhod_text(child)
            if value is not None:
                result[child.mhod_type] = value
    return result


# --- a friendly view of the database --------------------------------------------------------

# Song (mhit) header offsets.
MHIT_ID, MHIT_FILETYPE, MHIT_RATING, MHIT_SIZE, MHIT_LENGTH = 0x10, 0x18, 0x1F, 0x24, 0x28
MHIT_TRACK_NUMBER, MHIT_TOTAL_TRACKS, MHIT_YEAR, MHIT_BITRATE, MHIT_SAMPLE_RATE = 0x2C, 0x30, 0x34, 0x38, 0x3C
MHIT_PLAY_COUNT, MHIT_DISC_NUMBER, MHIT_TOTAL_DISCS, MHIT_DATE_ADDED, MHIT_DBID = 0x50, 0x5C, 0x60, 0x68, 0x70
MHIT_MEDIA_TYPE = 0xD0


@dataclass
class SongView:
    chunk: Chunk
    id: int
    dbid: int
    title: str
    artist: str
    album: str
    album_artist: str
    genre: str
    location: str  # iPod path like ":iPod_Control:Music:F12:ABCD.m4a"
    length_ms: int
    size: int
    track_number: int
    year: int
    play_count: int
    rating: int  # 0-100 (stars x 20)
    media_type: int

    @property
    def relative_path(self) -> str:
        """Path inside the iPod volume, e.g. iPod_Control/Music/F12/ABCD.m4a."""
        return self.location.replace(":", "/").lstrip("/")


@dataclass
class PlaylistView:
    chunk: Chunk
    name: str
    is_master: bool
    is_podcast_section: bool
    track_ids: list[int]
    is_smart: bool
    is_podcasts: bool = False  # the iPod's Podcasts playlist


def section(db: Chunk, kind: int) -> Chunk | None:
    for child in db.find(b"mhsd"):
        if child.u32(0x0C) == kind:
            return child
    return None


def songs(db: Chunk) -> list[SongView]:
    tracks = section(db, 1)
    if not tracks or not tracks.children:
        return []
    result = []
    for mhit in tracks.children[0].find(b"mhit"):
        text = texts(mhit)
        result.append(
            SongView(
                chunk=mhit,
                id=mhit.u32(MHIT_ID),
                dbid=mhit.u64(MHIT_DBID),
                title=text.get(TITLE, ""),
                artist=text.get(ARTIST, ""),
                album=text.get(ALBUM, ""),
                album_artist=text.get(ALBUM_ARTIST, ""),
                genre=text.get(GENRE, ""),
                location=text.get(LOCATION, ""),
                length_ms=mhit.u32(MHIT_LENGTH),
                size=mhit.u32(MHIT_SIZE),
                track_number=mhit.u32(MHIT_TRACK_NUMBER),
                year=mhit.u32(MHIT_YEAR),
                play_count=mhit.u32(MHIT_PLAY_COUNT),
                rating=mhit.u8(MHIT_RATING),
                media_type=mhit.u32(MHIT_MEDIA_TYPE),
            )
        )
    return result


def playlists(db: Chunk, kind: int = 2) -> list[PlaylistView]:
    part = section(db, kind)
    if not part or not part.children:
        return []
    result = []
    for mhyp in part.children[0].find(b"mhyp"):
        text = texts(mhyp)
        smart = any(c.tag == b"mhod" and c.mhod_type in (50, 51) for c in mhyp.children)
        result.append(
            PlaylistView(
                chunk=mhyp,
                name=text.get(TITLE, ""),
                is_master=mhyp.u8(0x14) == 1,
                is_podcast_section=kind == 3,
                track_ids=[mhip.u32(0x18) for mhip in mhyp.find(b"mhip") if mhip.u16(0x10) != 0x100],  # not show headers
                is_smart=smart,
                is_podcasts=len(mhyp.header) > 0x2B and bool(mhyp.u16(0x2A) & 0x01),
            )
        )
    return result
