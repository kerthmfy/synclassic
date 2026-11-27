"""Change an iPod database: add/remove songs, create/fill/remove playlists.

New entries are modeled on entries that already exist in the same database (same header sizes
and constant fields), so they match what this iPod's firmware already reads. Built-in
defaults (from libgpod/iOpenPod) are only used for an empty database.

Playlists live in section 2 and, on 5G+ iPods, again in section 3 (the podcast-aware copy
the firmware actually reads); both copies share the playlist id and are kept identical.
"""

from __future__ import annotations

import copy
import random
import struct
import time
from dataclasses import dataclass

from synclassic.ipod import itunesdb, library_index
from synclassic.ipod.itunesdb import Chunk

MAC_EPOCH_OFFSET = 2082844800  # seconds between 1904-01-01 and 1970-01-01


def mac_time(unix: float | None = None) -> int:
    """iPod timestamps: seconds since 1904, in local time."""
    unix = time.time() if unix is None else unix
    return int(unix + MAC_EPOCH_OFFSET + time.localtime(unix).tm_gmtoff) & 0xFFFFFFFF


FILETYPES = {
    "m4a": (0x4D344120, "AAC audio file", 0x33),  # "M4A "
    "mp3": (0x4D503320, "MPEG audio file", 0x0C),  # "MP3 "
    "wav": (0x57415620, "WAV audio file", 0),
    "aif": (0x41494646, "AIFF audio file", 0),
}

# Song header fields that are the same for every song (constants this iPod's database uses);
# copied from an existing song. Everything else in a new song header starts at zero.
_MHIT_CONSTANTS = [(0x7E, 2), (0x168, 4), (0x197, 1), (0x1EC, 4), (0x20C, 4), (0x229, 1), (0x22B, 1)]
_MHIT_DEFAULTS = {0x7E: (2, 0xFFFF), 0x168: (4, 1), 0x197: (1, 1), 0x1EC: (4, 1), 0x20C: (4, 2)}
DEFAULT_MHIT_LENGTH = 0x184
DEFAULT_MHYP_LENGTH = 0x6C
DEFAULT_MHIP_LENGTH = 0x4C


@dataclass
class NewSong:
    location: str  # ":iPod_Control:Music:F12:ABCD.m4a"
    filetype: str  # m4a / mp3 / wav / aif
    size: int
    length_ms: int
    bitrate: int  # kbps
    sample_rate: int
    vbr: bool
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    genre: str = ""
    track_number: int = 0
    disc_number: int = 0
    year: int = 0
    explicit: bool = False
    # Podcast episodes (shown in the iPod's Podcasts menu):
    podcast: bool = False
    video: bool = False
    enclosure_url: str = ""
    rss_url: str = ""
    description: str = ""
    category: str = ""
    released_unix: int = 0
    soundcheck: int = 0  # the iPod's Sound Check value (0 = unknown)
    lyrics: bool = False  # the file has lyrics inside


PODCAST_PLAYLIST_FLAG = 0x01  # mhyp +0x2A bit 0: the Podcasts playlist
GROUP_HEADER = 0x100  # mhip +0x10 (u16): a show's header in the grouped (section 3) Podcasts playlist


def _blank(tag: bytes, length: int) -> bytearray:
    header = bytearray(length)
    header[0:4] = tag
    struct.pack_into("<I", header, 4, length)
    return header


def _string_mhod_like(kind: int, text: str) -> Chunk:
    return itunesdb.make_string_mhod(kind, text)


