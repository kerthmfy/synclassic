"""Podcast search, feeds, subscriptions and downloads (against a simulated internet)."""

import pathlib

import pytest

from synclassic import config
from synclassic.library import Library
from synclassic.podcasts import feed as feeds
from synclassic.podcasts.store import Podcasts

FEED = (pathlib.Path(__file__).parent / "fixtures" / "podcasts" / "feed.xml").read_bytes()
FEED_URL = "https://example.com/feed.xml"


class Response:
    def __init__(self, status=200, content=b"", json_data=None):
        self.status_code, self.content, self._json = status, content, json_data
        self.headers = {"Content-Length": str(len(content))}

    def json(self):
        return self._json

    def iter_content(self, size):
        for start in range(0, len(self.content), size):
            yield self.content[start : start + size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Internet:
    def __init__(self, feed=FEED):
        self.feed = feed
        self.files = {"https://cdn.example.com/ep2.mp3?x=1": b"ID3" + b"2" * 2000,
                      "https://cdn.example.com/ep1.m4a": b"1" * 1000}

    def get(self, url, params=None, timeout=None, headers=None, stream=False):
        if url == feeds.SEARCH_URL:
            return Response(json_data={"results": [
                {"collectionName": "Sample Talk Show", "artistName": "Sample Host", "feedUrl": FEED_URL,
                 "artworkUrl600": "https://example.com/600.jpg", "primaryGenreName": "Society", "trackCount": 2},
                {"collectionName": "No feed", "artistName": "x"},
            ]})
        if url == FEED_URL:
            return Response(content=self.feed)
        if url in self.files:
            return Response(content=self.files[url])
        return Response(status=404)


@pytest.fixture
def store():
    return Podcasts(Library(config.app_dir() / "library.db"))


def test_search_the_podcast_directory():
    results = feeds.search("sample", Internet())
    assert [(r.title, r.feed_url, r.episodes) for r in results] == [("Sample Talk Show", FEED_URL, 2)]


def test_parse_feed():
    feed = feeds.parse(FEED)
    assert (feed.title, feed.author, feed.artwork_url, feed.category) == (
        "Sample Talk Show", "Sample Host", "https://example.com/cover.jpg", "Society & Culture")
    assert feed.description == "A show about things ."
    assert [e.guid for e in feed.episodes] == ["ep-2", "ep-1"]  # newest first; the post without audio is skipped
    later, earlier = feed.episodes
    assert (later.duration_s, later.number, later.mime) == (2730, 2, "audio/mpeg")
    assert earlier.duration_s == 3600 and earlier.description == "First episode ."
    with pytest.raises(feeds.FeedError):
        feeds.parse(b"<html>not a feed</html>")


def test_subscribe_refresh_and_download(store, tmp_path):
    internet = Internet()
    podcast = store.subscribe(FEED_URL, internet)
    assert store.subscribe(FEED_URL, internet).id == podcast.id  # subscribing twice is harmless
    assert [e.title for e in store.episodes(podcast.id)] == ["Episode 2: Later", "Episode 1: Earlier"]

    store.set_keep(podcast.id, 1)
    new, downloaded = store.update_all(tmp_path, lambda s: None, internet)
    assert (new, downloaded) == (0, 1)
    latest = store.episodes(podcast.id)[0]
    assert latest.downloaded and pathlib.Path(latest.file_path).name == "2026-10-20 Episode 2_ Later.mp3"
    assert pathlib.Path(latest.file_path).read_bytes().startswith(b"ID3")
    assert store.new_count() == 1

    # A new episode appears; the played one is replaced.
    internet.feed = FEED.replace(b"<item>", b"""<item><title>Episode 3</title><guid>ep-3</guid>
        <pubDate>Tue, 27 Oct 2026 10:00:00 +0000</pubDate>
        <enclosure url="https://cdn.example.com/ep3.mp3" length="10" type="audio/mpeg"/></item><item>""", 1)
    internet.files["https://cdn.example.com/ep3.mp3"] = b"3" * 10
    store.set_played(latest.id, True)
    new, downloaded = store.update_all(tmp_path, lambda s: None, internet)
    assert (new, downloaded) == (1, 1)
    episodes = {e.guid: e for e in store.episodes(podcast.id)}
    assert episodes["ep-3"].downloaded and not episodes["ep-2"].downloaded  # played download removed


def test_failed_download_is_recorded(store, tmp_path):
    internet = Internet()
    podcast = store.subscribe(FEED_URL, internet)
    del internet.files["https://cdn.example.com/ep2.mp3?x=1"]
    messages = []
    store.update_all(tmp_path, messages.append, internet)
    episode = store.episodes(podcast.id)[0]
    assert episode.status == "failed" and "404" in episode.error
    assert not list(tmp_path.rglob("*.part"))


def test_unsubscribe_deletes_downloads(store, tmp_path):
    internet = Internet()
    podcast = store.subscribe(FEED_URL, internet)
    store.update_all(tmp_path, lambda s: None, internet)
    files = [e.file_path for e in store.episodes(podcast.id) if e.file_path]
    assert files
    store.unsubscribe(podcast.id)
    assert store.podcasts() == [] and not any(pathlib.Path(f).exists() for f in files)


# --- on the iPod --------------------------------------------------------------------------------

from synclassic.ipod import itunesdb, sync  # noqa: E402
from synclassic.ipod.check import check  # noqa: E402
from synclassic.ipod.edit import GROUP_HEADER, Editor  # noqa: E402
from tests.conftest import make_audio  # noqa: E402
from tests.test_ipod_sync import ipod, read_db  # noqa: E402,F401


@pytest.fixture
def audio_internet(tmp_path):
    internet = Internet()
    for url in internet.files:
        path = make_audio(tmp_path / ("src" + (".m4a" if url.endswith(".m4a") else ".mp3")), seconds=2)
        internet.files[url] = path.read_bytes()
    internet.files["https://example.com/cover.jpg"] = _jpeg()
    return internet


def _jpeg() -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (300, 300), (200, 40, 40)).save(out, "JPEG")
    return out.getvalue()


