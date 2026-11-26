"""Find the YouTube Music recording that matches a song."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from synclassic.models import Track
from synclassic.textnorm import core_title, normalize, variant_words

ACCEPT = 0.80  # downloaded and trusted
REVIEW = 0.60  # downloaded, but listed by 'synclassic review' so you can check it


@dataclass
class Candidate:
    video_id: str
    title: str
    artists: list[str]
    album: str = ""
    duration_s: int | None = None
    explicit: bool | None = None
    thumbnail: str | None = None
    kind: str = "song"  # "song" (official audio) or "video"

    @property
    def url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"


@dataclass
class MatchResult:
    candidate: Candidate | None
    score: float
    status: str  # "matched", "review" or "unmatched"
    notes: list[str] = field(default_factory=list)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 4 and f" {shorter} " in f" {longer} ":
        ratio = max(ratio, 0.9)
    return ratio


def _duration_score(track_ms: int | None, cand_s: int | None) -> tuple[float, float | None]:
    if not track_ms or not cand_s:
        return 0.6, None
    diff = abs(track_ms / 1000 - cand_s)
    if diff <= 3:
        return 1.0, diff
    if diff <= 6:
        return 0.85, diff
    if diff <= 10:
        return 0.6, diff
    if diff <= 20:
        return 0.3, diff
    return 0.0, diff


def score_candidate(track: Track, cand: Candidate) -> tuple[float, list[str]]:
    notes: list[str] = []
    src_title = core_title(track.title)
    cand_title = core_title(cand.title)
    title_score = _ratio(src_title, cand_title)

    src_artists = [normalize(a) for a in track.artists if a]
    cand_artists = [normalize(a) for a in cand.artists if a]
    if src_artists and cand_artists:
        artist_score = max(_ratio(s, c) for s in src_artists for c in cand_artists)
    elif src_artists:
        artist_score = 0.0
    else:
        artist_score = 0.5  # the source didn't tell us the artist
    # Music videos are often titled "Artist - Song" on a channel with another name.
    if src_artists and artist_score < 0.8 and any(a and a in normalize(cand.title) for a in src_artists):
        artist_score = max(artist_score, 0.85)
        if cand_title.count(" ") > src_title.count(" "):
            title_score = max(title_score, _ratio(src_title, core_title(re.split(r"\s+-\s+", cand.title, 1)[-1])))

    duration_score, diff = _duration_score(track.duration_ms, cand.duration_s)

    score = 0.45 * title_score + 0.35 * artist_score + 0.20 * duration_score
    if track.album and cand.album and _ratio(core_title(track.album), core_title(cand.album)) >= 0.9:
        score += 0.05

    extra_words = variant_words(cand.title) - variant_words(track.title)
    if extra_words:
        score -= min(0.25 * len(extra_words), 0.5)
        notes.append("looks like a different version: " + ", ".join(sorted(extra_words)))
    if diff is not None and diff > 45:
        score = min(score, 0.4)
        notes.append(f"length differs by {int(diff)}s")
    if track.explicit is True and cand.explicit is False:
        score -= 0.05
        notes.append("clean version (original is explicit)")
    if cand.kind == "video":
        score -= 0.05
    return max(0.0, min(1.0, score)), notes


def _thumbnail(thumbnails: list[dict] | None) -> str | None:
    if not thumbnails:
        return None
    url = max(thumbnails, key=lambda t: t.get("width") or 0).get("url")
    if url and "googleusercontent.com" in url:
        url = re.sub(r"=w\d+-h\d+", "=w1000-h1000", url)
    return url


def candidate_from_result(result: dict) -> Candidate | None:
    video_id = result.get("videoId")
    if not video_id:
        return None
    album = result.get("album") or {}
    return Candidate(
        video_id=video_id,
        title=result.get("title") or "",
        artists=[a.get("name") for a in result.get("artists") or [] if a.get("name")],
        album=album.get("name", "") if isinstance(album, dict) else str(album),
        duration_s=result.get("duration_seconds"),
        explicit=result.get("isExplicit"),
        thumbnail=_thumbnail(result.get("thumbnails")),
        kind="video" if result.get("resultType") == "video" else "song",
    )


SearchFn = Callable[[str, str], list[dict]]


class Matcher:
    def __init__(self, search: SearchFn | None = None):
        self._search = search
        self._client = None

    def search(self, query: str, kind: str) -> list[dict]:
        if self._search:
            return self._search(query, kind)
        if self._client is None:
            from ytmusicapi import YTMusic

            self._client = YTMusic()
        return self._client.search(query, filter=kind, limit=10) or []

    @staticmethod
    def _queries(track: Track) -> list[tuple[str, str]]:
        base_title = core_title(track.title) if track.title else ""
        queries = [
            ("songs", f"{track.primary_artist} {track.title}".strip()),
            ("songs", f"{' '.join(track.artists)} {base_title}".strip()),
            ("videos", f"{track.primary_artist} {track.title}".strip()),
        ]
        unique: list[tuple[str, str]] = []
        for query in queries:
            if query[1] and query not in unique:
                unique.append(query)
        return unique

    def _scored(self, track: Track, kind: str, query: str) -> list[MatchResult]:
        results = []
        for raw in self.search(query, kind):
            cand = candidate_from_result(raw)
            if not cand:
                continue
            if kind == "videos":
                cand.kind = "video"
            score, notes = score_candidate(track, cand)
            results.append(MatchResult(cand, score, _status(score), notes))
        return results

    def match(self, track: Track) -> MatchResult:
        """The best recording. Stops searching early once a confident match is found."""
        best = MatchResult(None, 0.0, "unmatched", ["no results"])
        for kind, query in self._queries(track):
            for result in self._scored(track, kind, query):
                if result.score > best.score:
                    best = result
            if best.score >= ACCEPT:
                break
        return best

    def rank(self, track: Track, query: str | None = None, limit: int = 8) -> list[MatchResult]:
        """Several choices, best first, for picking by hand. `query` replaces the automatic search."""
        searches = [("songs", query), ("videos", query)] if query else self._queries(track)
        by_id: dict[str, MatchResult] = {}
        for kind, text in searches:
            for result in self._scored(track, kind, text):
                vid = result.candidate.video_id  # type: ignore[union-attr]
                if vid not in by_id or result.score > by_id[vid].score:
                    by_id[vid] = result
        return sorted(by_id.values(), key=lambda r: r.score, reverse=True)[:limit]


def _status(score: float) -> str:
    return "matched" if score >= ACCEPT else "review" if score >= REVIEW else "unmatched"
