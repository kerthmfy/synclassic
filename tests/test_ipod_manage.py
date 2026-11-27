"""Managing the iPod directly: playlists and songs."""

import struct

import pytest

from synclassic.ipod import itunesdb, manage, sync
from synclassic.ipod.device import ipod_at
from tests.conftest import make_audio
from tests.test_ipod_read import fake_ipod, load


@pytest.fixture
def ipod(tmp_path):
    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    for song in itunesdb.songs(itunesdb.parse(load("captured-album-index-36.b64"))):
        if not song.location:
            continue
        path = root / song.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 10)
    return ipod_at(root)


def db(ipod):
    return itunesdb.parse(ipod.database_path.read_bytes())


def titles(ipod, name, kind=2):
    data = db(ipod)
    by_id = {s.id: s.title for s in itunesdb.songs(data)}
    for view in itunesdb.playlists(data, kind):
        if view.name == name:
            return [by_id[i] for i in view.track_ids]
    return None


def test_create_rename_reorder_and_delete_a_playlist(ipod):
    songs = itunesdb.songs(db(ipod))
    pid = manage.create_playlist(ipod, "Road Trip", [songs[3].id, songs[1].id])
    for kind in (2, 3):  # the iPod reads section 3; both must match
        assert titles(ipod, "Road Trip", kind) == [songs[3].title, songs[1].title]

    added = manage.add_to_playlist(ipod, pid, [songs[1].id, songs[7].id])
    assert added == 1  # songs[1] was already there
    manage.set_playlist_songs(ipod, pid, [songs[7].id, songs[3].id, songs[1].id])  # reorder
    assert titles(ipod, "Road Trip") == [songs[7].title, songs[3].title, songs[1].title]

    manage.rename_playlist(ipod, pid, "Highway")
    assert titles(ipod, "Road Trip") is None and len(titles(ipod, "Highway")) == 3
    manage.delete_playlist(ipod, pid)
    assert titles(ipod, "Highway") is None
    assert len(itunesdb.songs(db(ipod))) == 60  # songs untouched


def test_deleting_songs_removes_files_and_entries(ipod, tmp_path):
    # The sample database has no file paths, so put one real song on the fake iPod first.
    session = manage.EditSession(ipod)
    copied_id = session.copy_file(make_audio(tmp_path / "Real.m4a", seconds=2))
    session.commit()
    songs = itunesdb.songs(db(ipod))
    real = next(s for s in songs if s.id == copied_id)
    path = ipod.root / real.relative_path
    assert path.is_file()
    others = [s for s in songs if s.id != copied_id][:3]
    manage.create_playlist(ipod, "Mix", [others[0].id, real.id, others[1].id, others[2].id])

    assert manage.delete_songs(ipod, {real.id}) == 1
    after = itunesdb.songs(db(ipod))
    assert len(after) == 60 and real.dbid not in {s.dbid for s in after}
    assert not path.exists()
    assert titles(ipod, "Mix") == [s.title for s in others]
    master = next(p for p in itunesdb.playlists(db(ipod)) if p.is_master)
    assert len(master.track_ids) == 60


def test_smart_and_synclassic_playlists_are_protected(ipod):
    songs = itunesdb.songs(db(ipod))
    pid = manage.create_playlist(ipod, "From Spotify", [songs[0].id])
    state = sync.load_state(ipod)
    state["playlists"]["spotify|x|"] = {"id": f"{pid:016x}", "name": "From Spotify"}
    sync.save_state(ipod, state)
    before = ipod.database_path.read_bytes()
    with pytest.raises(manage.ManageError, match="kept in sync"):
        manage.rename_playlist(ipod, pid, "Mine now")
    with pytest.raises(manage.ManageError):
        manage.delete_playlist(ipod, pid)
    assert ipod.database_path.read_bytes() == before


def test_play_counts_are_merged_before_songs_move(ipod):
    songs = itunesdb.songs(db(ipod))
    entries = [struct.pack("<IIIIIII", 2 if i == 10 else 0, 0, 0, 0xFFFFFFFF, 0, 0, 0) for i in range(len(songs))]
    (ipod.control / "iTunes" / "Play Counts").write_bytes(
        b"mhdp" + struct.pack("<III", 0x60, 0x1C, len(entries)) + bytes(0x60 - 16) + b"".join(entries))
    manage.delete_songs(ipod, {songs[0].id})  # shifts every position
    after = {s.dbid: s for s in itunesdb.songs(db(ipod))}
    assert after[songs[10].dbid].play_count == songs[10].play_count + 2  # landed on the right song
    assert not (ipod.control / "iTunes" / "Play Counts").exists()


