"""Album art on the iPod: iPod_Control/Artwork/ArtworkDB plus the F<format>_<n>.ithmb image files.

    mhfd  the artwork database
      mhsd 1 -> mhli -> mhii (one per song: links to the song's dbid)
                          -> mhod 2 -> mhni (one per size: which .ithmb file, offset, width, height)
                                         -> mhod 3 (the file name, e.g. ":F1029_1.ithmb")
      mhsd 2 -> mhla  (photo albums; unused for music)
      mhsd 3 -> mhlf -> mhif (one per image size the iPod uses: format id + bytes per image)

The .ithmb files are raw pixels (RGB565, little-endian on the 5G/5.5G: 100x100 as format
1028, 200x200 as format 1029), one image after another. Synclassic only ever *appends* to
them, so restoring a backed-up ArtworkDB always leaves the files consistent.

Like the music database, every byte that's read is kept: an unchanged ArtworkDB is rebuilt
byte-for-byte, and new entries are modeled on existing ones from the same database.
Format knowledge: libgpod and iOpenPod's ArtworkDB research (GPL-3.0-or-later).
"""

from __future__ import annotations

import copy
import hashlib
import io
import os
import pathlib
import struct
from dataclasses import dataclass

from synclassic.ipod.itunesdb import Chunk, DatabaseError

LIST_TAGS = {b"mhli", b"mhla", b"mhlf"}
MAX_ITHMB_BYTES = 500 * 1024 * 1024  # start a new .ithmb file after this

# Image sizes of iPods with color screens, by format id: (width, height).
KNOWN_FORMATS = {
    1028: (100, 100), 1029: (200, 200),  # 5G / 5.5G iPod (Video)
    1027: (100, 100), 1031: (42, 42),  # nano 1G/2G
    1055: (128, 128), 1061: (56, 56), 1068: (128, 128),  # Classic / nano 3G
}


# --- parsing / writing (same rules as iTunesDB, but mhod 2/5 contain an mhni) ------------------


def _is_container(tag: bytes, header: bytes, data: bytes, body_start: int) -> bool:
    if tag == b"mhod":
        return data[body_start : body_start + 4] == b"mhni"
    return tag.startswith(b"mh")


def parse(data: bytes) -> Chunk:
    if data[:4] != b"mhfd":
        raise DatabaseError("This isn't an ArtworkDB file (it doesn't start with 'mhfd').")
    chunk, end = _parse_chunk(data, 0)
    chunk.after = bytes(data[end:])
    return chunk


def _parse_chunk(data: bytes, offset: int) -> tuple[Chunk, int]:
    if offset + 12 > len(data):
        raise DatabaseError(f"ArtworkDB ends unexpectedly at byte {offset}.")
    tag = bytes(data[offset : offset + 4])
    header_len, third = struct.unpack_from("<II", data, offset + 4)
    if header_len < 12 or offset + header_len > len(data):
        raise DatabaseError(f"Damaged artwork chunk '{tag.decode(errors='replace')}' at byte {offset}.")
    chunk = Chunk(tag, bytearray(data[offset : offset + header_len]))
    if tag in LIST_TAGS:
        position = offset + header_len
        for _ in range(third):
            child, position = _parse_chunk(data, position)
            chunk.children.append(child)
        return chunk, position
    end = offset + third
    if third < header_len or end > len(data):
        raise DatabaseError(f"Damaged artwork chunk '{tag.decode(errors='replace')}' at byte {offset}.")
    body_start = offset + header_len
    if not _is_container(tag, chunk.header, data, body_start):
        chunk.body = bytes(data[body_start:end])
        return chunk, end
    position = body_start
    while position + 12 <= end and data[position : position + 2] == b"mh":
        child, position = _parse_chunk(data, position)
        chunk.children.append(child)
    if position < end:
        chunk.trailing = bytes(data[position:end])
    elif position > end:
        raise DatabaseError(f"Artwork chunk '{tag.decode(errors='replace')}' at byte {offset} overruns its length.")
    return chunk, end


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
        return len(header) + sum(_write(child, out) for child in chunk.children)
    index = len(out)
    out.append(b"")
    size = len(header)
    if chunk.children:
        for child in chunk.children:
            size += _write(child, out)
        if chunk.tag == b"mhfd":
            chunk.set_u32(0x14, len(chunk.children))
        elif chunk.tag in (b"mhii", b"mhni", b"mhif"):
            chunk.set_u32(0x0C, len(chunk.children))
    else:
        out.append(chunk.body)
        size += len(chunk.body)
    out.append(chunk.trailing)
    size += len(chunk.trailing)
    struct.pack_into("<I", header, 8, size)
    out[index] = bytes(header)
    return size


