"""Last.fm scrobbling, against a simulated Last.fm that checks signatures like the real one."""

import hashlib
import os
import stat
import struct
import time
from types import SimpleNamespace

import pytest

from synclassic import lastfm, scrobble
from synclassic.ipod import itunesdb, playcounts

KEY, SECRET = "k" * 32, "s" * 32


class FakeLastfm:
    """Answers like ws.audioscrobbler.com/2.0, verifying api_sig on every request."""

    def __init__(self, approve_after: int = 0, fail_scrobbles: bool = False, ignore_titles=()):
        self.approve_after = approve_after
        self.fail_scrobbles = fail_scrobbles
        self.ignore_titles = set(ignore_titles)
        self.session_polls = 0
        self.scrobbled: list[dict] = []

    def _check(self, params):
        given = params.pop("api_sig")
        params.pop("format", None)
        text = "".join(f"{k}{params[k]}" for k in sorted(params))
        assert hashlib.md5((text + SECRET).encode()).hexdigest() == given, "bad signature"

    def get(self, url, params=None, timeout=None):
        return self._answer(dict(params))

    def post(self, url, data=None, timeout=None):
        return self._answer(dict(data))

    def _answer(self, params):
        self._check(dict(params))
        method = params["method"]
        if method == "auth.getToken":
            return SimpleNamespace(status_code=200, json=lambda: {"token": "TOKEN"})
        if method == "auth.getSession":
            self.session_polls += 1
            if self.session_polls <= self.approve_after:
                return SimpleNamespace(status_code=403, json=lambda: {"error": 14, "message": "unauthorized token"})
            return SimpleNamespace(status_code=200, json=lambda: {"session": {"name": "kerth", "key": "SESSION"}})
        if method == "track.scrobble":
            assert params["sk"] == "SESSION"
            if self.fail_scrobbles:
                return SimpleNamespace(status_code=503, json=lambda: {"error": 16, "message": "service unavailable"})
            count = len([k for k in params if k.startswith("track[")])
            items, accepted = [], 0
            for i in range(count):
                title = params[f"track[{i}]"]
                if title in self.ignore_titles:
                    items.append({"ignoredMessage": {"code": "2", "#text": ""}})
                else:
                    accepted += 1
                    self.scrobbled.append({"artist": params[f"artist[{i}]"], "track": title,
                                           "timestamp": int(params[f"timestamp[{i}]"])})
                    items.append({"ignoredMessage": {"code": "0", "#text": ""}})
            body = {"scrobbles": {"@attr": {"accepted": accepted, "ignored": count - accepted}, "scrobble": items}}
            return SimpleNamespace(status_code=200, json=lambda: body)
        raise AssertionError(method)


def test_signature_follows_lastfm_rules():
    params = {"method": "auth.getSession", "api_key": "abc", "token": "t", "format": "json"}
    expected = hashlib.md5(b"api_keyabcmethodauth.getSessiontokent" + b"secret").hexdigest()
    assert lastfm.sign(params, "secret") == expected


def test_connect_waits_for_approval_and_saves_privately():
    server = FakeLastfm(approve_after=2)
    opened = []
    client = lastfm.Client(KEY, SECRET, session=server)
    user = client.connect(opened.append, sleep=lambda s: None)
    assert user == "kerth" and opened == [f"https://www.last.fm/api/auth/?api_key={KEY}&token=TOKEN"]
    assert server.session_polls == 3
    assert lastfm.connected_user() == "kerth" and lastfm.load_credentials()["session_key"] == "SESSION"
    if os.name == "posix":
        assert stat.S_IMODE(lastfm.credentials_file().stat().st_mode) == 0o600
    lastfm.disconnect()
    assert lastfm.connected_user() is None and lastfm.load_credentials()["api_key"] == KEY


def song(n, length_s=200, kind=1, artist="Artist"):
    return SimpleNamespace(dbid=n, title=f"Song {n}", artist=artist, album="Album", album_artist="Artist",
                           length_ms=length_s * 1000, media_type=kind)


def entry(plays, unix_last_played):
    from synclassic.ipod.edit import mac_time

    return playcounts.PlayCount(plays=plays, last_played=mac_time(unix_last_played) if unix_last_played else 0)


def test_each_play_becomes_a_scrobble_spaced_by_song_length():
    now = time.time()
    added = scrobble.capture(
        [song(1), song(2, kind=4), song(3), song(4, artist="")],  # podcast and no-artist songs are skipped
        [entry(3, now - 100), entry(5, now - 50), entry(1, now - 1000), entry(2, now - 10)],
    )
    queue = scrobble.load_queue()
    assert added == 4 and len(queue) == 4
    song1 = sorted(p["timestamp"] for p in queue if p["track"] == "Song 1")
    assert [b - a for a, b in zip(song1, song1[1:])] == [200, 200]
    assert abs(song1[-1] - (now - 100)) <= 1
    # Reading the same Play Counts again doesn't queue the plays twice.
    assert scrobble.capture([song(1)], [entry(3, now - 100)]) == 0


