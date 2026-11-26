"""Syncing to an iPod, on a fake iPod made from a database reconstructed from a real iPod."""

import struct

import pytest

from synclassic import config
from synclassic.ipod import itunesdb, library_index, sync
from synclassic.ipod.device import ipod_at
from synclassic.ipod.edit import Editor
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from synclassic.update import apply_source
from tests.conftest import make_audio
from tests.test_ipod_read import fake_ipod, load


@pytest.fixture
def ipod(tmp_path):
    raw = load("captured-album-index-36.b64")
    db = itunesdb.parse(raw)
    # Give the iPod a playlist of its own, like a real one would have.
    editor = Editor(db)
    songs = itunesdb.songs(db)
    pid = editor.create_playlist("Old Mix")
    editor.set_playlist_songs(pid, [(s.id, s.dbid) for s in songs[:3]])
    editor.finish()
    root = fake_ipod(tmp_path, itunesdb.serialize(db))
    # Files for the songs already on the iPod.
    for song in songs:
        path = root / song.relative_path
        if song.location:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * 10)
    return ipod_at(root)


def library_with(tmp_path, playlists: dict[str, list[Track]]):
    library = Library(config.app_dir() / "library.db")
    music = tmp_path / "files"
    music.mkdir(exist_ok=True)
    for n, (name, tracks) in enumerate(playlists.items()):
        playlist = library.add_playlist(name, SourceSpec("spotify", f"https://open.spotify.com/playlist/{n:022d}"))
        apply_source(library, playlist, SourcePlaylist(name, tracks))
        for item in library.playlist_tracks(playlist):
            if not item.has_file and item.track.title != "Not Downloaded":
                path = music / f"{item.id}.m4a"
                make_audio(path, seconds=2)
                library.update_track(item.id, status="downloaded", file_path=str(path))
    return library


def song(n, title=None, artist="Synclassic Artist"):
    return Track(title or f"New Song {n}", [artist], album="New Album", duration_ms=2000, spotify_id=f"{n:022d}", track_number=n)


def read_db(ipod):
    return itunesdb.parse(ipod.database_path.read_bytes())


def playlist_titles(db, name, kind=2):
    titles = {s.id: s.title for s in itunesdb.songs(db)}
    for p in itunesdb.playlists(db, kind):
        if p.name == name:
            return [titles[i] for i in p.track_ids]
    return None


def test_first_sync_adds_songs_and_playlists(tmp_path, ipod):
    original = itunesdb.songs(read_db(ipod))
    existing = original[5]  # a song already on the iPod: should be reused, not copied
    library = library_with(tmp_path, {
        "Gym": [song(1), Track(existing.title, [existing.artist], duration_ms=existing.length_ms, spotify_id="e" * 22), song(2)],
        "Chill": [song(2), song(3)],
    })
    plan = sync.make_plan(library, ipod)
    assert len(plan.copy) == 3 and len(plan.reuse) == 1
    assert [p.action for p in plan.playlists] == ["create", "create"]
    text = "\n".join(plan.summary())
    assert "Copy 3 songs" in text and "Reuse 1" in text

    backup_folder = sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert itunesdb.serialize(db) == ipod.database_path.read_bytes()  # reads back cleanly
    songs = itunesdb.songs(db)
    assert len(songs) == len(original) + 3
    added = [s for s in songs if s.title.startswith("New Song")]
    for s in added:
        assert (ipod.root / s.relative_path).is_file() and s.relative_path.startswith("iPod_Control/Music/F")
        assert s.media_type == 1 and s.length_ms > 1500 and s.size > 0
        assert s.artist == "Synclassic Artist" and s.album == "New Album"
    # Playlists in Spotify order, in both playlist sections (the iPod reads section 3).
    for kind in (2, 3):
        assert playlist_titles(db, "Gym", kind) == ["New Song 1", existing.title, "New Song 2"]
        assert playlist_titles(db, "Chill", kind) == ["New Song 2", "New Song 3"]
        assert playlist_titles(db, "Old Mix", kind) == [s.title for s in original[:3]]  # untouched
        master = next(p for p in itunesdb.playlists(db, kind) if p.is_master)
        assert sorted(master.track_ids) == sorted(s.id for s in songs)
        # The menu index covers every song exactly once, keeping the old songs' order.
        for child in master.chunk.children:
            if child.tag == b"mhod" and child.mhod_type == 52:
                count = struct.unpack_from("<I", child.body, 4)[0]
                positions = struct.unpack_from(f"<{count}I", child.body, 48)
                assert sorted(positions) == list(range(len(songs)))
    assert (backup_folder / "iTunesDB").is_file()
    state = sync.load_state(ipod)
    assert len(state["playlists"]) == 2 and sum(v["added"] for v in state["tracks"].values()) == 3


