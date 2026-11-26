"""Write one .m3u8 per playlist, in source (Spotify) order."""

from __future__ import annotations

import os
import pathlib

from synclassic.library import Library
from synclassic.textnorm import safe_filename

MARKER = "#SYNCLASSIC"


def playlists_dir(music_dir: pathlib.Path) -> pathlib.Path:
    return music_dir / "Playlists"


def render_m3u8(name: str, entries: list[tuple[int, str, pathlib.Path]], base: pathlib.Path) -> str:
    lines = ["#EXTM3U", f"#PLAYLIST:{name}", MARKER]
    for seconds, display, path in entries:
        try:
            location = os.path.relpath(path, base)
        except ValueError:  # different drive on Windows
            location = str(path)
        lines.append(f"#EXTINF:{seconds},{display}")
        lines.append(location.replace(os.sep, "/"))
    return "\n".join(lines) + "\n"


def write_all(library: Library, music_dir: pathlib.Path) -> list[pathlib.Path]:
    folder = playlists_dir(music_dir)
    folder.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []
    for playlist in library.playlists():
        entries = []
        for item in library.playlist_tracks(playlist):
            if not item.has_file:
                continue
            seconds = round((item.track.duration_ms or 0) / 1000) or -1
            entries.append((seconds, item.track.display(), pathlib.Path(item.file_path)))  # type: ignore[arg-type]
        target = folder / f"{safe_filename(playlist.name, 120)}.m3u8"
        text = render_m3u8(playlist.name, entries, folder)
        if not target.exists() or target.read_text(encoding="utf-8", errors="replace") != text:
            tmp = target.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)
        written.append(target)

    # Remove playlists Synclassic wrote earlier that no longer exist (renamed/removed).
    keep = {p.resolve() for p in written}
    for old in folder.glob("*.m3u8"):
        if old.resolve() in keep:
            continue
        try:
            head = old.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
        except OSError:
            continue
        if MARKER in head:
            old.unlink()
    return written
