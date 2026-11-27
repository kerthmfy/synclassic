"""Music videos: finding the official one, converting it for the iPod, syncing it."""

import json
import shutil
import subprocess

import pytest

from synclassic import config, videos
from synclassic.ipod import itunesdb, sync
from synclassic.ipod.check import check
from synclassic.matcher import Matcher
from synclassic.models import Track
from tests.test_ipod_sync import ipod, library_with, read_db, song  # noqa: F401

needs_x264 = pytest.mark.skipif(not videos.has_h264(), reason="ffmpeg without libx264")


def result(video_id, title, seconds=200):
    return {"videoId": video_id, "title": title, "artists": [{"name": "Synclassic Artist"}], "duration_seconds": seconds,
            "resultType": "video"}


def test_the_official_video_is_chosen():
    def search(query, kind):
        assert kind == "videos"
        return [result("lyric", "New Song 1 (Lyric Video)"), result("audio", "New Song 1 (Official Audio)"),
                result("omv", "New Song 1 (Official Video)", 215), result("other", "Something Else")]

    found = videos.find_video(Matcher(search), Track("New Song 1", ["Synclassic Artist"], duration_ms=200_000))
    assert found.video_id == "omv"
    assert videos.find_video(Matcher(lambda q, k: [result("x", "Totally Different")]),
                             Track("New Song 1", ["Synclassic Artist"])) is None


def make_video(path, size="1280x720"):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(path)], check=True)
    return path


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


@needs_x264
def test_conversion_fits_the_ipod(tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    out = videos.convert_for_ipod(make_video(tmp_path / "hd.mp4"), tmp_path / "out.m4v")
    streams = probe(out)
    video, audio = streams["video"], streams["audio"]
    assert video["codec_name"] == "h264" and video["profile"] in ("Baseline", "Constrained Baseline")
    assert (video["width"], video["height"]) == (640, 360) and int(video["level"]) <= 30
    assert audio["codec_name"] == "aac" and audio["channels"] == 2


@needs_x264
def test_videos_are_found_converted_and_synced(tmp_path, ipod):
    settings = config.load_settings()
    settings.music_videos = True
    config.save_settings(settings)
    library = library_with(tmp_path, {"Mix": [song(1), song(2)]})
    source = make_video(tmp_path / "src.mp4", "640x480")

    def search(query, kind):
        return [result("omv1", "New Song 1 (Official Video)", 2)] if "New Song 1" in query else []

    def download(video_id, destination, settings):
        return videos.convert_for_ipod(source, destination.with_suffix(".m4v"))

    report = videos.Videos(library).update(settings, Matcher(search), download=download)
    assert (report.found, report.none) == (1, 1)
    assert videos.Videos(library).update(settings, Matcher(search), download=download).found == 0  # remembered

    plan = sync.make_plan(library, ipod)
    assert len(plan.videos) == 1 and "Copy 1 music video" in "\n".join(plan.summary())
    sync.apply_plan(library, plan, lambda s: None)
    db = read_db(ipod)
    clip = next(s for s in itunesdb.songs(db) if s.media_type == 0x20)
    assert clip.title == "New Song 1" and clip.chunk.u8(0xB1) == 1 and clip.chunk.u32(0x18) == 0x4D345620
    assert (ipod.root / clip.relative_path).suffix == ".m4v"
    assert check(ipod).safe_to_manage
    assert not sync.make_plan(library, ipod).videos  # not copied twice

    library.remove_playlist(library.playlists()[0])
    plan = sync.make_plan(library, ipod)
    assert len(plan.remove_videos) == 1
    sync.apply_plan(library, plan, lambda s: None)
    assert not [s for s in itunesdb.songs(read_db(ipod)) if s.media_type == 0x20]
