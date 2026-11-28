"""Where ffmpeg and deno come from.

The standalone Synclassic.app carries its own (in Contents/Resources/bin), so it works without
Homebrew; otherwise Homebrew's are used (apps opened from Finder don't get Terminal's PATH, so
Homebrew's folders are added). The app's own copies come first.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

HOMEBREW = ("/opt/homebrew/bin", "/usr/local/bin")


def bundled_dir() -> pathlib.Path | None:
    """The app's own tools folder (standalone app only)."""
    if not getattr(sys, "frozen", False):
        return None
    folder = pathlib.Path(sys.executable).resolve().parent.parent / "Resources" / "bin"
    return folder if folder.is_dir() else None


def add_to_path() -> None:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    folders = [str(f) for f in [bundled_dir()] if f] + ([f for f in HOMEBREW if os.path.isdir(f)]
                                                       if sys.platform == "darwin" else [])
    for folder in reversed(folders):
        if folder in parts:
            parts.remove(folder)
        parts.insert(0, folder)
    os.environ["PATH"] = os.pathsep.join(parts)


def where(name: str) -> str:
    """Where a tool is found, in words: "in the app", "Homebrew", a path, or "missing"."""
    found = shutil.which(name)
    if not found:
        return "missing"
    bundled = bundled_dir()
    if bundled and pathlib.Path(found).resolve().parent == bundled.resolve():
        return "in the app"
    if any(found.startswith(folder) for folder in HOMEBREW):
        return f"Homebrew ({found})"
    return found
