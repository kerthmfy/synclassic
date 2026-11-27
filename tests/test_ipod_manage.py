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
