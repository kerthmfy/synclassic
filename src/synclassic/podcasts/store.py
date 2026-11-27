"""Podcast subscriptions and episodes, kept in the library database; downloading episodes."""

from __future__ import annotations

import datetime
import pathlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

import requests

from synclassic.library import Library, now
from synclassic.podcasts.feed import USER_AGENT, Feed, FeedError, fetch
from synclassic.textnorm import safe_filename

SCHEMA = """
CREATE TABLE IF NOT EXISTS podcasts (
    id INTEGER PRIMARY KEY,
    feed_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    artwork_url TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    keep_on_ipod INTEGER NOT NULL DEFAULT 3,  -- newest unplayed episodes to keep (0 = none)
    added_at TEXT NOT NULL,
    refreshed_at TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    guid TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published TEXT,
    duration_s INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    mime TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    number INTEGER NOT NULL DEFAULT 0,
    file_path TEXT,
    status TEXT NOT NULL DEFAULT 'new',  -- new, downloaded, failed
    played INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE (podcast_id, guid)
);
"""

Say = Callable[[str], None]


@dataclass
class Podcast:
    id: int
    feed_url: str
    title: str
    author: str
    description: str
    artwork_url: str
    category: str
    keep_on_ipod: int
    refreshed_at: str | None


@dataclass
class StoredEpisode:
    id: int
    podcast_id: int
    guid: str
    title: str
    url: str
    published: datetime.datetime | None
    duration_s: int
    size: int
    mime: str
    description: str
    number: int
    file_path: str | None
    status: str
    played: bool
    error: str | None

    @property
    def downloaded(self) -> bool:
        return self.status == "downloaded" and bool(self.file_path) and pathlib.Path(self.file_path).is_file()


