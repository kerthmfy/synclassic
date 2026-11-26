"""The local library: which songs exist, where their files are, and every playlist's order.

Stored in one SQLite file. A song that appears in several playlists is stored (and
downloaded) once.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from synclassic.models import Track
from synclassic.sources import SourceSpec
from synclassic.textnorm import core_title, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,            -- the Track as JSON
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, downloaded, review, unmatched, failed, local
    video_id TEXT,
    match_score REAL,
    match_notes TEXT,
    file_path TEXT,
    error TEXT,
    manual INTEGER NOT NULL DEFAULT 0,  -- 1 when you chose the YouTube recording yourself
    added_at TEXT NOT NULL,
    downloaded_at TEXT
);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_selector TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    refreshed_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX IF NOT EXISTS playlist_tracks_track ON playlist_tracks(track_id);
"""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def track_key(track: Track) -> str:
    """Identity of a song, so the same song in two playlists is stored once."""
    if track.spotify_id:
        return f"spotify:{track.spotify_id}"
    if track.local_path:
        return f"file:{track.local_path}"
    artist = normalize(track.primary_artist)
    return f"text:{artist}|{core_title(track.title)}"


def track_to_json(track: Track) -> str:
    return json.dumps(track.__dict__, ensure_ascii=False)


def track_from_json(text: str) -> Track:
    raw = json.loads(text)
    known = set(Track.__dataclass_fields__)
    return Track(**{k: v for k, v in raw.items() if k in known})


@dataclass
class LibraryTrack:
    id: int
    key: str
    track: Track
    status: str
    video_id: str | None
    match_score: float | None
    match_notes: str | None
    file_path: str | None
    error: str | None
    manual: bool

    @property
    def has_file(self) -> bool:
        return bool(self.file_path) and pathlib.Path(self.file_path).is_file()


@dataclass
class LibraryPlaylist:
    id: int
    name: str
    spec: SourceSpec
    refreshed_at: str | None
    note: str | None


