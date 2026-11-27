"""Fix album art: find missing and low-quality covers on the iPod and put sharp ones on."""

import io
import random
from types import SimpleNamespace

from PIL import Image  # noqa: F401

from synclassic import config, tagging
from synclassic.artwork import image_size
from synclassic.ipod import art_fix, artwork, itunesdb, sync
from synclassic.ipod.check import check
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from synclassic.update import apply_source
from tests.conftest import make_audio
from tests.test_ipod_artwork import ipod, png, thumbnails  # noqa: F401

SPOTIFY_SMALL = "https://i.scdn.co/image/ab67616d00004851" + "a" * 24
SPOTIFY_BIG = "https://i.scdn.co/image/ab67616d0000b273" + "a" * 24


def photo(size: int, seed: int = 1) -> bytes:
    """A detailed picture (random blocks), as JPEG."""
    rng = random.Random(seed)
    image = Image.new("RGB", (size, size))
    step = max(1, size // 40)
    for x in range(0, size, step):
        for y in range(0, size, step):
            image.paste((rng.randrange(256), rng.randrange(256), rng.randrange(256)), (x, y, x + step, y + step))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=92)
    return out.getvalue()


class Web:
    """Deezer, Apple and the image servers, simulated."""

    def __init__(self):
        self.images = {SPOTIFY_BIG: photo(640, 2), "https://cdn.deezer/album2.jpg": photo(1000, 3),
                       "https://apple/1000x1000bb.jpg": photo(1000, 4)}
        self.asked = []

    def get(self, url, params):
        self.asked.append((url, params))
        if url == art_fix.DEEZER_ALBUMS and "Album 2" in params["q"]:
            return SimpleNamespace(status_code=200, json=lambda: {"data": [
                {"title": "Album 2 (Deluxe Edition)", "artist": {"name": "Painter"}, "cover_xl": "https://cdn.deezer/album2.jpg"}]})
        if url == art_fix.ITUNES_SEARCH and "Album 3" in params["term"]:
            return SimpleNamespace(status_code=200, json=lambda: {"results": [
                {"collectionName": "Album 3", "artistName": "Painter", "artworkUrl100": "https://apple/100x100bb.jpg"}]})
        return SimpleNamespace(status_code=200, json=lambda: {"data": [], "results": []})

    def fetch(self, url):
        return self.images.get(url)


def small_cover_library(tmp_path):
    """Three albums synced with 64 px covers (like the old pixelated ones)."""
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Art", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "6" * 22))
    tracks = [Track(f"Song {n}", ["Painter"], album=f"Album {n}", duration_ms=2000, spotify_id=f"{n:022d}",
                    cover_url=SPOTIFY_SMALL if n == 1 else None) for n in (1, 2, 3)]
    apply_source(library, playlist, SourcePlaylist("Art", tracks))
    for item in library.playlist_tracks(playlist):
        path = tmp_path / f"{item.track.title}.m4a"
        make_audio(path, seconds=2)
        tagging.tag_file(path, item.track, photo(64))
        library.update_track(item.id, status="downloaded", file_path=str(path))
    sync.apply_plan(library, sync.make_plan(library, ipod_of(tmp_path)), lambda s: None)
    return library


def ipod_of(tmp_path):
    from synclassic.ipod.device import ipod_at

    return ipod_at(tmp_path / "iPod")


def test_scan_finds_low_and_missing_covers(tmp_path, ipod):
    library = small_cover_library(tmp_path)
    albums = {a.title: a for a in art_fix.scan(ipod, library)}
    for n in (1, 2, 3):
        album = albums[f"Album {n}"]
        assert (album.reason, album.current_px) == ("low", 64) and album.problem == "Low quality (64×64)"
        assert len(album.library_files) == 1
    assert albums["Album 1"].cover_urls == [SPOTIFY_SMALL]
    first = itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes()))[0]
    reasons = {a.reason for a in albums.values() if first.dbid not in {d for _, d, _ in a.songs}}
    assert "missing" in reasons  # the iPod's other albums have no art
    in_first = [a for a in albums.values() if first.dbid in {d for _, d, _ in a.songs}]
    assert in_first == []  # it has art, but nothing to measure it by: only checked on request
    everything = {a.title: a for a in art_fix.scan(ipod, library, everything=True)}
    first_album = next(a for a in everything.values() if first.dbid in {d for _, d, _ in a.songs})
    assert first_album.reason == "unknown" and first_album.problem == "Size unknown"


