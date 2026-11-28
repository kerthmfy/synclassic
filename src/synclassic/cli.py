"""The `synclassic` command."""

from __future__ import annotations

import argparse
import pathlib
import sys

from synclassic import __version__, playlist_writer
from synclassic.config import SETTING_HELP, app_dir, load_settings, save_settings, set_setting
from synclassic.library import Library, LibraryPlaylist
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyApiError, SpotifyClient
from synclassic.update import DownloadReport, RefreshReport, add_inputs, download_missing, finalize, refresh_playlist


def say(text: str = "") -> None:
    print(text, flush=True)


def open_library() -> Library:
    return Library(app_dir() / "library.db")


def spotify_client(required: bool = False) -> SpotifyClient | None:
    try:
        client = SpotifyClient.from_saved_login()
    except spotify_auth.SpotifyAuthError as error:
        if required:
            raise SystemExit(str(error))
        say(f"Note: {error} Continuing without login.")
        return None
    if required and not client:
        raise SystemExit("You're not logged in to Spotify. Run: synclassic login")
    return client


def find_playlists(library: Library, names: list[str]) -> list[LibraryPlaylist]:
    if not names:
        return library.playlists()
    found = []
    for name in names:
        playlist = library.playlist_by_name(name)
        if not playlist:
            raise SystemExit(f"No playlist called '{name}'. See: synclassic list")
        found.append(playlist)
    return found


def print_refresh(report: RefreshReport) -> None:
    if report.error:
        say(f"  {report.name}: couldn't read it: {report.error}")
        return
    changes = []
    if report.added:
        changes.append(f"{len(report.added)} new")
    if report.removed:
        changes.append(f"{len(report.removed)} removed")
    say(f"  {report.name}: {report.total} songs" + (f" ({', '.join(changes)})" if changes else ""))
    for title in report.removed[:20]:
        say(f"      - {title}")
    if len(report.removed) > 20:
        say(f"      ...and {len(report.removed) - 20} more removed")
    if report.note:
        say(f"    Note: {report.note}")


def print_download_report(report: DownloadReport) -> None:
    if report.stopped_early:
        say("")
        say(report.stopped_early)
    parts = []
    if report.downloaded:
        parts.append(f"{report.downloaded} downloaded")
    if report.review:
        parts.append(f"{report.review} to double-check")
    if report.unmatched:
        parts.append(f"{report.unmatched} not found")
    if report.failed:
        parts.append(f"{report.failed} failed")
    if parts:
        say("")
        say("Done: " + ", ".join(parts) + ".")
    if report.review or report.unmatched or report.failed:
        say("Run 'synclassic review' to listen and pick the right versions yourself.")


def finish(library: Library) -> None:
    settings = load_settings()
    finalize(library, settings)
    if library.playlists():
        where = settings.music_path if settings.folder_layout == "playlist" else playlist_writer.playlists_dir(settings.music_path)
        say(f"Playlists saved in {where}")


# --- commands ---------------------------------------------------------------------------------


def cmd_login(args) -> int:
    settings = load_settings()
    client_id = args.client_id or settings.spotify_client_id
    if not client_id:
        say("To log in, Synclassic needs the Client ID of your own Spotify developer app (free, ~2 minutes):")
        say("  1. Go to https://developer.spotify.com/dashboard and click 'Create app'.")
        say(f"  2. Add this Redirect URI exactly: {spotify_auth.REDIRECT_URI}")
        say("  3. Tick 'Web API', save, then copy the app's Client ID.")
        say("  (Spotify requires the developer account to have Premium.)")
        client_id = ask("Client ID: ").strip()
    if not client_id:
        return 1
    try:
        spotify_auth.login(client_id, open_browser=not args.no_browser)
    except spotify_auth.SpotifyAuthError as error:
        say(str(error))
        return 1
    settings.spotify_client_id = client_id
    save_settings(settings)
    client = spotify_client(required=True)
    try:
        name = client.me().get("display_name") or "your account"  # type: ignore[union-attr]
    except SpotifyApiError:
        name = "your account"
    say(f"Logged in to Spotify as {name}.")
    return 0


def cmd_logout(args) -> int:
    say("Logged out of Spotify." if spotify_auth.forget_token() else "You weren't logged in.")
    return 0


def cmd_spotify_playlists(args) -> int:
    client = spotify_client(required=True)
    me = client.me()  # type: ignore[union-attr]
    my_id = me.get("id")
    say("Your Spotify playlists (✓ = all songs readable; · = only the first 100 without owning it):")
    for item in client.my_playlists():  # type: ignore[union-attr]
        full = item["owner_id"] == my_id or item["collaborative"]
        total = item["total"] if item["total"] is not None else "?"
        say(f"  {'✓' if full else '·'} {item['name']}  ({total} songs)  {item['url'] or ''}")
    return 0


def full_reader():
    """The full-playlist reader, when it's turned on and the built-in browser is installed."""
    from synclassic.sources import full_reader as reader

    if load_settings().full_playlist_reader and reader.available():
        return reader.terminal_reader
    return None


def add_sources(library: Library, inputs: list[str], name: str | None) -> list[LibraryPlaylist]:
    return add_inputs(library, inputs, name, spotify_client(), say, full_reader())