# --- reading ---------------------------------------------------------------------------------


def section(db: Chunk, kind: int) -> Chunk | None:
    for child in db.find(b"mhsd"):
        if child.u16(0x0C) == kind:
            return child
    return None


def images(db: Chunk) -> list[Chunk]:
    part = section(db, 1)
    return part.children[0].find(b"mhii") if part and part.children else []


def formats(db: Chunk) -> dict[int, int]:
    """Image formats the iPod uses: format id -> bytes per image."""
    part = section(db, 3)
    if not part or not part.children:
        return {}
    return {mhif.u32(0x10): mhif.u32(0x14) for mhif in part.children[0].find(b"mhif")}


def _string_mhod_text(mhod: Chunk) -> str:
    body = mhod.body
    if len(body) < 12:
        return ""
    length, encoding = struct.unpack_from("<IB", body, 0)
    raw = body[12 : 12 + length]
    return raw.decode("utf-16-le" if encoding == 2 else "utf-8", errors="replace")


def _string_mhod(kind: int, text: str) -> Chunk:
    """An ArtworkDB string field (UTF-16LE, padded to 4 bytes, as iTunes writes file names)."""
    raw = text.encode("utf-16-le")
    padding = (-len(raw)) % 4
    header = bytearray(24)
    header[0:4] = b"mhod"
    struct.pack_into("<IIH", header, 4, 24, 0, kind)
    header[0x0F] = padding
    body = struct.pack("<IB3xI", len(raw), 2, 0) + raw + bytes(padding)
    return Chunk(b"mhod", header, body=body)


@dataclass
class SizeSpec:
    format_id: int
    width: int
    height: int
    image_size: int  # bytes per image in the .ithmb file


def size_specs(db: Chunk) -> list[SizeSpec]:
    """The image sizes this iPod uses, in the order existing songs list them."""
    specs: dict[int, SizeSpec] = {}
    for mhii in images(db):
        for mhod in mhii.children:
            for mhni in mhod.find(b"mhni"):
                fid = mhni.u32(0x10)
                if fid not in specs:
                    specs[fid] = SizeSpec(fid, mhni.u16(0x22), mhni.u16(0x20), mhni.u32(0x18))
        if specs:
            break
    for fid, size in formats(db).items():
        if fid not in specs and fid in KNOWN_FORMATS:
            width, height = KNOWN_FORMATS[fid]
            specs[fid] = SizeSpec(fid, width, height, size)
    return [s for s in specs.values() if s.width and s.height and s.image_size == s.width * s.height * 2]


# --- images -----------------------------------------------------------------------------------


