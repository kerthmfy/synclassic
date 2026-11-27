"""Sound Check: loudness measured once per file, written to the iPod so songs play evenly."""

import shutil
import subprocess

import pytest

from synclassic import config, loudness
from synclassic.ipod import itunesdb, soundcheck, sync
from synclassic.ipod.check import check
from synclassic.library import Library
from tests.test_ipod_sync import ipod, library_with, read_db, song  # noqa: F401


def test_soundcheck_values_follow_the_ipod_formula():
    assert loudness.soundcheck(-18.0) == 1000  # at the reference: as is
    assert loudness.soundcheck(-8.0) == 10000  # 10 dB too loud: played quieter
    assert loudness.soundcheck(-24.0) == 251  # 6 dB too quiet: played louder
    assert loudness.soundcheck(-60.0) == 63  # boosts are capped at 12 dB
    assert loudness.soundcheck(None) == 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_measure_tells_loud_from_quiet(tmp_path):
    paths = {}
    for name, volume in (("loud", "0dB"), ("quiet", "-12dB")):
        paths[name] = tmp_path / f"{name}.m4a"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-af", f"volume={volume}", "-c:a", "aac", "-b:a", "128k", str(paths[name])], check=True)
    loud, quiet = loudness.measure(paths["loud"]), loudness.measure(paths["quiet"])
    assert 11 < loud - quiet < 13


def test_each_file_is_measured_once(tmp_path):
    calls = []
    library = Library(config.app_dir() / "library.db")
    cache = loudness.LoudnessCache(library, lambda p: calls.append(p) or -12.0)
    path = tmp_path / "a.m4a"
    path.write_bytes(b"x")
    assert cache.get(path) == -12.0 and cache.get(path) == -12.0 and len(calls) == 1
    path.write_bytes(b"changed")  # a new file there: measured again
    cache.get(path)
    assert len(calls) == 2


def test_sync_writes_sound_check(tmp_path, ipod, monkeypatch):
    monkeypatch.setattr(loudness, "measure", lambda path, run=None: -8.0)  # 10 dB too loud
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    plan = sync.make_plan(library, ipod)
    assert plan.to_measure == 2 and any("Measure the loudness of 2 songs" in line for line in plan.summary())
    sync.apply_plan(library, plan, lambda s: None)
    values = {s.title: s.chunk.u32(0x4C) for s in itunesdb.songs(read_db(ipod))}
    assert values["New Song 1"] == values["New Song 2"] == 10000
    assert sync.make_plan(library, ipod).to_measure == 0  # remembered

    # Songs synced before Sound Check existed get it at the next sync.
    db = read_db(ipod)
    for s in itunesdb.songs(db):
        if s.title == "New Song 1":
            s.chunk.set_u32(0x4C, 0)
    ipod.database_path.write_bytes(itunesdb.serialize(db))
    plan = sync.make_plan(library, ipod)
    assert len(plan.soundcheck) == 1 and plan.has_changes
    sync.apply_plan(library, plan, lambda s: None)
    assert {s.title: s.chunk.u32(0x4C) for s in itunesdb.songs(read_db(ipod))}["New Song 1"] == 10000


def test_sound_check_off_leaves_it_alone(tmp_path, ipod, monkeypatch):
    settings = config.load_settings()
    settings.sound_check = False
    config.save_settings(settings)
    monkeypatch.setattr(loudness, "measure", lambda path, run=None: pytest.fail("measured with Sound Check off"))
    library = library_with(tmp_path, {"Mix": [song(1)]})
    plan = sync.make_plan(library, ipod)
    sync.apply_plan(library, plan, lambda s: None)
    assert {s.title: s.chunk.u32(0x4C) for s in itunesdb.songs(read_db(ipod))}["New Song 1"] == 0


def test_songs_synclassic_didnt_add_can_get_sound_check(ipod):
    from synclassic.ipod.edit import Editor, NewSong

    db = read_db(ipod)
    editor = Editor(db)
    for n in (1, 2):  # songs another app put on the iPod
        editor.add_song(NewSong(location=f":iPod_Control:Music:F00:OTHR{n}.mp3", filetype="mp3", size=10,
                                length_ms=2000, bitrate=128, sample_rate=44100, vbr=False, title=f"Other {n}",
                                artist="Someone"))
        (ipod.root / f"iPod_Control/Music/F00/OTHR{n}.mp3").write_bytes(b"x" * 10)
    editor.finish()
    ipod.database_path.write_bytes(itunesdb.serialize(db))
    before = soundcheck.missing(ipod)
    assert before
    measured = []
    done = soundcheck.apply(ipod, measure_file=lambda path: measured.append(path) or -24.0)
    assert done == len(before) == len(measured)
    assert soundcheck.missing(ipod) == []
    assert {s.chunk.u32(0x4C) for s in itunesdb.songs(read_db(ipod)) if s.media_type in (0, 1) and s.location} == {251}
    assert check(ipod).safe_to_manage
