import base64
import struct

from synclassic import artwork
from synclassic.sources.full_reader import parse_time, rows_to_tracks
from synclassic.sources.spotify_links import SpotifyRef
from synclassic.sources.spotify_page import parse_page
from tests.conftest import FIXTURES

HASH = "c50b7d96737041f4bfd844d6"


def jpeg(width, height):
    """A minimal JPEG header with a size (enough for image_size)."""
    sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, height, width) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def png(width, height):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


def test_spotify_thumbnails_are_upgraded_to_640():
    for size in ("4851", "1e02", "b273", "82c1"):
        url = f"https://i.scdn.co/image/ab67616d0000{size}{HASH}"
        assert artwork.upgrade_cover_url(url) == f"https://i.scdn.co/image/ab67616d0000b273{HASH}"
    cdn = f"https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e02{HASH}"
    assert artwork.upgrade_cover_url(cdn) == f"https://i.scdn.co/image/ab67616d0000b273{HASH}"
    # Playlist mosaics and other images are left alone.
    other = "https://i.scdn.co/image/ab67706f00000002ffe80c69d2e795ea01e63447"
    assert artwork.upgrade_cover_url(other) == other


def test_youtube_music_art_is_upgraded():
    url = "https://lh3.googleusercontent.com/abc=w120-h120-l90-rj"
    assert artwork.upgrade_cover_url(url) == "https://lh3.googleusercontent.com/abc=w1000-h1000-l90-rj"


def test_image_size():
    assert artwork.image_size(jpeg(640, 640)) == (640, 640)
    assert artwork.image_size(png(300, 200)) == (300, 200)
    assert artwork.image_size(b"not an image") is None


def test_best_cover_prefers_a_sharp_image():
    served = {
        f"https://i.scdn.co/image/ab67616d0000b273{HASH}": jpeg(640, 640),
        "https://example.com/small.jpg": jpeg(64, 64),
    }
    fetched = []

    def fetch(url):
        fetched.append(url)
        return served.get(url)

    small = f"https://i.scdn.co/image/ab67616d00004851{HASH}"
    assert artwork.image_size(artwork.best_cover([small], fetch=fetch)) == (640, 640)
    assert fetched == [f"https://i.scdn.co/image/ab67616d0000b273{HASH}"]  # the 64 px one was never downloaded

    # Only small images available: use the biggest instead of nothing.
    assert artwork.image_size(artwork.best_cover(["https://example.com/small.jpg"], fetch=fetch)) == (64, 64)


def test_best_cover_is_lazy():
    def candidates():
        yield f"https://i.scdn.co/image/ab67616d0000b273{HASH}"
        raise AssertionError("should not look further once a sharp cover is found")

    assert artwork.best_cover(candidates(), fetch=lambda url: jpeg(640, 640))


def test_page_gives_total_and_album_names():
    page = (FIXTURES / "page_playlist.html").read_text(encoding="utf-8")
    info = parse_page(page, SpotifyRef("playlist", "37i9dQZF1DX4o1oenSJRJd"))
    assert info.name == "All Out 2000s" and info.total == 150
    first = info.tracks[0]
    assert (first.title, first.artists, first.album) == ("Oops!...I Did It Again", ["Britney Spears"], "Oops!... I Did It Again")
    assert first.spotify_id == "6naxalmIoLFWR0siv8dnQQ" and first.duration_ms == 211160
    assert first.cover_url.startswith("https://i.scdn.co/image/ab67616d0000b273")


def test_full_reader_rows_become_tracks_in_order():
    rows = {
        "3": {"id": "3850dYVgOFIXJh5U4BFEWH", "title": "Hung Up", "artists": ["Madonna"], "album": "Confessions on a Dance Floor",
              "time": "5:37", "explicit": False, "image": f"https://i.scdn.co/image/ab67616d00004851{HASH}"},
        "2": {"id": "6naxalmIoLFWR0siv8dnQQ", "title": "Oops!...I Did It Again", "artists": ["Britney Spears"],
              "album": "Oops!... I Did It Again", "time": "3:31", "explicit": True, "image": ""},
        "10": {"id": "", "title": "broken row"},
    }
    tracks = rows_to_tracks(rows)
    assert [t.title for t in tracks] == ["Oops!...I Did It Again", "Hung Up"]
    assert tracks[0].duration_ms == 211000 and tracks[0].explicit is True and tracks[0].cover_url is None
    assert tracks[1].cover_url == f"https://i.scdn.co/image/ab67616d0000b273{HASH}"  # not the 64 px thumbnail
    assert parse_time("1:02:03") == 3723000 and parse_time("") is None
