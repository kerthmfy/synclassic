"""Write song info and cover art into downloaded files."""

from __future__ import annotations

import pathlib

import requests

from synclassic.models import Track

FREEFORM = "----:com.synclassic:"


def fetch_cover(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException:
        return None
    if response.status_code != 200 or not response.content:
        return None
    return response.content


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
    from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX, ID3NoHeaderError

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