def podcast_songs(db):
    return [s for s in itunesdb.songs(db) if s.media_type == 0x04]


def podcasts_playlist(db, kind):
    for mhyp in itunesdb.section(db, kind).children[0].find(b"mhyp"):
        if len(mhyp.header) > 0x2B and mhyp.u16(0x2A) & 1:
            return mhyp
    return None


def show_groups(db) -> dict[str, list[str]]:
    """Section 3's Podcasts playlist as the iPod shows it: show -> episode titles."""
    titles = {s.id: s.title for s in itunesdb.songs(db)}
    mhyp = podcasts_playlist(db, 3)
    groups, names = {}, {}
    for mhip in mhyp.find(b"mhip"):
        if mhip.u16(0x10) == GROUP_HEADER:
            names[mhip.u32(0x14)] = itunesdb.texts(mhip)[itunesdb.TITLE]
            groups[names[mhip.u32(0x14)]] = []
        else:
            groups[names[mhip.u32(0x20)]].append(titles[mhip.u32(0x18)])
    return groups


def sync_now(library, ipod):
    messages = []
    plan = sync.make_plan(library, ipod)
    sync.apply_plan(library, plan, messages.append)
    return plan, messages


def test_episodes_go_on_the_ipod_as_podcasts(tmp_path, ipod, audio_internet):
    from tests.test_ipod_artwork import video_ipod_artwork

    video_ipod_artwork(ipod.root, itunesdb.songs(read_db(ipod))[0].dbid)
    library = Library(config.app_dir() / "library.db")
    store = Podcasts(library)
    podcast = store.subscribe(FEED_URL, audio_internet)
    store.update_all(tmp_path / "music", lambda s: None, audio_internet)
    assert store.cover_path(podcast, tmp_path / "music").is_file()

    plan, _ = sync_now(library, ipod)
    assert len(plan.podcasts.copy) == 2 and "  Podcasts: add 2 episodes" in plan.summary()
    db = read_db(ipod)
    episodes = podcast_songs(db)
    assert sorted(e.title for e in episodes) == ["Episode 1: Earlier", "Episode 2: Later"]
    for episode in episodes:
        mhit = episode.chunk
        assert (mhit.u8(0xA5), mhit.u8(0xA6), mhit.u8(0xA7), mhit.u8(0xB2)) == (1, 1, 1, 2)
        assert episode.album == "Sample Talk Show" and episode.genre == "Podcast"
        assert itunesdb.texts(mhit)[itunesdb.RSS_URL] == FEED_URL
        assert mhit.u8(0xA4) == 1  # the show's artwork
    assert itunesdb.texts(next(e.chunk for e in episodes if e.title.startswith("Episode 2")))[
        itunesdb.ENCLOSURE_URL] == "https://cdn.example.com/ep2.mp3?x=1"
    # Section 2: one flat Podcasts playlist; section 3: grouped under the show.
    flat = podcasts_playlist(db, 2)
    assert len(flat.find(b"mhip")) == 2 and all(m.u16(0x10) != GROUP_HEADER for m in flat.find(b"mhip"))
    assert sorted(show_groups(db)["Sample Talk Show"]) == ["Episode 1: Earlier", "Episode 2: Later"]
    view = next(p for p in itunesdb.playlists(db, 3) if p.is_podcasts)
    assert len(view.track_ids) == 2  # the show header isn't a song
    assert itunesdb.serialize(itunesdb.parse(ipod.database_path.read_bytes())) == ipod.database_path.read_bytes()
    assert check(ipod).safe_to_manage

    plan, _ = sync_now(library, ipod)
    assert not plan.podcasts.has_changes


