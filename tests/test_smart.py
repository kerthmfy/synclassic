"""iPod smart playlists, written like iTunes writes them and refreshed at every sync."""

import struct

from synclassic import cli
from synclassic.ipod import itunesdb, smart, sync
from synclassic.ipod.check import check
from synclassic.ipod.edit import mac_time
from tests.test_ipod_sync import ipod, library_with, read_db, song  # noqa: F401


def test_format_follows_itunes():
    preset = smart.BY_KEY["top_25"]
    pref = smart.pref_mhod(preset)
    assert len(pref.header) + len(pref.body) == 96
    assert pref.body[:4] == bytes([1, 1, 1, smart.LIMIT_SONGS]) and pref.body[4] == smart.SORT_MOST_OFTEN_PLAYED
    assert struct.unpack_from("<I", pref.body, 8)[0] == 25
    rules = smart.rules_mhod(smart.BY_KEY["recently_added"])
    assert rules.body[:4] == b"SLst" and len(rules.body) == 4 + 12 + 120 + 2 * (8 + 44 + 4 + 0x44)
    assert smart.parse_rules(rules) == [(smart.DATE_ADDED, smart.IS_IN_THE_LAST, smart.DATE_IDENTIFIER),
                                        (smart.MEDIA_KIND, smart.BINARY_AND, 1)]


def playlist(db, name):
    return next((p for p in itunesdb.playlists(db, 2) if p.name == name), None)


def test_smart_playlists_on_the_ipod(tmp_path, ipod):
    db = read_db(ipod)
    songs = itunesdb.songs(db)
    for n, s in enumerate(songs[:30]):  # some plays and ratings
        s.chunk.set_u32(0x50, 30 - n)
        s.chunk.set_u32(0x58, mac_time() - 3600 * n)
    songs[0].chunk.set_u8(0x1F, 100)
    songs[1].chunk.set_u8(0x1F, 40)
    ipod.database_path.write_bytes(itunesdb.serialize(db))

    changes = smart.set_smart_playlists(ipod, {"top_25", "top_rated", "never_played"})
    assert sorted(changes) == ["added “My Top Rated”", "added “Never Played”", "added “Top 25 Most Played”"]
    db = read_db(ipod)
    titles = {s.id: s.title for s in itunesdb.songs(db)}
    top = playlist(db, "Top 25 Most Played")
    assert top.is_smart and len(top.track_ids) == 25 and titles[top.track_ids[0]] == songs[0].title
    assert [titles[i] for i in playlist(db, "My Top Rated").track_ids] == [songs[0].title]
    assert len(playlist(db, "Never Played").track_ids) == len(songs) - 30
    for kind in (2, 3):  # in both playlist sections
        assert any(p.name == "Top 25 Most Played" and p.is_smart for p in itunesdb.playlists(db, kind))
    assert check(ipod).safe_to_manage

    # A sync refreshes them: a newly synced song is never played.
    library = library_with(tmp_path, {"Mix": [song(1)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    db = read_db(ipod)
    never = {s.id: s.title for s in itunesdb.songs(db)}
    assert "New Song 1" in [never[i] for i in playlist(db, "Never Played").track_ids]

    smart.set_smart_playlists(ipod, {"top_25"})
    db = read_db(ipod)
    assert playlist(db, "Never Played") is None and playlist(db, "Top 25 Most Played") is not None
    assert check(ipod).safe_to_manage


def test_smart_from_the_terminal(ipod, monkeypatch, capsys):
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))
    assert cli.main(["ipod", "smart", "recently_added"]) == 0
    assert cli.main(["ipod", "smart"]) == 0
    assert "[x] recently_added" in capsys.readouterr().out
    assert cli.main(["ipod", "smart", "bogus"]) == 1
