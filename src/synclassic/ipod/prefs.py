"""Settings that belong to one iPod, kept on the iPod itself (.synclassic/settings.json), so
each iPod remembers its own choices whichever Mac it's plugged into:

- which playlists go on it (all of them, or the ones you tick),
- how big the song files on it are (as they are, or converted to AAC at 256/192/128 kbps so
  more fits),
- (Rockbox iPods) which firmware it's synced for.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import asdict, dataclass, field

FOLDER = ".synclassic"
BITRATES = (0, 256, 192, 128)  # 0 = songs as they are


@dataclass
class IPodPrefs:
    playlists: list[str] | None = None  # None = every playlist; else the chosen playlist keys
    bitrate: int = 0  # kbps for AAC copies, 0 = copy songs as they are
    mode: str = ""  # "apple" or "rockbox" (only asked for on iPods with both)
    replace: list[str] = field(default_factory=list)  # playlist keys to replace on the iPod at the next sync, no matter what

    @property
    def all_playlists(self) -> bool:
        return self.playlists is None


def _path(root: pathlib.Path) -> pathlib.Path:
    return root / FOLDER / "settings.json"


def load(ipod) -> IPodPrefs:
    try:
        data = json.loads(_path(ipod.root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    prefs = IPodPrefs()
    chosen = data.get("playlists")
    if isinstance(chosen, list):
        prefs.playlists = [str(k) for k in chosen]
    bitrate = data.get("bitrate", 0)
    prefs.bitrate = bitrate if bitrate in BITRATES else 0
    prefs.mode = str(data.get("mode") or "")
    replace = data.get("replace")
    prefs.replace = [str(k) for k in replace] if isinstance(replace, list) else []
    return prefs


def save(ipod, prefs: IPodPrefs) -> None:
    path = _path(ipod.root)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(prefs), indent=1), encoding="utf-8")
    os.replace(tmp, path)


def update(ipod, **changes) -> IPodPrefs:
    prefs = load(ipod)
    for name, value in changes.items():
        setattr(prefs, name, value)
    save(ipod, prefs)
    return prefs


def chosen_playlists(library, ipod, prefs: IPodPrefs | None = None):
    """The library playlists that go on this iPod, in the library's order."""
    from synclassic.ipod.sync import playlist_key

    prefs = prefs or load(ipod)
    playlists = library.playlists()
    if prefs.all_playlists:
        return playlists
    wanted = set(prefs.playlists or [])
    return [p for p in playlists if playlist_key(p) in wanted]


def describe(library, ipod, prefs: IPodPrefs | None = None) -> str:
    """One line for the iPod page, e.g. "All playlists · songs as they are"."""
    prefs = prefs or load(ipod)
    if prefs.all_playlists:
        which = "All playlists"
    else:
        count = len(chosen_playlists(library, ipod, prefs))
        which = f"{count} chosen playlist{'s' if count != 1 else ''}"
    size = f"new songs as {prefs.bitrate} kbps AAC" if prefs.bitrate else "songs as they are"
    return f"{which} · {size}"
