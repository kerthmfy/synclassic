"""Spotify Web API reads for a logged-in user.

Written for the February 2026 Web API rules for personal ("Development Mode") apps:
- playlist songs come from GET /playlists/{id}/items, and each entry's song is under "item";
- songs are only returned for playlists the user owns or collaborates on; other playlists
  fall back to the public (no-login) reader;
- Liked Songs come from GET /me/tracks.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import requests

from synclassic.artwork import upgrade_cover_url
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import spotify_auth

API = "https://api.spotify.com/v1"


class SpotifyApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SpotifyClient:
    def __init__(self, token: dict, session: requests.Session | None = None):
        self.token = token
        self.http = session or requests.Session()

    @classmethod
    def from_saved_login(cls) -> "SpotifyClient | None":
        token = spotify_auth.valid_token()
        return cls(token) if token else None

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body=body)

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = path if path.startswith("https://") else API + path
        for attempt in range(5):
            if time.time() >= self.token.get("expires_at", 0):
                self.token = spotify_auth.refresh(self.token)
            headers = {"Authorization": f"Bearer {self.token['access_token']}"}
            if method == "GET":
                response = self.http.get(url, params=params, headers=headers, timeout=30)
            else:
                response = self.http.post(url, json=body, headers=headers, timeout=30)
            if response.status_code == 401 and attempt == 0:
                self.token = spotify_auth.refresh(self.token)
                continue
            if response.status_code == 429:
                reason = _error_reason(response)
                if reason == "QUOTA_EXCEEDED":
                    raise SpotifyApiError("Spotify's API quota for your developer app is used up. Try again later.", 429)
                time.sleep(min(int(response.headers.get("Retry-After", "5") or 5), 60))
                continue
            if response.status_code >= 500 and attempt < 4:
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code not in (200, 201):
                raise SpotifyApiError(f"Spotify API {response.status_code}: {_error_message(response)}", response.status_code)
            try:
                return response.json()
            except ValueError:
                return {}
        raise SpotifyApiError("Spotify API kept failing; try again later.")

    def _pages(self, path: str, params: dict | None = None) -> Iterator[dict]:
        page = self.get(path, {"limit": 50, **(params or {})})
        while True:
            yield from page.get("items") or []
            if not page.get("next"):
                return
            page = self.get(page["next"])

    def me(self) -> dict:
        return self.get("/me")

    # --- making playlists (On-The-Go playlists from the iPod) -------------------------------------

    def create_playlist(self, name: str, description: str = "") -> str:
        """A new private playlist in your account. Returns its id."""
        body = {"name": name, "description": description, "public": False}
        try:
            made = self.post("/me/playlists", body)
        except SpotifyApiError as error:
            if error.status not in (403, 404, 405):
                raise
            made = self.post(f"/users/{self.me()['id']}/playlists", body)  # the older address
        return made["id"]

    def add_to_playlist(self, playlist_id: str, uris: list[str]) -> None:
        for start in range(0, len(uris), 100):
            batch = {"uris": uris[start:start + 100]}
            try:
                self.post(f"/playlists/{playlist_id}/items", batch)
            except SpotifyApiError as error:
                if error.status not in (404, 405):
                    raise
                self.post(f"/playlists/{playlist_id}/tracks", batch)  # the older address

    def find_track(self, artist: str, title: str, duration_ms: int | None = None) -> str | None:
        """The Spotify id of this song (same artist and title, length within 5 s), or None."""
        from synclassic.textnorm import core_title, normalize, split_artists

        first_artist = (split_artists(artist) or [artist])[0]
        query = f"track:{title} artist:{first_artist}"
        found = self.get("/search", {"q": query, "type": "track", "limit": 10})
        wanted_title, wanted_artist = core_title(title), normalize(first_artist)
        best = None
        for song in (found.get("tracks") or {}).get("items") or []:
            if not song or core_title(song.get("name", "")) != wanted_title:
                continue
            if wanted_artist not in {normalize(a.get("name", "")) for a in song.get("artists") or []}:
                continue
            difference = abs((song.get("duration_ms") or 0) - duration_ms) if duration_ms else 0
            if difference <= 5000 and (best is None or difference < best[0]):
                best = (difference, song["id"])
        return best[1] if best else None

    def my_playlists(self) -> list[dict]:
        """Playlists the user owns or follows: id, name, owner, total, collaborative."""
        result = []
        for item in self._pages("/me/playlists"):
            if not item:
                continue
            counts = item.get("items") or item.get("tracks") or {}
            result.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name") or "",
                    "owner_id": (item.get("owner") or {}).get("id"),
                    "owner_name": (item.get("owner") or {}).get("display_name") or "",
                    "collaborative": bool(item.get("collaborative")),
                    "total": counts.get("total"),
                    "url": (item.get("external_urls") or {}).get("spotify"),
                }
            )
        return result

    def playlist(self, playlist_id: str) -> SourcePlaylist:
        meta = self.get(f"/playlists/{playlist_id}", {"fields": "name,images,owner(id),collaborative"})
        tracks: list[Track] = []
        for entry in self._pages(f"/playlists/{playlist_id}/items", {"additional_types": "track"}):
            song = entry.get("item") or entry.get("track")
            track = track_from_api(song, local_ok=True)
            if track:
                tracks.append(track)
        images = meta.get("images") or []
        return SourcePlaylist(name=meta.get("name") or "Spotify playlist", tracks=tracks, cover_url=images[0]["url"] if images else None)

    def liked_songs(self) -> SourcePlaylist:
        tracks = [t for t in (track_from_api(entry.get("track") or entry.get("item")) for entry in self._pages("/me/tracks")) if t]
        return SourcePlaylist(name="Liked Songs", tracks=tracks)

    def album(self, album_id: str) -> SourcePlaylist:
        album = self.get(f"/albums/{album_id}")
        tracks: list[Track] = []
        for song in self._pages(f"/albums/{album_id}/tracks"):
            song = dict(song)
            song["album"] = album  # album tracks come back without their album
            track = track_from_api(song)
            if track:
                tracks.append(track)
        images = album.get("images") or []
        return SourcePlaylist(name=album.get("name") or "Album", tracks=tracks, cover_url=images[0]["url"] if images else None)

    def track(self, track_id: str) -> SourcePlaylist:
        track = track_from_api(self.get(f"/tracks/{track_id}"))
        if not track:
            raise SpotifyApiError("That track isn't available.")
        return SourcePlaylist(name=f"{track.primary_artist} - {track.title}", tracks=[track])


def track_from_api(song: dict | None, local_ok: bool = False) -> Track | None:
    """Convert a Spotify track object. Returns None for episodes and removed songs."""
    if not song or song.get("type", "track") != "track":
        return None
    if song.get("is_local") and not local_ok:
        return None
    album = song.get("album") or {}
    images = album.get("images") or []
    album_artists = [a.get("name") for a in album.get("artists") or [] if a.get("name")]
    return Track(
        title=song.get("name") or "",
        artists=[a.get("name") for a in song.get("artists") or [] if a.get("name")],
        album=album.get("name") or "",
        album_artist=album_artists[0] if album_artists else "",
        duration_ms=song.get("duration_ms"),
        spotify_id=None if song.get("is_local") else song.get("id"),
        isrc=(song.get("external_ids") or {}).get("isrc"),
        track_number=song.get("track_number"),
        disc_number=song.get("disc_number"),
        release_date=album.get("release_date") or "",
        explicit=song.get("explicit"),
        cover_url=upgrade_cover_url(max(images, key=lambda i: i.get("width") or 0)["url"]) if images else None,
    )


def _error_body(response: requests.Response) -> dict:
    try:
        return (response.json() or {}).get("error") or {}
    except ValueError:
        return {}


def _error_reason(response: requests.Response) -> str:
    body = _error_body(response)
    return body.get("reason", "") if isinstance(body, dict) else ""


def _error_message(response: requests.Response) -> str:
    body = _error_body(response)
    if isinstance(body, dict) and body.get("message"):
        return body["message"]
    return response.text[:200]