def test_played_episodes_make_way_for_newer_ones(tmp_path, ipod, audio_internet):
    import struct

    library = Library(config.app_dir() / "library.db")
    store = Podcasts(library)
    podcast = store.subscribe(FEED_URL, audio_internet)
    store.set_keep(podcast.id, 1)
    store.update_all(tmp_path / "music", lambda s: None, audio_internet)
    sync_now(library, ipod)
    db = read_db(ipod)
    songs = itunesdb.songs(db)
    assert [s.title for s in podcast_songs(db)] == ["Episode 2: Later"]

    # Listened to on the iPod: the Play Counts file says so.
    entries = [struct.pack("<IIIIIII", 1 if s.media_type == 4 else 0, 0, 0, 0xFFFFFFFF, 0, 0, 0) for s in songs]
    (ipod.control / "iTunes" / "Play Counts").write_bytes(
        b"mhdp" + struct.pack("<III", 0x60, 0x1C, len(entries)) + bytes(0x60 - 16) + b"".join(entries))
    plan, _ = sync_now(library, ipod)
    assert plan.podcasts.marked_played == 1 and len(plan.podcasts.remove) == 1
    db = read_db(ipod)
    assert podcast_songs(db) == [] and show_groups(db) == {}  # the empty show header went too
    assert store.episodes(podcast.id)[0].played

    store.update_all(tmp_path / "music", lambda s: None, audio_internet)  # downloads the next one
    sync_now(library, ipod)
    db = read_db(ipod)
    assert [s.title for s in podcast_songs(db)] == ["Episode 1: Earlier"]
    assert show_groups(db) == {"Sample Talk Show": ["Episode 1: Earlier"]}
    assert check(ipod).safe_to_manage


def test_episodes_already_on_the_ipod_are_reused_and_kept(tmp_path, ipod, audio_internet):
    from synclassic.ipod.edit import NewSong

    db = read_db(ipod)
    editor = Editor(db)
    song_id, dbid = editor.add_song(NewSong(
        location=":iPod_Control:Music:F00:OLD1.mp3", filetype="mp3", size=10, length_ms=2000, bitrate=128,
        sample_rate=44100, vbr=False, title="Episode 2 (from iTunes)", artist="Sample Host",
        album="Sample Talk Show", podcast=True, enclosure_url="https://cdn.example.com/ep2.mp3?x=1"))
    editor.add_podcast_episode(song_id, dbid, "Sample Talk Show")
    editor.finish()
    ipod.database_path.write_bytes(itunesdb.serialize(db))
    (ipod.root / "iPod_Control/Music/F00").mkdir(parents=True, exist_ok=True)
    (ipod.root / "iPod_Control/Music/F00/OLD1.mp3").write_bytes(b"x" * 10)

    library = Library(config.app_dir() / "library.db")
    store = Podcasts(library)
    podcast = store.subscribe(FEED_URL, audio_internet)
    store.set_keep(podcast.id, 1)
    plan, _ = sync_now(library, ipod)
    assert plan.podcasts.copy == [] and list(plan.podcasts.reuse.values()) == [dbid]

    store.set_played(store.episodes(podcast.id)[0].id, True)  # played here: never deletes what it didn't add
    plan = sync.make_plan(library, ipod)
    assert plan.podcasts.remove == []
    assert [s.dbid for s in podcast_songs(read_db(ipod))] == [dbid]


