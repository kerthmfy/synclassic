"""Per-iPod choices: which playlists go on it, smaller files, FLAC conversion, several iPods."""

import pathlib
import subprocess

import pytest
from mutagen.mp4 import MP4

from synclassic import cli, config, convert
from synclassic.ipod import itunesdb, prefs, rockbox, sync
from synclassic.ipod.device import ipod_at
from tests.test_ipod_sync import ipod, library_with, playlist_titles, read_db, song  # noqa: F401  (fixture)


def flac(path: pathlib.Path, seconds: float = 2.0) -> pathlib.Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}:sample_rate=96000", "-ac", "2", "-c:a", "flac", str(path)],
                   check=True)
    return path


def use_flac(library, item):
    path = flac(pathlib.Path(item.file_path).with_suffix(".flac"))
    library.update_track(item.id, file_path=str(path), status="local")


def test_only_the_chosen_playlists_go_on(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1), song(2)], "Chill": [song(3)]})
    gym, chill = library.playlist_by_name("Gym"), library.playlist_by_name("Chill")
    prefs.update(ipod, playlists=[sync.playlist_key(chill)])
    plan = sync.make_plan(library, ipod)
    assert [i.track.title for i in plan.copy] == ["New Song 3"] and plan.chosen_only
    assert [p.library.name for p in plan.playlists] == ["Chill"]
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert playlist_titles(db, "Chill") == ["New Song 3"] and playlist_titles(db, "Gym") is None

    prefs.update(ipod, playlists=[sync.playlist_key(gym)])  # swap: Chill comes off, Gym goes on
    plan = sync.make_plan(library, ipod)
    assert [name for _, _, name in plan.remove_playlists] == ["Chill"]
    assert any("didn't choose for this iPod" in line for line in plan.summary())
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert playlist_titles(db, "Gym") == ["New Song 1", "New Song 2"] and playlist_titles(db, "Chill") is None
    assert "New Song 3" not in {s.title for s in itunesdb.songs(db)}

    prefs.update(ipod, playlists=None)
    assert {p.library.name for p in sync.make_plan(library, ipod).playlists} == {"Gym", "Chill"}


def test_flac_becomes_apple_lossless_or_smaller_aac(tmp_path, ipod):
    library = library_with(tmp_path, {"Hi-Fi": [song(1)], "Later": [song(2)]})
    (first,), (second,) = (library.playlist_tracks(library.playlist_by_name(n)) for n in ("Hi-Fi", "Later"))
    use_flac(library, first)
    prefs.update(ipod, playlists=[sync.playlist_key(library.playlist_by_name("Hi-Fi"))])
    plan = sync.make_plan(library, ipod)
    assert plan.convert == {first.key: convert.Target("alac")}
    assert any("1 to Apple Lossless" in line for line in plan.summary())
    sync.apply_plan(library, plan, lambda s: None)
    added = {s.title: s for s in itunesdb.songs(read_db(ipod))}["New Song 1"]
    on_ipod = ipod.root / added.relative_path
    audio = MP4(on_ipod)
    assert on_ipod.suffix == ".m4a" and audio.info.codec == "alac" and audio.info.sample_rate == 44100
    assert audio.tags["\xa9nam"] == ["New Song 1"]
    assert pathlib.Path(library.track(first.id).file_path).suffix == ".flac"  # your own file isn't changed

    # With smaller files chosen for this iPod, a new FLAC song becomes 128 kbps AAC instead.
    prefs.update(ipod, bitrate=128, playlists=None)
    use_flac(library, second)
    plan = sync.make_plan(library, ipod)
    assert plan.convert == {second.key: convert.Target("aac", 128)} and plan.bitrate == 128
    assert first.key in plan.reuse  # songs already on the iPod stay as they are
    sync.apply_plan(library, plan, lambda s: None)
    added = {s.title: s for s in itunesdb.songs(read_db(ipod))}["New Song 2"]
    audio = MP4(ipod.root / added.relative_path)
    assert audio.info.codec.startswith("mp4a") and audio.info.bitrate < 160_000
    assert not list(pathlib.Path("/tmp").glob("synclassic-convert-*/*.m4a"))  # converted copies are deleted


def test_lossless_to_aac_setting(tmp_path):
    config.save_settings(config.Settings(lossless_to="aac"))
    path = flac(tmp_path / "x.flac")
    assert convert.target_for(path, 2000, lossless_to=config.load_settings().lossless_to) == convert.Target("aac", 256)


def test_target_rules(tmp_path):
    lossy = tmp_path / "a.ogg"
    lossy.write_bytes(b"x" * 64_000)  # 256 kbps over 2 s
    big = tmp_path / "b.m4a"
    big.write_bytes(b"x" * 80_000)  # 320 kbps over 2 s
    small = tmp_path / "c.m4a"
    small.write_bytes(b"x" * 32_000)  # 128 kbps
    assert convert.target_for(lossy, 2000) == convert.Target("aac", 256)
    assert convert.target_for(big, 2000) is None
    assert convert.target_for(big, 2000, bitrate=128) == convert.Target("aac", 128)
    assert convert.target_for(small, 2000, bitrate=128) is None
    assert convert.target_for(lossy, 2000, apple=False) is None  # Rockbox plays Ogg
    assert convert.estimated_size(big, 200_000, convert.Target("aac", 128)) < big.stat().st_size * 100 / 2  # 200 s
    command = convert.command(big, tmp_path / "out.m4a", convert.Target("aac", 128), 96000, encoders={"aac_at", "aac"})
    assert command[command.index("-c:a") + 1] == "aac_at" and command[command.index("-ar") + 1] == "44100"


