"""Contacts, calendars and notes for the iPod's Extras menu."""

from types import SimpleNamespace

import pytest

from synclassic.ipod import extras
from synclassic.ipod.device import ipod_at
from tests.test_ipod_read import fake_ipod, load

CARDS = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n"
         "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alan Turing\r\nEND:VCARD\r\n")


@pytest.fixture
def ipod(tmp_path):
    return ipod_at(fake_ipod(tmp_path, load("original-empty.b64")))


def test_contacts_replace_only_synclassics(ipod, tmp_path):
    (ipod.root / "Contacts").mkdir()
    (ipod.root / "Contacts" / "Mine.vcf").write_text("BEGIN:VCARD\nFN:Me\nEND:VCARD\n")
    path = tmp_path / "all.vcf"
    path.write_text(CARDS)
    assert extras.put_contacts(ipod, extras.contacts_from_files([path])) == 2
    names = sorted(p.name for p in (ipod.root / "Contacts").iterdir())
    assert names == ["Mine.vcf", "Synclassic Ada Lovelace.vcf", "Synclassic Alan Turing.vcf"]
    assert extras.counts(ipod)["contacts"] == 3
    extras.put_contacts(ipod, extras.split_vcards(CARDS)[:1])
    assert extras.counts(ipod)["contacts"] == 2  # replaced, yours kept


def test_mac_contacts_via_applescript():
    run = lambda args, **k: SimpleNamespace(returncode=0, stdout=CARDS, stderr="")  # noqa: E731
    assert len(extras.mac_contacts(run=run)) == 2
    denied = lambda args, **k: SimpleNamespace(returncode=1, stdout="", stderr="not allowed")  # noqa: E731
    with pytest.raises(RuntimeError, match="Privacy & Security"):
        extras.mac_contacts(run=denied)


def test_calendars_and_notes(ipod, tmp_path):
    ics = tmp_path / "Work.ics"
    ics.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    assert extras.put_calendars(ipod, [ics]) == 1
    assert (ipod.root / "Calendars" / "Synclassic Work.ics").is_file()
    bad = tmp_path / "x.ics"
    bad.write_text("hello")
    with pytest.raises(ValueError):
        extras.put_calendars(ipod, [bad])
    said = []
    assert extras.put_notes(ipod, [("Groceries", "eggs"), ("Long", "x" * 5000), ("Groceries", "milk")], said.append) == 3
    notes = sorted(p.name for p in (ipod.root / "Notes" / "Synclassic").iterdir())
    assert notes == ["Groceries 2.txt", "Groceries.txt", "Long.txt"]
    assert (ipod.root / "Notes" / "Synclassic" / "Long.txt").stat().st_size == extras.NOTE_LIMIT and said
    output = "Shopping\x1eeggs\nmilk\x1fIdeas\x1ebuild an app\x1f"
    run = lambda args, **k: SimpleNamespace(returncode=0, stdout=output, stderr="")  # noqa: E731
    assert extras.mac_notes(run=run) == [("Shopping", "eggs\nmilk"), ("Ideas", "build an app")]
    assert extras.remove(ipod, "notes") == 3 and extras.counts(ipod)["notes"] == 0
