"""Text helpers for comparing song titles/artists and for safe file names."""

from __future__ import annotations

import re
import unicodedata

# Bits of a title that describe a version rather than the song itself.
_VERSION_NOISE = re.compile(
    r"""
    \s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]   # (feat. X)
    | \s+-\s+(?:feat\.?|ft\.?)\s.*$                                  # - feat. X
    | \s*[\(\[][^\)\]]*\b(?:remaster(?:ed)?|mono|stereo|deluxe|edition|version|anniversary|single|explicit|clean)\b[^\)\]]*[\)\]]
    | \s+-\s+[^-]*\b(?:remaster(?:ed)?|mono|stereo|single\ version|radio\ edit|album\ version)\b.*$
    | \s*[\(\[]\s*(?:from|taken\ from)\s[^\)\]]*[\)\]]               # (From "Movie")
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Words that mean "a different recording" when they appear in a candidate but not in the
# song we are looking for.
VARIANT_WORDS = (
    "live", "remix", "mix", "cover", "karaoke", "instrumental", "acoustic", "sped up",
    "slowed", "reverb", "8d", "nightcore", "demo", "edit", "a cappella", "acapella",
    "version", "rehearsal", "tribute", "lyrics", "lyric video", "bass boosted",
)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    """Lowercase, accent-free, punctuation-free text for comparisons."""
    text = strip_accents(text or "").lower()
    text = text.replace("&", " and ").replace("’", "'")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def core_title(title: str) -> str:
    """The song title without featuring credits, remaster notes and similar."""
    previous = None
    text = title or ""
    while previous != text:
        previous = text
        text = _VERSION_NOISE.sub("", text)
    return normalize(text) or normalize(title)


def variant_words(text: str) -> set[str]:
    norm = f" {normalize(text)} "
    return {word for word in VARIANT_WORDS if f" {word} " in norm}


def split_artists(text: str) -> list[str]:
    """Split a combined artist string ("A, B & C", "A; B", "A feat. B")."""
    if not text:
        return []
    parts = re.split(r"\s*(?:;|,|\s&\s|\sfeat\.?\s|\sft\.?\s|\sx\s|\swith\s)\s*", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(text: str, max_length: int = 80) -> str:
    """A file/folder name that works on macOS, Windows and the iPod's disk."""
    text = unicodedata.normalize("NFC", text or "")
    text = _BAD_CHARS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    return text or "Unknown"