def to_rgb565(cover: bytes, width: int, height: int) -> bytes:
    """A cover image (JPEG/PNG) -> raw little-endian RGB565 pixels, cropped to fill the square."""
    from PIL import Image, ImageOps

    from PIL import ImageChops

    with Image.open(io.BytesIO(cover)) as source:
        image = ImageOps.fit(source.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    # RGB565 = rrrrrggg gggbbbbb; stored little-endian: low byte first. Built per channel by
    # Pillow (the bit groups don't overlap, so adding them never overflows).
    r, g, b = image.split()
    high = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    low = ImageChops.add(g.point(lambda v: ((v >> 2) & 0x07) << 5), b.point(lambda v: v >> 3))
    return Image.merge("LA", (low, high)).tobytes()


def from_rgb565(data: bytes, width: int, height: int):
    """Raw little-endian RGB565 pixels -> a PIL image (Pillow decodes this layout natively)."""
    from PIL import Image

    return Image.frombytes("RGB", (width, height), bytes(data), "raw", "BGR;16")


def embedded_cover(path: pathlib.Path) -> bytes | None:
    """The cover image stored inside a song file (M4A covr, MP3 APIC, FLAC/Ogg pictures)."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
    except Exception:
        return None
    pictures = getattr(audio, "pictures", None)  # FLAC
    if pictures:
        front = [p for p in pictures if p.type == 3]
        return bytes((front or pictures)[0].data)
    tags = getattr(audio, "tags", None)
    if tags and hasattr(tags, "get") and tags.get("metadata_block_picture"):  # Ogg Vorbis / Opus
        import base64

        from mutagen.flac import Picture

        try:
            return bytes(Picture(base64.b64decode(tags["metadata_block_picture"][0])).data)
        except Exception:
            return None
    if not tags:
        return None
    covers = tags.get("covr") if hasattr(tags, "get") else None
    if covers:
        return bytes(covers[0])
    for key in list(tags.keys()):
        if str(key).startswith("APIC"):
            return tags[key].data
    return None


# --- writing new artwork -----------------------------------------------------------------------


class ArtworkWriter:
    """Adds covers for songs. Call add() per song, then commit()."""

    def __init__(self, artwork_dir: pathlib.Path):
        self.dir = artwork_dir
        self.path = artwork_dir / "ArtworkDB"
        self.raw = self.path.read_bytes()
        self.db = parse(self.raw)
        if serialize(self.db) != self.raw:
            raise DatabaseError("The artwork database can't be rebuilt exactly, so it won't be changed.")
        self.specs = size_specs(self.db)
        self.template = next((m for m in images(self.db) if m.find(b"mhod")), None)
        self.next_id = max([self.db.u32(0x1C)] + [m.u32(0x10) + 1 for m in images(self.db)])
        self.pending: dict[int, bytearray] = {}  # format id -> bytes to append
        self.file_for: dict[int, tuple[str, int]] = {}  # format id -> (file name, start size)
        self.shared: dict[bytes, int] = {}  # cover hash -> mhii whose pixels can be reused
        self.offsets: dict[tuple[bytes, int], int] = {}  # (cover hash, format) -> offset
        self.added: list[Chunk] = []

    @property
    def usable(self) -> bool:
        return bool(self.specs) and section(self.db, 1) is not None

    def _file(self, spec: SizeSpec) -> tuple[str, int]:
        if spec.format_id not in self.file_for:
            number = 1
            while (self.dir / f"F{spec.format_id}_{number + 1}.ithmb").exists():
                number += 1
            path = self.dir / f"F{spec.format_id}_{number}.ithmb"
            size = path.stat().st_size if path.exists() else 0
            if size >= MAX_ITHMB_BYTES:
                number += 1
                size = 0
            self.file_for[spec.format_id] = (f"F{spec.format_id}_{number}.ithmb", size)
        return self.file_for[spec.format_id]

    def add(self, dbid: int, cover: bytes) -> int:
        """Add a cover for the song with this dbid. Returns the new image id."""
        digest = hashlib.sha1(cover).digest()
        image_id = self.next_id
        self.next_id += 1
        if self.template is not None:
            mhii = Chunk(b"mhii", bytearray(len(self.template.header)))
        else:
            mhii = Chunk(b"mhii", bytearray(152))
        mhii.header[0:4] = b"mhii"
        mhii.set_u32(0x10, image_id)
        mhii.set_u64(0x14, dbid)
        mhii.set_u32(0x30, len(cover))
        for spec in self.specs:
            name, start = self._file(spec)
            key = (digest, spec.format_id)
            if key not in self.offsets:
                buffer = self.pending.setdefault(spec.format_id, bytearray())
                self.offsets[key] = start + len(buffer)
                buffer += to_rgb565(cover, spec.width, spec.height)
            mhii.children.append(self._thumbnail(spec, name, self.offsets[key]))
        self.added.append(mhii)
        return image_id

    def _thumbnail(self, spec: SizeSpec, file_name: str, offset: int) -> Chunk:
        """mhod type 2 -> mhni (location) -> mhod type 3 (file name), modeled on an existing one."""
        model = None
        if self.template is not None:
            for mhod in self.template.find(b"mhod"):
                for mhni in mhod.find(b"mhni"):
                    if mhni.u32(0x10) == spec.format_id:
                        model = (mhod, mhni)
        if model:
            container = Chunk(b"mhod", bytearray(model[0].header))
            mhni = Chunk(b"mhni", bytearray(len(model[1].header)))
            mhni.header[0:4] = b"mhni"
        else:
            container = Chunk(b"mhod", bytearray(24))
            container.header[0:4] = b"mhod"
            struct.pack_into("<H", container.header, 0x0C, 2)
            mhni = Chunk(b"mhni", bytearray(76))
            mhni.header[0:4] = b"mhni"
        mhni.set_u32(0x10, spec.format_id)
        mhni.set_u32(0x14, offset)
        mhni.set_u32(0x18, spec.image_size)
        mhni.set_u16(0x20, spec.height)
        mhni.set_u16(0x22, spec.width)
        if len(mhni.header) >= 0x2C and model and model[1].u32(0x28) == model[1].u32(0x18):
            mhni.set_u32(0x28, spec.image_size)
        mhni.children = [_string_mhod(3, ":" + file_name)]
        container.children = [mhni]
        return container

    def remove_for(self, dbids: set[int]) -> int:
        """Drop images of songs that were removed (their pixels just become unused)."""
        part = section(self.db, 1)
        if not part or not part.children or not dbids:
            return 0
        before = len(part.children[0].children)
        part.children[0].children = [m for m in part.children[0].children if m.u64(0x14) not in dbids]
        return before - len(part.children[0].children)

    def commit(self) -> None:
        """Append pixels to the .ithmb files, then write the new ArtworkDB atomically."""
        part = section(self.db, 1)
        if part is None or not part.children:
            raise DatabaseError("The artwork database has no image list.")
        part.children[0].children.extend(self.added)
        self.db.set_u32(0x1C, self.next_id)
        self._ensure_formats()
        written: list[tuple[pathlib.Path, int]] = []
        try:
            for format_id, data in self.pending.items():
                name, start = self.file_for[format_id]
                path = self.dir / name
                with open(path, "ab") as handle:
                    if handle.tell() != start:
                        raise DatabaseError(f"{name} changed while syncing.")
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                written.append((path, start))
            data = serialize(self.db)
            if serialize(parse(data)) != data:
                raise DatabaseError("The new artwork database didn't verify.")
            tmp = self.path.with_name("ArtworkDB.synclassic-new")
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            for path, start in written:  # undo the appends
                with open(path, "r+b") as handle:
                    handle.truncate(start)
            raise

    def _ensure_formats(self) -> None:
        part = section(self.db, 3)
        if part is None or not part.children:
            return
        listed = {mhif.u32(0x10) for mhif in part.children[0].find(b"mhif")}
        template = next(iter(part.children[0].find(b"mhif")), None)
        for spec in self.specs:
            if spec.format_id in listed:
                continue
            mhif = Chunk(b"mhif", bytearray(template.header) if template else bytearray(124))
            mhif.header[0:4] = b"mhif"
            mhif.set_u32(0x10, spec.format_id)
            mhif.set_u32(0x14, spec.image_size)
            part.children[0].children.append(copy.deepcopy(mhif))


def link_song(mhit: Chunk, image_id: int, cover_size: int) -> None:
    """Tell the music database that a song has artwork."""
    length = len(mhit.header)
    if length >= 0x84:
        mhit.set_u16(0x7C, 1)  # artwork count
        mhit.set_u32(0x80, cover_size)
    if length >= 0xA5:
        mhit.set_u8(0xA4, 1)  # has artwork
    if length >= 0x164:
        mhit.set_u32(0x160, image_id)
