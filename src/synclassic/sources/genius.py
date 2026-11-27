"""Genius-style playlists: a song plus 24 that go well with it, from Last.fm's "similar tracks".

Like the iTunes 8 Genius button. The playlist is refreshed with Update like other playlists
(it changes as Last.fm's listeners do), downloads like them, and becomes an iPod playlist.
"""

from __future__ import annotations

from synclassic.models import SourcePlaylist, Track
from synclassic.textnorm import normalize, split_artists

KIND = "genius"
SIZE = 25
PER_ARTIST = 3


class GeniusError(RuntimeError):
    pass


def ref_for(artist: str, title: str) -> str:
    return f"lastfm:similar:{artist}|{title}"


def seed_of(ref: str) -> tuple[str, str]:
    artist, title = ref.split("lastfm:similar:", 1)[1].split("|", 1)
    return artist, title


def playlist_name(title: str) -> str:
    return f"Genius: {title}"


def similar(artist: str, title: str, client=None, limit: int = 60) -> list[Track]:
    """Songs Last.fm's listeners play with this one, best first."""
    from synclassic import lastfm

    if client is None:
        key, secret = lastfm.app_key()
        try:
            client = lastfm.Client(key, secret)
        except lastfm.LastfmError as error:
            raise GeniusError(str(error)) from error
    main = (split_artists(artist) or [artist])[0]
    try:
        body = client.call("track.getSimilar", {"artist": main, "track": title, "limit": limit, "autocorrect": 1},
                           signed=False)
    except lastfm.LastfmError as error:
        raise GeniusError(f"Last.fm: {error}") from error
    items = (body.get("similartracks") or {}).get("track") or []
    if isinstance(items, dict):
        items = [items]
    tracks = []
    for item in items:
        name = item.get("name") or ""
        who = (item.get("artist") or {}).get("name") or ""
        if name and who:
            seconds = int(item.get("duration") or 0)
            tracks.append(Track(name, split_artists(who) or [who], duration_ms=seconds * 1000 or None))
    return tracks


def build(artist: str, title: str, client=None) -> SourcePlaylist:
    """The seed song and up to 24 similar ones, at most 3 per artist."""
    seed = Track(title, split_artists(artist) or [artist])
    chosen, per_artist = [seed], {normalize(seed.primary_artist): 1}
    seen = {(normalize(seed.primary_artist), normalize(title))}
    for track in similar(artist, title, client):
        key = (normalize(track.primary_artist), normalize(track.title))
        count = per_artist.get(key[0], 0)
        if key in seen or count >= PER_ARTIST:
            continue
        chosen.append(track)
        seen.add(key)
        per_artist[key[0]] = count + 1
        if len(chosen) >= SIZE:
            break
    if len(chosen) == 1:
        raise GeniusError(f"Last.fm doesn't know enough songs like “{title}” to make a Genius playlist.")
    return SourcePlaylist(playlist_name(title), chosen)


def make(library, artist: str, title: str, client=None):
    """Create (or refresh) the Genius playlist for a song. Returns the library playlist."""
    from synclassic.sources import SourceSpec
    from synclassic.update import apply_source

    source = build(artist, title, client)
    spec = SourceSpec(KIND, ref_for(artist, title))
    playlist = library.playlist_by_spec(spec) or library.add_playlist(library.unique_name(source.name), spec)
    apply_source(library, playlist, source)
    return playlist
