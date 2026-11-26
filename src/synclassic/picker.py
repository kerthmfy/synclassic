"""Choose the right recording by hand, with a listen-first preview.

Used by 'synclassic review' (songs the matcher wasn't sure about) and 'synclassic pick'.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import webbrowser
from collections.abc import Callable

from synclassic.config import Settings
from synclassic.library import Library, LibraryTrack
from synclassic.matcher import Candidate, Matcher, MatchResult, candidate_from_result, score_candidate
from synclassic.update import save_candidate

_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")

HELP = """\
  1-8          download that choice
  p 1-8        play a preview (press Enter to stop)
  s WORDS      search YouTube Music with your own words
  u LINK       use a YouTube / YouTube Music link you found yourself
  k            keep the current file (mark this song as OK)
  n            skip for now
  q            quit"""


def video_id_from_text(text: str) -> str | None:
    text = text.strip()
    match = _VIDEO_ID.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    return None


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "--:--"
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def describe_choice(number: int, result: MatchResult, wanted_seconds: float | None) -> str:
    cand = result.candidate
    assert cand is not None
    length = _fmt_duration(cand.duration_s)
    if wanted_seconds and cand.duration_s:
        diff = int(round(cand.duration_s - wanted_seconds))
        if diff:
            length += f" ({diff:+d}s)"
    kind = "video" if cand.kind == "video" else "song"
    album = f"  ·  {cand.album}" if cand.album else ""
    explicit = " [E]" if cand.explicit else ""
    line = f"  {number}. {', '.join(cand.artists) or '?'} - {cand.title}{explicit}{album}  ·  {length}  ·  {kind}  ·  match {result.score:.2f}"
    if result.notes:
        line += f"\n       ({'; '.join(result.notes)})"
    return line


def preview(video_id: str, settings: Settings, wait: Callable[[], object] = input) -> str:
    """Play a recording. Uses ffplay when available (audio only), otherwise opens the browser."""
    url = f"https://music.youtube.com/watch?v={video_id}"
    ffplay = shutil.which("ffplay")
    if ffplay:
        stream = stream_url(video_id, settings)
        if stream:
            process = subprocess.Popen(
                [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", stream],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                wait()
            except (EOFError, KeyboardInterrupt):
                pass
            finally:
                if process.poll() is None:
                    process.terminate()
            return "played"
    webbrowser.open(url)
    return f"opened {url} in your browser"


def stream_url(video_id: str, settings: Settings) -> str | None:
    import yt_dlp

    options = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True}
    if settings.cookies_from_browser:
        options["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
    except Exception:
        return None
    return (info or {}).get("url")


def candidate_from_link(matcher: Matcher, item: LibraryTrack, video_id: str) -> MatchResult:
    """Look the pasted video up so we can still show its title and fill in tags."""
    cand = Candidate(video_id, "", [])
    try:
        for raw in matcher.search(video_id, "songs") + matcher.search(video_id, "videos"):
            if raw.get("videoId") == video_id:
                found = candidate_from_result(raw)
                if found:
                    cand = found
                    break
    except Exception:
        pass
    score = score_candidate(item.track, cand)[0] if cand.title else 1.0
    return MatchResult(cand, score, "matched", ["chosen by you"])


def pick_interactively(
    library: Library,
    item: LibraryTrack,
    settings: Settings,
    matcher: Matcher,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> str:
    """Returns 'picked', 'kept', 'skipped' or 'quit'."""
    track = item.track
    wanted = (track.duration_ms or 0) / 1000 or None
    say("")
    say(f"Song:   {track.display()}" + (f"  ·  {track.album}" if track.album else "") + f"  ·  {_fmt_duration(wanted)}")
    state = {"downloaded": "downloaded, but the match is uncertain", "review": "downloaded, but the match is uncertain",
             "unmatched": "no confident match found", "failed": f"failed: {item.error or 'unknown error'}"}
    say(f"Status: {state.get(item.status, item.status)}")
    if item.video_id:
        say(f"Current pick: https://music.youtube.com/watch?v={item.video_id}")

    say("Searching YouTube Music...")
    choices = matcher.rank(track)
    while True:
        if choices:
            for number, result in enumerate(choices, start=1):
                say(describe_choice(number, result, wanted))
        else:
            say("  (no results; try 's' with different words or paste a link with 'u')")
        answer = ask("Choose [1-8, p N, s words, u link, k, n, q, ?]: ").strip()
        if not answer:
            continue
        command, _, rest = answer.partition(" ")
        command = command.lower()
        if command in ("?", "h", "help"):
            say(HELP)
        elif command == "q":
            return "quit"
        elif command == "n":
            return "skipped"
        elif command == "k":
            if item.has_file:
                library.update_track(item.id, status="downloaded", manual=True, match_notes="kept by you")
                say("Kept.")
                return "kept"
            say("There's no downloaded file to keep for this song; pick a choice instead.")
        elif command == "p":
            index = _index(rest, choices)
            if index is None:
                say("Which one? e.g. p 2")
                continue
            say("Playing... press Enter to stop.")
            outcome = preview(choices[index].candidate.video_id, settings, wait=lambda: ask(""))  # type: ignore[union-attr]
            if outcome != "played":
                say(outcome)
        elif command == "s":
            if not rest.strip():
                say("Type what to search for, e.g. s daft punk one more time radio edit")
                continue
            say("Searching...")
            choices = matcher.rank(track, query=rest.strip())
        elif command == "u":
            video_id = video_id_from_text(rest)
            if not video_id:
                say("That doesn't look like a YouTube link.")
                continue
            choice = candidate_from_link(matcher, item, video_id)
            return _download(library, item, choice, settings, say)
        else:
            index = _index(answer, choices)
            if index is None:
                say("Type a number from the list, or ? for help.")
                continue
            return _download(library, item, choices[index], settings, say)


def _index(text: str, choices: list[MatchResult]) -> int | None:
    try:
        number = int(text.strip())
    except ValueError:
        return None
    return number - 1 if 1 <= number <= len(choices) else None


def _download(library: Library, item: LibraryTrack, choice: MatchResult, settings: Settings, say) -> str:
    say("Downloading your choice...")
    status = save_candidate(library, item, choice, settings, lambda text: say(text), manual=True)
    return "picked" if status == "downloaded" else status