def test_the_podcasts_playlist_cant_be_edited_by_hand(tmp_path, ipod, audio_internet):
    from synclassic.ipod import manage

    library = Library(config.app_dir() / "library.db")
    store = Podcasts(library)
    store.subscribe(FEED_URL, audio_internet)
    store.update_all(tmp_path / "music", lambda s: None, audio_internet)
    sync_now(library, ipod)
    pid = next(p.chunk.u64(0x1C) for p in itunesdb.playlists(read_db(ipod), 2) if p.is_podcasts)
    with pytest.raises(manage.ManageError):
        manage.rename_playlist(ipod, pid, "Mine")
    # Deleting an episode by hand keeps the playlist tidy.
    episode = podcast_songs(read_db(ipod))[0]
    manage.delete_songs(ipod, {episode.id})
    assert len(show_groups(read_db(ipod))["Sample Talk Show"]) == 1
    assert check(ipod).safe_to_manage


def test_episodes_you_download_yourself_go_on_the_ipod_too(tmp_path, ipod, audio_internet):
    library = Library(config.app_dir() / "library.db")
    store = Podcasts(library)
    podcast = store.subscribe(FEED_URL, audio_internet)
    store.set_keep(podcast.id, 1)
    older = store.episodes(podcast.id)[1]
    store.download(older, tmp_path / "music", audio_internet)  # double-clicked in the list
    plan = sync.make_plan(library, ipod)
    assert [e.title for _, e in plan.podcasts.copy] == ["Episode 1: Earlier"]
    assert plan.podcasts.not_downloaded == 1  # the newest one, which the show keeps
    assert any("not downloaded yet" in line for line in plan.summary())
    store.set_keep(podcast.id, 0)  # "None (don't put on iPod)"
    assert sync.make_plan(library, ipod).podcasts.copy == []


def test_eject_retries_while_macos_holds_the_ipod_then_can_force(tmp_path):
    from types import SimpleNamespace

    busy = ("Unmount of disk4 failed: at least one volume could not be unmounted\nUnmount was dissented by PID 720 "
            "(/System/Library/PrivateFrameworks/AMPDevices.framework/Versions/A/AMPDeviceDiscoveryAgent)")
    info = ("<?xml version='1.0'?><plist version='1.0'><dict><key>ParentWholeDisk</key><string>disk4</string>"
            "</dict></plist>")
    calls = []

    def run(args, answers=iter([1, 1, 0])):
        calls.append(args)
        if args[1] == "info":
            return SimpleNamespace(returncode=0, stdout=info, stderr="")
        code = next(answers)
        return SimpleNamespace(returncode=code, stdout="", stderr=busy if code else "")

    ipod = SimpleNamespace(root=tmp_path)
    assert sync.eject_ipod(ipod, run=run, sleep=lambda s: None).ok
    assert calls[1:] == [["diskutil", "eject", "disk4"]] * 3

    calls.clear()
    failing = sync.eject_ipod(ipod, run=lambda a: run(a, iter([1] * 9)), sleep=lambda s: None)
    assert not failing.ok and failing.blocked_by == "AMPDeviceDiscoveryAgent"
    calls.clear()
    assert sync.eject_ipod(ipod, force=True, run=lambda a: run(a, iter([0, 0])), sleep=lambda s: None).ok
    assert calls[1:] == [["diskutil", "unmountDisk", "force", "disk4"], ["diskutil", "eject", "disk4"]]
