"""A report for Help → Report a Problem (and `synclassic report`): what someone helping you would
need, and nothing private.

It has: versions (Synclassic, macOS, Python, the libraries), where ffmpeg and deno are, the
settings (your Spotify client ID only as "set"), whether Spotify and Last.fm are connected
(not who you are), how many playlists and songs there are by state, each connected iPod's model,
firmware, format and check result (not its serial number or name), the last error, and the end
of the activity log.

Everything goes through redact(): tokens, keys, passwords, session ids, e-mail addresses and
your home folder's name are replaced. Nothing is sent anywhere: you copy or save the report and
decide where it goes.
"""

from __future__ import annotations

import dataclasses
import pathlib
import platform
import re
import subprocess
import sys

from synclassic import __version__

_SECRET_WORDS = r"(?:access_token|refresh_token|token|secret|password|passwd|api_key|apikey|api_sig|session_key|" \
                r"sk|client_id|client_secret|authorization|cookie|code_verifier)"
_PATTERNS = [
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer •••"),
    (re.compile(rf"(?i)([\"']?\b{_SECRET_WORDS}[\"']?\s*[:=]\s*[\"']?)([^\s\"'&,;}}]+)"), r"\1•••"),
    (re.compile(rf"(?i)([?&](?:{_SECRET_WORDS[3:-1]}|code|state)=)([^&\s]+)"), r"\1•••"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "•••@•••"),
]
LOG_LINES = 300


