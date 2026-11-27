"""The scrobble queue: plays logged on the iPod, waiting to be sent to Last.fm.

The iPod's "Play Counts" file only says, per song, how many times it was played since the last
sync and when it was last played. Each play becomes one queued scrobble: the last one at the
last-played time, earlier ones spaced back by the song's length (the usual iPod-scrobbler
method). The queue is saved on this computer, so plays are never lost when offline; it's filled
whenever Synclassic reads the Play Counts file (every sync or change), before they're merged.
"""

from __future__ import annotations

import datetime
import json
import os
import time

from synclassic.config import app_dir
from synclassic.ipod import itunesdb

MIN_LENGTH_S = 30
MAX_AGE_S = 14 * 24 * 3600  # Last.fm ignores plays older than two weeks
MAC_EPOCH = datetime.datetime(1904, 1, 1)


def queue_file():
    return app_dir() / "scrobble_queue.json"


def load_queue() -> list[dict]:
    try:
        return json.loads(queue_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_queue(plays: list[dict]) -> None:
    path = queue_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plays, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, path)


def mac_local_to_unix(value: int) -> int | None:
    """iPod time (local time, seconds since 1904) -> Unix time."""
    if not value or value == 0xFFFFFFFF:
        return None
    local = MAC_EPOCH + datetime.timedelta(seconds=value)
    return int(time.mktime(local.timetuple()))


def capture(songs: list[itunesdb.SongView], entries) -> int:
    """Queue the plays from Play Counts entries (in song-list order). Returns how many were queued."""
    queue = load_queue()
    seen = {(p.get("dbid"), p["timestamp"]) for p in queue}
    added = 0
    for song, entry in zip(songs, entries):
        if not entry.plays or song.media_type not in (0, 1):  # music only
            continue
        last = mac_local_to_unix(entry.last_played)
        if last is None or not song.artist or not song.title:
            continue
        length = max(1, round(song.length_ms / 1000))
        for n in range(entry.plays):
            stamp = last - n * length
            if (f"{song.dbid:016x}", stamp) in seen:
                continue
            queue.append({
                "artist": song.artist, "track": song.title, "album": song.album,
                "album_artist": song.album_artist, "duration": length, "timestamp": stamp,
                "dbid": f"{song.dbid:016x}",
            })
            seen.add((f"{song.dbid:016x}", stamp))
            added += 1
    if added:
        queue.sort(key=lambda p: p["timestamp"])
        save_queue(queue)
    return added


def waiting() -> int:
    return len(load_queue())


def submit(client=None, now: float | None = None):
    """Send the queue to Last.fm. Plays Last.fm accepts or permanently refuses leave the queue;
    if a request fails (offline, Last.fm down), the rest stay for next time."""
    from synclassic import lastfm

    client = client or lastfm.Client.from_saved()
    now = time.time() if now is None else now
    queue = load_queue()
    result = lastfm.ScrobbleResult(reasons={})
    keep: list[dict] = []
    sendable = []
    for play in queue:
        if play.get("duration") and play["duration"] < MIN_LENGTH_S:
            result.ignored += 1
            result.reasons["shorter than 30 seconds"] = result.reasons.get("shorter than 30 seconds", 0) + 1
        elif now - play["timestamp"] > MAX_AGE_S:
            result.too_old += 1
        else:
            sendable.append(play)
    for start in range(0, len(sendable), lastfm.BATCH):
        batch = sendable[start : start + lastfm.BATCH]
        try:
            accepted, ignored = client.scrobble(batch)
        except lastfm.LastfmError:
            keep.extend(sendable[start:])  # this batch and the rest: try again later
            save_queue(keep)
            result.left = len(keep)
            raise
        result.accepted += accepted
        for _play, reason in ignored:
            result.ignored += 1
            result.reasons[reason] = result.reasons.get(reason, 0) + 1
    save_queue(keep)
    result.left = len(keep)
    return result
