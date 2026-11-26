import time

import pytest

from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec, read_source
from synclassic.sources import spotify_public
from synclassic.sources.spotify_api import SpotifyClient


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        return self._body


class FakeSession:
    """Answers like the 2026 Web API: playlist songs live under items[].item."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.replace("https://api.spotify.com/v1", "")
        self.calls.append((path, params))
        offset = int((params or {}).get("offset", 0)) if params else 0
        key = path.split("?")[0]
        if "offset=" in path:
            offset = int(path.split("offset=")[1].split("&")[0])
        handler = self.routes.get(key)
        if handler is None:
            return FakeResponse(404, {"error": {"status": 404, "message": "Not found"}})
        return handler(offset)


def api_track(n):
    return {
        "type": "track",
        "id": f"{n:022d}",
        "name": f"Song {n}",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album", "artists": [{"name": "Artist"}], "release_date": "2021-05-01",
                  "images": [{"url": "small", "width": 64}, {"url": "big", "width": 640}]},
        "duration_ms": 180000 + n,
        "track_number": n,
        "disc_number": 1,
        "explicit": False,
        "external_ids": {"isrc": f"ISRC{n}"},
        "is_local": False,
    }


def paged(items, page_size=2, base="/x"):
    def handler(offset):
        chunk = items[offset : offset + page_size]
        nxt = f"https://api.spotify.com/v1{base}?offset={offset + page_size}&limit={page_size}" if offset + page_size < len(items) else None
        return FakeResponse(200, {"items": chunk, "next": nxt, "total": len(items)})

    return handler


def client(routes):
    token = {"access_token": "x", "refresh_token": "r", "client_id": "c", "expires_at": time.time() + 3600}
    return SpotifyClient(token, session=FakeSession(routes))


def test_owned_playlist_reads_all_pages_using_item_field():
    entries = [{"item": api_track(n)} for n in range(1, 6)]
    entries.append({"item": {"type": "episode", "id": "e", "name": "Podcast"}})
    entries.append({"item": None})  # removed from Spotify
    c = client({
        "/playlists/PL": lambda o: FakeResponse(200, {"name": "Mine", "images": [{"url": "cover"}]}),
        "/playlists/PL/items": paged(entries, base="/playlists/PL/items"),
    })
    playlist = c.playlist("PL")
    assert playlist.name == "Mine"
    assert [t.title for t in playlist.tracks] == [f"Song {n}" for n in range(1, 6)]
    first = playlist.tracks[0]
    assert (first.album, first.cover_url, first.isrc, first.track_number) == ("Album", "big", "ISRC1", 1)


def test_liked_songs():
    c = client({"/me/tracks": paged([{"added_at": "x", "track": api_track(n)} for n in range(1, 4)], base="/me/tracks")})
    liked = c.liked_songs()
    assert liked.name == "Liked Songs" and len(liked.tracks) == 3


def test_followed_playlist_falls_back_to_public_reader(monkeypatch):
    c = client({"/playlists/37i9dQZF1DX4o1oenSJRJd": lambda o: FakeResponse(403, {"error": {"status": 403, "message": "Forbidden"}})})
    public = SourcePlaylist("Someone's mix", [Track("A", ["B"])] * 100, truncated=True, note="x")
    monkeypatch.setattr(spotify_public, "read_public", lambda ref: public)
    result = read_source(SourceSpec("spotify", "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd"), c)
    assert result.name == "Someone's mix"
    assert "own or collaborate" in result.note


def test_quota_exceeded_is_reported():
    from synclassic.sources.spotify_api import SpotifyApiError

    c = client({"/me": lambda o: FakeResponse(429, {"error": {"status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED"}})})
    with pytest.raises(SpotifyApiError, match="quota"):
        c.me()
