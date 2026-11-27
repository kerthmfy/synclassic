"""Contacts, calendars and notes for the iPod's Extras menu.

The iPod reads these as plain files in folders at the top of the disk: Contacts (vCard), Calendars
(iCalendar .ics) and Notes (text, up to 4 KB each, up to 1000 notes). What Synclassic copies is
named "Synclassic …" (notes go in Notes/Synclassic), so copying again replaces it and files you put
there yourself stay.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable

from synclassic.ipod.device import IPod
from synclassic.textnorm import safe_filename

NOTE_LIMIT = 4096  # bytes the iPod shows of a note
MAX_NOTES = 1000
PREFIX = "Synclassic"


def folder(ipod: IPod, kind: str) -> pathlib.Path:
    return ipod.root / {"contacts": "Contacts", "calendars": "Calendars", "notes": "Notes"}[kind]


def ours(ipod: IPod, kind: str) -> list[pathlib.Path]:
    base = folder(ipod, kind)
    if kind == "notes":
        base = base / PREFIX
        return sorted(base.glob("*.txt")) if base.is_dir() else []
    return sorted(p for p in base.glob(f"{PREFIX} *") if p.is_file()) if base.is_dir() else []


def counts(ipod: IPod) -> dict[str, int]:
    """How many of each are on the iPod (all of them, not just Synclassic's)."""
    result = {}
    for kind, pattern in (("contacts", "*.vcf"), ("calendars", "*.ics"), ("notes", "**/*.txt")):
        base = folder(ipod, kind)
        if not base.is_dir():
            result[kind] = 0
        elif kind == "contacts":
            result[kind] = sum(_card_count(p) for p in base.glob(pattern))
        else:
            result[kind] = sum(1 for _ in base.glob(pattern))
    return result


def _card_count(path: pathlib.Path) -> int:
    try:
        return max(1, path.read_text(encoding="utf-8", errors="replace").upper().count("BEGIN:VCARD"))
    except OSError:
        return 0


def remove(ipod: IPod, kind: str) -> int:
    files = ours(ipod, kind)
    for path in files:
        path.unlink(missing_ok=True)
    return len(files)


# --- contacts ---------------------------------------------------------------------------------------


def split_vcards(text: str) -> list[str]:
    return [card.strip() + "\r\n" for card in re.findall(r"BEGIN:VCARD.*?END:VCARD", text, flags=re.S | re.I)]


def _card_name(card: str) -> str:
    found = re.search(r"^FN[^:]*:(.+)$", card, flags=re.M)
    return found.group(1).strip() if found else "Contact"


def put_contacts(ipod: IPod, cards: list[str]) -> int:
    """Replace Synclassic's contacts on the iPod with these vCards (one file per contact)."""
    remove(ipod, "contacts")
    base = folder(ipod, "contacts")
    base.mkdir(exist_ok=True)
    used: set[str] = set()
    for card in cards:
        name = safe_filename(f"{PREFIX} {_card_name(card)}", 90)
        stem, number = name, 2
        while stem.casefold() in used:
            stem, number = f"{name} {number}", number + 1
        used.add(stem.casefold())
        (base / f"{stem}.vcf").write_text(card, encoding="utf-8")
    return len(cards)


def contacts_from_files(paths: list[pathlib.Path]) -> list[str]:
    cards = []
    for path in paths:
        cards += split_vcards(path.read_text(encoding="utf-8", errors="replace"))
    return cards


def mac_contacts(run=subprocess.run) -> list[str]:
    """Every contact in the Mac's Contacts app, as vCards (macOS asks for permission once)."""
    if sys.platform != "darwin" and run is subprocess.run:
        raise RuntimeError("Copying the Mac's contacts only works on macOS.")
    result = run(["osascript", "-e", 'tell application "Contacts" to get vcard of every person'],
                 capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError("Couldn't read your contacts. Allow Synclassic (or Terminal) in System Settings → "
                           "Privacy & Security → Contacts, then try again. " + (result.stderr or "").strip())
    return split_vcards(result.stdout)


# --- calendars --------------------------------------------------------------------------------------


def put_calendars(ipod: IPod, paths: list[pathlib.Path]) -> int:
    """Copy .ics calendars (Calendar app: File → Export) to the iPod."""
    base = folder(ipod, "calendars")
    base.mkdir(exist_ok=True)
    for path in paths:
        if "BEGIN:VCALENDAR" not in path.read_text(encoding="utf-8", errors="replace")[:2000].upper():
            raise ValueError(f"{path.name} isn't a calendar (.ics) file.")
        shutil.copyfile(path, base / safe_filename(f"{PREFIX} {path.stem}.ics", 120))
    return len(paths)


# --- notes ------------------------------------------------------------------------------------------


def put_notes(ipod: IPod, notes: list[tuple[str, str]], say: Callable[[str], None] = lambda s: None) -> int:
    """Replace Synclassic's notes on the iPod with these (title, text). Long notes are cut to what
    the iPod shows (4 KB)."""
    remove(ipod, "notes")
    base = folder(ipod, "notes") / PREFIX
    base.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    written = 0
    for title, text in notes[:MAX_NOTES]:
        stem, number = safe_filename(title or "Note", 80), 2
        original = stem
        while stem.casefold() in used:
            stem, number = f"{original} {number}", number + 1
        used.add(stem.casefold())
        data = text.replace("\r\n", "\n").encode("utf-8")
        if len(data) > NOTE_LIMIT:
            say(f"“{title}” is longer than the iPod shows; it was shortened.")
            data = data[:NOTE_LIMIT].decode("utf-8", errors="ignore").encode("utf-8")
        (base / f"{stem}.txt").write_bytes(data)
        written += 1
    if len(notes) > MAX_NOTES:
        say(f"The iPod holds {MAX_NOTES} notes; the rest were left out.")
    return written


def notes_from_files(paths: list[pathlib.Path]) -> list[tuple[str, str]]:
    return [(path.stem, path.read_text(encoding="utf-8", errors="replace")) for path in paths]


_NOTES_SCRIPT = '''
set out to ""
tell application "Notes"
    repeat with n in every note
        set out to out & (name of n) & (ASCII character 30) & (plaintext of n) & (ASCII character 31)
    end repeat
end tell
return out
'''


def mac_notes(run=subprocess.run) -> list[tuple[str, str]]:
    """Every note in Apple Notes as (title, text) (macOS asks for permission once)."""
    if sys.platform != "darwin" and run is subprocess.run:
        raise RuntimeError("Copying Apple Notes only works on macOS.")
    result = run(["osascript", "-e", _NOTES_SCRIPT], capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError("Couldn't read your notes. Allow Synclassic (or Terminal) to control Notes in System "
                           "Settings → Privacy & Security → Automation, then try again. " + (result.stderr or "").strip())
    notes = []
    for record in result.stdout.split("\x1f"):
        if "\x1e" in record:
            title, text = record.split("\x1e", 1)
            notes.append((title.strip(), text.strip()))
    return notes