def cmd_add(args) -> int:
    library = open_library()
    inputs = list(args.sources)
    if args.all_mine:
        client = spotify_client(required=True)
        my_id = client.me().get("id")  # type: ignore[union-attr]
        inputs.append("liked")
        for item in client.my_playlists():  # type: ignore[union-attr]
            if (item["owner_id"] == my_id or item["collaborative"]) and item["url"]:
                inputs.append(item["url"])
    if not inputs:
        say("Give a Spotify link, 'liked', an .m3u8 file or a .csv file. Example:")
        say("  synclassic add https://open.spotify.com/playlist/...")
        return 1
    playlists = add_sources(library, inputs, args.name)
    if not playlists:
        return 1
    if args.to:
        for playlist in playlists:
            library.set_destination(playlist, args.to)
    if args.no_download:
        finish(library)
        say("Added. Run 'synclassic update' to download the songs.")
        return 0
    return run_update(library, playlists, retry_failed=False, refresh=False)


def run_update(
    library: Library, playlists: list[LibraryPlaylist], retry_failed: bool, download: bool = True, refresh: bool = True,
    to_ipod: bool = True,
) -> int:
    """`to_ipod`: afterwards, copy new songs to the connected iPod for playlists saved there too."""
    settings = load_settings()
    if refresh:
        client = spotify_client()
        reader = full_reader()
        say("Checking for changes...")
        for playlist in playlists:
            print_refresh(refresh_playlist(library, playlist, client, reader))
    if download:
        say("")
        say(f"Saving music to {settings.music_path} as {settings.format.upper()}")
        from synclassic.update import keys_on_connected_ipod

        ipod = _connected_ipod() if to_ipod else None
        if ipod is not None:
            from synclassic import destinations

            again = destinations.reclaim_for(library, ipod)
            if again:
                say(f"{again} songs kept only on another iPod are downloaded again for {ipod.name}.")
        report = download_missing(library, settings, say, retry_failed=retry_failed, only=playlists,
                                  skip_keys=keys_on_connected_ipod(library, settings))
        if report.on_ipod:
            say(f"{report.on_ipod} songs weren't downloaded: they're already on your iPod.")
        if settings.music_videos:
            from synclassic.videos import Videos

            items = {i.id: i for p in playlists for i in library.playlist_tracks(p)}
            videos = Videos(library).update(settings, say=say, items=list(items.values()))
            if videos.found or videos.failed:
                say(f"Music videos: {videos.found} new" + (f", {videos.failed} failed" if videos.failed else ""))
        print_download_report(report)
        if not (report.downloaded or report.review or report.unmatched or report.failed or report.stopped_early):
            say("Everything is already downloaded.")
    finish(library)
    if download and to_ipod:
        _send_to_ipod(library, playlists)
    return 0


def _connected_ipod():
    from synclassic.ipod.device import find_ipods

    try:
        ipods = find_ipods()
    except OSError:
        return None
    return ipods[0] if ipods else None


def _send_to_ipod(library: Library, playlists: list[LibraryPlaylist]) -> None:
    """For playlists saved to the iPod too: copy the new songs now (only when nothing would be removed)."""
    from synclassic import destinations

    if not destinations.wants_ipod(playlists):
        return
    ipod = _connected_ipod()
    if ipod is None:
        say("These playlists are saved to the iPod too: plug it in and run 'synclassic ipod sync'.")
        return
    from synclassic.ipod import rockbox
    from synclassic.ipod import sync as ipod_sync
    from synclassic.ipod.check import check

    say("")
    say(f"Copying the new songs to {ipod.name}…")
    try:
        if rockbox.mode(ipod) == rockbox.ROCKBOX:
            plan = rockbox.make_plan(library, ipod)
        else:
            if not check(ipod).safe_to_manage:
                say("The iPod check didn't pass, so nothing was copied: see 'synclassic ipod'.")
                return
            plan = ipod_sync.make_plan(library, ipod)
        for planned in getattr(plan, "waiting", []):
            say(f"⏸ {planned.wait_reason()}")
        if not plan.has_changes:
            say("The iPod already has everything it can have for now.")
            return
        if not plan.only_adds or not plan.fits:
            for line in plan.summary():
                say(line)
            say("This sync would also remove things (or doesn't fit), so it waits for you: synclassic ipod sync")
            return
        if isinstance(plan, rockbox.RockboxPlan):
            rockbox.apply(library, plan, say)
        else:
            ipod_sync.apply_plan(library, plan, say)
        say("Done. Eject before unplugging: synclassic ipod eject")
    except ipod_sync.SyncError as error:
        say(f"Copying to the iPod stopped: {error}")


def cmd_update(args) -> int:
    from synclassic.background import update_lock

    with update_lock() as ok:
        if not ok:
            say("Another update is running (the app, or the background update); skipping this one.")
            return 0
        if args.background:
            import datetime

            say(f"--- background update, {datetime.datetime.now():%Y-%m-%d %H:%M} ---")
        library = open_library()
        playlists = find_playlists(library, args.playlists)
        code = 0
        if playlists:
            code = run_update(library, playlists, retry_failed=args.retry_failed, download=not args.no_download,
                              to_ipod=not args.background)  # the background update never writes to the iPod
        elif not args.playlists:
            say("No playlists yet. Add one with: synclassic add <link or file>")
        if not args.playlists and not args.no_download:  # updating everything: podcasts too
            from synclassic.podcasts.store import Podcasts

            store = Podcasts(library)
            if store.podcasts():
                new, downloaded = store.update_all(load_settings().music_path, say)
                say(f"Podcasts: {new} new episodes, {downloaded} downloaded.")
        return code


def cmd_background(args) -> int:
    from synclassic import background

    if args.action == "on":
        say(background.enable(args.every))
    elif args.action == "off":
        say(background.disable())
    else:
        hours = background.current_hours()
        say(f"Background updates: {'every %g hours' % hours if hours else 'off'}.")
        if hours:
            say(f"Log: {background.log_path()}")
    return 0


