"""Where new songs go: this Mac, this Mac and the iPod, or the iPod only.

Each playlist (and each album or song from Find Music) can have its own choice; playlists left on
"default" follow the setting download_to.

- "mac":  downloaded to this Mac; they go on the iPod when you sync (as always).
- "both": downloaded to this Mac and copied to the iPod right away when it's connected (a sync
          that only adds runs by itself after the download; anything it would remove is asked first).
- "ipod": like "both", and once a song is safely on the iPod, Synclassic's own copy on this Mac is
          removed to save space. The song shows as "On iPod only". Songs that are also in a playlist
          kept on this Mac stay; files that are yours (music folders, playlist files) are never removed.
          If an iPod that should have the song doesn't, the song is downloaded again and goes on it.
"""

from __future__ import annotations

import pathlib

MAC, BOTH, IPOD = "mac", "both", "ipod"
CHOICES = {MAC: "This Mac", BOTH: "This Mac and the iPod", IPOD: "The iPod only (no copy kept on this Mac)"}
ONLY_ON_IPOD = "ipod"  # the song's status once its Mac copy is gone


def default() -> str:
    from synclassic.config import load_settings

    value = load_settings().download_to
    return value if value in CHOICES else MAC


def of_playlist(playlist, fallback: str | None = None) -> str:
    """The playlist's choice ("" = the setting)."""
    value = getattr(playlist, "destination", "") or ""
    return value if value in CHOICES else (fallback or default())


def wants_ipod(playlists) -> bool:
    """Should these playlists' new songs go to the iPod right after downloading?"""
    fallback = default()
    return any(of_playlist(p, fallback) in (BOTH, IPOD) for p in playlists)


def ipod_only_keys(library) -> set[str]:
    """Songs whose every playlist says "iPod only" (and only those)."""
    fallback = default()
    choice: dict[str, set[str]] = {}
    for playlist in library.playlists():
        where = of_playlist(playlist, fallback)
        for item in library.playlist_tracks(playlist):
            choice.setdefault(item.key, set()).add(where)
    return {key for key, places in choice.items() if places == {IPOD}}


def release_mac_copies(library, on_ipod: dict[str, pathlib.Path], say=lambda s: None) -> int:
    """Remove this Mac's copy of "iPod only" songs that are now on the iPod.

    `on_ipod`: library key -> the song's file on the iPod (only songs that are really there).
    Returns how many were removed."""
    from synclassic.organize import remove_song_file

    wanted = ipod_only_keys(library)
    removed = 0
    freed = 0
    for item in library.tracks_in_playlists():
        if item.key not in wanted or item.key not in on_ipod or not item.editable_file:
            continue
        on_device = on_ipod[item.key]
        try:
            if not on_device.is_file() or on_device.stat().st_size == 0:
                continue  # not safely there: keep the Mac's copy
            size = pathlib.Path(item.file_path).stat().st_size  # type: ignore[arg-type]
            remove_song_file(library, item)
        except OSError as error:
            say(f"Couldn't remove this Mac's copy of {item.track.display()}: {error}")
            continue
        library.update_track(item.id, status=ONLY_ON_IPOD, file_path=None)
        removed += 1
        freed += size
    if removed:
        say(f"{removed} song{'s are' if removed != 1 else ' is'} now only on the iPod "
            f"({freed / 1000**2:,.0f} MB freed on this Mac).")
    return removed


def reclaim_missing(library, keys_on_ipod: set[str], wanted_keys: set[str]) -> int:
    """"iPod only" songs this iPod should have but doesn't: download them again. Returns how many."""
    count = 0
    for item in library.tracks_with_status(ONLY_ON_IPOD):
        if item.key in wanted_keys and item.key not in keys_on_ipod:
            library.update_track(item.id, status="pending", error=None)
            count += 1
    return count


def reclaim_for(library, ipod) -> int:
    """Before downloading: songs kept only on an iPod that the connected iPod should have but lacks."""
    from synclassic.ipod import prefs
    from synclassic.ipod.sync import library_keys_on_ipod

    if not library.tracks_with_status(ONLY_ON_IPOD):
        return 0
    wanted = {item.key for playlist in prefs.chosen_playlists(library, ipod)
              for item in library.playlist_tracks(playlist)}
    from synclassic.ipod import rockbox

    try:
        if rockbox.mode(ipod) == rockbox.APPLE:
            on_ipod = library_keys_on_ipod(library, ipod)
        else:
            state = rockbox.load_state(ipod.root)
            on_ipod = {key for key, relative in state["files"].items() if (ipod.root / relative).is_file()}
    except Exception:
        return 0  # an unreadable iPod: nothing is downloaded again because of it
    return reclaim_missing(library, on_ipod, wanted)
