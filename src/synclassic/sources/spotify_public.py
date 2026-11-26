"""Read public Spotify playlists, albums and tracks without logging in.

This uses the data embedded in Spotify's public "embed" player page. It needs no account,
but Spotify only includes the first 100 songs of a playlist there, and it does not include
album names (Synclassic fills those in from YouTube Music when it finds the song).
"""

from __future__ import annotations

import json
import re

import requests

from synclassic.artwork import upgrade_cover_url
from synclassic.models import SourcePlaylist, Track
from synclassic.sources.spotify_links import SpotifyRef

EMBED_LIMIT = 100
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


class SpotifyPublicError(RuntimeError):
    pass


def fetch_embed_entity(ref: SpotifyRef, session: requests.Session | None = None) -> dict:
    http = session or requests
    url = f"https://open.spotify.com/embed/{ref.kind}/{ref.id}"
    try:
        response = http.get(url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "en"}, timeout=30)
    except requests.RequestException as error:
        raise SpotifyPublicError(f"Couldn't reach Spotify: {error}") from error
    if response.status_code == 404:
        raise SpotifyPublicError("Spotify says this link doesn't exist (or the playlist is private).")
    if response.status_code != 200:
        raise SpotifyPublicError(f"Spotify returned HTTP {response.status_code} for {url}")
    return parse_embed_html(response.text)


def parse_embed_html(html: str) -> dict:
    match = _NEXT_DATA.search(html)
    if not match:
        raise SpotifyPublicError("Spotify's page format changed: no embedded data found.")
    try:
        data = json.loads(match.group(1))
        return data["props"]["pageProps"]["state"]["data"]["entity"]
    except (ValueError, KeyError, TypeError) as error:
        raise SpotifyPublicError("Spotify's page format changed: couldn't read the embedded data.") from error


def _artists_from_subtitle(subtitle: str) -> list[str]:
    # The embed joins artists with ", " (comma + no-break space). Splitting only on that
    # keeps names like "Tyler, The Creator" intact.
    parts = [part.strip() for part in (subtitle or "").split(", ")]
    return [part for part in parts if part]


def _track_id(uri: str) -> str | None:
    if uri and uri.startswith("spotify:track:"):
        return uri.rsplit(":", 1)[1]
    return None


def _best_image(entity: dict) -> str | None:
    images = ((entity.get("visualIdentity") or {}).get("image")) or []
    images = [img for img in images if img.get("url")]
    if images:
        return upgrade_cover_url(max(images, key=lambda img: img.get("maxWidth") or 0)["url"])
    sources = ((entity.get("coverArt") or {}).get("sources")) or []
    return upgrade_cover_url(sources[0]["url"]) if sources else None


def entity_to_playlist(entity: dict) -> SourcePlaylist:
    kind = entity.get("type")
    name = entity.get("name") or entity.get("title") or "Spotify"
    cover = _best_image(entity)
    release = ((entity.get("releaseDate") or {}).get("isoString") or "")[:10]

    if kind == "track":
        artists = [a.get("name", "") for a in entity.get("artists") or [] if a.get("name")]
        track = Track(
            title=name,
            artists=artists,
            duration_ms=entity.get("duration"),
            spotify_id=entity.get("id") or _track_id(entity.get("uri", "")),
            release_date=release,
            explicit=entity.get("isExplicit"),
            cover_url=cover,
        )
        return SourcePlaylist(name=f"{track.primary_artist} - {track.title}", tracks=[track], cover_url=cover)

    tracks: list[Track] = []
    album_artist = entity.get("subtitle", "") if kind == "album" else ""
    for number, item in enumerate(entity.get("trackList") or [], start=1):
        if item.get("entityType", "track") != "track":
            continue  # podcast episodes etc.
        track = Track(
            title=item.get("title") or "",
            artists=_artists_from_subtitle(item.get("subtitle", "")),
            duration_ms=item.get("duration"),
            spotify_id=_track_id(item.get("uri", "")),
            explicit=item.get("isExplicit"),
        )
        if kind == "album":
            track.album = name
            track.album_artist = _artists_from_subtitle(album_artist)[0] if album_artist else ""
            track.track_number = number
            track.release_date = release
            track.cover_url = cover
        if track.title:
            tracks.append(track)

    truncated = kind == "playlist" and len(entity.get("trackList") or []) >= EMBED_LIMIT
    note = ""
    if truncated:
        note = (
            f"Spotify only shares the first {EMBED_LIMIT} songs of a playlist without logging in. "
            "Run 'synclassic login' to read the whole playlist (works for playlists you own or collaborate on)."
        )
    return SourcePlaylist(name=name, tracks=tracks, cover_url=cover, truncated=truncated, note=note)


def read_public(ref: SpotifyRef, session: requests.Session | None = None) -> SourcePlaylist:
    return entity_to_playlist(fetch_embed_entity(ref, session))


def track_cover_url(track_id: str) -> str | None:
    """The 640 px album cover of one song, from Spotify's public song page."""
    try:
        entity = fetch_embed_entity(SpotifyRef("track", track_id))
    except SpotifyPublicError:
        return None
    return _best_image(entity)