def cmd_list(args) -> int:
    library = open_library()
    playlists = library.playlists()
    if not playlists:
        say("No playlists yet. Add one with: synclassic add <link or file>")
        return 0
    for playlist in playlists:
        counts = library.counts(playlist)
        total = sum(counts.values())
        ready = counts.get("downloaded", 0) + counts.get("review", 0) + counts.get("local", 0)
        issues = counts.get("unmatched", 0) + counts.get("failed", 0)
        line = f"{playlist.name}: {ready}/{total} ready"
        if issues:
            line += f", {issues} need attention"
        if counts.get("pending"):
            line += f", {counts['pending']} waiting to download"
        say(line)
        say(f"    from {playlist.spec.describe()}")
    return 0


STATUS_LABEL = {
    "downloaded": "ok",
    "local": "local file",
    "ipod": "only on the iPod",
    "review": "check",
    "pending": "waiting",
    "unmatched": "not found",
    "failed": "failed",
}


def cmd_show(args) -> int:
    library = open_library()
    playlist = find_playlists(library, [args.playlist])[0]
    for position, item in enumerate(library.playlist_tracks(playlist), start=1):
        label = STATUS_LABEL.get(item.status, item.status)
        if item.status in ("downloaded", "review") and not item.has_file:
            label = "file missing"
        say(f"{position:4d}. [{label:>10}] #{item.id}  {item.track.display()}")
    return 0


def cmd_review(args) -> int:
    from synclassic.matcher import Matcher
    from synclassic.picker import pick_interactively

    library = open_library()
    settings = load_settings()
    statuses = ("review", "unmatched", "failed")
    items = [i for i in library.tracks_in_playlists() if i.status in statuses]
    if args.playlist:
        wanted = {i.id for i in library.playlist_tracks(find_playlists(library, [args.playlist])[0])}
        items = [i for i in items if i.id in wanted]
    if not items:
        say("Nothing to review. Every song has a confident match.")
        return 0
    say(f"{len(items)} songs to check. Type ? for help at any prompt.")
    matcher = Matcher()
    changed = False
    for number, item in enumerate(items, start=1):
        say(f"\n--- {number}/{len(items)} ---")
        outcome = pick_interactively(library, item, settings, matcher)
        if outcome == "quit":
            break
        changed = changed or outcome in ("picked", "kept")
    if changed:
        finish(library)
    return 0


def cmd_pick(args) -> int:
    from synclassic.matcher import Matcher
    from synclassic.picker import pick_interactively

    library = open_library()
    item = library.track(args.track_id)
    if not item:
        say(f"No song #{args.track_id}. Song numbers are shown by: synclassic show <playlist>")
        return 1
    outcome = pick_interactively(library, item, load_settings(), Matcher())
    if outcome in ("picked", "kept"):
        finish(library)
    return 0


def cmd_rename(args) -> int:
    library = open_library()
    playlist = find_playlists(library, [args.playlist])[0]
    new_name = library.rename_playlist(playlist, args.new_name)
    finish(library)
    say(f"Renamed to '{new_name}'.")
    return 0


def cmd_remove(args) -> int:
    library = open_library()
    for playlist in find_playlists(library, args.playlists):
        library.remove_playlist(playlist)
        say(f"Removed '{playlist.name}'. (Songs stay on disk until you run 'synclassic clean'.)")
    finish(library)
    return 0


def cmd_clean(args) -> int:
    library = open_library()
    orphans = [o for o in library.orphan_tracks() if o.status != "local"]
    if not orphans:
        say("Nothing to clean: every downloaded song is in a playlist.")
        return 0
    say(f"{len(orphans)} songs are no longer in any playlist:")
    for item in orphans[:30]:
        say(f"  {item.track.display()}")
    if len(orphans) > 30:
        say(f"  ...and {len(orphans) - 30} more")
    if not args.yes and ask("Delete these files? [y/N] ").strip().lower() != "y":
        return 0
    from synclassic.organize import remove_song_file

    for item in orphans:
        remove_song_file(library, item)
        library.delete_track(item.id)
    finalize(library)
    say("Deleted.")
    return 0


def cmd_config(args) -> int:
    settings = load_settings()
    if args.key and args.value is not None:
        try:
            set_setting(settings, args.key, args.value)
        except ValueError as error:
            say(str(error))
            return 1
        save_settings(settings)
        if args.key == "folder_layout":
            library = open_library()
            finalize(library, settings)
            say(f"Folders arranged by {'playlist' if settings.folder_layout == 'playlist' else 'artist and album'}.")
    for key, help_text in SETTING_HELP.items():
        value = getattr(settings, key)
        if key == "music_dir" and not value:
            value = f"{settings.music_path} (default)"
        if args.key and key != args.key:
            continue
        say(f"{key} = {value}")
        say(f"    {help_text}")
    return 0


def _pick_ipod(args):
    from synclassic.ipod.device import find_ipods, ipod_at

    try:
        ipods = [ipod_at(args.path)] if args.path else find_ipods()
    except FileNotFoundError as error:
        say(str(error))
        return None
    if not ipods:
        say("No iPod found. Plug it in and wait until it shows up in Finder, then try again.")
        say("(If it's mounted somewhere unusual: synclassic ipod --path /Volumes/YourIPod)")
        return None
    wanted = getattr(args, "ipod", None)
    if wanted:
        matches = [i for i in ipods if wanted.casefold() in (i.name.casefold(), i.root.name.casefold())]
        matches = matches or [i for i in ipods if wanted.casefold() in i.name.casefold() or wanted.casefold() in i.root.name.casefold()]
        if not matches:
            say(f"No connected iPod is called “{wanted}”. Connected: " + ", ".join(f"{i.name} ({i.root})" for i in ipods))
            return None
        return matches[0]
    if len(ipods) > 1:
        say(f"More than one iPod is connected ({', '.join(i.name for i in ipods)}); using {ipods[0].name}. "
            f"Choose another with --ipod NAME.")
    return ipods[0]


