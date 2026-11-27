"""Read everything on an iPod for browsing: songs, playlists, albums, and where each cover is.

Read-only. Covers are read lazily from the .ithmb files (see `cover`).
"""

from __future__ import annotations

import datetime
import pathlib
from dataclasses import dataclass, field

from synclassic.ipod import artwork, itunesdb
from synclassic.ipod.device import IPod

MAC_EPOCH = datetime.datetime(1904, 1, 1)


def mac_to_datetime(value: int) -> datetime.datetime | None:
    """iPod timestamps are local time, in seconds since 1904."""
    if not value or value == 0xFFFFFFFF:
        return None
    try:
        return MAC_EPOCH + datetime.timedelta(seconds=value)
    except OverflowError:
        return None


@dataclass
class Song:
    id: int
    dbid: int
    title: str
    artist: str
    album: str
    album_artist: str
    genre: str
    length_ms: int
    track_number: int
    disc_number: int
    year: int
    plays: int
    rating: int  # 0-5 stars
    added: datetime.datetime | None
    size: int
    kind: str  # "song", "podcast", "video", "audiobook", "other"
    path: str  # inside the iPod, e.g. iPod_Control/Music/F12/ABCD.m4a

    @property
    def album_key(self) -> tuple[str, str]:
        return ((self.album_artist or self.artist).casefold(), self.album.casefold())


@dataclass
class Playlist:
    id: int
    name: str
    song_ids: list[int]
    smart: bool
    synclassic: bool = False  # made or managed by Synclassic
    podcasts: bool = False  # the iPod's Podcasts playlist


@dataclass
class Album:
    title: str
    artist: str
    song_ids: list[int] = field(default_factory=list)
    year: int = 0
    cover_dbid: int | None = None  # a song whose cover represents the album


@dataclass
class Contents:
    name: str  # the iPod's own name (its main playlist's title)
    songs: dict[int, Song]
    playlists: list[Playlist]
    covers: dict[int, tuple[str, int, int, int]]  # song dbid -> (ithmb file, offset, width, height)

    def albums(self) -> list[Album]:
        groups: dict[tuple[str, str], Album] = {}
        for song in self.songs.values():
            if song.kind != "song":
                continue
            album = groups.setdefault(song.album_key, Album(song.album or "Unknown Album", song.album_artist or song.artist))
            album.song_ids.append(song.id)
            album.year = album.year or song.year
            if album.cover_dbid is None and song.dbid in self.covers:
                album.cover_dbid = song.dbid
        for album in groups.values():
            album.song_ids.sort(key=lambda i: (self.songs[i].disc_number, self.songs[i].track_number, self.songs[i].title.casefold()))
        return sorted(groups.values(), key=lambda a: (a.artist.casefold(), a.title.casefold()))

    def grouped(self, attribute: str) -> dict[str, list[int]]:
        """Song ids by artist / genre (music only)."""
        groups: dict[str, list[int]] = {}
        for song in self.songs.values():
            if song.kind == "song":
                groups.setdefault(getattr(song, attribute) or "Unknown", []).append(song.id)
        return dict(sorted(groups.items(), key=lambda kv: kv[0].casefold()))

    def songs_of_kind(self, kind: str) -> list[int]:
        return [s.id for s in self.songs.values() if s.kind == kind]


def _kind(media_type: int) -> str:
    if media_type in (0, 1):
        return "song"
    if media_type & 0x04:
        return "podcast"
    if media_type & (0x02 | 0x20 | 0x40):
        return "video"
    if media_type & 0x08:
        return "audiobook"
    return "other"


def read(ipod: IPod) -> Contents:
    db = itunesdb.parse(ipod.database_path.read_bytes())
    songs: dict[int, Song] = {}
    for view in itunesdb.songs(db):
        mhit = view.chunk
        songs[view.id] = Song(
            id=view.id, dbid=view.dbid, title=view.title or "(no title)", artist=view.artist, album=view.album,
            album_artist=view.album_artist, genre=view.genre, length_ms=view.length_ms,
            track_number=view.track_number, disc_number=mhit.u32(itunesdb.MHIT_DISC_NUMBER), year=view.year,
            plays=view.play_count, rating=min(5, view.rating // 20), added=mac_to_datetime(mhit.u32(itunesdb.MHIT_DATE_ADDED)),
            size=view.size, kind=_kind(view.media_type), path=view.relative_path,
        )
    name = ipod.name
    playlists: list[Playlist] = []
    from synclassic.ipod.sync import load_state

    managed = {int(v["id"], 16) for v in load_state(ipod)["playlists"].values()}
    for view in itunesdb.playlists(db, 2):
        if view.is_master:
            name = view.name or name
            continue
        pid = view.chunk.u64(0x1C)
        playlists.append(Playlist(pid, view.name, [i for i in view.track_ids if i in songs], view.is_smart, pid in managed,
                                  view.is_podcasts))

    covers: dict[int, tuple[str, int, int, int]] = {}
    art_path = ipod.control / "Artwork" / "ArtworkDB"
    if art_path.is_file():
        try:
            art_db = artwork.parse(art_path.read_bytes())
            for mhii in artwork.images(art_db):
                best = None
                for mhod in mhii.children:
                    for mhni in mhod.find(b"mhni"):
                        size = (mhni.u16(0x22), mhni.u16(0x20))
                        # The largest square thumbnail up to 200 px (sharp in the grid, small to read).
                        if size[0] == size[1] and size[0] <= 200 and (best is None or size[0] > best[3]):
                            file_name = artwork._string_mhod_text(mhni.children[0]).lstrip(":") if mhni.children else ""
                            if file_name and mhni.u32(0x18) == size[0] * size[1] * 2:
                                best = (file_name, mhni.u32(0x14), size[0], size[1])
                if best:
                    covers[mhii.u64(0x14)] = best
        except (OSError, itunesdb.DatabaseError):
            pass
    return Contents(name, songs, playlists, covers)


def cover(ipod: IPod, location: tuple[str, int, int, int]):
    """A cover as a PIL image, read from the iPod's .ithmb file."""
    file_name, offset, width, height = location
    path: pathlib.Path = ipod.control / "Artwork" / file_name.replace(":", "/")
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read(width * height * 2)
    if len(data) != width * height * 2:
        return None
    return artwork.from_rgb565(data, width, height)
