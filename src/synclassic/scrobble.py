"""The scrobble queue: plays logged on the iPod, waiting to be sent to Last.fm.

The iPod's "Play Counts" file only says, per song, how many times it was played since the last
sync and when it was last played. Each play becomes one queued scrobble: the last one at the
last-played time, earlier ones spaced back by the song's length (the usual iPod-scrobbler
method). The queue is saved on this computer, so plays are never lost when offline; it's filled
whenever Synclassic reads the Play Counts file (every sync or change), before they're merged.

Ratings: a song you rate 5 stars on the iPod is "loved" on Last.fm, and one you take down from
5 stars is un-loved (setting lastfm_love). They wait in their own queue the same way.
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
    new: list[dict] = []
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
            play = {
                "artist": song.artist, "track": song.title, "album": song.album,
                "album_artist": song.album_artist, "duration": length, "timestamp": stamp,
                "dbid": f"{song.dbid:016x}",
            }
            queue.append(play)
            new.append({**play, "genre": getattr(song, "genre", ""), "source": "ipod"})
            seen.add((f"{song.dbid:016x}", stamp))
            added += 1
    if added:
        queue.sort(key=lambda p: p["timestamp"])
        save_queue(queue)
        from synclassic import stats

        stats.record(new)
    return added


def loves_file():
    return app_dir() / "lastfm_loves.json"


def load_loves() -> list[dict]:
    try:
        return json.loads(loves_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_loves(loves: list[dict]) -> None:
    path = loves_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(loves, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, path)


FIVE_STARS = 100  # the iPod keeps ratings as stars × 20


def capture_loves(songs: list[itunesdb.SongView], entries) -> int:
    """Queue Last.fm loves/un-loves for ratings changed on the iPod (Play Counts entries, in
    song-list order, read before they're merged). Returns how many were queued."""
    from synclassic.config import load_settings

    if not load_settings().lastfm_love:
        return 0
    loves = load_loves()
    added = 0
    for song, entry in zip(songs, entries):
        if entry.rating is None or entry.rating > 100 or song.media_type not in (0, 1):
            continue
        if not song.artist or not song.title:
            continue
        before = song.chunk.u8(itunesdb.MHIT_RATING)
        if entry.rating == before:
            continue
        if entry.rating == FIVE_STARS:
            love = True
        elif before == FIVE_STARS:
            love = False
        else:
            continue
        loves = [x for x in loves if (x["artist"], x["track"]) != (song.artist, song.title)]  # the latest wins
        loves.append({"artist": song.artist, "track": song.title, "love": love, "dbid": f"{song.dbid:016x}"})
        added += 1
    if added:
        save_loves(loves)
    return added


def waiting() -> int:
    """Plays and loves waiting to be sent."""
    return len(load_queue()) + len(load_loves())


def _send_loves(client, result) -> None:
    from synclassic import lastfm

    loves = load_loves()
    keep = []
    for number, love in enumerate(loves):
        try:
            client.love(love["artist"], love["track"], love["love"])
            if love["love"]:
                result.loved += 1
            else:
                result.unloved += 1
        except lastfm.LastfmError as error:
            if error.code in (6, 7):  # Last.fm doesn't know the song: nothing more to do
                result.ignored += 1
                continue
            keep.extend(loves[number:])
            save_loves(keep)
            raise
    save_loves(keep)


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
    if load_loves():
        _send_loves(client, result)
    result.left = waiting()
    return result


def add_play(artist: str, track: str, album: str, album_artist: str, duration_s: int, timestamp: int) -> bool:
    """Queue one play heard in the app (not on the iPod). Returns False if it was already queued."""
    if not artist or not track:
        return False
    queue = load_queue()
    if any(p["timestamp"] == timestamp and p["track"] == track and p["artist"] == artist for p in queue):
        return False
    queue.append({"artist": artist, "track": track, "album": album, "album_artist": album_artist,
                  "duration": duration_s, "timestamp": timestamp, "dbid": "mac"})
    queue.sort(key=lambda p: p["timestamp"])
    save_queue(queue)
    from synclassic import stats

    stats.record([{**queue[-1], **{"artist": artist, "track": track, "album": album, "album_artist": album_artist,
                                   "duration": duration_s, "timestamp": timestamp, "source": "mac"}}])
    return True