def test_import_m3u8_matches_ipod_songs_and_copies_local_files(ipod, tmp_path):
    songs = itunesdb.songs(db(ipod))
    local = make_audio(tmp_path / "Local Song.m4a", seconds=2)
    from mutagen.mp4 import MP4

    audio = MP4(local)
    audio["\xa9nam"], audio["\xa9ART"] = ["Local Song"], ["Me"]
    audio.save()
    m3u = tmp_path / "Mix.m3u8"
    m3u.write_text(
        "#EXTM3U\n"
        f"#EXTINF:{songs[4].length_ms // 1000},{songs[4].artist} - {songs[4].title}\nnot/here.mp3\n"
        f"#EXTINF:2,Me - Local Song\n{local}\n"
        "#EXTINF:200,Nobody - Missing Song\nmissing.mp3\n",
        encoding="utf-8",
    )
    result = manage.import_m3u(ipod, m3u)
    assert (result.matched, result.copied, len(result.missing)) == (1, 1, 1)
    assert titles(ipod, "Mix") == [songs[4].title, "Local Song"]
    copied = next(s for s in itunesdb.songs(db(ipod)) if s.title == "Local Song")
    assert (ipod.root / copied.relative_path).is_file() and copied.artist == "Me"


def test_export_m3u8(ipod, tmp_path):
    songs = itunesdb.songs(db(ipod))
    pid = manage.create_playlist(ipod, "Out", [songs[2].id, songs[5].id])
    target = tmp_path / "Out.m3u8"
    assert manage.export_m3u(ipod, pid, target) == 2
    text = target.read_text(encoding="utf-8")
    assert "#PLAYLIST:Out" in text and str(ipod.root / songs[5].relative_path) in text


def test_failed_check_changes_nothing(ipod):
    data = bytearray(ipod.database_path.read_bytes())
    data[0x58] = 1
    ipod.database_path.write_bytes(bytes(data))
    with pytest.raises(manage.ManageError):
        manage.create_playlist(ipod, "Nope", [])
    assert ipod.database_path.read_bytes() == bytes(data)


def test_edit_song_info_and_resort_menus(ipod):
    songs = itunesdb.songs(db(ipod))
    first, second = songs[0], songs[1]
    manage.edit_songs(ipod, [first.id], {"title": "Zzz Last", "artist": "Aaa First", "year": 1999, "track_number": 7})
    after = {s.id: s for s in itunesdb.songs(db(ipod))}
    edited = after[first.id]
    assert (edited.title, edited.artist, edited.year, edited.track_number) == ("Zzz Last", "Aaa First", 1999, 7)
    assert after[second.id].title == second.title  # others untouched
    # Many songs at once: only the given field changes.
    manage.edit_songs(ipod, [s.id for s in songs[2:5]], {"genre": "Jazz"})
    after = {s.id: s for s in itunesdb.songs(db(ipod))}
    assert all(after[s.id].genre == "Jazz" and after[s.id].title == s.title for s in songs[2:5])
    # The menu index still lists every song exactly once.
    master = next(p for p in itunesdb.playlists(db(ipod)) if p.is_master).chunk
    for child in master.children:
        if child.tag == b"mhod" and child.mhod_type == 52:
            count = struct.unpack_from("<I", child.body, 4)[0]
            assert sorted(struct.unpack_from(f"<{count}I", child.body, 48)) == list(range(60))


