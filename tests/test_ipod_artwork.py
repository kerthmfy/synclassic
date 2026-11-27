"""Album art on the iPod."""

import base64
import io
import pathlib

import pytest
from PIL import Image

from synclassic import config, tagging
from synclassic.ipod import artwork, itunesdb, sync
from synclassic.ipod.device import ipod_at
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from synclassic.update import apply_source
from tests.conftest import make_audio
from tests.test_ipod_read import fake_ipod, load

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ipod"


def art_fixture(name: str) -> bytes:
    return base64.b64decode((FIXTURES / f"artworkdb-{name}.b64").read_text())


def png(color, size=(600, 600)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["original-empty", "original-with-unknown-data", "nano6-original-1x"])
def test_artwork_databases_round_trip_byte_for_byte(name):
    raw = art_fixture(name)
    assert artwork.serialize(artwork.parse(raw)) == raw


def test_rgb565_colors():
    pixels = artwork.to_rgb565(png((255, 0, 0)), 4, 4)
    assert pixels[:2] == b"\x00\xf8" and len(pixels) == 32  # red = 0xF800, little-endian
    back = artwork.from_rgb565(artwork.to_rgb565(png((30, 144, 255)), 8, 8), 8, 8)
    r, g, b = back.getpixel((3, 3))
    assert abs(r - 30) < 9 and abs(g - 144) < 5 and abs(b - 255) < 9


def video_ipod_artwork(root: pathlib.Path, existing_dbid: int) -> None:
    """An ArtworkDB like a 5G/5.5G iPod's (100x100 + 200x200), with one cover for an existing song."""
    db = artwork.parse(art_fixture("nano6-original-1x"))
    mhii = artwork.images(db)[0]
    mhii.set_u64(0x14, existing_dbid)
    containers = mhii.find(b"mhod")[:2]
    mhii.children = containers
    for container, (fid, size) in zip(containers, [(1028, 100), (1029, 200)]):
        mhni = container.find(b"mhni")[0]
        mhni.set_u32(0x10, fid)
        mhni.set_u32(0x14, 0)
        mhni.set_u32(0x18, size * size * 2)
        mhni.set_u16(0x20, size)
        mhni.set_u16(0x22, size)
        if len(mhni.header) >= 0x2C and mhni.u32(0x28):
            mhni.set_u32(0x28, size * size * 2)
        mhni.children = [artwork._string_mhod(3, f":F{fid}_1.ithmb")]
    formats = artwork.section(db, 3).children[0]
    formats.children = formats.children[:2]
    for mhif, (fid, size) in zip(formats.children, [(1028, 100), (1029, 200)]):
        mhif.set_u32(0x10, fid)
        mhif.set_u32(0x14, size * size * 2)
    folder = root / "iPod_Control" / "Artwork"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ArtworkDB").write_bytes(artwork.serialize(db))
    (folder / "F1028_1.ithmb").write_bytes(artwork.to_rgb565(png((0, 255, 0)), 100, 100))
    (folder / "F1029_1.ithmb").write_bytes(artwork.to_rgb565(png((0, 255, 0)), 200, 200))


@pytest.fixture
def ipod(tmp_path):
    raw = load("captured-album-index-36.b64")
    root = fake_ipod(tmp_path, raw)
    first = itunesdb.songs(itunesdb.parse(raw))[0]
    video_ipod_artwork(root, first.dbid)
    return ipod_at(root)


def library_with_covers(tmp_path, covers: dict[int, tuple]):
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Art", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "5" * 22))
    tracks = [Track(f"Covered {n}", ["Painter"], album=f"Album {n}", duration_ms=2000, spotify_id=f"{n:022d}") for n in covers]
    apply_source(library, playlist, SourcePlaylist("Art", tracks))
    for item, (n, color) in zip(library.playlist_tracks(playlist), covers.items()):
        path = tmp_path / f"cover{n}.m4a"
        make_audio(path, seconds=2)
        tagging.tag_file(path, item.track, png(color) if color else None)
        library.update_track(item.id, status="downloaded", file_path=str(path))
    return library


def thumbnails(ipod, dbid):
    db = artwork.parse((ipod.control / "Artwork" / "ArtworkDB").read_bytes())
    for mhii in artwork.images(db):
        if mhii.u64(0x14) == dbid:
            return mhii.u32(0x10), [
                (n.u32(0x10), n.u32(0x14), n.u16(0x22), n.u16(0x20), artwork._string_mhod_text(n.children[0]))
                for c in mhii.children for n in c.find(b"mhni")
            ]
    return None


