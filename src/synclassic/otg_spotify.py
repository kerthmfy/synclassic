"""On-The-Go playlists you make on the iPod, copied to your Spotify account.

At each sync, On-The-Go playlists become iPod playlists ("On-The-Go 2026-11-28"). With this on
(setting otg_to_spotify) and Spotify logged in, they're also made as private playlists in your
Spotify account, with the same songs in the same order. Songs Synclassic got from Spotify are
added exactly; others are looked up on Spotify by artist, title and length (songs Spotify doesn't
have are left out, and the result says how many).

The playlists wait in otg_to_spotify.json until they're made, so being offline loses nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from synclassic.config import app_dir

PERMISSION = "playlist-modify-private"


def queue_file():
    return app_dir() / "otg_to_spotify.json"


def load() -> list[dict]:
    try:
        return json.loads(queue_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save(items: list[dict]) -> None:
    path = queue_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def waiting() -> int:
    return len(load())


def wanted() -> bool:
    """On, and logged in to Spotify."""
    from synclassic.config import load_settings
    from synclassic.sources import spotify_auth

    return load_settings().otg_to_spotify and spotify_auth.load_token() is not None


def capture(name: str, songs, spotify_ids: dict[int, str] | None = None) -> bool:
    """Queue one On-The-Go playlist (iPod songs in order; spotify_ids: dbid -> Spotify track id)."""
    if not wanted():
        return False
    spotify_ids = spotify_ids or {}
    entries = [{"artist": s.artist, "title": s.title, "album": s.album, "length_ms": s.length_ms,
                "spotify_id": spotify_ids.get(s.dbid)} for s in songs if s.title]
    if not entries:
        return False
    queue = load()
    queue.append({"name": name, "songs": entries})
    save(queue)
    return True


def has_permission(token: dict | None) -> bool:
    return bool(token) and PERMISSION in str(token.get("scope", ""))  # type: ignore[union-attr]


@dataclass
class SendResult:
    made: list[str] = field(default_factory=list)
    missing: int = 0  # songs Spotify doesn't have

    def describe(self) -> str:
        if not self.made:
            return "No On-The-Go playlists to send to Spotify."
        text = f"Made {len(self.made)} playlist{'s' if len(self.made) != 1 else ''} on Spotify: " + ", ".join(self.made)
        if self.missing:
            text += f" ({self.missing} song{'s' if self.missing != 1 else ''} Spotify doesn't have were left out)"
        return text


class NeedsLogin(RuntimeError):
    pass


def send(client=None) -> SendResult:
    """Make the waiting playlists on Spotify. A playlist that fails stays queued (with the rest)."""
    from synclassic.sources.spotify_api import SpotifyClient

    result = SendResult()
    queue = load()
    if not queue:
        return result
    client = client or SpotifyClient.from_saved_login()
    if client is None:
        raise NeedsLogin("Log in to Spotify first (Settings → Spotify).")
    if not has_permission(client.token):
        raise NeedsLogin("To make playlists in your Spotify account, Synclassic needs one more permission: "
                         "log in to Spotify again (Settings → Spotify → Log In).")
    while queue:
        playlist = queue[0]
        ids = []
        for song in playlist["songs"]:
            track_id = song.get("spotify_id") or client.find_track(song["artist"], song["title"], song.get("length_ms"))
            if track_id:
                ids.append(track_id)
            else:
                result.missing += 1
        playlist_id = playlist.get("made_id")
        if not playlist_id:
            playlist_id = client.create_playlist(playlist["name"], "Made on my iPod (On-The-Go), copied by Synclassic.")
            playlist["made_id"] = playlist_id  # if adding the songs fails, the next try doesn't make it twice
            save(queue)
        client.add_to_playlist(playlist_id, [f"spotify:track:{i}" for i in ids])
        result.made.append(playlist["name"])
        queue.pop(0)
        save(queue)
    return result