def _ipod_playlists(ipod, names: list[str]) -> int:
    """Show or choose the playlists that go on this iPod."""
    from synclassic.ipod import prefs
    from synclassic.ipod.sync import playlist_key

    library = open_library()
    playlists = library.playlists()
    if names:
        if [n.lower() for n in names] == ["all"]:
            prefs.update(ipod, playlists=None)
        else:
            chosen = []
            for name in names:
                if name.isdigit() and 1 <= int(name) <= len(playlists):
                    chosen.append(playlists[int(name) - 1])
                    continue
                found = [p for p in playlists if p.name.casefold() == name.casefold()]
                if not found:
                    say(f"There's no playlist “{name}”. Use the numbers or names below.")
                    names = []
                    break
                chosen.extend(found)
            if names:
                prefs.update(ipod, playlists=[playlist_key(p) for p in chosen])
    current = prefs.load(ipod)
    on = {playlist_key(p) for p in prefs.chosen_playlists(library, ipod, current)}
    say(f"Playlists for {ipod.name}: " + ("all of them" if current.all_playlists else f"{len(on)} chosen"))
    for number, playlist in enumerate(playlists, start=1):
        mark = "x" if playlist_key(playlist) in on else " "
        count = len(library.playlist_track_ids(playlist))
        say(f"  [{mark}] {number:>3}. {playlist.name} ({count} song{'s' if count != 1 else ''})")
    say("Choose: synclassic ipod playlists 1 4 \"Road Trip\"   ·   every playlist: synclassic ipod playlists all")
    return 0