def test_submit_sends_in_batches_and_handles_rejections():
    now = time.time()
    songs = [song(n) for n in range(60)] + [song(100, length_s=20)]
    entries = [entry(1, now - 3600 - n) for n in range(60)] + [entry(1, now - 10)]
    songs.append(song(200))
    entries.append(entry(1, now - 30 * 24 * 3600))  # a month old
    scrobble.capture(songs, entries)
    lastfm.save_credentials({"api_key": KEY, "secret": SECRET, "session_key": "SESSION", "user": "kerth"})
    server = FakeLastfm(ignore_titles={"Song 7"})
    result = scrobble.submit(lastfm.Client(KEY, SECRET, "SESSION", session=server), now=now)
    assert (result.accepted, result.too_old, result.left) == (59, 1, 0)
    assert result.ignored == 2  # Song 7 (Last.fm said no) and the 20-second song
    assert len(server.scrobbled) == 59 and scrobble.waiting() == 0


def test_offline_keeps_the_plays_for_next_time():
    now = time.time()
    scrobble.capture([song(1), song(2)], [entry(1, now - 60), entry(1, now - 30)])
    with pytest.raises(lastfm.LastfmError):
        scrobble.submit(lastfm.Client(KEY, SECRET, "SESSION", session=FakeLastfm(fail_scrobbles=True)), now=now)
    assert scrobble.waiting() == 2
    server = FakeLastfm()
    result = scrobble.submit(lastfm.Client(KEY, SECRET, "SESSION", session=server), now=now)
    assert result.accepted == 2 and scrobble.waiting() == 0


def test_sync_queues_the_ipods_plays(tmp_path):
    from synclassic import config
    from synclassic.ipod import sync
    from synclassic.ipod.device import ipod_at
    from synclassic.library import Library
    from tests.test_ipod_read import fake_ipod, load

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    ipod = ipod_at(root)
    songs = itunesdb.songs(itunesdb.parse(ipod.database_path.read_bytes()))
    from synclassic.ipod.edit import mac_time

    recent = mac_time(time.time() - 600)
    entries = [struct.pack("<IIIIIII", 2 if i == 3 else 0, recent if i == 3 else 0, 0, 0xFFFFFFFF, 0, 0, 0)
               for i in range(len(songs))]
    (ipod.control / "iTunes" / "Play Counts").write_bytes(
        b"mhdp" + struct.pack("<III", 0x60, 0x1C, len(entries)) + bytes(0x60 - 16) + b"".join(entries))
    library = Library(config.app_dir() / "library.db")
    messages = []
    sync.apply_plan(library, sync.make_plan(library, ipod), messages.append)
    queue = scrobble.load_queue()
    assert [p["track"] for p in queue] == [songs[3].title] * 2
    assert any("2 plays saved for scrobbling" in m for m in messages)


def test_builtin_key_means_one_click_connect(monkeypatch):
    from synclassic import lastfm_app

    monkeypatch.setattr(lastfm_app, "API_KEY", KEY)
    monkeypatch.setattr(lastfm_app, "SHARED_SECRET", SECRET)
    assert lastfm.app_key() == (KEY, SECRET)
    server = FakeLastfm()
    key, secret = lastfm.app_key()
    assert lastfm.Client(key, secret, session=server).connect(lambda url: None, sleep=lambda s: None) == "kerth"
    saved = lastfm.load_credentials()
    assert "api_key" not in saved and saved["session_key"] == "SESSION"  # nothing to paste, nothing stored
    client = lastfm.Client.from_saved()
    assert (client.api_key, client.secret, client.session_key) == (KEY, SECRET, "SESSION")


def test_own_key_overrides_the_builtin_one(monkeypatch):
    from synclassic import lastfm_app

    monkeypatch.setattr(lastfm_app, "API_KEY", "builtin")
    monkeypatch.setattr(lastfm_app, "SHARED_SECRET", "builtin")
    lastfm.save_credentials({"api_key": KEY, "secret": SECRET})
    assert lastfm.app_key() == (KEY, SECRET)


def test_now_playing_is_sent_signed():
    sent = []

    class Server(FakeLastfm):
        def _answer(self, params):
            if params["method"] == "track.updateNowPlaying":
                self._check(dict(params))
                sent.append(params)
                return SimpleNamespace(status_code=200, json=lambda: {"nowplaying": {}})
            return super()._answer(params)

    client = lastfm.Client(KEY, SECRET, "SESSION", session=Server())
    client.now_playing("Artist", "Song", "Album", "", 200)
    assert sent[0]["track"] == "Song" and sent[0]["sk"] == "SESSION" and sent[0]["duration"] == "200"
