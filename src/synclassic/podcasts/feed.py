"""Find podcasts (the iTunes podcast directory) and read their RSS feeds."""

from __future__ import annotations

import datetime
import email.utils
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field

import requests

SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
USER_AGENT = "Synclassic/1.0 (podcast client)"


class FeedError(RuntimeError):
    pass


@dataclass
class SearchResult:
    title: str
    author: str
    feed_url: str
    artwork_url: str
    genre: str = ""
    episodes: int = 0


@dataclass
class Episode:
    guid: str
    title: str
    url: str  # the enclosure (audio/video file)
    published: datetime.datetime | None = None
    duration_s: int = 0
    size: int = 0
    mime: str = ""
    description: str = ""
    number: int = 0

    @property
    def is_video(self) -> bool:
        return self.mime.startswith("video/") or self.url.lower().split("?")[0].endswith((".mp4", ".m4v", ".mov"))


@dataclass
class Feed:
    title: str
    author: str = ""
    description: str = ""
    artwork_url: str = ""
    link: str = ""
    category: str = ""
    episodes: list[Episode] = field(default_factory=list)


def search(term: str, session: requests.Session | None = None, limit: int = 30) -> list[SearchResult]:
    http = session or requests
    try:
        response = http.get(SEARCH_URL, params={"media": "podcast", "term": term, "limit": limit}, timeout=30,
                            headers={"User-Agent": USER_AGENT})
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        raise FeedError(f"Couldn't search the podcast directory: {error}") from error
    results = []
    for item in data.get("results", []):
        if not item.get("feedUrl"):
            continue
        results.append(SearchResult(
            title=item.get("collectionName") or item.get("trackName") or "",
            author=item.get("artistName", ""),
            feed_url=item["feedUrl"],
            artwork_url=item.get("artworkUrl600") or item.get("artworkUrl100") or "",
            genre=item.get("primaryGenreName", ""),
            episodes=int(item.get("trackCount") or 0),
        ))
    return results


def fetch(url: str, session: requests.Session | None = None) -> Feed:
    http = session or requests
    try:
        response = http.get(url, timeout=60, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as error:
        raise FeedError(f"Couldn't reach the podcast: {error}") from error
    if response.status_code != 200:
        raise FeedError(f"The podcast's feed answered HTTP {response.status_code}.")
    return parse(response.content)


def _text(element, path: str) -> str:
    found = element.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _duration(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    parts = [p for p in text.split(":") if p.strip().isdigit()]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _plain(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse(content: bytes) -> Feed:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise FeedError("That address isn't a podcast feed (it isn't valid RSS).") from error
    channel = root.find("channel")
    if channel is None:
        raise FeedError("That address isn't a podcast feed (no RSS channel).")
    image = channel.find(f"{ITUNES_NS}image")
    artwork = image.get("href", "") if image is not None else _text(channel, "image/url")
    category = channel.find(f"{ITUNES_NS}category")
    feed = Feed(
        title=_text(channel, "title") or "Untitled podcast",
        author=_text(channel, f"{ITUNES_NS}author") or _text(channel, "managingEditor"),
        description=_plain(_text(channel, "description") or _text(channel, f"{ITUNES_NS}summary")),
        artwork_url=artwork,
        link=_text(channel, "link"),
        category=category.get("text", "") if category is not None else "",
    )
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None or not enclosure.get("url"):
            continue
        published = None
        date_text = _text(item, "pubDate")
        if date_text:
            try:
                published = email.utils.parsedate_to_datetime(date_text)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=datetime.timezone.utc)
            except (TypeError, ValueError):
                published = None
        number = _text(item, f"{ITUNES_NS}episode")
        try:
            size = int(enclosure.get("length") or 0)
        except ValueError:
            size = 0
        feed.episodes.append(Episode(
            guid=_text(item, "guid") or enclosure.get("url"),
            title=_text(item, "title") or "Untitled episode",
            url=enclosure.get("url"),
            published=published,
            duration_s=_duration(_text(item, f"{ITUNES_NS}duration")),
            size=size,
            mime=enclosure.get("type", ""),
            description=_plain(_text(item, f"{ITUNES_NS}summary") or _text(item, "description")),
            number=int(number) if number.isdigit() else 0,
        ))
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    feed.episodes.sort(key=lambda e: e.published or epoch, reverse=True)
    return feed
