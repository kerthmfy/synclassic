"""Download a YouTube Music recording as M4A (AAC) or MP3."""

from __future__ import annotations

import pathlib
import shutil
import tempfile
from dataclasses import dataclass

from synclassic.config import Settings


class DownloadError(RuntimeError):
    pass


@dataclass
class DownloadResult:
    path: pathlib.Path
    source_codec: str
    source_bitrate: float | None
    converted: bool


def check_tools() -> list[str]:
    """Problems that would stop downloads from working, as readable messages."""
    problems = []
    if not shutil.which("ffmpeg"):
        problems.append("ffmpeg is not installed. On a Mac run: brew install ffmpeg")
    if not shutil.which("deno") and not shutil.which("node"):
        problems.append("YouTube needs a JavaScript runtime for downloads. On a Mac run: brew install deno")
    return problems


def _ydl_options(settings: Settings, workdir: pathlib.Path) -> dict:
    fmt = settings.format
    if fmt == "m4a":
        # YouTube Music already serves AAC in M4A: take it as-is (no re-encoding) whenever possible.
        format_selector = "bestaudio[ext=m4a]/bestaudio/best"
        quality = "256"  # only used if the source isn't AAC and must be converted
    else:
        format_selector = "bestaudio/best"
        quality = "0" if settings.mp3_quality == "v0" else "320"
    options = {
        "format": format_selector,
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": quality}],
    }
    js_runtimes = {}
    if shutil.which("deno"):
        js_runtimes["deno"] = {}
    if shutil.which("node"):
        js_runtimes["node"] = {}
    if js_runtimes:
        options["js_runtimes"] = js_runtimes
    if settings.cookies_from_browser:
        options["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    return options


def download(video_id: str, destination: pathlib.Path, settings: Settings) -> DownloadResult:
    """Download into `destination` (without extension); returns the final file path."""
    import yt_dlp

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synclassic-") as tmp:
        workdir = pathlib.Path(tmp)
        try:
            with yt_dlp.YoutubeDL(_ydl_options(settings, workdir)) as ydl:
                info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=True)
        except yt_dlp.utils.DownloadError as error:
            raise DownloadError(_friendly_error(str(error))) from error
        produced = sorted(workdir.glob(f"*.{settings.format}"))
        if not produced:
            raise DownloadError("The download finished but no audio file was produced (is ffmpeg installed?).")
        final = destination.with_suffix(f".{settings.format}")
        shutil.move(str(produced[0]), final)
    codec = (info or {}).get("acodec") or ""
    converted = not (settings.format == "m4a" and codec.startswith("mp4a"))
    return DownloadResult(final, codec, (info or {}).get("abr"), converted)


def _friendly_error(message: str) -> str:
    text = message.replace("ERROR: ", "").strip()
    lowered = text.lower()
    if "sign in to confirm" in lowered and "bot" in lowered:
        return "YouTube asked to confirm you're not a bot. Wait a while, or set cookies_from_browser."
    if "age" in lowered and ("restricted" in lowered or "confirm your age" in lowered):
        return "Age-restricted on YouTube. Set cookies_from_browser to a browser where you're signed in."
    if "video unavailable" in lowered or "not available" in lowered:
        return "This recording isn't available on YouTube in your country."
    if "403" in lowered:
        return "YouTube refused the download (HTTP 403). Try again later or update: pip install -U yt-dlp"
    return text.splitlines()[0][:300] if text else "Download failed"