def cmd_ipod(args) -> int:
    from synclassic.ipod import sync as ipod_sync
    from synclassic.ipod.check import check, format_report

    ipod = _pick_ipod(args)
    if ipod is None:
        return 1
    action = args.action

    if action == "check":
        report = check(ipod)
        for line in format_report(report):
            say(line)
        return 0 if report.safe_to_manage else 2

    if action == "eject":
        say(ipod_sync.eject(ipod, force=args.force))
        return 0

    if action == "art":
        return _fix_art(ipod, args)

    if action == "audiobooks":
        from synclassic.ipod import manage

        paths = [pathlib.Path(name).expanduser() for name in args.names]
        if not paths or not all(p.is_file() for p in paths):
            say("Usage: synclassic ipod audiobooks FILE.m4b [FILE...]")
            return 1
        say(f"Added {manage.add_audiobooks(ipod, paths, say=say)} audiobooks.")
        return 0

    if action == "smart":
        from synclassic.ipod import smart

        if not args.names:
            current = smart.chosen(ipod_sync.load_state(ipod))
            for preset in smart.PRESETS:
                say(f"  [{'x' if preset.key in current else ' '}] {preset.key:<16} {preset.name}: {preset.description}")
            say("Choose: synclassic ipod smart recently_added top_25 ...   (none: synclassic ipod smart none)")
            return 0
        wanted = set() if args.names == ["none"] else set(args.names)
        unknown = wanted - set(smart.BY_KEY)
        if unknown:
            say(f"Unknown smart playlist: {', '.join(sorted(unknown))}")
            return 1
        changes = smart.set_smart_playlists(ipod, wanted, say=say)
        say("Smart playlists: " + (", ".join(changes) or "up to date"))
        return 0

    if action == "soundcheck":
        from synclassic.ipod import soundcheck

        count = len(soundcheck.missing(ipod))
        if not count:
            say("Every song on the iPod has Sound Check.")
            return 0
        say(f"Measuring {count} songs Synclassic didn't add (Ctrl+C keeps nothing; let it finish)…")
        done = soundcheck.apply(ipod, say=say, progress=lambda n, total, text: say(f"  [{n}/{total}] {text}"))
        say(f"Sound Check set for {done} songs. Turn it on on the iPod: Settings → Sound Check.")
        return 0

    if action == "restore":
        backups = ipod_sync.list_backups(ipod)
        if not backups:
            say("There are no backups of this iPod yet (one is made before every sync).")
            return 1
        if args.backup is None:
            say("Backups of this iPod (newest first):")
            for number, folder in enumerate(backups, start=1):
                say(f"  {number}. {folder.name}")
            say("Restore one with: synclassic ipod restore NUMBER")
            return 0
        if not 1 <= args.backup <= len(backups):
            say(f"Choose a number from 1 to {len(backups)}.")
            return 1
        folder = backups[args.backup - 1]
        if not args.yes and ask(f"Put the database from {folder.name} back on the iPod? [y/N] ").strip().lower() != "y":
            return 0
        ipod_sync.restore(ipod, folder)
        say(f"Restored the database from {folder.name}.")
        return 0

    if action == "playlists":
        return _ipod_playlists(ipod, args.names)

    if action == "size":
        from synclassic.ipod import prefs

        if args.names:
            choice = args.names[0].lower().removesuffix("k").removesuffix("kbps")
            bitrate = 0 if choice in ("as-is", "asis", "original", "0", "off") else int(choice) if choice.isdigit() else -1
            if bitrate not in prefs.BITRATES:
                say("Usage: synclassic ipod size as-is|256|192|128")
                return 1
            prefs.update(ipod, bitrate=bitrate)
        bitrate = prefs.load(ipod).bitrate
        say(f"Songs copied to {ipod.name}: " + (f"converted to {bitrate} kbps AAC when they're bigger" if bitrate
                                                else "as they are"))
        if bitrate:
            say("(Songs already on the iPod stay as they are.)")
        return 0

    if action == "mode":
        from synclassic.ipod import rockbox

        if not ipod.is_rockbox:
            say("This iPod doesn't have Rockbox; it's synced for Apple's firmware.")
            return 0
        if args.names:
            if args.names[0] not in (rockbox.APPLE, rockbox.ROCKBOX):
                say("Usage: synclassic ipod mode apple|rockbox")
                return 1
            rockbox.set_mode(ipod, args.names[0])
        say(f"Synced for: {'Rockbox' if rockbox.mode(ipod) == rockbox.ROCKBOX else 'Apple firmware'}")
        return 0

    # sync
    from synclassic.ipod import rockbox

    if rockbox.mode(ipod) == rockbox.ROCKBOX:
        library = open_library()
        plan = rockbox.make_plan(library, ipod)
        for line in plan.summary():
            say(line)
        if args.dry_run or not plan.has_changes or not plan.fits:
            return 0 if plan.fits else 1
        if not args.yes and ask("Sync now? [y/N] ").strip().lower() != "y":
            return 0
        rockbox.apply(library, plan, say)
        return 0
    report = check(ipod)
    if not report.safe_to_manage:
        for line in format_report(report):
            say(line)
        say("Not syncing: the check above has to pass first. Nothing was changed.")
        return 2
    library = open_library()
    if args.replace:
        from synclassic.ipod import prefs

        keys = []
        for name in args.replace:
            found = library.playlist_by_name(name)
            if found is None:
                say(f"There's no playlist “{name}” in Synclassic.")
                return 1
            keys.append(ipod_sync.playlist_key(found))
        if not args.dry_run:
            prefs.update(ipod, replace=sorted(set(prefs.load(ipod).replace) | set(keys)))
        else:
            current = prefs.load(ipod)
            current.replace = sorted(set(current.replace) | set(keys))
            prefs.save(ipod, current)  # for the preview; taken back below
    plan = ipod_sync.make_plan(library, ipod)
    if args.replace and args.dry_run:
        from synclassic.ipod import prefs

        current = prefs.load(ipod)
        current.replace = [k for k in current.replace if k not in keys]
        prefs.save(ipod, current)
    for line in plan.summary():
        say(line)
    if args.dry_run or not plan.has_changes or not plan.fits:
        if args.dry_run:
            say("(Dry run: nothing was changed.)")
        return 0 if plan.fits else 1
    if not args.yes and ask("Sync now? [y/N] ").strip().lower() != "y":
        return 0
    try:
        saved = ipod_sync.apply_plan(library, plan, say)
    except ipod_sync.SyncError as error:
        say(f"Sync stopped: {error}")
        return 1
    say(f"(Backup of the previous database: {saved})")
    from synclassic import lastfm, scrobble

    if load_settings().scrobble_after_sync and lastfm.connected_user() and scrobble.waiting():
        try:
            result = scrobble.submit()
            say(result.describe() + ".")
        except lastfm.LastfmError as error:
            say(f"Last.fm: {error} (the plays are kept for next time)")
    from synclassic import otg_spotify

    if otg_spotify.waiting():
        try:
            say(otg_spotify.send().describe())
        except Exception as error:  # offline, or the login needs renewing: they wait for next time
            say(f"Spotify: {error} (the On-The-Go playlists wait for next time)")
    if args.eject:
        say(ipod_sync.eject(ipod))
    else:
        say("Eject the iPod before unplugging it: synclassic ipod eject")
    return 0


def cmd_find(args) -> int:
    from synclassic.sources import store

    term = " ".join(args.words)
    country = load_settings().store_country
    try:
        if args.kind == "albums":
            for album in store.search_albums(term, country, limit=25):
                say(f"  {album.id:>14}  {album.title} — {album.artist}" + (f" ({album.year})" if album.year else ""))
            say("Get one: synclassic get album <id>   (e.g. dz:302127)")
        else:
            for song in store.search_songs(term, country, limit=25):
                say(f"  {song.id:>14}  {song.artist} - {song.title}  [{song.album}]")
            say("Get one: synclassic get song <id>")
    except store.StoreError as error:
        say(str(error))
        return 1
    return 0


def cmd_get(args) -> int:
    from synclassic.sources import store

    country = load_settings().store_country
    library = open_library()
    try:
        if args.kind == "album":
            album, songs = store.album_tracks(args.id, country)
            playlist = store.get_album(library, album, songs)
            say(f"Added the album {album.name} ({len(songs)} songs).")
        else:
            song = _store_song(store, args.id, country)
            if song is None:
                say("No song with that id.")
                return 1
            playlist = store.get_song(library, song)
            say(f"Added {song.artist} - {song.title} to {store.SONGS_NAME}.")
    except store.StoreError as error:
        say(str(error))
        return 1
    return run_update(library, [playlist], retry_failed=False, refresh=False)


def cmd_genius(args) -> int:
    from synclassic.sources import genius

    text = " ".join(args.song)
    if " - " not in text:
        say('Usage: synclassic genius "Artist - Song"')
        return 1
    artist, title = (part.strip() for part in text.split(" - ", 1))
    library = open_library()
    try:
        playlist = genius.make(library, artist, title)
    except genius.GeniusError as error:
        say(str(error))
        return 1
    say(f"Made “{playlist.name}” ({len(library.playlist_track_ids(playlist))} songs).")
    return run_update(library, [playlist], retry_failed=False, refresh=False)


