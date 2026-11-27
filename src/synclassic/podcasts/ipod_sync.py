"""Podcasts in the iPod sync: which episodes go on the iPod, which come off, and played state back.

- The newest unplayed episodes each show keeps are put on the iPod as real podcasts (Podcasts
  menu, grouped by show, "new" dot, resume position, skipped by shuffle).
- Episodes already on the iPod (e.g. put there by another app) are recognised by their
  download link and reused.
- An episode played on the iPod is marked played here, and makes way for newer ones.
- Only episodes Synclassic put on the iPod are ever removed.
What Synclassic manages is remembered in the iPod's state.json under "episodes".
"""

from __future__ import annotations

import datetime
import pathlib
from dataclasses import dataclass, field

from synclassic.ipod import itunesdb, playcounts
from synclassic.ipod.edit import Editor, NewSong
from synclassic.library import Library
from synclassic.podcasts.store import Podcast, Podcasts, StoredEpisode


def episode_key(podcast: Podcast, episode: StoredEpisode) -> str:
    return f"{podcast.feed_url}|{episode.guid}"


@dataclass
class PodcastPlan:
    copy: list[tuple[Podcast, StoredEpisode]] = field(default_factory=list)
    reuse: dict[str, int] = field(default_factory=dict)  # episode key -> dbid already on the iPod
    remove: list[itunesdb.SongView] = field(default_factory=list)
    marked_played: int = 0
    not_downloaded: int = 0  # episodes that should go on but aren't downloaded yet
    bytes_to_copy: int = 0
    bytes_to_free: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.copy or self.remove)

    def summary(self) -> list[str]:
        lines = []
        if self.copy:
            lines.append(f"  Podcasts: add {len(self.copy)} episode{'s' if len(self.copy) != 1 else ''}")
        if self.remove:
            lines.append(f"  Podcasts: remove {len(self.remove)} played or older episode{'s' if len(self.remove) != 1 else ''}")
        if self.not_downloaded:
            lines.append(f"  Podcasts: {self.not_downloaded} episode{'s' if self.not_downloaded != 1 else ''} not "
                         "downloaded yet (Update Podcasts, or Update & Sync, gets them)")
        if self.marked_played:
            lines.append(f"  Podcasts: {self.marked_played} episode{'s' if self.marked_played != 1 else ''} played on the iPod")
        return lines


def _played_on_ipod(song: itunesdb.SongView, pending_plays: int) -> bool:
    return song.play_count > 0 or pending_plays > 0 or (len(song.chunk.header) > 0xB2 and song.chunk.u8(0xB2) == 1)


def plan(library: Library, ipod, db: itunesdb.Chunk, state: dict) -> PodcastPlan:
    store = Podcasts(library)
    result = PodcastPlan()
    known: dict = state.setdefault("episodes", {})
    songs = itunesdb.songs(db)
    by_dbid = {s.dbid: s for s in songs}
    by_url = {}
    for song in songs:
        url = itunesdb.texts(song.chunk).get(itunesdb.ENCLOSURE_URL)
        if url:
            by_url[url] = song

    # Plays the iPod logged since the last sync (not merged yet).
    pending: dict[int, int] = {}
    counts = ipod.control / "iTunes" / "Play Counts"
    if counts.is_file():
        try:
            for song, entry in zip(songs, playcounts.parse_play_counts(counts.read_bytes())):
                if entry.plays:
                    pending[song.dbid] = entry.plays
        except ValueError:
            pass

    podcasts = store.podcasts()
    # 1. Played on the iPod -> played here.
    for podcast in podcasts:
        for episode in store.episodes(podcast.id):
            entry = known.get(episode_key(podcast, episode))
            song = by_dbid.get(int(entry["dbid"], 16)) if entry else by_url.get(episode.url)
            if song is not None and not episode.played and _played_on_ipod(song, pending.get(song.dbid, 0)):
                store.set_played(episode.id, True)
                result.marked_played += 1

    # 2. What each show wants on the iPod.
    wanted_keys = set()
    for podcast in podcasts:
        for episode in store.for_ipod(podcast):
            key = episode_key(podcast, episode)
            entry = known.get(key)
            if entry and int(entry["dbid"], 16) in by_dbid:
                wanted_keys.add(key)
                continue
            if episode.url in by_url:
                result.reuse[key] = by_url[episode.url].dbid
                wanted_keys.add(key)
                continue
            path = pathlib.Path(episode.file_path) if episode.downloaded else None  # type: ignore[arg-type]
            if path is not None and path.is_file():
                result.copy.append((podcast, episode))
                result.bytes_to_copy += path.stat().st_size
                wanted_keys.add(key)
            else:
                result.not_downloaded += 1

    # 3. Episodes Synclassic added that nothing wants any more.
    for key, entry in known.items():
        song = by_dbid.get(int(entry["dbid"], 16))
        if song is not None and entry.get("added") and key not in wanted_keys:
            result.remove.append(song)
            result.bytes_to_free += song.size
    return result


def episode_song(podcast: Podcast, episode: StoredEpisode, path: pathlib.Path, location: str) -> NewSong:
    from mutagen import File as MutagenFile

    try:
        info = getattr(MutagenFile(path), "info", None)
    except Exception:
        info = None
    extension = path.suffix.lower()
    filetype = {".mp3": "mp3", ".wav": "wav"}.get(extension, "m4a")
    released = int(episode.published.timestamp()) if episode.published else 0
    return NewSong(
        location=location, filetype=filetype, size=path.stat().st_size,
        length_ms=int((getattr(info, "length", 0) or episode.duration_s) * 1000),
        bitrate=int((getattr(info, "bitrate", 0) or 0) / 1000), sample_rate=int(getattr(info, "sample_rate", 0) or 44100),
        vbr=False, title=episode.title, artist=podcast.author or podcast.title, album=podcast.title,
        album_artist=podcast.author, genre="Podcast",
        year=episode.published.year if episode.published else 0, track_number=episode.number,
        podcast=True, video=extension in (".mp4", ".m4v", ".mov"), enclosure_url=episode.url,
        rss_url=podcast.feed_url, description=episode.description, category=podcast.category,
        released_unix=released,
    )


def add_episode(editor: Editor, podcast: Podcast, episode: StoredEpisode, target: pathlib.Path, ipod_root: pathlib.Path) -> int:
    """List a copied episode file on the iPod as a podcast. Returns its dbid."""
    location = ":" + target.relative_to(ipod_root).as_posix().replace("/", ":")
    song_id, dbid = editor.add_song(episode_song(podcast, episode, target, location))
    editor.add_podcast_episode(song_id, dbid, podcast.title)
    return dbid


def remember(state: dict, podcast_plan: PodcastPlan, added: dict[str, int]) -> None:
    known = state.setdefault("episodes", {})
    removed = {song.dbid for song in podcast_plan.remove}
    for key, entry in list(known.items()):
        if int(entry["dbid"], 16) in removed:
            del known[key]
    for key, dbid in podcast_plan.reuse.items():
        known[key] = {"dbid": f"{dbid:016x}", "added": False}
    for key, dbid in added.items():
        known[key] = {"dbid": f"{dbid:016x}", "added": True}
    state["last_podcast_sync"] = datetime.datetime.now().isoformat(timespec="seconds")
