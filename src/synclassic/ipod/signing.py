"""Signing the music database for iPods that require it.

The iPod 5G/5.5G accepts any well-formed iTunesDB. Later models only accept one carrying a
signature made with a key derived from their FireWire ID (a 16-hex-digit hardware number):

  scheme 0  none                         iPod 5G/5.5G and earlier
  scheme 1  "hash58" (HMAC-SHA1)         iPod classic 6G/6.5G/7G, iPod nano 3G/4G
  scheme 2+ "hash72", "hashAB"           iPod nano 5G and later: not supported (refused)

The database header says which scheme it uses (u16 at 0x30), so Synclassic follows what the
iPod's own database already uses. Before writing, the existing signature is checked with the
FireWire ID Synclassic found: only if it matches can Synclassic sign a new database the iPod
will accept.

The hash58 algorithm is ported from iOpenPod (src/iPodDB/iTunesDB/writer/signature.py,
GPL-3.0-or-later), which derives libgpod's lookup tables from the AES S-box: table 1 is the
S-box and table 2 its inverse, so no tables need to be copied in by hand.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import plistlib
import re
import subprocess
import sys

from synclassic.ipod.device import IPod

SCHEME_OFFSET = 0x30  # u16
DB_ID = (0x18, 8)  # zeroed while hashing
UNKNOWN_0x32 = (0x32, 20)  # zeroed while hashing
HASH58 = (0x58, 20)  # the signature

NONE, HASH58_SCHEME = 0, 1
_SEED = bytes.fromhex("6723fe304533f890992107c1d012b2a10781")


class SigningError(RuntimeError):
    pass


def _multiply(left: int, right: int) -> int:
    value = 0
    for _ in range(8):
        if right & 1:
            value ^= left
        left = ((left << 1) ^ (0x11B if left & 0x80 else 0)) & 255
        right >>= 1
    return value


def _substitute(value: int) -> int:
    inverse = 0
    if value:
        inverse = 1
        for _ in range(254):
            inverse = _multiply(inverse, value)
    result = inverse ^ 0x63
    for shift in range(1, 5):
        result ^= ((inverse << shift) | (inverse >> (8 - shift))) & 255
    return result


_SBOX = bytes(_substitute(i) for i in range(256))  # the AES S-box
_INVERSE = bytes(_SBOX.index(i) for i in range(256))


def scheme(data: bytes) -> int:
    """Which signature the database uses (0 = none)."""
    if data[:4] != b"mhbd" or len(data) < SCHEME_OFFSET + 2:
        return NONE
    header_size = int.from_bytes(data[4:8], "little")
    value = int.from_bytes(data[SCHEME_OFFSET:SCHEME_OFFSET + 2], "little") if header_size >= 0x32 else 0
    if value == NONE and header_size >= HASH58[0] + HASH58[1] and any(data[HASH58[0]:HASH58[0] + HASH58[1]]):
        return HASH58_SCHEME  # a hash58 signature without the scheme number set
    return value


def _hash58_key(guid: bytes) -> bytes:
    if len(guid) != 8:
        raise SigningError("The FireWire ID must be 8 bytes (16 hex digits).")
    derived = bytearray()
    for left, right in zip(guid[::2], guid[1::2]):
        value = math.lcm(left, right) if left and right else 1
        for part in (value >> 8, value & 255):
            derived.extend((_SBOX[part], _INVERSE[part]))
    return hashlib.sha1(_SEED + derived).digest()


def sign_hash58(data: bytes, guid: bytes) -> bytes:
    """The database with a fresh hash58 signature (and the scheme set to 1)."""
    header_size = int.from_bytes(data[4:8], "little")
    if data[:4] != b"mhbd" or header_size < HASH58[0] + HASH58[1]:
        raise SigningError("This database header is too small to hold a signature.")
    output, normalized = bytearray(data), bytearray(data)
    for offset, size in (DB_ID, UNKNOWN_0x32, HASH58):
        normalized[offset:offset + size] = bytes(size)
    for buffer in (output, normalized):
        buffer[SCHEME_OFFSET:SCHEME_OFFSET + 2] = HASH58_SCHEME.to_bytes(2, "little")
    output[HASH58[0]:HASH58[0] + HASH58[1]] = hmac.digest(_hash58_key(guid), bytes(normalized), "sha1")
    return bytes(output)


def verify_hash58(data: bytes, guid: bytes) -> bool:
    return hmac.compare_digest(data, sign_hash58(data, guid))


# --- the iPod's FireWire ID ------------------------------------------------------------------------


def _parse_guid(text: str | None) -> bytes | None:
    if not text:
        return None
    digits = re.sub(r"[^0-9A-Fa-f]", "", text.strip().removeprefix("0x").removeprefix("0X"))
    if len(digits) != 16:
        return None
    return bytes.fromhex(digits)


def firewire_guid(ipod: IPod, run=subprocess.run) -> bytes | None:
    """The iPod's FireWire ID, from SysInfo, SysInfoExtended, or (on a Mac) its USB serial number."""
    guid = _parse_guid(ipod.sysinfo.get("FirewireGuid") or ipod.sysinfo.get("FireWireGUID"))
    if guid:
        return guid
    extended = ipod.control / "Device" / "SysInfoExtended"
    if extended.is_file():
        try:
            info = plistlib.loads(extended.read_bytes())
            guid = _parse_guid(str(info.get("FireWireGUID") or info.get("FirewireGuid") or ""))
            if guid:
                return guid
        except Exception:
            pass
    if sys.platform == "darwin" or run is not subprocess.run:
        try:
            result = run(["system_profiler", "SPUSBDataType", "-json"], capture_output=True, text=True, timeout=30)
            for item in _usb_items(__import__("json").loads(result.stdout or "{}")):
                if "ipod" in str(item.get("_name", "")).lower():
                    guid = _parse_guid(str(item.get("serial_num", "")))
                    if guid:
                        return guid
        except Exception:
            pass
    return None


