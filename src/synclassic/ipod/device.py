"""Find connected iPods and describe them."""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
from dataclasses import dataclass, field

# Model numbers (the part after the first letter of "MA446") of the iPods Synclassic targets.
MODELS = {
    "A002": "iPod 5th generation (Video), 30 GB, white",
    "A146": "iPod 5th generation (Video), 30 GB, black",
    "A003": "iPod 5th generation (Video), 60 GB, white",
    "A147": "iPod 5th generation (Video), 60 GB, black",
    "A452": "iPod 5th generation (Video), U2 edition",
    "A444": "iPod 5.5th generation (Video), 30 GB, white",
    "A446": "iPod 5.5th generation (Video), 30 GB, black",
    "A448": "iPod 5.5th generation (Video), 80 GB, white",
    "A450": "iPod 5.5th generation (Video), 80 GB, black",
    "A664": "iPod 5.5th generation (Video), U2 edition",
    # iPod classic: their database is signed with each iPod's FireWire ID (see ipod/signing.py).
    "B029": "iPod classic (6th generation), 80 GB, silver",
    "B147": "iPod classic (6th generation), 80 GB, black",
    "B145": "iPod classic (6th generation), 160 GB, silver",
    "B150": "iPod classic (6th generation), 160 GB, black",
    "B562": "iPod classic (6.5th generation), 120 GB, silver",
    "B565": "iPod classic (6.5th generation), 120 GB, black",
    "C293": "iPod classic (7th generation), 160 GB, silver",
    "C297": "iPod classic (7th generation), 160 GB, black",
}
VIDEO_GENERATION = {code: ("5.5" if "5.5" in name else "5") for code, name in MODELS.items() if "Video" in name}


@dataclass
class IPod:
    root: pathlib.Path
    sysinfo: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def control(self) -> pathlib.Path:
        return self.root / "iPod_Control"

    @property
    def database_path(self) -> pathlib.Path:
        return self.control / "iTunes" / "iTunesDB"

    @property
    def model_code(self) -> str:
        raw = self.sysinfo.get("ModelNumStr", "")
        return raw[-4:].upper() if len(raw) >= 4 else ""

    @property
    def model(self) -> str:
        if self.model_code in MODELS:
            return MODELS[self.model_code]
        raw = self.sysinfo.get("ModelNumStr")
        return f"Unknown iPod model ({raw})" if raw else "iPod (model not reported)"

    @property
    def is_supported_model(self) -> bool | None:
        """True for 5G/5.5G, False for others, None when the iPod doesn't say."""
        if not self.model_code:
            return None
        return self.model_code in MODELS

    @property
    def firmware(self) -> str:
        # e.g. "visibleBuildID: 0x01338000 (1.3)" -> "1.3"
        raw = self.sysinfo.get("visibleBuildID") or self.sysinfo.get("buildID") or ""
        if "(" in raw and ")" in raw:
            return raw.split("(", 1)[1].split(")", 1)[0].strip()
        return raw.strip()

    def disk_usage(self) -> tuple[int, int]:
        usage = shutil.disk_usage(self.root)
        return usage.total, usage.free


def read_sysinfo(root: pathlib.Path) -> dict[str, str]:
    """iPod_Control/Device/SysInfo: simple 'Key: value' lines written by the iPod."""
    path = root / "iPod_Control" / "Device" / "SysInfo"
    info: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            info[key.strip()] = value.strip()
    return info


def _candidate_roots() -> list[pathlib.Path]:
    override = os.environ.get("SYNCLASSIC_IPOD")
    if override:
        return [pathlib.Path(override).expanduser()]
    roots: list[pathlib.Path] = []
    if sys.platform == "darwin":
        bases = [pathlib.Path("/Volumes")]
    elif os.name == "nt":
        bases = []
        roots = [pathlib.Path(f"{letter}:\\") for letter in "DEFGHIJKLMNOPQRSTUVWXYZ"]
    else:
        user = os.environ.get("USER", "")
        bases = [pathlib.Path("/media") / user, pathlib.Path("/run/media") / user, pathlib.Path("/media"), pathlib.Path("/mnt")]
    for base in bases:
        try:
            roots.extend(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
    return roots


def find_ipods() -> list[IPod]:
    found = []
    for root in _candidate_roots():
        try:
            if (root / "iPod_Control" / "iTunes").is_dir():
                found.append(IPod(root, read_sysinfo(root)))
        except OSError:
            continue
    return found


def ipod_at(path: str | pathlib.Path) -> IPod:
    root = pathlib.Path(path).expanduser()
    if not (root / "iPod_Control").is_dir():
        raise FileNotFoundError(f"No iPod at {root} (there's no iPod_Control folder).")
    return IPod(root, read_sysinfo(root))