def _store_song(store, song_id: str, country: str):
    if song_id.startswith("dz:"):
        found = store._get(f"{store.DEEZER}/track/{song_id[3:]}", {})
        album = store._get(f"{store.DEEZER}/album/{(found.get('album') or {}).get('id')}", {}) if found.get("album") else {}
        return store._deezer_song(found, album) if found.get("id") else None
    number = song_id.split(":")[-1]
    body = store._get(store.LOOKUP_URL, {"id": number, "country": store.country(country)})
    songs = [store._apple_song(r) for r in body.get("results", []) if r.get("wrapperType") == "track"]
    return songs[0] if songs else None


def cmd_lyrics(args) -> int:
    from synclassic.ipod import lyrics_sync
    from synclassic.ipod.device import find_ipods

    library = open_library()
    try:
        say("Looking up lyrics for songs on this Mac…")
        result = lyrics_sync.fill_library(library, progress=lambda n, total, text: say(f"  [{n}/{total}] {text}"), say=say)
        say("Mac: " + result.summary())
        ipods = [] if args.mac_only else find_ipods()
        if ipods:
            say(f"Looking up lyrics for songs on {ipods[0].name}…")
            result = lyrics_sync.fill_ipod(ipods[0], library, progress=lambda n, total, text: say(f"  [{n}/{total}] {text}"),
                                           say=say)
            say("iPod: " + result.summary())
    finally:
        library.close()
    return 0


def cmd_info(args) -> int:
    from synclassic.ipod import info_fill
    from synclassic.ipod.device import find_ipods

    library = open_library()
    try:
        say("Looking up missing genres and years for songs on this Mac…")
        result = info_fill.fill_library(library, progress=lambda n, total, text: say(f"  [{n}/{total}] {text}"), say=say)
        say("Mac: " + result.summary())
        ipods = [] if args.mac_only else find_ipods()
        if ipods:
            say(f"…and on {ipods[0].name}")
            result = info_fill.fill_ipod(ipods[0], library, progress=lambda n, total, text: say(f"  [{n}/{total}] {text}"),
                                         say=say)
            say("iPod: " + result.summary())
    finally:
        library.close()
    return 0


def _fix_art(ipod, args) -> int:
    from synclassic.ipod import art_fix

    library = open_library()
    try:
        say("Checking the covers on the iPod…")
        albums = art_fix.scan(ipod, library, everything=args.all)
    finally:
        library.close()
    if not albums:
        say("Every album on the iPod has a sharp cover.")
        return 0
    say(f"{len(albums)} albums could look better. Looking for sharp covers…")
    art_fix.find_covers(albums, progress=lambda n, total, text: say(f"  [{n}/{total}] {text}"))
    for album in albums:
        say(f"  {album.title} — {album.artist}: {album.problem} → {album.found}")
    found = [a for a in albums if a.new_cover]
    if not found:
        return 0
    if not args.apply:
        say(f"Put {len(found)} sharp covers on the iPod: synclassic ipod art{' --all' if args.all else ''} --apply")
        return 0
    count = art_fix.apply(ipod, found, say=say)
    say(f"New covers for {count} songs.")
    return 0


def cmd_lastfm(args) -> int:
    import webbrowser

    from synclassic import lastfm, scrobble

    if args.action == "login":
        default_key, default_secret = lastfm.app_key()
        key = args.key or default_key or ask("Last.fm API key: ").strip()
        secret = args.secret or default_secret or ask("Last.fm shared secret: ").strip()
        if not key or not secret:
            say(f"Create an API account first: {lastfm.CREATE_ACCOUNT_PAGE}")
            return 1
        say("Approve Synclassic on the Last.fm page that opens (waiting up to 3 minutes)…")
        try:
            user = lastfm.Client(key, secret).connect(lambda url: (say(url), webbrowser.open(url)))
        except lastfm.LastfmError as error:
            say(str(error))
            return 1
        say(f"Connected to Last.fm as {user}.")
        return 0
    if args.action == "logout":
        lastfm.disconnect()
        say("Disconnected from Last.fm.")
        return 0
    if args.action == "status":
        user = lastfm.connected_user()
        say(f"Last.fm: {'connected as ' + user if user else 'not connected'}; {scrobble.waiting()} plays and loves waiting.")
        return 0
    try:
        result = scrobble.submit()
    except lastfm.LastfmError as error:
        say(f"{error} (the plays are kept for next time)")
        return 1
    say(f"{result.describe()}; {result.left} waiting.")
    return 0