def test_sync_adds_album_art(tmp_path, ipod):
    art_dir = ipod.control / "Artwork"
    sizes_before = {f: (art_dir / f).stat().st_size for f in ("F1028_1.ithmb", "F1029_1.ithmb")}
    # Songs 1 and 2 share a cover (same album art), song 3 has its own, song 4 has none.
    library = library_with_covers(tmp_path, {1: (255, 0, 0), 2: (255, 0, 0), 3: (0, 0, 255), 4: None})
    plan = sync.make_plan(library, ipod)
    assert plan.artwork_ok and "Add album art" in "\n".join(plan.summary())
    sync.apply_plan(library, plan, lambda s: None)

    db = itunesdb.parse(ipod.database_path.read_bytes())
    songs = {s.title: s for s in itunesdb.songs(db)}
    red_id, red_thumbs = thumbnails(ipod, songs["Covered 1"].dbid)
    _, red2_thumbs = thumbnails(ipod, songs["Covered 2"].dbid)
    _, blue_thumbs = thumbnails(ipod, songs["Covered 3"].dbid)
    assert thumbnails(ipod, songs["Covered 4"].dbid) is None
    assert [(t[0], t[2], t[3], t[4]) for t in red_thumbs] == [(1028, 100, 100, ":F1028_1.ithmb"), (1029, 200, 200, ":F1029_1.ithmb")]
    assert [t[1] for t in red_thumbs] == [t[1] for t in red2_thumbs]  # same pixels reused
    assert [t[1] for t in blue_thumbs] != [t[1] for t in red_thumbs]
    # Two distinct covers were appended to each file; existing pixels untouched.
    assert (art_dir / "F1028_1.ithmb").stat().st_size == sizes_before["F1028_1.ithmb"] + 2 * 20000
    assert (art_dir / "F1029_1.ithmb").stat().st_size == sizes_before["F1029_1.ithmb"] + 2 * 80000
    # The pixels at the recorded offset are the cover.
    offset = blue_thumbs[1][1]
    data = (art_dir / "F1029_1.ithmb").read_bytes()[offset : offset + 80000]
    assert artwork.from_rgb565(data, 200, 200).getpixel((100, 100))[2] > 240
    # The music database says these songs have art and points at the image.
    covered = songs["Covered 1"].chunk
    assert covered.u8(0xA4) == 1 and covered.u16(0x7C) == 1 and covered.u32(0x160) == red_id
    assert songs["Covered 4"].chunk.u8(0xA4) == 2
    # The existing song's cover is still there.
    first = itunesdb.songs(itunesdb.parse(load("captured-album-index-36.b64")))[0]
    assert thumbnails(ipod, first.dbid) is not None


def test_songs_synced_before_art_support_get_art_later(tmp_path, ipod):
    art_dir = ipod.control / "Artwork"
    saved = (art_dir / "ArtworkDB").read_bytes()
    (art_dir / "ArtworkDB").unlink()  # like the first sync, before album art was supported
    library = library_with_covers(tmp_path, {1: (255, 0, 0)})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    song = next(s for s in itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes())) if s.title == "Covered 1")
    assert song.chunk.u8(0xA4) == 2

    (art_dir / "ArtworkDB").write_bytes(saved)
    plan = sync.make_plan(library, ipod)
    assert plan.has_changes and len(plan.art_backfill) == 1 and not plan.copy
    sync.apply_plan(library, plan, lambda s: None)
    song = next(s for s in itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes())) if s.title == "Covered 1")
    assert song.chunk.u8(0xA4) == 1 and thumbnails(ipod, song.dbid) is not None
    assert not sync.make_plan(library, ipod).has_changes


def test_removed_songs_lose_their_art_entry_and_restore_brings_it_back(tmp_path, ipod):
    library = library_with_covers(tmp_path, {1: (255, 0, 0)})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    song = next(s for s in itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes())) if s.title == "Covered 1")
    with_art = (ipod.control / "Artwork" / "ArtworkDB").read_bytes()

    library.remove_playlist(library.playlist_by_name("Art"))
    saved = sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    assert thumbnails(ipod, song.dbid) is None
    sync.restore(ipod, saved)
    assert (ipod.control / "Artwork" / "ArtworkDB").read_bytes() == with_art


def test_unreadable_artwork_database_skips_art_but_still_syncs(tmp_path, ipod):
    (ipod.control / "Artwork" / "ArtworkDB").write_bytes(b"mhfd" + b"\x00" * 20)  # damaged
    library = library_with_covers(tmp_path, {1: (255, 0, 0)})
    plan = sync.make_plan(library, ipod)
    assert not plan.artwork_ok and "can't be added" in "\n".join(plan.summary())
    sync.apply_plan(library, plan, lambda s: None)
    titles = [s.title for s in itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes()))]
    assert "Covered 1" in titles


def test_check_reports_artwork(ipod):
    from synclassic.ipod.check import check, format_report

    text = "\n".join(format_report(check(ipod)))
    assert "Album artwork: 1 images, sizes 100×100, 200×200; rebuilds byte-for-byte ✓" in text
