"""Read Spotify's regular public playlist page (no login).

It contains the playlist's real song count and the first ~30 songs with album names and
cover art. Synclassic uses it to know whether the 100-song embed reader missed songs, and to
fill in album names for those first songs.
"""

from __future__ import annotations

import base64
import html
import json
import re

import requests

from synclassic.artwork import upgrade_cover_url
from synclassic.models import Track
from synclassic.sources.spotify_links import SpotifyRef

_STATE = re.compile(r'<script[^>]+id=["\']initialState["\'][^>]*>(.*?)</script>', re.S | re.I)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class PageInfo:
    def __init__(self, name: str, total: int | None, tracks: list[Track]):
        self.name = name
        self.total = total
        self.tracks = tracks


def fetch_page_info(ref: SpotifyRef, session: requests.Session | None = None) -> PageInfo | None:
    """None when the page can't be read (that's fine: it's only extra information)."""
    http = session or requests
    try:
        response = http.get(
            ref.url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}, timeout=30
        )
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    return parse_page(response.text, ref)


def parse_page(page: str, ref: SpotifyRef) -> PageInfo | None:
    match = _STATE.search(page)
    if not match:
        return None
    try:
        state = json.loads(base64.b64decode(html.unescape(match.group(1).strip())))
        entity = state["entities"]["items"][f"spotify:{ref.kind}:{ref.id}"]
    except (ValueError, KeyError, TypeError):
        return None
    content = entity.get("content") or entity.get("tracksV2") or {}
    tracks = []
    for item in content.get("items") or []:
        data = ((item or {}).get("itemV2") or {}).get("data") or (item or {}).get("track") or {}
        track = _track(data)
        if track:
            tracks.append(track)
    total = content.get("totalCount")
    return PageInfo(entity.get("name") or "", total if isinstance(total, int) else None, tracks)


def _track(data: dict) -> Track | None:
    uri = data.get("uri") or ""
    if not uri.startswith("spotify:track:") or not data.get("name"):
        return None
    album = data.get("albumOfTrack") or {}
    sources = ((album.get("coverArt") or {}).get("sources")) or []
    cover = max(sources, key=lambda s: s.get("width") or 0)["url"] if sources else None
    artists = [((a or {}).get("profile") or {}).get("name") for a in (data.get("artists") or {}).get("items") or []]
    return Track(
        title=data["name"],
        artists=[a for a in artists if a],
        album=album.get("name") or "",
        duration_ms=((data.get("duration") or data.get("trackDuration") or {}).get("totalMilliseconds")),
        spotify_id=uri.rsplit(":", 1)[1],
        track_number=data.get("trackNumber"),
        disc_number=data.get("discNumber"),
        explicit=((data.get("contentRating") or {}).get("label") == "EXPLICIT") or None,
        cover_url=upgrade_cover_url(cover),
    )
