"""Your own music folder as a playlist: read as it is, never changed, FLAC converted when synced."""

import pathlib
import subprocess

from mutagen.flac import FLAC
from mutagen.mp4 import MP4

from synclassic import config
from synclassic.ipod import itunesdb, lyrics_sync, sync
from synclassic.library import Library
from synclassic.sources import SourceSpec, read_source, specs_from_input
from synclassic.sources.folder import parse_folder
from synclassic.update import add_inputs, needs_work
from tests.conftest import make_audio
from tests.test_ipod_sync import ipod, playlist_titles, read_db  # noqa: F401  (fixture)


def flac(path, title, artist, album, number, seconds=1.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-c:a", "flac", str(path)], check=True)
    audio = FLAC(path)
    audio.update({"title": title, "artist": artist, "album": album, "tracknumber": f"{number}/9", "date": "1999-03-01",
                  "albumartist": artist, "genre": "Rock"})
    audio.save()
    return path


def music_folder(tmp_path):
    folder = tmp_path / "My Music"
    flac(folder / "Band" / "Second Album" / "02.flac", "Later Song", "Band", "Second Album", 2)
    flac(folder / "Band" / "Second Album" / "01.flac", "Opening", "Band", "Second Album", 1)
    flac(folder / "Another Artist" / "Album" / "x.flac", "Hello", "Another Artist", "Album", 1)
    (folder / "Loose").mkdir()
    song = make_audio(folder / "Loose" / "Someone - Untagged Song.m4a")
    (folder / "Loose" / "._Someone - Untagged Song.m4a").write_bytes(b"mac junk")
    (folder / ".hidden").mkdir()
    make_audio(folder / ".hidden" / "skip.m4a")
    (folder / "cover.jpg").write_bytes(b"jpg")
    return folder, song


def test_folder_reads_details_in_album_order_and_remembers_them(tmp_path):
    folder, _ = music_folder(tmp_path)
    playlist = parse_folder(folder)
    assert playlist.name == "My Music"
    assert [(t.artist, t.title) for t in playlist.tracks] == [
        ("Another Artist", "Hello"), ("Band", "Opening"), ("Band", "Later Song"), ("Someone", "Untagged Song")]
    first = playlist.tracks[1]
    assert (first.album, first.track_number, first.year, first.genre) == ("Second Album", 1, "1999", "Rock")
    assert first.local_path.endswith("01.flac") and first.duration_ms > 1000

    reads = []
    again = parse_folder(folder, read=lambda p: reads.append(p) or {})
    assert reads == [] and [t.title for t in again.tracks] == [t.title for t in playlist.tracks]  # from memory


def test_add_a_folder_then_new_files_show_up(tmp_path):
    folder, _ = music_folder(tmp_path)
    assert specs_from_input(str(folder)) == [SourceSpec("folder", str(folder.resolve()))]
    library = Library(config.app_dir() / "library.db")
    (playlist,) = add_inputs(library, [str(folder)], None, None, lambda s: None)
    items = library.playlist_tracks(playlist)
    assert len(items) == 4 and all(i.status == "local" and i.has_file and not i.editable_file for i in items)
    assert not any(needs_work(i, retry_failed=True) for i in items)  # nothing to download

    flac(folder / "Band" / "Second Album" / "03.flac", "Closer", "Band", "Second Album", 3)
    from synclassic.update import apply_source

    apply_source(library, playlist, read_source(playlist.spec, None))
    assert [i.track.title for i in library.playlist_tracks(playlist)][-2:] == ["Closer", "Untagged Song"]


def test_folder_music_goes_on_the_ipod_converted_and_your_files_stay_as_they_are(tmp_path, ipod):
    folder, own_m4a = music_folder(tmp_path)
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    library = Library(config.app_dir() / "library.db")
    add_inputs(library, [str(folder)], None, None, lambda s: None)
    plan = sync.make_plan(library, ipod)
    assert len(plan.copy) == 4 and len(plan.convert) == 3  # the FLACs; the .m4a goes as it is
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    assert playlist_titles(db, "My Music") == ["Hello", "Opening", "Later Song", "Untagged Song"]
    opening = {s.title: s for s in itunesdb.songs(db)}["Opening"]
    assert MP4(ipod.root / opening.relative_path).info.codec == "alac"
    assert opening.album == "Second Album"

    # Lyrics found later go on the iPod, never into your own files.
    class Finder:
        def find(self, *args):
            return type("Found", (), {"text": "la la la"})()

    assert lyrics_sync.library_missing(library) == []
    lyrics_sync.fill_ipod(ipod, library, finder=Finder(), make_backup=False)
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
