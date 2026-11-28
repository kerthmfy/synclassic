"""Windows-formatted (FAT32) iPods: lowercase music folders, macOS's ._ files, the 4 GB file limit."""

import plistlib
from types import SimpleNamespace

from synclassic.ipod import device, itunesdb, manage, sync
from synclassic.ipod.check import check, format_report
from synclassic.ipod.device import ipod_at
from tests.test_ipod_read import fake_ipod, load
from tests.test_ipod_sync import library_with, song


def fat_ipod(tmp_path, monkeypatch):
    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    music = root / "iPod_Control" / "Music"
    (music / "F00").rmdir()
    for n in range(3):
        (music / f"f{n:02d}").mkdir()
    (music / "f01" / "._ABCD.mp3").write_bytes(b"\0" * 4096)  # left by macOS on FAT disks
    monkeypatch.setattr(device, "detect_filesystem", lambda root, run=None: "msdos")
    return ipod_at(root)


def test_filesystem_is_read_from_diskutil():
    answer = plistlib.dumps({"FilesystemType": "msdos"})
    assert device.detect_filesystem("/Volumes/IPOD", run=lambda *a, **k: SimpleNamespace(stdout=answer)) == "msdos"


def test_check_ignores_dot_underscore_files_and_says_the_format(tmp_path, monkeypatch):
    ipod = fat_ipod(tmp_path, monkeypatch)
    report = check(ipod)
    assert report.unlisted_files == 0 and report.safe_to_manage
    assert "Windows (FAT32)" in "\n".join(format_report(report))
    assert manage.find_cleanup(ipod).leftovers == []


def test_sync_uses_the_existing_lowercase_folders(tmp_path, monkeypatch):
    ipod = fat_ipod(tmp_path, monkeypatch)
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    db = itunesdb.parse(ipod.database_path.read_bytes())
    copied = [s for s in itunesdb.songs(db) if s.title.startswith("New Song")]
    assert len(copied) == 2
    for s in copied:
        assert s.relative_path.split("/")[2] in ("f00", "f01", "f02")
        assert (ipod.root / s.relative_path).is_file()
    assert sorted(p.name for p in (ipod.root / "iPod_Control" / "Music").iterdir()) == ["f00", "f01", "f02"]


def test_files_of_4_gb_are_skipped_on_fat(tmp_path, monkeypatch):
    ipod = fat_ipod(tmp_path, monkeypatch)
    assert ipod.fits_file(4 * 1024**3 - 1) and not ipod.fits_file(4 * 1024**3)
    monkeypatch.setattr(device, "detect_filesystem", lambda root, run=None: "hfs")
    assert ipod_at(ipod.root).fits_file(5 * 1024**3)
