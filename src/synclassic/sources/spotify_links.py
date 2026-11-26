"""Recognise Spotify links and URIs."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ID = r"[A-Za-z0-9]{22}"
_URL = re.compile(
    rf"^https?://open\.spotify\.com/(?:intl-[a-z]{{2}}(?:-[a-z]{{2}})?/)?(?:embed/)?(?:user/[^/]+/)?"
    rf"(?P<kind>playlist|album|track)/(?P<id>{_ID})",
    re.IGNORECASE,
)
_URI = re.compile(rf"^spotify:(?:user:[^:]+:)?(?P<kind>playlist|album|track):(?P<id>{_ID})$", re.IGNORECASE)


@dataclass(frozen=True)
class SpotifyRef:
    kind: str  # "playlist", "album" or "track"
    id: str

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/{self.kind}/{self.id}"


def parse_spotify_ref(text: str) -> SpotifyRef | None:
    text = (text or "").strip()
    match = _URL.match(text) or _URI.match(text)
    if not match:
        return None
    return SpotifyRef(match.group("kind").lower(), match.group("id"))


def is_liked_songs(text: str) -> bool:
    norm = (text or "").strip().lower()
    if norm in {"liked", "liked songs", "liked-songs", "likedsongs"}:
        return True
    return "open.spotify.com/collection/tracks" in norm or (norm.startswith("spotify:") and norm.endswith(":collection"))
