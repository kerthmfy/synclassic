"""Write song info and cover art into downloaded files."""

from __future__ import annotations

import pathlib

from synclassic.models import Track

FREEFORM = "----:com.synclassic:"


def fetch_cover(url: str | None) -> bytes | None:
    """Download an image (None if it isn't a readable JPEG/PNG)."""
    if not url:
        return None
    from synclassic.artwork import download_image

    return download_image(url)


def _is_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def tag_file(path: pathlib.Path, track: Track, cover: bytes | None, youtube_id: str | None = None) -> None:
    suffix = path.suffix.lower()
    if suffix == ".m4a":
        _tag_mp4(path, track, cover, youtube_id)
    elif suffix == ".mp3":
        _tag_mp3(path, track, cover, youtube_id)
    else:
        raise ValueError(f"Can't tag {suffix} files")


def _tag_mp4(path: pathlib.Path, track: Track, cover: bytes | None, youtube_id: str | None) -> None:
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags.clear()
    tags["\xa9nam"] = [track.title]
    if track.artists:
        tags["\xa9ART"] = [track.artist]
    album_artist = track.album_artist or track.primary_artist
    if album_artist:
        tags["aART"] = [album_artist]
    if track.album:
        tags["\xa9alb"] = [track.album]
    if track.release_date:
        tags["\xa9day"] = [track.release_date[:10]]
    if track.genre:
        tags["\xa9gen"] = [track.genre]
    if track.track_number:
        tags["trkn"] = [(track.track_number, 0)]
    if track.disc_number:
        tags["disk"] = [(track.disc_number, 0)]
    if track.explicit is not None:
        tags["rtng"] = [1 if track.explicit else 0]
    if cover:
        tags["covr"] = [MP4Cover(cover, MP4Cover.FORMAT_PNG if _is_png(cover) else MP4Cover.FORMAT_JPEG)]
    if track.spotify_id:
        tags[FREEFORM + "spotify_id"] = [MP4FreeForm(track.spotify_id.encode())]
    if youtube_id:
        tags[FREEFORM + "youtube_id"] = [MP4FreeForm(youtube_id.encode())]
    audio.save()


def _tag_mp3(path: pathlib.Path, track: Track, cover: bytes | None, youtube_id: str | None) -> None:
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, ID3NoHeaderError

    try:
        tags = ID3(path)
        tags.delete(path)
    except ID3NoHeaderError:
        pass
    tags = ID3()
    tags.add(TIT2(encoding=3, text=track.title))
    if track.artists:
        tags.add(TPE1(encoding=3, text=track.artist))
    album_artist = track.album_artist or track.primary_artist
    if album_artist:
        tags.add(TPE2(encoding=3, text=album_artist))
    if track.album:
        tags.add(TALB(encoding=3, text=track.album))
    if track.release_date:
        tags.add(TDRC(encoding=3, text=track.release_date[:10]))
    if track.genre:
        tags.add(TCON(encoding=3, text=track.genre))
    if track.track_number:
        tags.add(TRCK(encoding=3, text=str(track.track_number)))
    if track.disc_number:
        tags.add(TPOS(encoding=3, text=str(track.disc_number)))
    if cover:
        mime = "image/png" if _is_png(cover) else "image/jpeg"
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))
    if track.spotify_id:
        tags.add(TXXX(encoding=3, desc="SPOTIFY_ID", text=track.spotify_id))
    if youtube_id:
        tags.add(TXXX(encoding=3, desc="YOUTUBE_ID", text=youtube_id))
    # ID3v2.3 is the version old iPods and iTunes read most reliably.
    tags.save(path, v2_version=3)


def embed_cover(path: pathlib.Path, cover: bytes) -> None:
    """Replace just the cover inside a song file (M4A or MP3); everything else stays."""
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".m4b", ".mp4", ".aac"):
        from mutagen.mp4 import MP4, MP4Cover

        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["covr"] = [MP4Cover(cover, MP4Cover.FORMAT_PNG if _is_png(cover) else MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif suffix == ".mp3":
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime="image/png" if _is_png(cover) else "image/jpeg", type=3, desc="Cover", data=cover))
        tags.save(path)
    else:
        raise ValueError(f"Can't put a cover inside {suffix} files")


def update_info(path: pathlib.Path, genre: str = "", year: str = "") -> None:
    """Set a file's genre and/or year, leaving everything else as it is."""
    suffix = path.suffix.lower()
    if suffix in (".m4a", ".m4b", ".mp4", ".aac"):
        from mutagen.mp4 import MP4

        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        if genre:
            audio.tags["\xa9gen"] = [genre]
        if year:
            audio.tags["\xa9day"] = [year]
        audio.save()
    elif suffix == ".mp3":
        from mutagen.id3 import ID3, TCON, TDRC, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if genre:
            tags.setall("TCON", [TCON(encoding=3, text=genre)])
        if year:
            tags.setall("TDRC", [TDRC(encoding=3, text=year)])
        tags.save(path, v2_version=3)


def update_details(path: pathlib.Path, track: Track) -> None:
    """Write a song's details (title, artist, album, album artist, genre, year, track and disc
    number) into its file, leaving everything else inside (cover, lyrics…) as it is."""
    suffix = path.suffix.lower()
    album_artist = track.album_artist or track.primary_artist
    if suffix in (".m4a", ".m4b", ".mp4", ".aac"):
        from mutagen.mp4 import MP4

        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        for key, value in (("\xa9nam", track.title), ("\xa9ART", track.artist), ("aART", album_artist),
                           ("\xa9alb", track.album), ("\xa9gen", track.genre), ("\xa9day", track.release_date[:10])):
            if value:
                tags[key] = [value]
            elif key in tags:
                del tags[key]
        for key, number in (("trkn", track.track_number), ("disk", track.disc_number)):
            if number:
                tags[key] = [(number, 0)]
            elif key in tags:
                del tags[key]
        audio.save()
    elif suffix == ".mp3":
        from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, ID3NoHeaderError

        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        for frame, value in ((TIT2, track.title), (TPE1, track.artist), (TPE2, album_artist), (TALB, track.album),
                             (TCON, track.genre), (TDRC, track.release_date[:10]),
                             (TRCK, str(track.track_number or "")), (TPOS, str(track.disc_number or ""))):
            tags.delall(frame.__name__)
            if value:
                tags.add(frame(encoding=3, text=value))
        tags.save(path, v2_version=3)
    else:
        raise ValueError(f"Can't change details inside {suffix} files")