def test_resorted_after_rename(ipod):
    """A renamed song moves to its new place in the title index."""
    from synclassic.ipod import library_index

    data = db(ipod)
    master = next(p for p in itunesdb.playlists(data) if p.is_master).chunk
    songs = {s.id: s for s in itunesdb.songs(data)}
    ids = [m.u32(0x18) for m in master.find(b"mhip")]
    order = library_index.full_order(ids, songs, library_index.SORT_TITLE)
    position = {i: n for n, i in enumerate(ids)}
    index = itunesdb.Chunk(b"mhod", bytearray(24), body=library_index._body52(3, [position[i] for i in order]))
    index.header[0:4] = b"mhod"
    struct.pack_into("<III", index.header, 4, 24, 0, 52)
    master.children.append(index)
    ipod.database_path.write_bytes(itunesdb.serialize(data))

    victim = order[0]  # alphabetically first
    manage.edit_songs(ipod, [victim], {"title": "zzzz the very last"})
    master = next(p for p in itunesdb.playlists(db(ipod)) if p.is_master).chunk
    body = next(c for c in master.children if c.tag == b"mhod" and c.mhod_type == 52
                and struct.unpack_from("<I", c.body, 0)[0] == library_index.SORT_TITLE).body
    count = struct.unpack_from("<I", body, 4)[0]
    positions = struct.unpack_from(f"<{count}I", body, 48)
    ids = [m.u32(0x18) for m in master.find(b"mhip")]
    assert ids[positions[-1]] == victim  # now last


def test_cleanup_finds_and_fixes_leftovers_and_missing_files(ipod, tmp_path):
    # A leftover song file the library doesn't list, and a listed song whose file is gone.
    leftover = ipod.control / "Music" / "F00" / "LEFT.m4a"
    make_audio(leftover, seconds=2)
    from mutagen.mp4 import MP4

    audio = MP4(leftover)
    audio["\xa9nam"], audio["\xa9ART"] = ["Found Again"], ["Somebody"]
    audio.save()
    session = manage.EditSession(ipod)
    gone_id = session.copy_file(make_audio(tmp_path / "Gone.m4a", seconds=2))
    session.commit()
    gone = next(s for s in itunesdb.songs(db(ipod)) if s.id == gone_id)
    (ipod.root / gone.relative_path).unlink()

    report = manage.find_cleanup(ipod)
    assert report.leftovers == [leftover] and [i for i, _ in report.missing] == [gone_id]

    assert manage.add_leftovers(ipod, report.leftovers) == 1
    found = next(s for s in itunesdb.songs(db(ipod)) if s.title == "Found Again")
    assert found.artist == "Somebody" and found.relative_path == "iPod_Control/Music/F00/LEFT.m4a"
    assert manage.remove_missing(ipod, [gone_id]) == 1
    report = manage.find_cleanup(ipod)
    assert report.leftovers == [] and report.missing == []


def test_delete_leftovers_never_touches_listed_songs(ipod, tmp_path):
    leftover = ipod.control / "Music" / "F00" / "JUNK.mp3"
    leftover.write_bytes(b"x" * 100)
    session = manage.EditSession(ipod)
    kept_id = session.copy_file(make_audio(tmp_path / "Keep.m4a", seconds=2))
    session.commit()
    kept = ipod.root / next(s for s in itunesdb.songs(db(ipod)) if s.id == kept_id).relative_path
    assert manage.delete_leftovers(ipod, [leftover, kept]) == 1
    assert not leftover.exists() and kept.exists()


def test_backups_list_and_restore_can_be_undone(ipod):
    original = ipod.database_path.read_bytes()
    songs = itunesdb.songs(db(ipod))
    manage.create_playlist(ipod, "Before", [songs[0].id])  # backs up the original
    changed = ipod.database_path.read_bytes()
    backups = manage.list_backups(ipod)
    assert len(backups) == 1 and backups[0].songs == 60
    undo = manage.restore_backup(ipod, backups[0].folder)
    assert ipod.database_path.read_bytes() == original
    assert (undo / "iTunesDB").read_bytes() == changed  # the restore itself can be undone


def test_restoring_the_oldest_of_ten_backups(ipod, monkeypatch):
    from synclassic.ipod import sync as ipod_sync

    for n in range(10):
        manage.create_playlist(ipod, f"P{n}", [])
    oldest = manage.list_backups(ipod)[-1]
    assert len(manage.list_backups(ipod)) == 10
    expected = (oldest.folder / "iTunesDB").read_bytes()
    manage.restore_backup(ipod, oldest.folder)  # the backup rotation removes this very folder
    assert ipod.database_path.read_bytes() == expected
    assert not oldest.folder.exists() and len(ipod_sync.list_backups(ipod)) == 10