def test_converter_hands_songs_over_in_order_and_cleans_up(tmp_path):
    made = []

    def fake_run(args, **kwargs):
        out = pathlib.Path(args[-1])
        out.write_bytes(b"converted " + args[args.index("-i") + 1].encode())
        made.append(out)
        return subprocess.CompletedProcess(args, 0, "", "")

    sources = []
    for n in range(5):
        path = tmp_path / f"{n}.flac"
        path.write_bytes(b"f")
        sources.append(path)
    from synclassic.models import Track

    jobs = [(str(n), p, convert.Target("alac"), Track(f"T{n}", ["A"])) for n, p in enumerate(sources)]
    with convert.Converter(jobs, workers=2, run=fake_run) as converter:
        first = converter.get("0")
        assert first.read_bytes() == b"converted " + str(sources[0]).encode()
        first.unlink()
        out_dir = converter.out_dir
    assert not out_dir.exists()  # whatever wasn't used is gone


def test_sync_doesnt_fit_says_how_to_make_it_fit(tmp_path, ipod, monkeypatch):
    library = library_with(tmp_path, {"Big": [song(1), song(2)]})
    for item in library.playlist_tracks(library.playlists()[0]):
        path = flac(pathlib.Path(item.file_path).with_suffix(".flac"), seconds=30)
        library.update_track(item.id, file_path=str(path), status="local")
    need = sync.make_plan(library, ipod).bytes_to_copy
    monkeypatch.setattr(type(ipod), "disk_usage", lambda self: (10**9, sync.SPACE_MARGIN + need // 2))
    plan = sync.make_plan(library, ipod)
    assert not plan.fits and 0 < plan.shortfall <= need // 2 + 1
    text = "\n".join(plan.summary())
    assert "too much" in text and "Smaller files (256 kbps) for this iPod would make it fit" in text
    assert "Choose Playlists" in text

    monkeypatch.setattr(type(ipod), "disk_usage", lambda self: (10**9, sync.SPACE_MARGIN))
    assert "wouldn't be enough" in "\n".join(sync.make_plan(library, ipod).fit_hints())


def test_rockbox_uses_the_chosen_playlists(tmp_path):
    from tests.test_rockbox import rockbox_ipod

    device = rockbox_ipod(tmp_path)
    library = library_with(tmp_path, {"One": [song(1)], "Two": [song(2)]})
    two = library.playlist_by_name("Two")
    prefs.update(device, playlists=[sync.playlist_key(two)], bitrate=0)
    plan = rockbox.make_plan(library, device)
    assert [item.track.title for item, _ in plan.copy] == ["New Song 2"] and list(plan.playlists) == [sync.playlist_key(two)]
    rockbox.set_mode(device, rockbox.ROCKBOX)  # keeps the playlist choice
    assert prefs.load(device).playlists == [sync.playlist_key(two)]


def test_cli_chooses_playlists_and_size(tmp_path, ipod, capsys, monkeypatch):
    library = library_with(tmp_path, {"Gym": [song(1)], "Chill": [song(2)], "Road Trip": [song(3)]})
    monkeypatch.setattr(cli, "open_library", lambda: library)
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))
    assert cli.main(["ipod", "playlists", "1", "Road Trip"]) == 0
    out = capsys.readouterr().out
    assert "[x]   1. Chill" in out and "[ ]   2. Gym" in out and "[x]   3. Road Trip" in out
    assert len(prefs.load(ipod).playlists) == 2
    assert cli.main(["ipod", "playlists", "all"]) == 0
    assert prefs.load(ipod).all_playlists
    assert cli.main(["ipod", "size", "128"]) == 0 and prefs.load(ipod).bitrate == 128
    assert cli.main(["ipod", "size", "as-is"]) == 0 and prefs.load(ipod).bitrate == 0
    assert cli.main(["ipod", "size", "100"]) == 1


def test_several_ipods_pick_by_name(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from tests.test_ipod_read import fake_ipod, load

    first = ipod_at(fake_ipod(tmp_path / "a", load("captured-album-index-36.b64")))
    second_root = fake_ipod(tmp_path / "b", load("captured-album-index-36.b64"))
    second = ipod_at(second_root.rename(second_root.with_name("Gym iPod")))
    monkeypatch.setattr("synclassic.ipod.device.find_ipods", lambda: [first, second])
    picked = cli._pick_ipod(SimpleNamespace(path=None, ipod=None))
    assert picked is first and "--ipod NAME" in capsys.readouterr().out
    assert cli._pick_ipod(SimpleNamespace(path=None, ipod="gym ipod")).root == second.root
    assert cli._pick_ipod(SimpleNamespace(path=None, ipod="Gym")).root == second.root  # part of the name
    assert cli._pick_ipod(SimpleNamespace(path=None, ipod="nope")) is None
