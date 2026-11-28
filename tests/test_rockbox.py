"""Rockbox mode: songs as files in folders, playlists as .m3u8, only Synclassic's own files removed."""

import pathlib

from PIL import Image

from synclassic import tagging
from synclassic.ipod import rockbox
from synclassic.ipod.device import find_ipods, ipod_at
from tests.test_ipod_artwork import png
from tests.test_ipod_read import fake_ipod, load
from tests.test_ipod_sync import library_with, song


def rockbox_ipod(tmp_path, apple_too=False):
    if apple_too:
        root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    else:
        root = tmp_path / "iPod"
        root.mkdir()
    (root / ".rockbox").mkdir()
    (root / "Music" / "Mine").mkdir(parents=True)
    (root / "Music" / "Mine" / "my song.flac").write_bytes(b"f" * 10)  # yours: never touched
    return ipod_at(root)


def test_detected_and_synced_for_rockbox(tmp_path, monkeypatch):
    ipod = rockbox_ipod(tmp_path)
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))
    assert [i.root for i in find_ipods()] == [ipod.root] and ipod.is_rockbox
    assert rockbox.mode(ipod) == rockbox.ROCKBOX  # Rockbox only

    library = library_with(tmp_path, {"Late Night": [song(1), song(2)], "Also": [song(2)]})
    for item in library.tracks_in_playlists():
        tagging.tag_file(pathlib.Path(item.file_path), item.track, png((0, 90, 200)))
    plan = rockbox.make_plan(library, ipod)
    assert len(plan.copy) == 2 and len(plan.changed_playlists) == 2 and plan.only_adds
    rockbox.apply(library, plan, lambda s: None)

    album = ipod.root / "Music" / "Synclassic Artist" / "New Album"
    assert sorted(p.name for p in album.iterdir()) == ["01 New Song 1.m4a", "02 New Song 2.m4a", "cover.jpg"]
    with Image.open(album / "cover.jpg") as cover:
        assert cover.format == "JPEG" and not cover.info.get("progressive") and max(cover.size) <= 500
    playlist = (ipod.root / "Playlists" / "Late Night.m3u8").read_text().splitlines()
    assert playlist[0] == "#EXTM3U" and playlist[2] == "/Music/Synclassic Artist/New Album/01 New Song 1.m4a"
    assert playlist[4] == "/Music/Synclassic Artist/New Album/02 New Song 2.m4a"
    assert not rockbox.make_plan(library, ipod).has_changes

    library.remove_playlist(next(p for p in library.playlists() if p.name == "Late Night"))
    plan = rockbox.make_plan(library, ipod)
    assert plan.remove_files == ["Music/Synclassic Artist/New Album/01 New Song 1.m4a"]
    assert plan.remove_playlists == ["Playlists/Late Night.m3u8"] and not plan.only_adds
    rockbox.apply(library, plan, lambda s: None)
    assert not (ipod.root / "Playlists" / "Late Night.m3u8").exists()
    assert (album / "02 New Song 2.m4a").is_file() and not (album / "01 New Song 1.m4a").exists()
    assert (ipod.root / "Music" / "Mine" / "my song.flac").is_file()

    library.remove_playlist(library.playlists()[0])
    rockbox.apply(library, rockbox.make_plan(library, ipod), lambda s: None)
    assert not (ipod.root / "Music" / "Synclassic Artist").exists()  # emptied folders go, cover and all


def test_dual_boot_ipod_is_synced_for_apple_unless_chosen(tmp_path):
    ipod = rockbox_ipod(tmp_path, apple_too=True)
    assert rockbox.mode(ipod) == rockbox.APPLE
    rockbox.set_mode(ipod, rockbox.ROCKBOX)
    assert rockbox.mode(ipod_at(ipod.root)) == rockbox.ROCKBOX


def test_names_are_safe_on_fat32():
    from types import SimpleNamespace

    from synclassic.models import Track

    item = SimpleNamespace(track=Track('What? "Now": <Live>...', ["AC/DC"], album="Back in Black.", track_number=3))
    path = rockbox.song_path(item, ".m4a")
    assert not any(ch in path.split("/", 1)[1] for ch in '?":<>\\*|') and not path.split("/")[2].endswith(".")
