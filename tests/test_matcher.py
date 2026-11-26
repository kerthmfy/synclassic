from synclassic.matcher import ACCEPT, Matcher
from synclassic.models import Track
from synclassic.textnorm import core_title, safe_filename, split_artists


def result(video_id, title, artists, seconds, album="", kind="song"):
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": a} for a in artists],
        "album": {"name": album} if album else None,
        "duration_seconds": seconds,
        "resultType": kind,
        "thumbnails": [{"url": "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj", "width": 60}],
    }


def fake_search(table):
    def search(query, kind):
        return table.get(kind, [])

    return search


def test_core_title_strips_version_noise():
    assert core_title("One More Time - Remastered 2011") == "one more time"
    assert core_title("Song (feat. Someone) [Deluxe Edition]") == "song"
    assert core_title("Flower In A Hurricane (feat. A, B) [From GTAVI: Original Score]") == "flower in a hurricane"
    assert core_title("Live and Let Die") == "live and let die"


def test_split_artists_and_filenames():
    assert split_artists("A, B & C") == ["A", "B", "C"]
    assert safe_filename('AC/DC: "Back" in Black?') == "AC_DC_ _Back_ in Black_"
    assert safe_filename("   ") == "Unknown"


def test_prefers_studio_version_over_live_and_cover():
    track = Track("Karma Police", ["Radiohead"], album="OK Computer", duration_ms=264_000)
    matcher = Matcher(fake_search({"songs": [
        result("live0000000", "Karma Police (Live)", ["Radiohead"], 270),
        result("cover000000", "Karma Police", ["Some Cover Band"], 262),
        result("studio00000", "Karma Police", ["Radiohead"], 264, album="OK Computer"),
    ]}))
    best = matcher.match(track)
    assert best.candidate.video_id == "studio00000"
    assert best.status == "matched" and best.score >= ACCEPT
    assert "w1000-h1000" in best.candidate.thumbnail


def test_wrong_length_is_not_trusted():
    track = Track("Intro", ["The xx"], duration_ms=128_000)
    matcher = Matcher(fake_search({"songs": [result("long0000000", "Intro", ["The xx"], 420)]}))
    assert matcher.match(track).status == "unmatched"


def test_music_video_titles_are_understood():
    track = Track("Feel Good Inc.", ["Gorillaz"], duration_ms=221_000)
    matcher = Matcher(fake_search({"videos": [result("video000000", "Gorillaz - Feel Good Inc. (Official Video)", ["Gorillaz"], 223, kind="video")]}))
    best = matcher.match(track)
    assert best.candidate.video_id == "video000000"
    assert best.status in ("matched", "review")


def test_rank_returns_choices_best_first_without_duplicates():
    track = Track("Digital Love", ["Daft Punk"], duration_ms=301_000)
    songs = [
        result("aaaaaaaaaaa", "Digital Love", ["Daft Punk"], 301),
        result("bbbbbbbbbbb", "Digital Love (Remix)", ["Daft Punk"], 330),
    ]
    matcher = Matcher(fake_search({"songs": songs, "videos": songs[:1]}))
    choices = matcher.rank(track)
    assert [c.candidate.video_id for c in choices] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert choices[0].score > choices[1].score
