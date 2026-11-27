"""Background updates (macOS): a LaunchAgent runs 'synclassic update' every few hours, even when the
app is closed, so new songs and podcast episodes are downloaded before you plug the iPod in.

Only one update runs at a time: the app and the background task share a lock file, and whoever
finds it taken skips this round.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import plistlib
import subprocess
import sys

from synclassic.config import app_dir

LABEL = "io.github.kerthmfy.synclassic.update"
PATHS = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def agent_path() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "Logs" / "Synclassic-background.log"


def agent_plist(hours: float, python: str | None = None) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "synclassic.cli", "update", "--background"],
        "StartInterval": int(hours * 3600),
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "EnvironmentVariables": {"PATH": PATHS, **({"SYNCLASSIC_HOME": os.environ["SYNCLASSIC_HOME"]}
                                                    if os.environ.get("SYNCLASSIC_HOME") else {})},
        "StandardOutPath": str(log_path()),
        "StandardErrorPath": str(log_path()),
    }


def _launchctl(*args: str, run=subprocess.run):
    return run(["launchctl", *args], capture_output=True, text=True)


def enable(hours: float, run=subprocess.run) -> str:
    """Install (or change) the background update. Returns a message."""
    if sys.platform != "darwin" and run is subprocess.run:
        return "Background updates are only available on macOS."
    path = agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}", run=run)  # an older one, if any
    with open(path, "wb") as handle:
        plistlib.dump(agent_plist(hours), handle)
    result = _launchctl("bootstrap", domain, str(path), run=run)
    if result.returncode != 0:
        return f"Couldn't start the background update: {(result.stderr or result.stdout).strip()}"
    return f"Synclassic checks for new songs every {hours:g} hours in the background."


def disable(run=subprocess.run) -> str:
    path = agent_path()
    if sys.platform == "darwin" or run is not subprocess.run:
        _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}", run=run)
    path.unlink(missing_ok=True)
    return "Background updates are off."


def current_hours() -> float | None:
    """How often the background update runs (None: off)."""
    try:
        with open(agent_path(), "rb") as handle:
            return plistlib.load(handle).get("StartInterval", 0) / 3600 or None
    except (OSError, plistlib.InvalidFileException):
        return None


@contextlib.contextmanager
def update_lock():
    """Yields True when this process may update (nobody else is), False otherwise."""
    path = app_dir() / "update.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # not on a Mac/Linux: no sharing to worry about
            pass
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()  # releases the lock
