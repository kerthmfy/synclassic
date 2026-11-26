"""Import playlists exported as CSV (Exportify, TuneMyMusic and similar tools)."""

from __future__ import annotations

import csv
import pathlib

from synclassic.models import SourcePlaylist, Track
from synclassic.sources.spotify_links import parse_spotify_ref
from synclassic.textnorm import split_artists

# Accepted column names (lowercased) for each field, in order of preference.
COLUMNS = {
    "title": ["track name", "title", "name", "song", "track"],
    "artists": ["artist name(s)", "artist name", "artists", "artist"],
    "album": ["album name", "album"],
    "album_artist": ["album artist name(s)", "album artist"],
    "duration_ms": ["track duration (ms)", "duration (ms)", "duration_ms", "duration"],
    "spotify": ["track uri", "spotify - id", "spotify id", "spotify uri", "uri", "spotify url"],
    "isrc": ["isrc"],
    "track_number": ["track number"],
    "disc_number": ["disc number"],
    "release_date": ["album release date", "release date"],
    "explicit": ["explicit"],
    "playlist": ["playlist name", "playlist"],
}


def _pick(row: dict[str, str], field: str) -> str:
    for name in COLUMNS[field]:
        value = row.get(name)
        if value:
            return value.strip()
    return ""


def _int(text: str) -> int | None:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _artists(text: str) -> list[str]:
    # Exportify separates multiple artists with ";" in newer exports and "," in older ones.
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    return split_artists(text)


def parse_csv(path: str | pathlib.Path) -> list[SourcePlaylist]:
    """One SourcePlaylist per playlist in the file (TuneMyMusic can export several at once)."""
    path = pathlib.Path(path).expanduser().resolve()
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(raw.splitlines(), dialect=dialect)
    groups: dict[str, list[Track]] = {}
    for original in reader:
        row = {(k or "").strip().lower(): (v or "") for k, v in original.items()}
        title = _pick(row, "title")
        if not title:
            continue
        spotify_id = None
        spotify = _pick(row, "spotify")
        if spotify:
            ref = parse_spotify_ref(spotify)
            if ref and ref.kind == "track":
                spotify_id = ref.id
            elif len(spotify) == 22 and spotify.isalnum():
                spotify_id = spotify
        duration = _int(_pick(row, "duration_ms"))
        if duration is not None and duration < 10_000:  # given in seconds
            duration *= 1000
        explicit_text = _pick(row, "explicit").lower()
        album_artists = _artists(_pick(row, "album_artist"))
        groups.setdefault(_pick(row, "playlist") or path.stem, []).append(
            Track(
                title=title,
                artists=_artists(_pick(row, "artists")),
                album=_pick(row, "album"),
                album_artist=album_artists[0] if album_artists else "",
                duration_ms=duration,
                spotify_id=spotify_id,
                isrc=_pick(row, "isrc") or None,
                track_number=_int(_pick(row, "track_number")),
                disc_number=_int(_pick(row, "disc_number")),
                release_date=_pick(row, "release_date"),
                explicit=explicit_text in ("true", "1", "yes") if explicit_text else None,
            )
        )
    return [SourcePlaylist(name=name, tracks=tracks) for name, tracks in groups.items()]
