"""Get sharp album art.

Spotify serves every album cover in four sizes, chosen by a code inside the image link:
``ab67616d00004851…`` = 64 px, ``…00001e02…`` = 300 px, ``…0000b273…`` = 640 px and
``…000082c1…`` = 2000 px. Pages and playlist rows often show the 64 px version, which looks
pixelated once embedded in a song, so every Spotify link is upgraded to 640 px (sharp on the
iPod, which shows at most 200 px, and on a Mac).
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable, Iterable

import requests

# 640 px. ("82c1" would be 2000 px: about 10x bigger files for no visible gain on an iPod.)
SPOTIFY_COVER_SIZE = "b273"
MIN_GOOD_SIZE = 300

_SPOTIFY_ALBUM_IMAGE = re.compile(
    r"^https://(?:i\.scdn\.co|image-cdn-[a-z]{2}\.spotifycdn\.com)/image/ab67616d0000(?:4851|1e02|b273|82c1)([0-9a-f]{24})$"
)


def upgrade_cover_url(url: str | None) -> str | None:
    """The best-size version of a cover link (Spotify and YouTube Music links are upgraded)."""
    if not url:
        return url
    match = _SPOTIFY_ALBUM_IMAGE.match(url)
    if match:
        return f"https://i.scdn.co/image/ab67616d0000{SPOTIFY_COVER_SIZE}{match.group(1)}"
    if "googleusercontent.com" in url or "ggpht.com" in url:
        # YouTube Music album art: ask for a 1000 px square instead of the small thumbnail.
        return re.sub(r"=w\d+-h\d+[^/]*$", "=w1000-h1000-l90-rj", url)
    return url


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a JPEG or PNG, read from its header."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        index += 2 + length
    return None


def download_image(url: str) -> bytes | None:
    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException:
        return None
    if response.status_code != 200 or not response.content or image_size(response.content) is None:
        return None
    return response.content


def best_cover(urls: Iterable[str | None], fetch: Callable[[str], bytes | None] = download_image) -> bytes | None:
    """The first cover that is at least 300 px; otherwise the biggest one found."""
    best: tuple[int, bytes] | None = None
    tried: set[str] = set()
    for url in urls:
        for candidate in (upgrade_cover_url(url), url):
            if not candidate or candidate in tried:
                continue
            tried.add(candidate)
            data = fetch(candidate)
            if not data:
                continue
            size = image_size(data) or (0, 0)
            if min(size) >= MIN_GOOD_SIZE:
                return data
            if best is None or min(size) > best[0]:
                best = (min(size), data)
            break  # the upgraded link worked; no need to try the original
    return best[1] if best else None