def test_covers_are_found_and_put_on(tmp_path, ipod):
    library = small_cover_library(tmp_path)
    albums = [a for a in art_fix.scan(ipod, library) if a.title.startswith("Album ")]
    web = Web()
    art_fix.find_covers(albums, art_fix.CoverFinder(web.get, web.fetch, sleep=lambda s: None))
    found = {a.title: (a.source, a.new_size) for a in albums}
    assert found == {"Album 1": ("Spotify", (640, 640)), "Album 2": ("Deezer", (1000, 1000)),
                     "Album 3": ("Apple Music", (1000, 1000))}

    db = itunesdb.parse(ipod.database_path.read_bytes())
    songs = {s.title: s for s in itunesdb.songs(db)}
    old_id, _ = thumbnails(ipod, songs["Song 2"].dbid)
    assert art_fix.apply(ipod, albums) == 3
    db = itunesdb.parse(ipod.database_path.read_bytes())
    songs = {s.title: s for s in itunesdb.songs(db)}
    new_id, thumbs = thumbnails(ipod, songs["Song 2"].dbid)
    assert new_id != old_id and songs["Song 2"].chunk.u32(0x160) == new_id
    art_db = artwork.parse((ipod.control / "Artwork" / "ArtworkDB").read_bytes())
    assert sum(1 for m in artwork.images(art_db) if m.u64(0x14) == songs["Song 2"].dbid) == 1  # old one gone
    assert check(ipod).safe_to_manage
    # The Mac's copy has the sharp cover too, so later syncs keep it.
    item = next(i for i in library.tracks_in_playlists() if i.track.title == "Song 2")
    assert image_size(artwork.embedded_cover(__import__("pathlib").Path(item.file_path))) == (1000, 1000)
    # Nothing left to fix for those albums.
    assert not [a for a in art_fix.scan(ipod, library) if a.title.startswith("Album ")]


def test_only_clearly_better_covers_are_offered(tmp_path, ipod):
    album = art_fix.AlbumArt("Painter", "Album 2", songs=[(1, 1, "Song 2")], reason="low", current_px=64)
    web = Web()
    web.images["https://cdn.deezer/album2.jpg"] = photo(250)  # bigger, but still not sharp
    art_fix.CoverFinder(web.get, web.fetch, sleep=lambda s: None).find(album)
    assert album.new_cover is None and album.found == "No better cover found"
    unknown = art_fix.AlbumArt("Painter", "Album 2", songs=[(1, 1, "Song 2")], reason="unknown")
    web.images["https://cdn.deezer/album2.jpg"] = photo(500)  # fine, but not clearly better than "unknown"
    art_fix.CoverFinder(web.get, web.fetch, sleep=lambda s: None).find(unknown)
    assert unknown.new_cover is None


def test_apple_stops_being_asked_when_it_says_slow_down():
    calls = []

    def get(url, params):
        calls.append(url)
        return SimpleNamespace(status_code=429, json=lambda: {})

    finder = art_fix.CoverFinder(get, lambda url: None, sleep=lambda s: None)
    for n in range(3):
        finder.find(art_fix.AlbumArt("A", f"B{n}", songs=[(1, 1, "T")]))
    assert calls.count(art_fix.ITUNES_SEARCH) == 1


def test_songs_only_on_this_mac_get_sharp_covers_too(tmp_path):
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Mac", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "7" * 22))
    apply_source(library, playlist, SourcePlaylist("Mac", [
        Track("Song 2", ["Painter"], album="Album 2", duration_ms=2000, spotify_id="2" * 22),
        Track("Fine", ["Painter"], album="Sharp", duration_ms=2000, spotify_id="3" * 22)]))
    for item, cover in zip(library.playlist_tracks(playlist), (photo(64), photo(600))):
        path = tmp_path / f"{item.track.title}.m4a"
        make_audio(path, seconds=1)
        tagging.tag_file(path, item.track, cover)
        library.update_track(item.id, status="downloaded", file_path=str(path))
    albums = art_fix.scan_library(library)
    assert [(a.title, a.reason, a.where) for a in albums] == [("Album 2", "low", "Mac")]
    web = Web()
    art_fix.find_covers(albums, art_fix.CoverFinder(web.get, web.fetch, sleep=lambda s: None))
    assert art_fix.apply(None, albums) == 1
    assert art_fix.scan_library(library) == []