class Podcasts:
    def __init__(self, library: Library):
        self.db: sqlite3.Connection = library.db
        self.db.executescript(SCHEMA)

    # --- subscriptions ------------------------------------------------------------------------

    def podcasts(self) -> list[Podcast]:
        rows = self.db.execute("SELECT * FROM podcasts ORDER BY title COLLATE NOCASE").fetchall()
        return [self._podcast(r) for r in rows]

    def podcast(self, podcast_id: int) -> Podcast | None:
        row = self.db.execute("SELECT * FROM podcasts WHERE id = ?", (podcast_id,)).fetchone()
        return self._podcast(row) if row else None

    @staticmethod
    def _podcast(row) -> Podcast:
        return Podcast(row["id"], row["feed_url"], row["title"], row["author"], row["description"],
                       row["artwork_url"], row["category"], row["keep_on_ipod"], row["refreshed_at"])

    def subscribe(self, feed_url: str, session: requests.Session | None = None) -> Podcast:
        existing = self.db.execute("SELECT id FROM podcasts WHERE feed_url = ?", (feed_url,)).fetchone()
        if existing:
            return self.podcast(existing["id"])  # type: ignore[return-value]
        feed = fetch(feed_url, session)
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO podcasts (feed_url, title, author, description, artwork_url, category, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feed_url, feed.title, feed.author, feed.description, feed.artwork_url, feed.category, now()),
            )
        podcast_id = int(cursor.lastrowid)
        self._store_episodes(podcast_id, feed)
        return self.podcast(podcast_id)  # type: ignore[return-value]

    def unsubscribe(self, podcast_id: int, delete_files: bool = True) -> None:
        if delete_files:
            for episode in self.episodes(podcast_id):
                if episode.file_path:
                    pathlib.Path(episode.file_path).unlink(missing_ok=True)
        with self.db:
            self.db.execute("DELETE FROM episodes WHERE podcast_id = ?", (podcast_id,))
            self.db.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))

    def set_keep(self, podcast_id: int, keep: int) -> None:
        with self.db:
            self.db.execute("UPDATE podcasts SET keep_on_ipod = ? WHERE id = ?", (max(0, keep), podcast_id))

    def refresh(self, podcast: Podcast, session: requests.Session | None = None) -> int:
        """Read the feed again. Returns how many new episodes appeared."""
        feed = fetch(podcast.feed_url, session)
        with self.db:
            self.db.execute(
                "UPDATE podcasts SET title = ?, author = ?, description = ?, artwork_url = ?, category = ?, "
                "refreshed_at = ? WHERE id = ?",
                (feed.title, feed.author, feed.description, feed.artwork_url or podcast.artwork_url, feed.category,
                 now(), podcast.id),
            )
        return self._store_episodes(podcast.id, feed)

    def _store_episodes(self, podcast_id: int, feed: Feed) -> int:
        added = 0
        with self.db:
            for e in feed.episodes:
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO episodes (podcast_id, guid, title, url, published, duration_s, size, mime, "
                    "description, number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (podcast_id, e.guid, e.title, e.url, e.published.isoformat() if e.published else None,
                     e.duration_s, e.size, e.mime, e.description[:2000], e.number),
                )
                added += cursor.rowcount
        return added

    # --- episodes -----------------------------------------------------------------------------

    def episodes(self, podcast_id: int | None = None) -> list[StoredEpisode]:
        """Newest first."""
        if podcast_id is None:
            rows = self.db.execute("SELECT * FROM episodes ORDER BY published DESC").fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM episodes WHERE podcast_id = ? ORDER BY published DESC, id DESC", (podcast_id,)
            ).fetchall()
        return [self._episode(r) for r in rows]

    def episode(self, episode_id: int) -> StoredEpisode | None:
        row = self.db.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return self._episode(row) if row else None

    @staticmethod
    def _episode(row) -> StoredEpisode:
        published = datetime.datetime.fromisoformat(row["published"]) if row["published"] else None
        return StoredEpisode(row["id"], row["podcast_id"], row["guid"], row["title"], row["url"], published,
                             row["duration_s"], row["size"], row["mime"], row["description"], row["number"],
                             row["file_path"], row["status"], bool(row["played"]), row["error"])

    def set_played(self, episode_id: int, played: bool) -> None:
        with self.db:
            self.db.execute("UPDATE episodes SET played = ? WHERE id = ?", (int(played), episode_id))

    def remove_download(self, episode_id: int) -> None:
        episode = self.episode(episode_id)
        if episode and episode.file_path:
            pathlib.Path(episode.file_path).unlink(missing_ok=True)
        with self.db:
            self.db.execute("UPDATE episodes SET status = 'new', file_path = NULL WHERE id = ?", (episode_id,))

    def wanted(self, podcast: Podcast) -> list[StoredEpisode]:
        """The newest unplayed episodes this podcast keeps (on this Mac and on the iPod)."""
        if podcast.keep_on_ipod <= 0:
            return []
        return [e for e in self.episodes(podcast.id) if not e.played][: podcast.keep_on_ipod]

    def new_count(self) -> int:
        """Downloaded episodes not played yet (the badge in the source list)."""
        return self.db.execute(
            "SELECT COUNT(*) FROM episodes WHERE status = 'downloaded' AND played = 0"
        ).fetchone()[0]

    # --- downloading --------------------------------------------------------------------------

    def target_path(self, podcast: Podcast, episode: StoredEpisode, music_dir: pathlib.Path) -> pathlib.Path:
        suffix = pathlib.Path(episode.url.split("?")[0]).suffix.lower()
        if suffix not in (".mp3", ".m4a", ".m4b", ".aac", ".mp4", ".m4v", ".mov", ".wav"):
            suffix = {"audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "video/mp4": ".mp4",
                      "audio/aac": ".aac"}.get(episode.mime, ".mp3")
        date = episode.published.strftime("%Y-%m-%d") if episode.published else "undated"
        name = safe_filename(f"{date} {episode.title}", 110) + suffix
        return self.show_folder(podcast, music_dir) / name

    def show_folder(self, podcast: Podcast, music_dir: pathlib.Path) -> pathlib.Path:
        return music_dir / "Podcasts" / safe_filename(podcast.title, 100)

    def cover_path(self, podcast: Podcast, music_dir: pathlib.Path) -> pathlib.Path:
        return self.show_folder(podcast, music_dir) / "cover.jpg"

    def fetch_cover(self, podcast: Podcast, music_dir: pathlib.Path, session: requests.Session | None = None) -> pathlib.Path | None:
        """The show's artwork, downloaded once (it goes on the iPod with episodes that have none)."""
        path = self.cover_path(podcast, music_dir)
        if path.is_file():
            return path
        if not podcast.artwork_url:
            return None
        try:
            response = (session or requests).get(podcast.artwork_url, timeout=30, headers={"User-Agent": USER_AGENT})
        except requests.RequestException:
            return None
        if response.status_code != 200 or not response.content:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        return path

    def download(self, episode: StoredEpisode, music_dir: pathlib.Path, session: requests.Session | None = None,
                 progress: Callable[[int, int], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> pathlib.Path:
        podcast = self.podcast(episode.podcast_id)
        if podcast is None:
            raise FeedError("That podcast was unsubscribed.")
        target = self.target_path(podcast, episode, music_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        http = session or requests
        try:
            with http.get(episode.url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as response:
                if response.status_code != 200:
                    raise FeedError(f"The episode's file answered HTTP {response.status_code}.")
                total = int(response.headers.get("Content-Length") or episode.size or 0)
                done = 0
                with open(part, "wb") as handle:
                    for chunk in response.iter_content(256 * 1024):
                        if should_stop and should_stop():
                            raise FeedError("Stopped.")
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except (requests.RequestException, FeedError, OSError) as error:
            part.unlink(missing_ok=True)
            message = str(error) if isinstance(error, FeedError) else f"Download failed: {error}"
            with self.db:
                self.db.execute("UPDATE episodes SET status = 'failed', error = ? WHERE id = ?", (message, episode.id))
            raise FeedError(message) from error
        part.replace(target)
        with self.db:
            self.db.execute("UPDATE episodes SET status = 'downloaded', file_path = ?, error = NULL WHERE id = ?",
                            (str(target), episode.id))
        return target

    def update_all(self, music_dir: pathlib.Path, say: Say, session: requests.Session | None = None,
                   should_stop: Callable[[], bool] | None = None,
                   progress: Callable[[int, int, str], None] | None = None,
                   only: list[int] | None = None) -> tuple[int, int]:
        """Refresh every feed (or those in `only`), download wanted episodes, delete downloads no
        longer wanted. Returns (new episodes, downloaded)."""
        new = downloaded = 0
        shows = [p for p in self.podcasts() if only is None or p.id in only]
        for podcast in shows:
            if should_stop and should_stop():
                break
            try:
                found = self.refresh(podcast, session)
            except FeedError as error:
                say(f"{podcast.title}: {error}")
                continue
            if found:
                say(f"{podcast.title}: {found} new episode{'s' if found != 1 else ''}")
            self.fetch_cover(podcast, music_dir, session)
            new += found
        shows = [p for p in self.podcasts() if only is None or p.id in only]
        todo = [(p, e) for p in shows for e in self.wanted(p) if not e.downloaded]
        for number, (podcast, episode) in enumerate(todo, start=1):
            if should_stop and should_stop():
                break
            if progress:
                progress(number, len(todo), f"{podcast.title}: {episode.title}")
            say(f"Downloading {podcast.title}: {episode.title}")
            try:
                self.download(episode, music_dir, session, should_stop=should_stop)
                downloaded += 1
            except FeedError as error:
                say(f"    {error}")
        # Played (or older) episodes beyond what each show keeps: free the space.
        for podcast in shows:
            keep = {e.id for e in self.wanted(podcast)}
            for episode in self.episodes(podcast.id):
                if episode.downloaded and episode.id not in keep and episode.played:
                    self.remove_download(episode.id)
        return new, downloaded