def cmd_podcast(args) -> int:
    from synclassic.podcasts import feed as feeds
    from synclassic.podcasts.store import Podcasts

    if args.action == "search":
        try:
            results = feeds.search(" ".join(args.words))
        except feeds.FeedError as error:
            say(str(error))
            return 1
        for result in results:
            say(f"{result.title} — {result.author} ({result.episodes} episodes)\n    {result.feed_url}")
        if not results:
            say("Nothing found.")
        return 0
    library = open_library()
    try:
        store = Podcasts(library)
        if args.action == "add":
            for url in args.words:
                try:
                    podcast = store.subscribe(url)
                except feeds.FeedError as error:
                    say(f"{url}: {error}")
                    return 1
                say(f"Subscribed to {podcast.title}.")
            return 0
        if args.action == "update":
            new, downloaded = store.update_all(load_settings().music_path, say)
            say(f"{new} new episodes, {downloaded} downloaded.")
            return 0
        if args.action == "keep":
            if len(args.words) != 2 or not args.words[1].isdigit():
                say("Usage: synclassic podcast keep <number from the list> <episodes>")
                return 1
            podcast = _podcast_by_number(store, args.words[0])
            if podcast is None:
                return 1
            store.set_keep(podcast.id, int(args.words[1]))
            say(f"{podcast.title}: keeps the {args.words[1]} newest unplayed episodes on the iPod.")
            return 0
        if args.action == "remove":
            for number in args.words:
                podcast = _podcast_by_number(store, number)
                if podcast is None:
                    return 1
                store.unsubscribe(podcast.id)
                say(f"Unsubscribed from {podcast.title}.")
            return 0
        podcasts = store.podcasts()
        for number, podcast in enumerate(podcasts, start=1):
            episodes = store.episodes(podcast.id)
            ready = sum(1 for e in episodes if e.downloaded and not e.played)
            say(f"{number}. {podcast.title} — {len(episodes)} episodes, {ready} new on this Mac, "
                f"keeps {podcast.keep_on_ipod} on the iPod")
        if not podcasts:
            say("No podcasts yet: synclassic podcast search <name>, then synclassic podcast add <feed address>.")
        return 0
    finally:
        library.close()


def _podcast_by_number(store, number: str):
    podcasts = store.podcasts()
    if not number.isdigit() or not 1 <= int(number) <= len(podcasts):
        say(f"No podcast number {number}; see synclassic podcast list.")
        return None
    return podcasts[int(number) - 1]


def cmd_install_app(args) -> int:
    from synclassic.macapp import build_app, default_destination

    if sys.platform != "darwin" and not args.dest:
        say("install-app makes a macOS app. On other systems run: synclassic-gui")
        return 1
    destination = pathlib.Path(args.dest).expanduser() if args.dest else default_destination()
    destination.mkdir(parents=True, exist_ok=True)
    app = build_app(destination)
    say(f"Made {app}")
    say("Open it from Finder (Applications) or Spotlight, and drag it to your Dock to keep it there.")
    say("Run this again after moving your synclassic folder or making a new .venv.")
    return 0


