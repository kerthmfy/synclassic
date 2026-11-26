"""Reading the iPod database, and the read-only check, on databases from real iPods."""

import base64
import pathlib

import pytest

from synclassic.ipod import itunesdb
from synclassic.ipod.check import check, format_report
from synclassic.ipod.device import find_ipods, ipod_at

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "ipod"


def load(name: str) -> bytes:
    return base64.b64decode((FIXTURES / name).read_text())


@pytest.mark.parametrize("name", ["original-empty.b64", "captured-album-index-36.b64"])
def test_unchanged_database_round_trips_byte_for_byte(name):
    raw = load(name)
    assert itunesdb.serialize(itunesdb.parse(raw)) == raw


def test_reads_songs_and_playlists():
    db = itunesdb.parse(load("captured-album-index-36.b64"))
    songs = itunesdb.songs(db)
    assert len(songs) == 60
    assert songs[0].title == "Fixture Track 001" and songs[0].artist
    master = [p for p in itunesdb.playlists(db) if p.is_master]
    assert len(master) == 1 and sorted(master[0].track_ids) == sorted(s.id for s in songs)


def test_editing_text_changes_only_that_song():
    raw = load("captured-album-index-36.b64")
    db = itunesdb.parse(raw)
    song = itunesdb.songs(db)[3]
    itunesdb.set_text(song.chunk, itunesdb.TITLE, "Renamed ✓ ünïcödé")
    out = itunesdb.serialize(db)
    again = itunesdb.parse(out)
    assert itunesdb.songs(again)[3].title == "Renamed ✓ ünïcödé"
    assert [s.title for i, s in enumerate(itunesdb.songs(again)) if i != 3] == [
        s.title for i, s in enumerate(itunesdb.songs(itunesdb.parse(raw))) if i != 3
    ]
    # Lengths were recalculated, so the result reads back cleanly and round-trips itself.
    assert itunesdb.serialize(itunesdb.parse(out)) == out


def test_damaged_database_is_refused():
    raw = bytearray(load("captured-album-index-36.b64"))
    with pytest.raises(itunesdb.DatabaseError):
        itunesdb.parse(bytes(raw[:5000]))
    with pytest.raises(itunesdb.DatabaseError):
        itunesdb.parse(b"not a database")


def fake_ipod(tmp_path, db_bytes: bytes, model="xA446"):
    root = tmp_path / "iPod"
    (root / "iPod_Control" / "iTunes").mkdir(parents=True)
    (root / "iPod_Control" / "Device").mkdir()
    (root / "iPod_Control" / "Music" / "F00").mkdir(parents=True)
    (root / "iPod_Control" / "iTunes" / "iTunesDB").write_bytes(db_bytes)
    (root / "iPod_Control" / "Device" / "SysInfo").write_text(f"ModelNumStr: {model}\nvisibleBuildID: 0x01338000 (1.3)\n")
    return root


def test_check_is_read_only_and_reports(tmp_path):
    raw = load("captured-album-index-36.b64")
    root = fake_ipod(tmp_path, raw)
    (root / "iPod_Control" / "Music" / "F00" / "LEFT.mp3").write_bytes(b"x")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    ipod = ipod_at(root)
    assert ipod.model == "iPod 5.5th generation (Video), 30 GB, black" and ipod.firmware == "1.3"
    report = check(ipod)
    text = "\n".join(format_report(report))
    assert report.songs == 60 and report.round_trip_ok and not report.hashed_database
    assert report.unlisted_files == 1
    assert "byte-for-byte" in text and "Safe for Synclassic" in text
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before  # nothing changed


def test_find_ipods_with_override(tmp_path, monkeypatch):
    root = fake_ipod(tmp_path, load("original-empty.b64"))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(root))
    (found,) = find_ipods()
    assert found.root == root and found.is_supported_model


def test_cli_ipod_command(tmp_path, capsys):
    from synclassic.cli import main

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    assert main(["ipod", "--path", str(root)]) == 0
    out = capsys.readouterr().out
    assert "60 songs" in out and "byte-for-byte" in out
