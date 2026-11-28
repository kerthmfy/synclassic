"""On-The-Go playlists from the iPod, made in your Spotify account too."""

import struct
import time

import pytest

from synclassic import config, otg_spotify
from synclassic.ipod import itunesdb, sync
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyApiError, SpotifyClient
from tests.test_ipod_sync import ipod, library_with, playlist_titles, read_db, song  # noqa: F401  (fixture)


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.headers = status, body or {}, {}

    def json(self):
        return self._body


class FakeSpotify:
    """Answers like api.spotify.com: the new addresses, or (old=True) only the older ones."""

    def __init__(self, old=False, fail_adding=False):
        self.old, self.fail_adding = old, fail_adding
        self.playlists: dict[str, dict] = {}
        self.searches = []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("/me"):
            return FakeResponse(200, {"id": "kerth"})
        if url.endswith("/search"):
            self.searches.append(params["q"])
            if "Old Song" in params["q"]:
                return FakeResponse(200, {"tracks": {"items": [
                    {"id": "wrongone", "name": "Old Song (Live)", "duration_ms": 1000, "artists": [{"name": "Band"}]},
                    {"id": "found1", "name": "Old Song", "duration_ms": 201_000, "artists": [{"name": "Band"}]}]}})
            return FakeResponse(200, {"tracks": {"items": []}})
        raise AssertionError(url)

    def post(self, url, json=None, headers=None, timeout=None):
        if url.endswith("/me/playlists") or url.endswith("/users/kerth/playlists"):
            if self.old and url.endswith("/me/playlists"):
                return FakeResponse(404, {"error": {"message": "not found"}})
            new_id = f"pl{len(self.playlists) + 1}"
            self.playlists[new_id] = {"name": json["name"], "public": json["public"], "uris": []}
            return FakeResponse(201, {"id": new_id})
        if "/playlists/" in url:
            if self.old and url.endswith("/items"):
                return FakeResponse(404, {"error": {"message": "not found"}})
            if self.fail_adding:
                return FakeResponse(400, {"error": {"message": "bad"}})
            self.playlists[url.split("/playlists/")[1].split("/")[0]]["uris"] += json["uris"]
            return FakeResponse(201, {"snapshot_id": "x"})
        raise AssertionError(url)


def logged_in(scope="playlist-read-private playlist-modify-private"):
    token = {"access_token": "A", "refresh_token": "R", "client_id": "C", "expires_at": time.time() + 3600, "scope": scope}
    spotify_auth.save_token(token)
    return token


def otg_file(ipod, positions):
    data = b"mhpo" + struct.pack("<III", 0x14, 4, len(positions)) + bytes(4) + b"".join(struct.pack("<I", p) for p in positions)
    (ipod.control / "iTunes" / "OTGPlaylistInfo").write_bytes(data)


def test_on_the_go_playlist_is_made_on_spotify_after_a_sync(tmp_path, ipod):
    token = logged_in()
    library = library_with(tmp_path, {"Gym": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    songs = itunesdb.songs(read_db(ipod))
    titles = [s.title for s in songs]
    first, second = titles.index("New Song 2"), titles.index("New Song 1")
    otg_file(ipod, [first, second, 0])  # 0: a song that was on the iPod already (not from Spotify)
    messages = []
    sync.apply_plan(library, sync.make_plan(library, ipod), messages.append)
    name = f"On-The-Go {__import__('datetime').date.today():%Y-%m-%d}"
    assert playlist_titles(read_db(ipod), name) == ["New Song 2", "New Song 1", songs[0].title]
    queued = otg_spotify.load()
    assert [q["name"] for q in queued] == [name] and any("made on Spotify too" in m for m in messages)
    assert [e["spotify_id"] for e in queued[0]["songs"]] == ["0000000000000000000002", "0000000000000000000001", None]

    server = FakeSpotify()
    result = otg_spotify.send(SpotifyClient(token, session=server))
    (made,) = server.playlists.values()
    assert made == {"name": name, "public": False,
                    "uris": ["spotify:track:0000000000000000000002", "spotify:track:0000000000000000000001"]}
    assert result.made == [name] and result.missing == 1 and "1 song Spotify doesn't have" in result.describe()
    assert otg_spotify.waiting() == 0


def test_older_spotify_addresses_and_lookups(tmp_path):
    token = logged_in()
    server = FakeSpotify(old=True)
    client = SpotifyClient(token, session=server)
    assert client.find_track("Band", "Old Song", 200_000) == "found1"  # not the live one
    assert client.find_track("Band", "Unknown", 200_000) is None
    playlist = client.create_playlist("Mix")
    client.add_to_playlist(playlist, [f"spotify:track:{n}" for n in range(150)])
    assert len(server.playlists["pl1"]["uris"]) == 150


def test_waits_when_it_cant_send_and_asks_for_the_new_permission(tmp_path):
    token = logged_in()
    otg_spotify.save([{"name": "On-The-Go 1", "songs": [{"artist": "Band", "title": "Old Song", "length_ms": 201_000}]}])
    server = FakeSpotify(fail_adding=True)
    with pytest.raises(SpotifyApiError):
        otg_spotify.send(SpotifyClient(token, session=server))
    assert otg_spotify.load()[0]["made_id"] == "pl1"  # made once; the retry doesn't make it again
    server.fail_adding = False
    otg_spotify.send(SpotifyClient(token, session=server))
    assert list(server.playlists) == ["pl1"] and server.playlists["pl1"]["uris"] == ["spotify:track:found1"]

    old_token = logged_in(scope="playlist-read-private")
    otg_spotify.save([{"name": "x", "songs": []}])
    with pytest.raises(otg_spotify.NeedsLogin, match="log in to Spotify again"):
        otg_spotify.send(SpotifyClient(old_token, session=FakeSpotify()))


def test_nothing_is_kept_when_its_off_or_not_logged_in(tmp_path):
    from types import SimpleNamespace

    songs = [SimpleNamespace(artist="A", title="T", album="", length_ms=1000, dbid=1)]
    assert not otg_spotify.capture("OTG", songs)  # not logged in
    logged_in()
    config.save_settings(config.Settings(otg_to_spotify=False))
    assert not otg_spotify.capture("OTG", songs)
    config.save_settings(config.Settings())
    assert otg_spotify.capture("OTG", songs) and otg_spotify.waiting() == 1