def cmd_doctor(args) -> int:
    from synclassic.downloader import check_tools

    settings = load_settings()
    say(f"Synclassic {__version__}")
    say(f"Settings and library: {app_dir()}")
    say(f"Music folder:         {settings.music_path}")
    problems = check_tools()
    for problem in problems:
        say(f"  ✗ {problem}")
    if not problems:
        from synclassic import tools

        say(f"  ✓ ffmpeg: {tools.where('ffmpeg')}; deno: {tools.where('deno')}")
    try:
        client = SpotifyClient.from_saved_login()
    except spotify_auth.SpotifyAuthError as error:
        client = None
        say(f"  ✗ Spotify: {error}")
    else:
        say("  ✓ Logged in to Spotify" if client else "  · Not logged in to Spotify (links still work, up to 100 songs)")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synclassic",
        description="Download your Spotify playlists as tagged M4A/MP3 files and keep them in sync.",
    )
    parser.add_argument("--version", action="version", version=f"synclassic {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("login", help="log in to Spotify (needed for Liked Songs and long playlists)")
    p.add_argument("--client-id", help="your Spotify developer app's Client ID")
    p.add_argument("--no-browser", action="store_true", help="print the login link instead of opening it")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="forget the Spotify login")
    p.set_defaults(func=cmd_logout)

    p = sub.add_parser("spotify-playlists", help="list the playlists in your Spotify account")
    p.set_defaults(func=cmd_spotify_playlists)

    p = sub.add_parser("add", help="add playlists: Spotify links, 'liked', .m3u8 or .csv files")
    p.add_argument("sources", nargs="*", help="Spotify playlist/album/track link, 'liked', or a file path")
    p.add_argument("--name", help="give the playlist your own name")
    p.add_argument("--all-mine", action="store_true", help="add Liked Songs and every playlist you own")
    p.add_argument("--no-download", action="store_true", help="only add; download later with 'update'")
    p.add_argument("--to", choices=["mac", "both", "ipod"],
                   help="where the new songs go: mac, both (this Mac and the iPod right away) or ipod (only the iPod)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("update", help="check playlists for new songs and download them")
    p.add_argument("playlists", nargs="*", help="only these playlists (default: all)")
    p.add_argument("--retry-failed", action="store_true", help="also retry songs that failed or weren't found")
    p.add_argument("--no-download", action="store_true", help="only check for changes")
    p.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("background", help="background updates (macOS): on [--every HOURS], off, status")
    p.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"])
    p.add_argument("--every", type=float, default=6, help="hours between updates (default 6)")
    p.set_defaults(func=cmd_background)

    p = sub.add_parser("list", help="show your playlists and how many songs are ready")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show the songs in a playlist")
    p.add_argument("playlist")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("review", help="listen to uncertain matches and pick the right version")
    p.add_argument("playlist", nargs="?", help="only songs from this playlist")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("pick", help="choose the YouTube version of one song yourself")
    p.add_argument("track_id", type=int, help="song number shown by 'synclassic show'")
    p.set_defaults(func=cmd_pick)

    p = sub.add_parser("rename", help="rename a playlist")
    p.add_argument("playlist")
    p.add_argument("new_name")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("remove", help="stop syncing playlists (files are kept)")
    p.add_argument("playlists", nargs="+")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("clean", help="delete songs that are no longer in any playlist")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("config", help="show or change settings (e.g. config format mp3)")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("ipod", help="iPod: check (default, read-only), sync, restore a backup, eject, art (fix album art), "
                                       "playlists (which go on it), size (smaller files)")
    p.add_argument("action", nargs="?", default="check", choices=["check", "sync", "restore", "eject", "art", "soundcheck", "smart", "audiobooks", "mode",
                            "playlists", "size"])
    p.add_argument("names", nargs="*", help="smart: which smart playlists to have; playlists: numbers or names "
                                            "(or 'all'); size: as-is, 256, 192 or 128")
    p.add_argument("backup", nargs="?", type=int, help="restore: backup number (see 'synclassic ipod restore')")
    p.add_argument("--path", help="the iPod's folder, if it isn't found automatically")
    p.add_argument("--ipod", help="which iPod, by name, when more than one is connected")
    p.add_argument("--dry-run", action="store_true", help="sync: only show what would change")
    p.add_argument("--replace", action="append", default=[], metavar="PLAYLIST",
                   help="sync: make the iPod's playlist of this name exactly Synclassic's version, even if it "
                        "waits otherwise (songs taken out of it stay on the iPod); can be given more than once")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("--eject", action="store_true", help="sync: eject the iPod afterwards")
    p.add_argument("--all", action="store_true", help="art: also check covers that can't be measured")
    p.add_argument("--apply", action="store_true", help="art: put the sharp covers on (default: just list them)")
    p.add_argument("--force", action="store_true", help="eject: even while macOS's device agent holds the iPod")
    p.set_defaults(func=cmd_ipod)

    p = sub.add_parser("report", help="details for reporting a problem (no passwords or keys); --out to save")
    p.add_argument("words", nargs="*", help="what happened (optional)")
    p.add_argument("--out", help="save to this file instead of showing it")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("gui", help="open the Synclassic app window")
    p.set_defaults(func=lambda args: __import__("synclassic.gui.app", fromlist=["main"]).main())

    p = sub.add_parser("lastfm", help="Last.fm: login, scrobble (send the iPod's plays), status, logout")
    p.add_argument("action", nargs="?", default="status", choices=["login", "scrobble", "status", "logout"])
    p.add_argument("--key", help="login: your Last.fm API key")
    p.add_argument("--secret", help="login: your Last.fm shared secret")
    p.set_defaults(func=cmd_lastfm)

    p = sub.add_parser("podcast", help="podcasts: list (default), search, add <feed>, update, keep <n> <episodes>, remove <n>")
    p.add_argument("action", nargs="?", default="list", choices=["list", "search", "add", "update", "keep", "remove"])
    p.add_argument("words", nargs="*", help="search words, feed addresses, or numbers from the list")
    p.set_defaults(func=cmd_podcast)

    p = sub.add_parser("find", help="search the music catalogue: find songs|albums WORDS")
    p.add_argument("kind", choices=["songs", "albums"])
    p.add_argument("words", nargs="+")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("get", help="get a song or album found with 'find': get song|album NUMBER")
    p.add_argument("kind", choices=["song", "album"])
    p.add_argument("id", help="as 'find' shows it, e.g. dz:3135556")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("genius", help='make a Genius playlist from a song: genius "Artist - Song"')
    p.add_argument("song", nargs="+")
    p.set_defaults(func=cmd_genius)

    p = sub.add_parser("lyrics", help="put lyrics inside songs that don't have them (this Mac and the iPod)")
    p.add_argument("--mac-only", action="store_true", help="leave the iPod alone")
    p.set_defaults(func=cmd_lyrics)

    p = sub.add_parser("info", help="fill in missing genres and years (this Mac and the iPod)")
    p.add_argument("--mac-only", action="store_true", help="leave the iPod alone")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("install-app", help="make a double-clickable Synclassic.app (macOS)")
    p.add_argument("--dest", help="folder to put it in (default ~/Applications)")
    p.set_defaults(func=cmd_install_app)

    p = sub.add_parser("doctor", help="check that everything needed is installed")
    p.set_defaults(func=cmd_doctor)
    return parser


def sane_terminal() -> None:
    """Put Terminal's typing back to normal: Return ends the line, and you see what you type.
    (The hidden browser that reads long Spotify playlists can leave it in a raw mode, where
    Return shows as ^M and questions never get their answer.)"""
    if not sys.stdin.isatty():
        return
    try:
        import termios

        attrs = termios.tcgetattr(sys.stdin.fileno())
        attrs[0] |= termios.ICRNL  # Return → end of line
        attrs[3] |= termios.ICANON | termios.ECHO  # whole lines, shown as typed
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attrs)
    except Exception:  # no termios, or termios.error: the question is still asked
        pass


def ask(question: str) -> str:
    """A question in Terminal (with typing set back to normal first)."""
    sane_terminal()
    return input(question).replace("\r", "")


def cmd_report(args) -> int:
    from synclassic import diagnostics

    text = diagnostics.collect(" ".join(args.words))
    if args.out:
        path = pathlib.Path(args.out).expanduser()
        path.write_text(text, encoding="utf-8")
        say(f"Saved the report to {path} (passwords, keys and e-mail addresses are taken out; read it before "
            "sharing). New issue: https://github.com/kerthmfy/synclassic/issues/new")
    else:
        say(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    from synclassic import tools

    tools.add_to_path()  # also when run by the background agent or from the standalone app
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        say("\nStopped. Run the same command again to continue where you left off.")
        return 130
    except Exception as error:
        import os

        if os.environ.get("SYNCLASSIC_DEBUG"):
            raise
        from synclassic import errors

        say(f"Something went wrong: {errors.remember(error)}")
        say("To report it: synclassic report --out ~/Desktop/synclassic-report.txt "
            "(the details of this error are included; SYNCLASSIC_DEBUG=1 shows them here)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
