"""Turn what the user typed (a link, a file, "liked") into playlists."""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from dataclasses import dataclass

from synclassic.models import SourcePlaylist
from synclassic.sources import spotify_page, spotify_public
from synclassic.sources.csv_import import parse_csv
from synclassic.sources.m3u import parse_m3u
from synclassic.sources.spotify_api import SpotifyApiError, SpotifyClient
from synclassic.sources.spotify_links import SpotifyRef, is_liked_songs, parse_spotify_ref


class SourceError(RuntimeError):
    pass


# Reads a whole public playlist from Spotify's web page: (link, expected song count, name) -> playlist.
# Provided by the app/terminal when the built-in browser is available (see sources/full_reader.py).
FullReader = Callable[[SpotifyRef, "int | None", str], "SourcePlaylist | None"]


@dataclass(frozen=True)
class SourceSpec:
    kind: str  # "spotify", "liked", "m3u", "csv", "folder", or "store-album" / "store-songs" (Find Music)
    ref: str  # Spotify URL, or an absolute file or folder path
    selector: str = ""  # CSV only: which playlist inside the file

    def describe(self) -> str:
        if self.kind == "liked":
            return "Spotify Liked Songs"
        if self.kind == "genius":
            artist, title = self.ref.split("lastfm:similar:", 1)[1].split("|", 1)
            return f"Genius: songs like {artist} - {title} (Last.fm)"
        if self.kind == "store-album":
            return "Album from Find Music"
        if self.kind == "store-songs":
            return "Songs you got in Find Music"
        if self.kind == "spotify":
            return self.ref
        if self.kind == "folder":
            return f"Your music folder: {self.ref}"
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
    if path.is_dir():
        return [SourceSpec("folder", str(path.resolve()))]
    if not path.is_file():
        raise SourceError(f"No such file or folder, and not a Spotify link: {text}")
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


def read_source(spec: SourceSpec, client: SpotifyClient | None, full_reader: FullReader | None = None) -> SourcePlaylist:
    try:
        if spec.kind == "liked":
            if not client:
                raise SourceError("Liked Songs needs a Spotify login. Run 'synclassic login' first.")
            return client.liked_songs()
        if spec.kind == "spotify":
            ref = parse_spotify_ref(spec.ref)
            if not ref:
                raise SourceError(f"Bad Spotify link: {spec.ref}")
            return _read_spotify(ref, client, full_reader)
        if spec.kind == "genius":
            from synclassic.sources import genius

            try:
                return genius.build(*genius.seed_of(spec.ref))
            except genius.GeniusError as error:
                raise SourceError(str(error)) from error
        if spec.kind == "store-album":
            from synclassic.config import load_settings
            from synclassic.sources import store

            try:
                return store.read_album(spec.ref, load_settings().store_country)
            except store.StoreError as error:
                raise SourceError(str(error)) from error
        if spec.kind == "folder":
            from synclassic.sources.folder import parse_folder

            if not pathlib.Path(spec.ref).is_dir():
                raise SourceError(f"The music folder isn't there (moved, or its drive isn't connected): {spec.ref}")
            return parse_folder(spec.ref)
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


def _read_spotify(ref: SpotifyRef, client: SpotifyClient | None, full_reader: FullReader | None) -> SourcePlaylist:
    if client:
        try:
            if ref.kind == "playlist":
                return client.playlist(ref.id)
            if ref.kind == "album":
                return client.album(ref.id)
            return client.track(ref.id)
        except SpotifyApiError as error:
            # Since Feb 2026, personal Spotify apps can only read the songs of playlists you own
            # or collaborate on. Anything else goes through the public readers.
            if error.status not in (401, 403, 404):
                raise
    playlist = spotify_public.read_public(ref)
    if ref.kind != "playlist":
        return playlist

    # The regular page tells us the real song count, and has album names for the first songs.
    page = spotify_page.fetch_page_info(ref)
    total = page.total if page and page.total else None
    missing = total is not None and total > len(playlist.tracks)
    if missing or (total is None and playlist.truncated):
        full = full_reader(ref, total, playlist.name) if full_reader else None
        if full and len(full.tracks) > len(playlist.tracks):
            full.name = playlist.name or full.name
            full.cover_url = full.cover_url or playlist.cover_url
            return full
        playlist.truncated = True
        count = f"{len(playlist.tracks)} of {total}" if total else f"the first {len(playlist.tracks)}"
        if full_reader:
            playlist.note = (
                f"Only {count} songs could be read: Spotify's web page couldn't be read in full this time. "
                "Try updating again later."
            )
        elif client:
            playlist.note = (
                f"Only {count} songs could be read. Spotify only lets personal apps read playlists you own or "
                "collaborate on; turn on the full-playlist reader in Settings, or copy the songs into a "
                "playlist of your own."
            )
        else:
            playlist.note = (
                f"Only {count} songs could be read without logging in. Turn on the full-playlist reader in "
                "Settings, or log in to read playlists you own."
            )
    else:
        playlist.truncated = False
        playlist.note = ""
    if page:
        _add_album_info(playlist, page.tracks)
    return playlist


def _add_album_info(playlist: SourcePlaylist, richer: list) -> None:
    """The embed has no album names; copy them from the page for the songs it covers."""
    by_id = {t.spotify_id: t for t in richer if t.spotify_id}
    for track in playlist.tracks:
        extra = by_id.get(track.spotify_id)
        if not extra:
            continue
        track.album = track.album or extra.album
        track.cover_url = track.cover_url or extra.cover_url
        track.track_number = track.track_number or extra.track_number
        track.disc_number = track.disc_number or extra.disc_number


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