def redact(text: str) -> str:
    """The text without secrets, e-mail addresses or your home folder's name."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    home = str(pathlib.Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    return text


def _versions() -> list[str]:
    lines = [f"Synclassic {__version__} ({'standalone app' if getattr(sys, 'frozen', False) else 'from source'})",
             f"macOS {platform.mac_ver()[0] or '-'} / {platform.platform()} ({platform.machine()})",
             f"Python {platform.python_version()}"]
    from importlib import metadata

    for name in ("PySide6", "yt-dlp", "ytmusicapi", "mutagen", "requests", "Pillow", "numpy"):
        try:
            lines.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            lines.append(f"{name}: not installed")
    return lines


def _tools(run=subprocess.run) -> list[str]:
    from synclassic import tools

    lines = []
    for name in ("ffmpeg", "deno"):
        where = tools.where(name)
        version = ""
        if where != "missing":
            try:
                flag = "-version" if name == "ffmpeg" else "--version"
                out = run([name, flag], capture_output=True, text=True, timeout=10).stdout
                version = (out.splitlines() or [""])[0]
            except (OSError, subprocess.SubprocessError):
                version = "(didn't answer)"
        lines.append(f"{name}: {where} {version}".rstrip())
    return lines


def _settings() -> list[str]:
    from synclassic.config import load_settings

    settings = dataclasses.asdict(load_settings())
    settings["spotify_client_id"] = "set" if settings.get("spotify_client_id") else "not set"
    return [f"{key} = {value}" for key, value in sorted(settings.items())]


def _accounts() -> list[str]:
    lines = []
    try:
        from synclassic.sources import spotify_auth

        token = spotify_auth.load_token()
        scope = str(token.get("scope", "")) if token else ""
        lines.append(f"Spotify: {'logged in' if token else 'not logged in'}" + (f" (permissions: {scope})" if scope else ""))
    except Exception as error:
        lines.append(f"Spotify: couldn't tell ({error.__class__.__name__})")
    try:
        from synclassic import lastfm, otg_spotify, scrobble

        lines.append(f"Last.fm: {'connected' if lastfm.connected_user() else 'not connected'}; "
                     f"{scrobble.waiting()} plays/loves waiting; {otg_spotify.waiting()} On-The-Go playlists waiting")
    except Exception as error:
        lines.append(f"Last.fm: couldn't tell ({error.__class__.__name__})")
    return lines


def _library() -> list[str]:
    from synclassic.config import app_dir
    from synclassic.library import Library

    path = app_dir() / "library.db"
    if not path.is_file():
        return ["No library yet"]
    library = Library(path)
    try:
        playlists = library.playlists()
        kinds: dict[str, int] = {}
        for playlist in playlists:
            kinds[playlist.spec.kind] = kinds.get(playlist.spec.kind, 0) + 1
        states: dict[str, int] = {}
        missing = 0
        for item in library.tracks_in_playlists():
            states[item.status] = states.get(item.status, 0) + 1
            if item.status in ("downloaded", "review", "local") and not item.has_file:
                missing += 1
        return [f"{len(playlists)} playlists (" + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ")",
                "Songs: " + ", ".join(f"{n} {s}" for s, n in sorted(states.items())),
                f"Songs whose file is missing: {missing}"]
    finally:
        library.close()


def _ipods() -> list[str]:
    from synclassic.ipod import prefs, rockbox
    from synclassic.ipod.device import find_ipods

    try:
        ipods = find_ipods()
    except OSError as error:
        return [f"Couldn't look for iPods: {error}"]
    if not ipods:
        return ["No iPod connected"]
    lines = []
    for number, ipod in enumerate(ipods, start=1):
        total, free = ipod.disk_usage()
        choice = prefs.load(ipod)
        lines.append(f"iPod {number}: {ipod.model}, firmware {ipod.firmware or '?'}, {ipod.filesystem}, "
                     f"{total / 1000**3:.0f} GB ({free / 1000**3:.1f} GB free), synced for {rockbox.mode(ipod)}, "
                     f"{'all playlists' if choice.all_playlists else 'chosen playlists'}, "
                     f"size {choice.bitrate or 'as they are'}")
        if ipod.database_path.is_file():
            try:
                from synclassic.ipod.check import check

                report = check(ipod)
                lines.append(f"  check: {'safe to manage' if report.safe_to_manage else 'NOT safe'}; "
                             f"{report.songs} songs; database {report.signature or '-'}; "
                             f"rebuilds exactly: {report.round_trip_ok}; artwork: {report.artwork_images} images"
                             + (f"; problems: {'; '.join(report.problems)}" if report.problems else ""))
            except Exception as error:
                lines.append(f"  check failed: {error}")
    return lines


def _log(activity: str | None) -> list[str]:
    lines = []
    log_file = pathlib.Path.home() / "Library" / "Logs" / "Synclassic.log"
    if log_file.is_file():
        try:
            tail = log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:]
            lines += ["--- Synclassic.log (end) ---", *tail]
        except OSError:
            pass
    if activity:
        lines += ["--- Activity (in the window) ---", *activity.splitlines()[-LOG_LINES:]]
    return lines


def collect(description: str = "", activity: str | None = None) -> str:
    """The whole report, secrets removed."""
    from synclassic import errors

    sections = [("What happened", [description.strip() or "(not described)"]),
                ("Versions", _versions), ("Tools", _tools), ("Settings", _settings), ("Accounts", _accounts),
                ("Library", _library), ("iPods", _ipods)]
    last = errors.last()
    if last:
        sections.append(("Last error", [f"{last.get('at', '')}: {last.get('message', '')}",
                                        *last.get("details", "").splitlines()]))
    sections.append(("Log", lambda: _log(activity)))
    out = []
    for title, part in sections:
        out.append(f"== {title} ==")
        try:
            out.extend(part() if callable(part) else part)
        except Exception as error:  # one part failing doesn't stop the report
            out.append(f"(couldn't collect: {error.__class__.__name__}: {error})")
        out.append("")
    return redact("\n".join(out))


def issue_link(summary: str) -> str:
    """A pre-filled 'new issue' page on GitHub (the report itself is pasted or attached by you)."""
    import urllib.parse

    title = (summary.strip().splitlines() or ["Problem"])[0][:80] or "Problem"
    body = ("What happened:\n\n" + summary.strip()[:1500] + "\n\n(Paste or attach the report from "
            "Help → Report a Problem here.)\n\nSynclassic " + __version__)
    return ("https://github.com/kerthmfy/synclassic/issues/new?" +
            urllib.parse.urlencode({"title": title, "body": redact(body)}))