def test_second_sync_has_nothing_to_do_and_changes_are_minimal(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1), song(2)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    plan = sync.make_plan(library, ipod)
    assert not plan.has_changes and not plan.copy and all(p.unchanged for p in plan.playlists)


def test_changes_on_spotify_reach_the_ipod(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1), song(2), song(3)], "Old": [song(4)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    db = read_db(ipod)
    song2_path = ipod.root / next(s for s in itunesdb.songs(db) if s.title == "New Song 2").relative_path

    gym = library.playlist_by_name("Gym")
    apply_source(library, gym, SourcePlaylist("Gym", [song(3), song(1)]))  # reordered, song 2 removed
    library.remove_playlist(library.playlist_by_name("Old"))
    plan = sync.make_plan(library, ipod)
    assert sorted(s.title for s in plan.remove_songs) == ["New Song 2", "New Song 4"]
    assert [name for _, _, name in plan.remove_playlists] == ["Old"]
    sync.apply_plan(library, plan, lambda s: None)

    db = read_db(ipod)
    titles = {s.title for s in itunesdb.songs(db)}
    assert "New Song 2" not in titles and "New Song 4" not in titles and "New Song 1" in titles
    assert not song2_path.exists()
    assert playlist_titles(db, "Gym") == ["New Song 3", "New Song 1"]
    assert playlist_titles(db, "Old") is None


def test_songs_used_by_other_ipod_playlists_are_kept(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1)]})
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    # Someone adds Synclassic's song to the iPod's own "Old Mix" playlist with another tool.
    db = read_db(ipod)
    editor = Editor(db)
    new = next(s for s in itunesdb.songs(db) if s.title == "New Song 1")
    old_mix = next(p for p in itunesdb.playlists(db) if p.name == "Old Mix")
    editor.set_playlist_songs(old_mix.chunk.u64(0x1C), [(i, 0) for i in old_mix.track_ids] + [(new.id, new.dbid)])
    editor.finish()
    ipod.database_path.write_bytes(itunesdb.serialize(db))

    library.remove_playlist(library.playlist_by_name("Gym"))
    plan = sync.make_plan(library, ipod)
    assert plan.remove_songs == []  # still used by "Old Mix"


def test_existing_playlist_with_the_same_name_is_taken_over(tmp_path, ipod):
    library = library_with(tmp_path, {"Old Mix": [song(7)]})
    plan = sync.make_plan(library, ipod)
    assert plan.playlists[0].action == "take over"
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert playlist_titles(db, "Old Mix") == ["New Song 7"]
    assert len([p for p in itunesdb.playlists(db) if p.name == "Old Mix"]) == 1


def test_play_counts_are_merged_before_anything_moves(tmp_path, ipod):
    db = read_db(ipod)
    songs = itunesdb.songs(db)
    # The iPod logged 3 plays and a 4-star rating for the 2nd song, 1 skip for the 4th.
    entries = []
    for i in range(len(songs)):
        plays, rating, skips = (3, 80, 0) if i == 1 else (0, songs[i].rating, 1 if i == 3 else 0)
        entries.append(struct.pack("<IIIIIII", plays, 3_700_000_000 if plays else 0, 0, rating, 0, skips, 0))
    header = b"mhdp" + struct.pack("<III", 0x60, 0x1C, len(entries)) + bytes(0x60 - 16)
    (ipod.control / "iTunes" / "Play Counts").write_bytes(header + b"".join(entries))

    library = library_with(tmp_path, {"Gym": [song(1)]})
    plan = sync.make_plan(library, ipod)
    assert plan.play_counts_pending
    sync.apply_plan(library, plan, lambda s: None)
    after = {s.dbid: s for s in itunesdb.songs(read_db(ipod))}
    second = after[songs[1].dbid]
    assert second.play_count == songs[1].play_count + 3 and second.rating == 80
    assert after[songs[3].dbid].chunk.u32(0x98) == songs[3].chunk.u32(0x98) + 1
    assert not (ipod.control / "iTunes" / "Play Counts").exists()


def test_restore_puts_the_old_database_back(tmp_path, ipod):
    before = ipod.database_path.read_bytes()
    library = library_with(tmp_path, {"Gym": [song(1)]})
    saved = sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    assert ipod.database_path.read_bytes() != before
    sync.restore(ipod, saved)
    assert ipod.database_path.read_bytes() == before


def test_failed_check_changes_nothing(tmp_path, ipod):
    library = library_with(tmp_path, {"Gym": [song(1)]})
    plan = sync.make_plan(library, ipod)
    data = bytearray(ipod.database_path.read_bytes())
    data[0x58] = 1  # looks like a signed (newer iPod) database
    ipod.database_path.write_bytes(bytes(data))
    with pytest.raises(sync.SyncError):
        sync.apply_plan(library, plan, lambda s: None)
    assert ipod.database_path.read_bytes() == bytes(data)
    assert not any(p.name.endswith(".m4a") for p in (ipod.control / "Music").rglob("*") if p.stat().st_size > 100)


