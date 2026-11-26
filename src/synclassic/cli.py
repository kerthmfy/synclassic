"""The `synclassic` command."""

from __future__ import annotations

import argparse
import pathlib
import sys

from synclassic import __version__, playlist_writer
from synclassic.config import SETTING_HELP, app_dir, load_settings, save_settings, set_setting
from synclassic.library import Library, LibraryPlaylist
from synclassic.sources import SourceError, read_source, specs_from_input
from synclassic.sources import spotify_auth
from synclassic.sources.spotify_api import SpotifyApiError, SpotifyClient
from synclassic.update import DownloadReport, RefreshReport, apply_source, download_missing, refresh_playlist


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
    written = playlist_writer.write_all(library, settings.music_path)
    if written:
        say(f"Playlists saved in {playlist_writer.playlists_dir(settings.music_path)}")


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
        client_id = input("Client ID: ").strip()
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


def add_sources(library: Library, inputs: list[str], name: str | None) -> list[LibraryPlaylist]:
    client = spotify_client()
    added: list[LibraryPlaylist] = []
    for text in inputs:
        try:
            specs = specs_from_input(text)
        except SourceError as error:
            say(f"Skipped '{text}': {error}")
            continue
        for spec in specs:
            existing = library.playlist_by_spec(spec)
            if existing:
                say(f"Already added as '{existing.name}'.")
                added.append(existing)
                continue
            try:
                source = read_source(spec, client)
            except SourceError as error:
                say(f"Couldn't read {spec.describe()}: {error}")
                continue
            wanted = name if (name and len(inputs) == 1 and len(specs) == 1) else source.name
            playlist = library.add_playlist(wanted, spec)
            apply_source(library, playlist, source)
            say(f"Added '{playlist.name}' ({len(source.tracks)} songs)")
            if source.note:
                say(f"  Note: {source.note}")
            added.append(playlist)
    return added


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
    if args.no_download:
        finish(library)
        say("Added. Run 'synclassic update' to download the songs.")
        return 0
    return run_update(library, playlists, retry_failed=False, refresh=False)


def run_update(
    library: Library, playlists: list[LibraryPlaylist], retry_failed: bool, download: bool = True, refresh: bool = True
) -> int:
    settings = load_settings()
    if refresh:
        client = spotify_client()
        say("Checking for changes...")
        for playlist in playlists:
            print_refresh(refresh_playlist(library, playlist, client))
    if download:
        say("")
        say(f"Saving music to {settings.music_path} as {settings.format.upper()}")
        report = download_missing(library, settings, say, retry_failed=retry_failed, only=playlists)
        print_download_report(report)
        if not (report.downloaded or report.review or report.unmatched or report.failed or report.stopped_early):
            say("Everything is already downloaded.")
    finish(library)
    return 0


def cmd_update(args) -> int:
    library = open_library()
    playlists = find_playlists(library, args.playlists)
    if not playlists:
        say("No playlists yet. Add one with: synclassic add <link or file>")
        return 0
    return run_update(library, playlists, retry_failed=args.retry_failed, download=not args.no_download)


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
    if not args.yes and input("Delete these files? [y/N] ").strip().lower() != "y":
        return 0
    for item in orphans:
        if item.file_path and pathlib.Path(item.file_path).is_file():
            pathlib.Path(item.file_path).unlink()
        library.delete_track(item.id)
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
    for key, help_text in SETTING_HELP.items():
        value = getattr(settings, key)
        if key == "music_dir" and not value:
            value = f"{settings.music_path} (default)"
        if args.key and key != args.key:
            continue
        say(f"{key} = {value}")
        say(f"    {help_text}")
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
        say("  ✓ ffmpeg and a JavaScript runtime are installed")
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
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("update", help="check playlists for new songs and download them")
    p.add_argument("playlists", nargs="*", help="only these playlists (default: all)")
    p.add_argument("--retry-failed", action="store_true", help="also retry songs that failed or weren't found")
    p.add_argument("--no-download", action="store_true", help="only check for changes")
    p.set_defaults(func=cmd_update)

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

    p = sub.add_parser("doctor", help="check that everything needed is installed")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
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


if __name__ == "__main__":
    sys.exit(main())
