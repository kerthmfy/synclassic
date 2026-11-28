"""Errors in plain words, and the last one kept (without secrets) for Report a Problem.

Synclassic's own errors (sync, sources, downloads, Last.fm, Spotify) already say what happened
and what to do. Errors from the system (disks, the network, the library file) are turned into
the same kind of message here.
"""

from __future__ import annotations

import errno
import pathlib
import sqlite3
import time
import traceback

LAST: dict = {}  # the last error: message, details (traceback, secrets removed), when


def _where(path) -> str:
    if not path:
        return "the disk"
    text = str(path)
    parts = pathlib.PurePath(text).parts
    if text.startswith("/Volumes/") and len(parts) > 2:
        return f"“{parts[2]}” (the iPod or drive)"
    return text.replace(str(pathlib.Path.home()), "~")


def _host(error) -> str:
    request = getattr(error, "request", None)
    url = getattr(request, "url", "") or ""
    if "://" in url:
        return url.split("://", 1)[1].split("/", 1)[0]
    return "the website"


def friendly(error: BaseException) -> str:
    """What went wrong and what to do, in plain words."""
    try:
        import requests

        if isinstance(error, requests.Timeout):
            return f"{_host(error)} took too long to answer. Try again in a moment."
        if isinstance(error, requests.ConnectionError):
            return (f"Couldn't reach {_host(error)}. Check that you're connected to the internet, then try again.")
    except ImportError:
        pass
    if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
        return ("Synclassic's library is busy: another Synclassic (or the background update) is using it. "
                "Try again in a minute.")
    if isinstance(error, sqlite3.DatabaseError) and "malformed" in str(error).lower():
        return ("Synclassic's library file is damaged. Quit Synclassic and use Help → Report a Problem; "
                "your music files are safe.")
    if isinstance(error, FileNotFoundError) and str(getattr(error, "filename", "")) in ("ffmpeg", "ffprobe", "deno"):
        return f"{error.filename} isn't installed. In Terminal: brew install ffmpeg deno (Homebrew: brew.sh)."
    if isinstance(error, OSError) and error.errno is not None:
        where = _where(error.filename)
        if error.errno == errno.ENOSPC:
            return f"There's no space left on {where}. Free some space (or choose fewer playlists), then try again."
        if error.errno == errno.EROFS:
            return (f"{where} can only be read, not changed. A Windows-formatted iPod sometimes ends up like this: "
                    "eject it, plug it in again, and if it stays that way, run First Aid on it in Disk Utility.")
        if error.errno in (errno.EACCES, errno.EPERM):
            return (f"macOS didn't let Synclassic change {where}. If it's on the iPod, check that it isn't locked "
                    "(Finder → Get Info). Otherwise check the folder's permissions.")
        if error.errno == errno.EIO:
            return (f"{where} stopped responding (an input/output error). If it's the iPod: eject it, plug it in "
                    "again, and use Check iPod. A disk that keeps doing this may be failing.")
        if error.errno in (errno.ENOENT, errno.ENODEV, errno.ENXIO) and str(error.filename or "").startswith("/Volumes/"):
            return f"{where} went away. Was the iPod unplugged? Plug it in again and try once more."
    return str(error) or error.__class__.__name__


def remember(error: BaseException) -> str:
    """Keep the error (for Report a Problem) and return its plain message."""
    from synclassic.diagnostics import redact

    message = friendly(error)
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    LAST.update(message=message, details=redact(details), at=time.strftime("%Y-%m-%d %H:%M:%S"))
    try:  # also for a report made later, from another window or Terminal
        import json

        from synclassic.config import app_dir

        (app_dir() / "last-error.json").write_text(json.dumps(LAST), encoding="utf-8")
    except (OSError, ValueError):
        pass
    return message


def last() -> dict:
    """The last error, from this run or an earlier one."""
    if LAST:
        return LAST
    try:
        import json

        from synclassic.config import app_dir

        return json.loads((app_dir() / "last-error.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