def test_index_update_keeps_existing_order(ipod):
    db = read_db(ipod)
    master = next(p for p in itunesdb.playlists(db) if p.is_master).chunk
    before = [bytes(c.body) for c in master.children if c.tag == b"mhod" and c.mhod_type == 52]
    ids = [m.u32(0x18) for m in master.find(b"mhip")]
    library_index.update(master, ids, ids, {s.id: s for s in itunesdb.songs(db)})
    assert [bytes(c.body) for c in master.children if c.tag == b"mhod" and c.mhod_type == 52] == before


def test_cli_dry_run_changes_nothing(tmp_path, ipod, capsys):
    from synclassic.cli import main

    library_with(tmp_path, {"Gym": [song(1)]})
    before = ipod.database_path.read_bytes()
    assert main(["ipod", "sync", "--dry-run", "--path", str(ipod.root)]) == 0
    out = capsys.readouterr().out
    assert "Copy 1 songs" in out and "Dry run" in out
    assert ipod.database_path.read_bytes() == before


def test_incomplete_playlist_never_replaces_a_bigger_ipod_playlist(tmp_path, ipod):
    """The 'instrumentals' case: Synclassic has 2 of 242 songs ready, the iPod has all 242."""
    original = itunesdb.songs(read_db(ipod))
    on_ipod = original[:3]  # "Old Mix" on the iPod has these 3
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("old mix", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "9" * 22))
    apply_source(library, playlist, SourcePlaylist("old mix", [song(1), song(2), Track("Never Downloaded", ["Nobody"], spotify_id="n" * 22)]))
    for item in library.playlist_tracks(playlist)[:2]:
        path = tmp_path / f"{item.id}.m4a"
        make_audio(path, seconds=2)
        library.update_track(item.id, status="downloaded", file_path=str(path))

    plan = sync.make_plan(library, ipod)
    assert plan.playlists[0].action == "wait"
    text = "\n".join(plan.summary())
    assert "left as it is on the iPod (3 songs)" in text and "1 not downloaded yet" in text
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert playlist_titles(db, "Old Mix") == [s.title for s in on_ipod]  # untouched
    assert "old mix" not in [p.name for p in itunesdb.playlists(db)]


def test_songs_already_on_the_ipod_need_no_download(tmp_path, ipod):
    existing = itunesdb.songs(read_db(ipod))[:4]
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("From iPod", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "8" * 22))
    # None of these are downloaded, but all are already on the iPod.
    apply_source(library, playlist, SourcePlaylist("From iPod", [
        Track(s.title, [s.artist], duration_ms=s.length_ms + 1500, spotify_id=f"{n:022d}") for n, s in enumerate(existing)
    ]))
    plan = sync.make_plan(library, ipod)
    assert not plan.copy and len(plan.reuse) == 4 and plan.playlists[0].action == "create"
    sync.apply_plan(library, plan, lambda s: None)
    assert playlist_titles(read_db(ipod), "From iPod") == [s.title for s in existing]


def test_jump_tables_keep_the_ipods_letters_for_existing_songs(ipod):
    db = read_db(ipod)
    master = next(p for p in itunesdb.playlists(db) if p.is_master).chunk
    songs = {s.id: s for s in itunesdb.songs(db)}
    ids = [m.u32(0x18) for m in master.find(b"mhip")]
    # Give the master a title index whose letters follow a different convention (all "Q").
    order = library_index.full_order(ids, songs, library_index.SORT_TITLE)
    position = {song_id: n for n, song_id in enumerate(ids)}
    index52 = itunesdb.Chunk(b"mhod", bytearray(24), body=library_index._body52(3, [position[i] for i in order]))
    index53 = itunesdb.Chunk(b"mhod", bytearray(24), body=library_index._body53(3, [(ord("Q"), 0, len(order))]))
    for chunk in (index52, index53):
        chunk.header[0:4] = b"mhod"
        struct.pack_into("<III", chunk.header, 4, 24, 0, 52 if chunk is index52 else 53)
    master.children.extend([index52, index53])

    editor = Editor(db)
    from synclassic.ipod.edit import NewSong

    editor.add_song(NewSong(":iPod_Control:Music:F00:NEW1.m4a", "m4a", 10, 1000, 128, 44100, False, "Aaaa New Song"))
    editor.finish()
    body = next(c for c in master.children if c.tag == b"mhod" and c.mhod_type == 53).body
    count = struct.unpack_from("<I", body, 4)[0]
    groups = [struct.unpack_from("<HHII", body, 16 + 12 * n) for n in range(count)]
    letters = [chr(g[0]) for g in groups]
    assert "Q" in letters and sum(g[3] for g in groups if chr(g[0]) == "Q") == len(order)  # kept
    assert sum(g[3] for g in groups) == len(order) + 1
    assert "A" in letters  # the new song got its own letter