class Library:
    def __init__(self, path: pathlib.Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # The app reads while a background download writes, so wait for locks and use WAL.
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- playlists -------------------------------------------------------------------------

    def _playlist(self, row: sqlite3.Row) -> LibraryPlaylist:
        spec = SourceSpec(row["source_kind"], row["source_ref"], row["source_selector"])
        return LibraryPlaylist(row["id"], row["name"], spec, row["refreshed_at"], row["note"])

    def playlists(self) -> list[LibraryPlaylist]:
        rows = self.db.execute("SELECT * FROM playlists ORDER BY name COLLATE NOCASE").fetchall()
        return [self._playlist(r) for r in rows]

    def playlist_by_name(self, name: str) -> LibraryPlaylist | None:
        row = self.db.execute("SELECT * FROM playlists WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        return self._playlist(row) if row else None

    def playlist_by_spec(self, spec: SourceSpec) -> LibraryPlaylist | None:
        row = self.db.execute(
            "SELECT * FROM playlists WHERE source_kind = ? AND source_ref = ? AND source_selector = ?",
            (spec.kind, spec.ref, spec.selector),
        ).fetchone()
        return self._playlist(row) if row else None

    def unique_name(self, wanted: str, ignore_id: int | None = None) -> str:
        name = wanted.strip() or "Playlist"
        candidate, number = name, 2
        while True:
            row = self.db.execute("SELECT id FROM playlists WHERE name = ? COLLATE NOCASE", (candidate,)).fetchone()
            if not row or row["id"] == ignore_id:
                return candidate
            candidate = f"{name} ({number})"
            number += 1

    def add_playlist(self, name: str, spec: SourceSpec) -> LibraryPlaylist:
        existing = self.playlist_by_spec(spec)
        if existing:
            return existing
        name = self.unique_name(name)
        with self.db:
            self.db.execute(
                "INSERT INTO playlists (name, source_kind, source_ref, source_selector, added_at) VALUES (?, ?, ?, ?, ?)",
                (name, spec.kind, spec.ref, spec.selector, now()),
            )
        return self.playlist_by_name(name)  # type: ignore[return-value]

    def rename_playlist(self, playlist: LibraryPlaylist, new_name: str) -> str:
        name = self.unique_name(new_name, ignore_id=playlist.id)
        with self.db:
            self.db.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist.id))
        return name

    def remove_playlist(self, playlist: LibraryPlaylist) -> None:
        with self.db:
            self.db.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist.id,))
            self.db.execute("DELETE FROM playlists WHERE id = ?", (playlist.id,))

    def set_playlist_tracks(self, playlist: LibraryPlaylist, track_ids: Iterable[int], note: str = "") -> None:
        with self.db:
            self.db.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist.id,))
            self.db.executemany(
                "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (?, ?, ?)",
                [(playlist.id, position, track_id) for position, track_id in enumerate(track_ids)],
            )
            self.db.execute("UPDATE playlists SET refreshed_at = ?, note = ? WHERE id = ?", (now(), note, playlist.id))

    def playlist_track_ids(self, playlist: LibraryPlaylist) -> list[int]:
        rows = self.db.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist.id,)
        ).fetchall()
        return [r["track_id"] for r in rows]

    def playlist_tracks(self, playlist: LibraryPlaylist) -> list[LibraryTrack]:
        rows = self.db.execute(
            "SELECT t.* FROM playlist_tracks p JOIN tracks t ON t.id = p.track_id WHERE p.playlist_id = ? ORDER BY p.position",
            (playlist.id,),
        ).fetchall()
        return [self._track(r) for r in rows]

    # --- tracks ----------------------------------------------------------------------------

    def _track(self, row: sqlite3.Row) -> LibraryTrack:
        return LibraryTrack(
            id=row["id"],
            key=row["key"],
            track=track_from_json(row["data"]),
            status=row["status"],
            video_id=row["video_id"],
            match_score=row["match_score"],
            match_notes=row["match_notes"],
            file_path=row["file_path"],
            error=row["error"],
            manual=bool(row["manual"]),
        )

    def track(self, track_id: int) -> LibraryTrack | None:
        row = self.db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return self._track(row) if row else None

    def upsert_track(self, track: Track) -> int:
        """Add a song (or refresh its info) and return its id."""
        key = track_key(track)
        row = self.db.execute("SELECT id, data FROM tracks WHERE key = ?", (key,)).fetchone()
        with self.db:
            if row:
                merged = _merge(track_from_json(row["data"]), track)
                self.db.execute("UPDATE tracks SET data = ? WHERE id = ?", (track_to_json(merged), row["id"]))
                return row["id"]
            status = "local" if track.local_path else "pending"
            cursor = self.db.execute(
                "INSERT INTO tracks (key, data, status, file_path, added_at) VALUES (?, ?, ?, ?, ?)",
                (key, track_to_json(track), status, track.local_path, now()),
            )
            return int(cursor.lastrowid)

    def update_track(self, track_id: int, **fields) -> None:
        if "track" in fields:
            fields["data"] = track_to_json(fields.pop("track"))
        if "manual" in fields:
            fields["manual"] = int(bool(fields["manual"]))
        columns = ", ".join(f"{name} = ?" for name in fields)
        with self.db:
            self.db.execute(f"UPDATE tracks SET {columns} WHERE id = ?", (*fields.values(), track_id))

    def tracks_with_status(self, *statuses: str) -> list[LibraryTrack]:
        marks = ",".join("?" * len(statuses))
        rows = self.db.execute(f"SELECT * FROM tracks WHERE status IN ({marks}) ORDER BY id", statuses).fetchall()
        return [self._track(r) for r in rows]

    def tracks_in_playlists(self) -> list[LibraryTrack]:
        rows = self.db.execute(
            "SELECT DISTINCT t.* FROM tracks t JOIN playlist_tracks p ON p.track_id = t.id ORDER BY t.id"
        ).fetchall()
        return [self._track(r) for r in rows]

    def orphan_tracks(self) -> list[LibraryTrack]:
        """Songs no playlist uses any more."""
        rows = self.db.execute(
            "SELECT * FROM tracks WHERE id NOT IN (SELECT track_id FROM playlist_tracks) ORDER BY id"
        ).fetchall()
        return [self._track(r) for r in rows]

    def delete_track(self, track_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    def counts(self, playlist: LibraryPlaylist) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT t.status, COUNT(*) AS n FROM playlist_tracks p JOIN tracks t ON t.id = p.track_id "
            "WHERE p.playlist_id = ? GROUP BY t.status",
            (playlist.id,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def _merge(old: Track, new: Track) -> Track:
    """Keep the richest info: new values win, but don't lose fields the new source lacks."""
    merged = Track(**old.__dict__)
    for name, value in new.__dict__.items():
        if value not in (None, "", []):
            setattr(merged, name, value)
    return merged