def test_deleting_a_synced_playlist_on_request(ipod):
    songs = itunesdb.songs(db(ipod))
    pid = manage.create_playlist(ipod, "OUT OF MY MIND", [songs[0].id])
    state = sync.load_state(ipod)
    state["playlists"]["spotify|https://open.spotify.com/playlist/x|"] = {"id": f"{pid:016x}", "name": "OUT OF MY MIND"}
    sync.save_state(ipod, state)
    with pytest.raises(manage.ManageError):
        manage.delete_playlist(ipod, pid)  # protected by default
    manage.delete_playlist(ipod, pid, synced_too=True)
    assert titles(ipod, "OUT OF MY MIND") is None
    assert sync.load_state(ipod)["playlists"] == {}  # forgotten: a sync won't try to update it


def test_duplicates_are_found_and_removed_without_losing_playlist_songs(tmp_path):
    from synclassic.ipod import itunesdb, manage
    from synclassic.ipod.check import check
    from synclassic.ipod.device import ipod_at
    from synclassic.ipod.edit import Editor, NewSong
    from tests.test_ipod_read import fake_ipod, load

    root = fake_ipod(tmp_path, load("captured-album-index-36.b64"))
    db = itunesdb.parse((root / "iPod_Control/iTunes/iTunesDB").read_bytes())
    editor = Editor(db)
    ids = {}
    for name, bitrate in (("LOW1", 128), ("HIGH", 256), ("LOW2", 96)):
        song_id, _ = editor.add_song(NewSong(location=f":iPod_Control:Music:F00:{name}.m4a", filetype="m4a", size=100,
                                             length_ms=200_000 + len(ids) * 1000, bitrate=bitrate, sample_rate=44100,
                                             vbr=False, title="Twice (Remastered)" if name == "LOW2" else "Twice",
                                             artist="Band"))
        (root / f"iPod_Control/Music/F00/{name}.m4a").write_bytes(b"x" * 100)
        ids[name] = song_id
    songs = {s.id: s for s in itunesdb.songs(db)}
    mix = editor.create_playlist("Mix")
    editor.set_playlist_songs(mix, [(ids["LOW1"], songs[ids["LOW1"]].dbid), (ids["LOW2"], songs[ids["LOW2"]].dbid)])
    editor.finish()
    (root / "iPod_Control/iTunes/iTunesDB").write_bytes(itunesdb.serialize(db))
    ipod = ipod_at(root)

    report = manage.find_cleanup(ipod)
    (group,) = report.duplicates
    assert group.keep.id == ids["HIGH"] and {s.id for s in group.extra} == {ids["LOW1"], ids["LOW2"]}
    assert report.duplicate_bytes == 200
    assert manage.remove_duplicates(ipod, report.duplicates) == 2
    db = itunesdb.parse(ipod.database_path.read_bytes())
    titles = {s.id: s.title for s in itunesdb.songs(db)}
    mix_view = next(p for p in itunesdb.playlists(db, 2) if p.name == "Mix")
    assert mix_view.track_ids == [ids["HIGH"]]  # both copies were in it: one entry, the kept copy
    assert ids["LOW1"] not in titles and not (root / "iPod_Control/Music/F00/LOW1.m4a").exists()
    assert manage.find_cleanup(ipod).duplicates == []
    assert check(ipod).safe_to_manage


def test_audiobooks_go_to_the_audiobooks_menu(tmp_path):
    from synclassic.ipod import browse, itunesdb, manage
    from synclassic.ipod.check import check
    from synclassic.ipod.device import ipod_at
    from tests.conftest import make_audio
    from tests.test_ipod_read import fake_ipod, load

    ipod = ipod_at(fake_ipod(tmp_path, load("captured-album-index-36.b64")))
    book = make_audio(tmp_path / "book.m4a", seconds=2).rename(tmp_path / "Book.m4b")
    assert manage.add_audiobooks(ipod, [book]) == 1
    db = itunesdb.parse(ipod.database_path.read_bytes())
    song = next(s for s in itunesdb.songs(db) if s.relative_path.endswith(".m4b"))
    mhit = song.chunk
    assert song.media_type == 0x08 and (mhit.u8(0xA5), mhit.u8(0xA6)) == (1, 1)
    assert mhit.u32(0x18) == 0x4D344220  # "M4B "
    assert browse.read(ipod).songs_of_kind("audiobook") == [song.id]
    assert check(ipod).safe_to_manage