class Editor:
    def __init__(self, db: Chunk):
        self.db = db
        self.songs_list = self._song_list()
        songs = itunesdb.songs(db)
        self.next_id = max((s.id for s in songs), default=0) + 1
        self.used_dbids = {s.dbid for s in songs}
        self.used_playlist_ids = {
            mhyp.u64(0x1C) for kind in self._playlist_sections() for mhyp in self._playlist_list(kind).find(b"mhyp")
        }
        self.next_group_id = 1 + max(
            (mhip.u32(0x14) for kind in self._playlist_sections() for mhyp in self._playlist_list(kind).find(b"mhyp")
             for mhip in mhyp.find(b"mhip")),
            default=0,
        )
        # Master playlist order before any change, per section (for updating the menu indexes).
        self.original_master_ids = {kind: self._ids(self.master(kind)) for kind in self._playlist_sections()}
        self.changed_ids: set[int] = set()  # songs whose names changed (re-sorted in the menu indexes)
        self._templates()

    # --- structure ----------------------------------------------------------------------------

    def _song_list(self) -> Chunk:
        part = itunesdb.section(self.db, 1)
        if part is None or not part.children:
            raise itunesdb.DatabaseError("The database has no song list.")
        return part.children[0]

    def _playlist_sections(self) -> list[int]:
        return [kind for kind in (2, 3) if itunesdb.section(self.db, kind) is not None]

    def _playlist_list(self, kind: int) -> Chunk:
        return itunesdb.section(self.db, kind).children[0]  # type: ignore[union-attr]

    def master(self, kind: int) -> Chunk:
        for mhyp in self._playlist_list(kind).find(b"mhyp"):
            if mhyp.u8(0x14) == 1:
                return mhyp
        raise itunesdb.DatabaseError("The database has no main (master) playlist.")

    @staticmethod
    def _ids(mhyp: Chunk) -> list[int]:
        return [mhip.u32(0x18) for mhip in mhyp.find(b"mhip")]

    def playlists(self, playlist_id: int) -> list[Chunk]:
        """Every copy (section 2 and 3) of one playlist."""
        return [
            mhyp for kind in self._playlist_sections() for mhyp in self._playlist_list(kind).find(b"mhyp")
            if mhyp.u64(0x1C) == playlist_id and mhyp.u8(0x14) != 1
        ]

    # --- templates ----------------------------------------------------------------------------

    def _templates(self) -> None:
        audio = [s.chunk for s in itunesdb.songs(self.db) if s.media_type in (0, 1)]
        self.mhit_template = max(audio, key=lambda c: len(c.header)) if audio else None
        self.mhip_template = None
        self.mhyp_template = None
        if 2 in self._playlist_sections():
            for mhyp in self._playlist_list(2).find(b"mhyp"):
                smart = any(c.tag == b"mhod" and c.mhod_type in (50, 51) for c in mhyp.children)
                if self.mhip_template is None and mhyp.find(b"mhip"):
                    self.mhip_template = mhyp.find(b"mhip")[0]
                if self.mhyp_template is None and mhyp.u8(0x14) != 1 and not smart and not mhyp.u16(0x2A):
                    self.mhyp_template = mhyp

    def _unique64(self, used: set[int]) -> int:
        while True:
            value = random.getrandbits(64)
            if value and value not in used:
                used.add(value)
                return value

    # --- songs --------------------------------------------------------------------------------

    def add_song(self, song: NewSong) -> tuple[int, int]:
        """Add a song to the song list and the master playlist(s). Returns (id, dbid)."""
        template = self.mhit_template
        length = len(template.header) if template is not None else DEFAULT_MHIT_LENGTH
        header = _blank(b"mhit", length)
        for offset, size in _MHIT_CONSTANTS:
            if offset + size <= length:
                if template is not None:
                    header[offset : offset + size] = template.header[offset : offset + size]
                elif offset in _MHIT_DEFAULTS:
                    header[offset : offset + size] = _MHIT_DEFAULTS[offset][1].to_bytes(size, "little")
        mhit = Chunk(b"mhit", header)
        song_id = self.next_id
        self.next_id += 1
        dbid = self._unique64(self.used_dbids)
        now = mac_time()
        code, type_text, audio_type = FILETYPES.get(song.filetype, FILETYPES["m4a"])
        is_mp3 = song.filetype == "mp3"
        mhit.set_u32(0x10, song_id)
        mhit.set_u32(0x14, 1)  # a file on the iPod
        mhit.set_u32(0x18, code)
        mhit.set_u8(0x1C, 1 if (is_mp3 and song.vbr) else 0)
        mhit.set_u8(0x1D, 1 if is_mp3 else 0)
        mhit.set_u32(0x20, now)
        mhit.set_u32(0x24, song.size)
        mhit.set_u32(0x28, song.length_ms)
        mhit.set_u32(0x2C, song.track_number)
        mhit.set_u32(0x34, song.year)
        mhit.set_u32(0x38, song.bitrate)
        mhit.set_u32(0x3C, (song.sample_rate & 0xFFFF) << 16)
        mhit.set_u32(0x4C, song.soundcheck)
        mhit.set_u32(0x5C, song.disc_number)
        mhit.set_u32(0x68, now)
        mhit.set_u64(0x70, dbid)
        if length >= 0x8C:
            struct.pack_into("<f", mhit.header, 0x88, float(song.sample_rate))
        if length >= 0x94:
            mhit.set_u16(0x90, audio_type)
            mhit.set_u8(0x92, 1 if song.explicit else 0)
        if length >= 0xB0:
            mhit.set_u8(0xA4, 2)  # no artwork (yet)
            if template is None or template.u64(0xA8) == template.u64(0x70):
                mhit.set_u64(0xA8, dbid)
        if length > 0xB0 and song.lyrics:
            mhit.set_u8(0xB0, 1)  # the iPod shows the lyrics inside the file
        if length >= 0xD4:
            mhit.set_u32(0xD0, (0x06 if song.video else 0x04) if song.podcast else 1)  # podcast / audio
        if song.podcast:
            if length >= 0xA8:
                mhit.set_u8(0xA5, 1)  # skip when shuffling
                mhit.set_u8(0xA6, 1)  # remember the playback position
                mhit.set_u8(0xA7, 1)  # podcast "now playing" screen
            if length >= 0xB3:
                mhit.set_u8(0xB2, 2)  # unplayed: the blue dot
            if length >= 0x90 and song.released_unix:
                mhit.set_u32(0x8C, (song.released_unix + MAC_EPOCH_OFFSET) & 0xFFFFFFFF)
        if length >= 0x130 and template is not None and template.u32(0x12C) == template.u32(0x24):
            mhit.set_u32(0x12C, song.size)
        if length >= 0x12C and template is not None and template.u64(0x124) == template.u64(0x70):
            mhit.set_u64(0x124, dbid)

        for kind, text in (
            (itunesdb.TITLE, song.title), (itunesdb.LOCATION, song.location), (itunesdb.ALBUM, song.album),
            (itunesdb.ARTIST, song.artist), (itunesdb.GENRE, song.genre), (itunesdb.FILETYPE, type_text),
            (itunesdb.ALBUM_ARTIST, song.album_artist),
        ):
            if text:
                mhit.children.append(_string_mhod_like(kind, text))
        if song.podcast:
            if song.description:
                mhit.children.append(_string_mhod_like(itunesdb.DESCRIPTION, song.description[:500]))
            if song.category:
                mhit.children.append(_string_mhod_like(itunesdb.CATEGORY, song.category))
            if song.enclosure_url:
                mhit.children.append(itunesdb.make_url_mhod(itunesdb.ENCLOSURE_URL, song.enclosure_url))
            if song.rss_url:
                mhit.children.append(itunesdb.make_url_mhod(itunesdb.RSS_URL, song.rss_url))
        self.songs_list.children.append(mhit)
        for kind in self._playlist_sections():
            master = self.master(kind)
            master.children.append(self._make_mhip(song_id, dbid, len(master.find(b"mhip"))))
        return song_id, dbid

    def remove_songs(self, song_ids: set[int]) -> None:
        """Remove songs from the song list and from every playlist."""
        if not song_ids:
            return
        self.songs_list.children = [c for c in self.songs_list.children if c.u32(0x10) not in song_ids]
        for kind in self._playlist_sections() + [k for k in (5,) if itunesdb.section(self.db, k) is not None]:
            part = itunesdb.section(self.db, kind)
            if not part or not part.children:
                continue
            for mhyp in part.children[0].find(b"mhyp"):
                before = len(mhyp.children)
                mhyp.children = [c for c in mhyp.children if not (c.tag == b"mhip" and c.u32(0x18) in song_ids)]
                if len(mhyp.children) != before:
                    if len(mhyp.header) > 0x2B and mhyp.u16(0x2A) & PODCAST_PLAYLIST_FLAG:
                        self._drop_empty_groups(mhyp)
                    self._renumber(mhyp)

    # Editable song fields: text fields by text-field type, numbers by header offset.
    TEXT_FIELDS = {"title": itunesdb.TITLE, "artist": itunesdb.ARTIST, "album": itunesdb.ALBUM,
                   "album_artist": itunesdb.ALBUM_ARTIST, "genre": itunesdb.GENRE, "composer": itunesdb.COMPOSER}
    # iTunes' "sort as" versions of those names; dropped when the name itself changes.
    SORT_FIELDS = {"title": 27, "artist": 23, "album": 28, "album_artist": 29, "composer": 30}
    NUMBER_FIELDS = {"year": 0x34, "track_number": 0x2C, "disc_number": 0x5C}

    def update_song(self, song_id: int, changes: dict[str, str | int]) -> None:
        """Change a song's info: title/artist/album/album_artist/genre/composer, year/track_number/disc_number."""
        mhit = next((c for c in self.songs_list.children if c.tag == b"mhit" and c.u32(0x10) == song_id), None)
        if mhit is None:
            raise itunesdb.DatabaseError("That song is no longer on the iPod.")
        for name, value in changes.items():
            if name in self.TEXT_FIELDS:
                text = str(value).strip()
                if itunesdb.texts(mhit).get(self.TEXT_FIELDS[name], "") == text:
                    continue
                itunesdb.set_text(mhit, self.TEXT_FIELDS[name], text or None)
                if name in self.SORT_FIELDS:
                    itunesdb.set_text(mhit, self.SORT_FIELDS[name], None)
                self.changed_ids.add(song_id)
            elif name in self.NUMBER_FIELDS:
                number = max(0, int(value or 0))
                if mhit.u32(self.NUMBER_FIELDS[name]) != number:
                    mhit.set_u32(self.NUMBER_FIELDS[name], number)
                    self.changed_ids.add(song_id)
            else:
                raise ValueError(f"Unknown song field: {name}")

    # --- playlists ----------------------------------------------------------------------------

    def _make_mhip(self, song_id: int, dbid: int, position: int) -> Chunk:
        template = self.mhip_template
        if template is not None:
            mhip = Chunk(b"mhip", bytearray(len(template.header)))
            mhip.header[0:4] = b"mhip"
            mhip.children = [copy.deepcopy(c) for c in template.children if c.tag == b"mhod" and c.mhod_type == 100]
        else:
            mhip = Chunk(b"mhip", _blank(b"mhip", DEFAULT_MHIP_LENGTH))
            position_mhod = Chunk(b"mhod", bytearray(24), body=bytes(20))
            position_mhod.header[0:4] = b"mhod"
            struct.pack_into("<III", position_mhod.header, 4, 24, 44, 100)
            mhip.children = [position_mhod]
        mhip.set_u32(0x14, self.next_group_id)
        self.next_group_id += 1
        mhip.set_u32(0x18, song_id)
        mhip.set_u32(0x1C, mac_time())
        if len(mhip.header) >= 0x34 and (template is None or template.u64(0x2C)):
            mhip.set_u64(0x2C, dbid)
        for child in mhip.children:
            if child.tag == b"mhod" and child.mhod_type == 100 and len(child.body) >= 4:
                child.body = struct.pack("<I", position) + child.body[4:]
        return mhip

    def _renumber(self, mhyp: Chunk) -> None:
        entries = [m for m in mhyp.find(b"mhip") if m.u16(0x10) != GROUP_HEADER]
        for position, mhip in enumerate(entries):
            for child in mhip.children:
                if child.tag == b"mhod" and child.mhod_type == 100 and len(child.body) >= 4:
                    child.body = struct.pack("<I", position) + child.body[4:]

    # --- the Podcasts playlist -------------------------------------------------------------------

    def podcast_playlists(self) -> dict[int, Chunk]:
        """The Podcasts playlist in each playlist section (section -> mhyp)."""
        found = {}
        for kind in self._playlist_sections():
            for mhyp in self._playlist_list(kind).find(b"mhyp"):
                if mhyp.u8(0x14) != 1 and len(mhyp.header) > 0x2B and mhyp.u16(0x2A) & PODCAST_PLAYLIST_FLAG:
                    found[kind] = mhyp
                    break
        return found

    def ensure_podcast_playlist(self) -> dict[int, Chunk]:
        found = self.podcast_playlists()
        if len(found) == len(self._playlist_sections()):
            return found
        playlist_id = next((m.u64(0x1C) for m in found.values()), None)
        if playlist_id is None:
            playlist_id = self.create_playlist("Podcasts")
            for mhyp in self.playlists(playlist_id):
                mhyp.set_u16(0x2A, PODCAST_PLAYLIST_FLAG)
        else:  # missing from one section: copy it there
            source = next(iter(found.values()))
            for kind in self._playlist_sections():
                if kind not in found:
                    copy_ = copy.deepcopy(source)
                    copy_.children = [c for c in copy_.children if c.tag != b"mhip"]
                    self._playlist_list(kind).children.append(copy_)
        return self.podcast_playlists()

    def add_podcast_episode(self, song_id: int, dbid: int, show: str) -> None:
        """Put an episode in the Podcasts playlist: flat in section 2, under its show's header in section 3."""
        for kind, mhyp in self.ensure_podcast_playlist().items():
            entries = mhyp.find(b"mhip")
            if kind != 3:  # section 2: a flat list; section 3 (what the iPod shows): grouped by show
                mhyp.children.append(self._make_mhip(song_id, dbid, len(entries)))
                self._renumber(mhyp)
                continue
            header = next((m for m in entries if m.u16(0x10) == GROUP_HEADER
                           and itunesdb.texts(m).get(itunesdb.TITLE, "").casefold() == show.casefold()), None)
            if header is None:
                header = self._make_group_header(show)
                mhyp.children.append(header)
                position = len(mhyp.children)
            else:
                group = header.u32(0x14)
                members = [i for i, c in enumerate(mhyp.children) if c is header or (c.tag == b"mhip" and c.u32(0x20) == group)]
                position = members[-1] + 1
            episode = self._make_mhip(song_id, dbid, 0)
            episode.set_u32(0x20, header.u32(0x14))
            mhyp.children.insert(position, episode)
            self._renumber(mhyp)

    def _make_group_header(self, show: str) -> Chunk:
        template = self.mhip_template
        length = len(template.header) if template is not None else DEFAULT_MHIP_LENGTH
        header = Chunk(b"mhip", _blank(b"mhip", length))
        header.set_u16(0x10, GROUP_HEADER)
        header.set_u32(0x14, self.next_group_id)
        self.next_group_id += 1
        header.set_u32(0x1C, mac_time())
        header.children = [_string_mhod_like(itunesdb.TITLE, show or "Podcast")]
        return header

    def _drop_empty_groups(self, mhyp: Chunk) -> None:
        used = {c.u32(0x20) for c in mhyp.children if c.tag == b"mhip" and c.u16(0x10) != GROUP_HEADER}
        mhyp.children = [c for c in mhyp.children
                         if not (c.tag == b"mhip" and c.u16(0x10) == GROUP_HEADER and c.u32(0x14) not in used)]

    def create_playlist(self, name: str) -> int:
        playlist_id = self._unique64(self.used_playlist_ids)
        now = mac_time()
        for kind in self._playlist_sections():
            template = self.mhyp_template
            if template is not None:
                mhyp = Chunk(b"mhyp", bytearray(template.header))
                mhyp.children = [copy.deepcopy(c) for c in template.children if c.tag == b"mhod" and c.mhod_type == 100]
                same_id_copy = template.u64(0x44) == template.u64(0x1C)
            else:
                mhyp = Chunk(b"mhyp", _blank(b"mhyp", DEFAULT_MHYP_LENGTH))
                same_id_copy = True
            mhyp.set_u8(0x14, 0)
            mhyp.set_u32(0x18, now)
            mhyp.set_u64(0x1C, playlist_id)
            if len(mhyp.header) >= 0x30:
                mhyp.set_u16(0x2A, 0)
                mhyp.set_u32(0x2C, 1)  # manual order
            if len(mhyp.header) >= 0x38:
                mhyp.set_u64(0x30, 0)
            if len(mhyp.header) >= 0x4C and same_id_copy:
                mhyp.set_u64(0x44, playlist_id)
            if len(mhyp.header) >= 0x54:
                mhyp.set_u32(0x50, 0)
            if len(mhyp.header) >= 0x5C and mhyp.u32(0x58):
                mhyp.set_u32(0x58, now)
            itunesdb.set_text(mhyp, itunesdb.TITLE, name)
            playlist_list = self._playlist_list(kind)
            # Keep the podcasts playlist (section 3) last, as iTunes does.
            position = len(playlist_list.children)
            for index, existing in enumerate(playlist_list.children):
                if existing.u16(0x2A) & 1:
                    position = index
                    break
            playlist_list.children.insert(position, mhyp)
        return playlist_id

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        for mhyp in self.playlists(playlist_id):
            if itunesdb.texts(mhyp).get(itunesdb.TITLE) != name:
                itunesdb.set_text(mhyp, itunesdb.TITLE, name)

    def set_playlist_songs(self, playlist_id: int, songs: list[tuple[int, int]]) -> None:
        """Set a playlist's songs, in order. `songs` = [(id, dbid)]. Unchanged entries are kept as-is."""
        for mhyp in self.playlists(playlist_id):
            existing = mhyp.find(b"mhip")
            if [m.u32(0x18) for m in existing] == [song_id for song_id, _ in songs]:
                continue
            reuse: dict[int, list[Chunk]] = {}
            for mhip in existing:
                reuse.setdefault(mhip.u32(0x18), []).append(mhip)
            entries = []
            for position, (song_id, dbid) in enumerate(songs):
                kept = reuse.get(song_id)
                entries.append(kept.pop(0) if kept else self._make_mhip(song_id, dbid, position))
            mhyp.children = [c for c in mhyp.children if c.tag != b"mhip"] + entries
            self._renumber(mhyp)

    def delete_playlist(self, playlist_id: int) -> None:
        for kind in self._playlist_sections():
            playlist_list = self._playlist_list(kind)
            playlist_list.children = [
                c for c in playlist_list.children if not (c.u64(0x1C) == playlist_id and c.u8(0x14) != 1)
            ]

    # --- finishing ----------------------------------------------------------------------------

    def finish(self) -> None:
        """Update the menu indexes in the master playlist(s)."""
        songs = {s.id: s for s in itunesdb.songs(self.db)}
        for kind in self._playlist_sections():
            master = self.master(kind)
            library_index.update(master, self.original_master_ids[kind], self._ids(master), songs, self.changed_ids)
