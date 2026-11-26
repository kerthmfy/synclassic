"""Plain data types shared by every part of Synclassic."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Track:
    """One song as described by a source (Spotify, a CSV row, an M3U entry)."""

    title: str
    artists: list[str]
    album: str = ""
    album_artist: str = ""
    duration_ms: int | None = None
    spotify_id: str | None = None
    isrc: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    release_date: str = ""
    explicit: bool | None = None
    cover_url: str | None = None
    # Set when the song already exists as a file on this computer (M3U imports).
    local_path: str | None = None

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def year(self) -> str:
        return self.release_date[:4] if self.release_date else ""

    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artists else self.title


@dataclass
class SourcePlaylist:
    """A playlist as read from its source, in source order."""

    name: str
    tracks: list[Track] = field(default_factory=list)
    cover_url: str | None = None
    # True when the source could only return part of the playlist.
    truncated: bool = False
    note: str = ""
