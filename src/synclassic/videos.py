"""Music videos (optional): each song's official music video, made playable on the iPod.

The 5G/5.5G plays H.264 Baseline video up to 640x480 at about 1.5 Mbps with AAC sound, so the
video is converted to exactly that (.m4v). On the iPod they're Music Videos (Videos menu).
Songs without an official video are remembered and looked for again after a month.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

from synclassic.textnorm import safe_filename

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    track_id INTEGER PRIMARY KEY,
    video_id TEXT,
    path TEXT,
    status TEXT NOT NULL,          -- ready, none (no official video), failed
    error TEXT,
    checked_at TEXT NOT NULL
);
"""
RETRY_AFTER_DAYS = 30
MIN_SCORE = 0.72
NOT_THE_VIDEO = re.compile(r"\b(lyrics?|lyric video|audio|visuali[sz]er|live|cover|reaction|karaoke|8d|slowed|sped up)\b",
                           re.I)


@dataclass
class VideoReport:
    found: int = 0
    none: int = 0
    failed: int = 0
    stopped: bool = False


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class Videos:
    def __init__(self, library):
        self.library = library
        self.db = library.db
        self.db.executescript(SCHEMA)

    def path_for(self, track_id: int) -> pathlib.Path | None:
        row = self.db.execute("SELECT path FROM videos WHERE track_id = ? AND status = 'ready'", (track_id,)).fetchone()
        if row and row["path"] and pathlib.Path(row["path"]).is_file():
            return pathlib.Path(row["path"])
        return None

    def needs_looking(self, track_id: int) -> bool:
        row = self.db.execute("SELECT status, path, checked_at FROM videos WHERE track_id = ?", (track_id,)).fetchone()
        if row is None:
            return True
        if row["status"] == "ready":
            return not (row["path"] and pathlib.Path(row["path"]).is_file())
        checked = datetime.datetime.fromisoformat(row["checked_at"])
        return datetime.datetime.now(datetime.timezone.utc) - checked > datetime.timedelta(days=RETRY_AFTER_DAYS)

    def _set(self, track_id: int, status: str, video_id: str | None = None, path: str | None = None,
             error: str | None = None) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO videos (track_id, video_id, path, status, error, checked_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (track_id, video_id, path, status, error, _now()))

    def update(self, settings, matcher=None, say: Callable[[str], None] = lambda s: None, progress=None,
               should_stop: Callable[[], bool] | None = None, items=None,
               download: Callable | None = None) -> VideoReport:
        """Find and convert the music videos of downloaded songs that don't have one yet."""
        from synclassic.matcher import Matcher

        matcher = matcher or Matcher()
        download = download or download_video
        report = VideoReport()
        todo = [i for i in (items if items is not None else self.library.tracks_in_playlists())
                if i.status in ("downloaded", "review", "local") and self.needs_looking(i.id)]
        for number, item in enumerate(todo, start=1):
            if should_stop and should_stop():
                report.stopped = True
                break
            if progress:
                progress(number, len(todo), item.track.display())
            video = find_video(matcher, item.track)
            if video is None:
                self._set(item.id, "none")
                report.none += 1
                continue
            folder = settings.music_path / "Music Videos"
            destination = folder / safe_filename(f"{item.track.primary_artist} - {item.track.title}", 120)
            say(f"Music video: {item.track.display()}")
            try:
                path = download(video.video_id, destination, settings)
            except Exception as error:  # one video failing shouldn't stop the rest
                self._set(item.id, "failed", video.video_id, error=str(error))
                say(f"    couldn't get the video: {error}")
                report.failed += 1
                continue
            self._set(item.id, "ready", video.video_id, str(path))
            report.found += 1
        return report


def find_video(matcher, track):
    """The song's official music video on YouTube, or None."""
    from synclassic.matcher import candidate_from_result, score_candidate

    best = None
    for kind, query in (("videos", f"{track.primary_artist} {track.title} official video"),
                        ("videos", f"{track.primary_artist} {track.title}")):
        for raw in matcher.search(query, kind):
            cand = candidate_from_result(raw)
            if cand is None or NOT_THE_VIDEO.search(cand.title) and not NOT_THE_VIDEO.search(track.title):
                continue
            cand.kind = "video"
            score, _ = score_candidate(track, cand)
            score += 0.05  # videos are scored down by default; here they're what we want
            if re.search(r"official (music )?video", cand.title, re.I):
                score += 0.05
            if score >= MIN_SCORE and (best is None or score > best[0]):
                best = (score, cand)
        if best:
            break
    return best[1] if best else None


# --- getting it onto the iPod's terms ---------------------------------------------------------------


def ipod_video_args(source: pathlib.Path, target: pathlib.Path) -> list[str]:
    """ffmpeg arguments for the iPod 5G/5.5G: H.264 Baseline 3.0, at most 640x480, ~1.2 Mbps, AAC."""
    return ["ffmpeg", "-loglevel", "error", "-y", "-i", str(source),
            "-vf", "scale=640:480:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
            "-b:v", "1200k", "-maxrate", "1500k", "-bufsize", "3000k",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "44100",
            "-movflags", "+faststart", "-f", "mp4", str(target)]


def convert_for_ipod(source: pathlib.Path, target: pathlib.Path, run=subprocess.run) -> pathlib.Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.stem + ".part.m4v")
    result = run(ipod_video_args(source, partial), capture_output=True, text=True)
    if result.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Converting the video failed: {(result.stderr or '').strip()[-300:]}")
    partial.replace(target)
    return target


def download_video(video_id: str, destination: pathlib.Path, settings) -> pathlib.Path:
    """Download a video (up to 480p) and convert it for the iPod. Returns the .m4v file."""
    import yt_dlp

    from synclassic import downloader

    with tempfile.TemporaryDirectory(prefix="synclassic-video-") as tmp:
        workdir = pathlib.Path(tmp)
        options = downloader._ydl_options(settings, workdir)
        options.pop("postprocessors", None)
        options["format"] = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        options["merge_output_format"] = "mp4"
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        except yt_dlp.utils.DownloadError as error:
            raise downloader.DownloadError(downloader._friendly_error(str(error))) from error
        produced = sorted(p for p in workdir.iterdir() if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov"))
        if not produced:
            raise downloader.DownloadError("The video download produced no file.")
        return convert_for_ipod(produced[0], destination.with_suffix(".m4v"))


def has_h264() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "libx264" in result.stdout
