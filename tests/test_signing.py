"""Signed databases (iPod classic 6G/7G, nano 3G/4G): hash58 with the iPod's FireWire ID."""

import hashlib
import json
import pathlib
import plistlib
from types import SimpleNamespace

from synclassic.ipod import itunesdb, signing, sync
from synclassic.ipod.check import check
from synclassic.ipod.device import ipod_at
from tests.test_ipod_read import fake_ipod, load
from tests.test_ipod_sync import library_with, song

VECTORS = json.loads((pathlib.Path(__file__).parent / "fixtures" / "ipod" / "hash58-vectors.json").read_text())
GUID = "000A2700200D5D8E"


def test_matches_the_original_implementation():
    data = bytearray((i * 37 + 11) % 256 for i in range(1024))
    data[0:4], data[4:8], data[8:12] = b"mhbd", (244).to_bytes(4, "little"), (1024).to_bytes(4, "little")
    for vector in VECTORS["vectors"]:
        signed = signing.sign_hash58(bytes(data), bytes.fromhex(vector["guid"]))
        assert signed[0x58:0x6C].hex() == vector["hash58"]
        assert hashlib.sha256(signed).hexdigest() == vector["output_sha256"]
        assert signing.verify_hash58(signed, bytes.fromhex(vector["guid"]))
        assert signing.scheme(signed) == signing.HASH58_SCHEME


def classic(tmp_path, guid_line=True, guid=GUID):
    raw = signing.sign_hash58(load("captured-album-index-36.b64"), bytes.fromhex(guid))
    root = fake_ipod(tmp_path, raw, model="xB150")
    if guid_line:
        sysinfo = root / "iPod_Control" / "Device" / "SysInfo"
        sysinfo.write_text(sysinfo.read_text() + f"FirewireGuid: 0x{GUID}\n")
    return ipod_at(root)


def test_firewire_id_from_sysinfo_extended_or_usb(tmp_path):
    ipod = classic(tmp_path, guid_line=False)
    assert signing.firewire_guid(ipod, run=lambda *a, **k: SimpleNamespace(stdout="{}")) is None
    usb = {"SPUSBDataType": [{"_items": [{"_name": "iPod", "serial_num": GUID}]}]}
    assert signing.firewire_guid(ipod, run=lambda *a, **k: SimpleNamespace(stdout=json.dumps(usb))) == bytes.fromhex(GUID)
    (ipod.control / "Device" / "SysInfoExtended").write_bytes(plistlib.dumps({"FireWireGUID": GUID}))
    assert signing.firewire_guid(ipod) == bytes.fromhex(GUID)


def test_check_accepts_a_classic_only_with_its_own_firewire_id(tmp_path, monkeypatch):
    ipod = classic(tmp_path)
    report = check(ipod)
    assert report.safe_to_manage and "hash58" in report.signature
    assert ipod.model == "iPod classic (6th generation), 160 GB, black"

    monkeypatch.setattr(signing, "firewire_guid", lambda ipod, run=None: bytes.fromhex("000A270000000001"))
    report = check(ipod)
    assert not report.safe_to_manage and "doesn't match" in " ".join(report.problems)
    monkeypatch.setattr(signing, "firewire_guid", lambda ipod, run=None: None)
    assert "FirewireGuid" in " ".join(check(ipod).problems)


def test_newer_signatures_are_refused(tmp_path):
    raw = bytearray(load("captured-album-index-36.b64"))
    raw[0x30:0x32] = (2).to_bytes(2, "little")  # hash72 (nano 5G+)
    report = check(ipod_at(fake_ipod(tmp_path, bytes(raw))))
    assert not report.safe_to_manage and "nano 5G" in " ".join(report.problems)


def test_sync_signs_the_new_database(tmp_path):
    ipod = classic(tmp_path)
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    data = ipod.database_path.read_bytes()
    assert signing.verify_hash58(data, bytes.fromhex(GUID))
    assert {"New Song 1", "New Song 2"} <= {s.title for s in itunesdb.songs(itunesdb.parse(data))}
    assert check(ipod).safe_to_manage


def test_edits_are_signed_too(tmp_path):
    from synclassic.ipod import manage

    ipod = classic(tmp_path)
    manage.create_playlist(ipod, "Signed Mix", [])
    assert signing.verify_hash58(ipod.database_path.read_bytes(), bytes.fromhex(GUID))