def _usb_items(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _usb_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from _usb_items(value)


# --- what the rest of Synclassic uses --------------------------------------------------------------


def describe(data: bytes) -> str:
    return {NONE: "not signed (iPod 5G/5.5G style)", HASH58_SCHEME: "signed (hash58: iPod classic / nano 3G–4G)"}.get(
        scheme(data), f"signed with scheme {scheme(data)} (iPod nano 5G or later)")


def problem(ipod: IPod, data: bytes) -> str | None:
    """Why Synclassic can't write this iPod's database, or None if it can."""
    used = scheme(data)
    if used == NONE:
        return None
    if used != HASH58_SCHEME:
        return ("This iPod's database uses a newer signature (iPod nano 5G or later), which Synclassic can't make. "
                "Nothing will be changed on it.")
    guid = firewire_guid(ipod)
    if guid is None:
        return ("This iPod needs its database signed with its FireWire ID, and Synclassic couldn't find it. "
                "Add a line 'FirewireGuid: 0x…' (16 hex digits, from System Information → USB → iPod → Serial "
                "Number) to iPod_Control/Device/SysInfo, then check again.")
    if not verify_hash58(data, guid):
        return ("The FireWire ID Synclassic found doesn't match this iPod's database signature, so it won't write "
                "(the iPod would refuse the database). Check the FirewireGuid line in iPod_Control/Device/SysInfo.")
    return None


def finish(ipod: IPod, data: bytes, original: bytes | None = None) -> bytes:
    """Sign a new database the way this iPod's database is signed (`original`: what was on it)."""
    used = scheme(original if original is not None else data)
    if used == NONE:
        return data
    if used != HASH58_SCHEME:
        raise SigningError("Synclassic can't sign databases for this iPod model.")
    guid = firewire_guid(ipod)
    if guid is None:
        raise SigningError("This iPod's FireWire ID is needed to sign its database.")
    return sign_hash58(data, guid)
