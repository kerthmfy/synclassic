"""Turn what the user typed (a link, a file, "liked") into playlists."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

from synclassic.models import SourcePlaylist
from synclassic.sources import spotify_public
from synclassic.sources.csv_import import parse_csv
from synclassic.sources.m3u import parse_m3u
from synclassic.sources.spotify_api import SpotifyApiError, SpotifyClient
from synclassic.sources.spotify_links import SpotifyRef, is_liked_songs, parse_spotify_ref


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceSpec:
    kind: str  # "spotify", "liked", "m3u" or "csv"
    ref: str  # Spotify URL, or an absolute file path
    selector: str = ""  # CSV only: which playlist inside the file

    def describe(self) -> str:
        if self.kind == "liked":
            return "Spotify Liked Songs"
        if self.kind == "spotify":
            return self.ref
        return f"{self.ref}" + (f" [{self.selector}]" if self.selector else "")


def specs_from_input(text: str) -> list[SourceSpec]:
    text = text.strip()
    if is_liked_songs(text):
        return [SourceSpec("liked", "liked")]
    ref = parse_spotify_ref(text)
    if ref:
        return [SourceSpec("spotify", ref.url)]
    if text.startswith(("http://", "https://")):
        if "spotify.link" in text or "spotify.app.link" in text:
            raise SourceError("Short spotify.link links aren't supported yet. Use 'Copy link' in Spotify's Share menu.")
        raise SourceError(f"Not a Spotify playlist, album or track link: {text}")
    path = pathlib.Path(text).expanduser()
    if not path.is_file():
        raise SourceError(f"No such file, and not a Spotify link: {text}")
    path = path.resolve()
    suffix = path.suffix.lower()
    if suffix in (".m3u", ".m3u8"):
        return [SourceSpec("m3u", str(path))]
    if suffix in (".csv", ".tsv"):
        playlists = parse_csv(path)
        if not playlists:
            raise SourceError(f"No songs found in {path.name}. Is it a playlist export?")
        if len(playlists) == 1:
            return [SourceSpec("csv", str(path))]
        return [SourceSpec("csv", str(path), playlist.name) for playlist in playlists]
    raise SourceError(f"Unsupported file type '{suffix}'. Use .m3u8, .m3u or .csv")


def read_source(spec: SourceSpec, client: SpotifyClient | None) -> SourcePlaylist:
    try:
        if spec.kind == "liked":
            if not client:
                raise SourceError("Liked Songs needs a Spotify login. Run 'synclassic login' first.")
            return client.liked_songs()
        if spec.kind == "spotify":
            ref = parse_spotify_ref(spec.ref)
            if not ref:
                raise SourceError(f"Bad Spotify link: {spec.ref}")
            return _read_spotify(ref, client)
        if spec.kind == "m3u":
            path = pathlib.Path(spec.ref)
            if not path.is_file():
                raise SourceError(f"Playlist file is gone: {path}")
            playlist = parse_m3u(path)
            _fill_spotify_only_tracks(playlist)
            return playlist
        if spec.kind == "csv":
            path = pathlib.Path(spec.ref)
            if not path.is_file():
                raise SourceError(f"CSV file is gone: {path}")
            playlists = parse_csv(path)
            if spec.selector:
                for playlist in playlists:
                    if playlist.name == spec.selector:
                        return playlist
                raise SourceError(f"Playlist '{spec.selector}' is no longer in {path.name}")
            if not playlists:
                raise SourceError(f"No songs found in {path.name}")
            return playlists[0]
    except (SpotifyApiError, spotify_public.SpotifyPublicError) as error:
        raise SourceError(str(error)) from error
    raise SourceError(f"Unknown source type {spec.kind}")


def _read_spotify(ref: SpotifyRef, client: SpotifyClient | None) -> SourcePlaylist:
    if client:
        try:
            if ref.kind == "playlist":
                return client.playlist(ref.id)
            if ref.kind == "album":
                return client.album(ref.id)
            return client.track(ref.id)
        except SpotifyApiError as error:
            # Since Feb 2026, personal Spotify apps can only read the songs of playlists you own
            # or collaborate on. Anything else goes through the public reader.
            if error.status not in (401, 403, 404):
                raise
    playlist = spotify_public.read_public(ref)
    if client and ref.kind == "playlist" and playlist.truncated:
        playlist.note = (
            f"Spotify only lets personal apps read playlists you own or collaborate on, so only the first "
            f"{spotify_public.EMBED_LIMIT} songs of this playlist could be read. Tip: in Spotify, add the songs "
            "to a playlist of your own and use that instead."
        )
    return playlist


def _fill_spotify_only_tracks(playlist: SourcePlaylist) -> None:
    """M3U entries that are only a Spotify track link need their title/artist looked up."""
    for index, track in enumerate(playlist.tracks):
        if track.spotify_id and not track.title:
            try:
                info = spotify_public.read_public(SpotifyRef("track", track.spotify_id)).tracks[0]
            except (spotify_public.SpotifyPublicError, IndexError):
                continue
            playlist.tracks[index] = info
    playlist.tracks = [t for t in playlist.tracks if t.title]
