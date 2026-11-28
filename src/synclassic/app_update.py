"""Is there a newer Synclassic? (Help → Check for Updates, and once a day by itself.)

New versions are published as GitHub releases of kerthmfy/synclassic (a version tag such as
v0.3.0 builds the .dmg and makes the release; see .github/workflows/mac-app.yml). Synclassic
only reads the latest release's version, notes and download link; it never installs anything by
itself: the .dmg opens in your browser to download, or (running from the source folder) it says
to git pull.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass

import requests

from synclassic import __version__
from synclassic.config import app_dir

REPOSITORY = "kerthmfy/synclassic"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
CHECK_EVERY_S = 24 * 3600


class UpdateError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    page: str
    download: str  # the .dmg, or the release page when there isn't one
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3); pre-release suffixes are ignored."""
    numbers = re.findall(r"\d+", (text or "").lstrip("vV").split("-")[0])
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def latest(get=None) -> Release | None:
    """The newest release, or None when there are none yet. Network problems raise UpdateError."""
    get = get or (lambda url: requests.get(url, timeout=15, headers={"Accept": "application/vnd.github+json",
                                                                       "User-Agent": f"Synclassic/{__version__}"}))
    try:
        response = get(LATEST)
    except requests.RequestException as error:
        raise UpdateError(f"Couldn't reach GitHub: {error}") from error
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise UpdateError(f"GitHub answered HTTP {response.status_code}; try again later.")
    data = response.json()
    if data.get("draft") or data.get("prerelease"):
        return None
    dmg = next((a.get("browser_download_url") for a in data.get("assets") or []
                if str(a.get("name", "")).lower().endswith(".dmg")), None)
    page = data.get("html_url") or RELEASES_PAGE
    return Release(str(data.get("tag_name") or "").lstrip("vV"), page, dmg or page, (data.get("body") or "").strip())


def standalone() -> bool:
    return bool(getattr(sys, "frozen", False))


def how_to_get(release: Release) -> str:
    if standalone():
        return "Download the new Synclassic, open it, and drag it to Applications (replacing this one)."
    return ("You run Synclassic from its folder: in Terminal, cd synclassic, then git pull, then "
            "pip install -e . (in the .venv).")


# --- once a day -----------------------------------------------------------------------------------


def _state_file():
    return app_dir() / "update-check.json"


def _state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _state_file().write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def due(now: float | None = None) -> bool:
    return (now or time.time()) - float(_state().get("checked_at", 0)) >= CHECK_EVERY_S


def check_now(get=None, now: float | None = None) -> Release | None:
    """The newer release worth mentioning (not one you skipped), and remember that we checked."""
    release = latest(get)
    state = _state()
    state["checked_at"] = now or time.time()
    _save_state(state)
    if release and is_newer(release.version) and release.version != state.get("skipped"):
        return release
    return None


def skip(version: str) -> None:
    state = _state()
    state["skipped"] = version
    _save_state(state)
