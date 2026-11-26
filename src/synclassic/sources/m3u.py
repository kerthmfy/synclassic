"""Import .m3u / .m3u8 playlists.

Each entry can be:
- a song file that exists on this computer: used as-is, nothing is downloaded;
- a missing file or a plain "Artist - Title" entry: Synclassic searches and downloads it;
- a Spotify track link/URI: read from Spotify, then downloaded.
"""

from __future__ import annotations

import pathlib
import re
import urllib.parse

from synclassic.models import SourcePlaylist, Track
from synclassic.textnorm import split_artists

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".alac", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus", ".m4b"}
_EXTINF = re.compile(r"^#EXTINF:\s*(-?\d+(?:\.\d+)?)?[^,]*,(.*)$", re.IGNORECASE)


def _read_text(path: pathlib.Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_display(text: str) -> tuple[list[str], str]:
    """'Artist - Title' -> (['Artist'], 'Title'). Without a dash, it's all title."""
    text = text.strip()
    if " - " in text:
        artist, title = text.split(" - ", 1)
        return split_artists(artist) or [artist.strip()], title.strip()
    return [], text


def _resolve_location(line: str, base: pathlib.Path) -> pathlib.Path | None:
    if line.lower().startswith("file://"):
        return pathlib.Path(urllib.parse.unquote(urllib.parse.urlparse(line).path))
    if re.match(r"^[a-z][a-z0-9+.-]*://", line, re.IGNORECASE):
        return None  # some other URL
    path = pathlib.Path(line.replace("\\", "/")).expanduser()
    return path if path.is_absolute() else (base / path)


def parse_m3u(path: str | pathlib.Path) -> SourcePlaylist:
    from synclassic.sources.spotify_links import parse_spotify_ref

    path = pathlib.Path(path).expanduser().resolve()
    base = path.parent
    name = path.stem
    tracks: list[Track] = []
    pending_info: tuple[int | None, list[str], str] | None = None
    album = ""
    album_artist = ""

    for raw in _read_text(path).splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#PLAYLIST:"):
            name = line.split(":", 1)[1].strip() or name
            continue
        if upper.startswith("#EXTALB:"):
            album = line.split(":", 1)[1].strip()
            continue
        if upper.startswith("#EXTART:"):
            album_artist = line.split(":", 1)[1].strip()
            continue
        match = _EXTINF.match(line)
        if match:
            seconds = match.group(1)
            duration = int(float(seconds) * 1000) if seconds and float(seconds) > 0 else None
            artists, title = _split_display(match.group(2))
            pending_info = (duration, artists, title)
            continue
        if line.startswith("#"):
            continue

        duration, artists, title = pending_info or (None, [], "")
        pending_info = None
        track: Track | None = None

        ref = parse_spotify_ref(line)
        if ref and ref.kind == "track":
            track = Track(title=title or "", artists=artists, duration_ms=duration, spotify_id=ref.id)
        else:
            location = _resolve_location(line, base)
            if location is not None and location.suffix.lower() in AUDIO_EXTENSIONS:
                if not title:
                    artists, title = _split_display(location.stem)
                track = Track(title=title, artists=artists, duration_ms=duration)
                if location.is_file():
                    track.local_path = str(location)
            elif title:
                track = Track(title=title, artists=artists, duration_ms=duration)
            elif location is None:
                continue
            else:
                artists, title = _split_display(line)
                track = Track(title=title, artists=artists, duration_ms=duration)

        if track:
            track.album = track.album or album
            track.album_artist = track.album_artist or album_artist
            album = album_artist = ""
            tracks.append(track)

    return SourcePlaylist(name=name, tracks=tracks)
