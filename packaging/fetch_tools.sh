#!/bin/bash
# Put ffmpeg, ffprobe and deno inside the app, so it works without Homebrew.
#   packaging/fetch_tools.sh arm64|x86_64 <folder>
# Exact versions with checked fingerprints (SHA-256): a download that doesn't match is refused.
#   ffmpeg/ffprobe 6.0, static builds (osxexperts.net for Apple silicon, evermeet.cx for Intel),
#     from github.com/eugeneware/ffmpeg-static release b6.0. GPL; source: https://ffmpeg.org
#   deno 2.4.5 from github.com/denoland/deno (MIT).
# To move to newer versions, change the version and fingerprints below together.
set -euo pipefail
ARCH="${1:?arm64 or x86_64}"
DEST="${2:?destination folder}"
FFMPEG_RELEASE="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0"
DENO_RELEASE="https://github.com/denoland/deno/releases/download/v2.4.5"

case "$ARCH" in
  arm64|aarch64)
    FILES=(
      "ffmpeg|$FFMPEG_RELEASE/ffmpeg-darwin-arm64.gz|6be74d6f449889c2e87a75873894f8520cad56c08ac76f2a628d85b0519daaca"
      "ffprobe|$FFMPEG_RELEASE/ffprobe-darwin-arm64.gz|a4db76bafdb8529312e138fb55aaf143cad21b6d672e2634c9dd07ae83c6a310"
      "deno|$DENO_RELEASE/deno-aarch64-apple-darwin.zip|d21374dc6aa02b493467ec2f6d865cab95cffebb89aab242dc1acf95274681ee"
    ) ;;
  x86_64|x64)
    FILES=(
      "ffmpeg|$FFMPEG_RELEASE/ffmpeg-darwin-x64.gz|a12354fce7eb62361473bbe10d53a1893695babd35869ec8e92e5dfea8d0440b"
      "ffprobe|$FFMPEG_RELEASE/ffprobe-darwin-x64.gz|b75d816c5658bd417a2a05e174957963e2f9f85be3eb731b2a04467545a665dc"
      "deno|$DENO_RELEASE/deno-x86_64-apple-darwin.zip|cd46e3d5c06fbd21ef12742def67cc12ef83a2f99d44e00419615f99de056574"
    ) ;;
  *) echo "Unknown architecture: $ARCH" >&2; exit 1 ;;
esac

sha256() { if command -v shasum >/dev/null; then shasum -a 256 "$1"; else sha256sum "$1"; fi | cut -d' ' -f1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$DEST"
for entry in "${FILES[@]}"; do
  IFS='|' read -r name url expected <<<"$entry"
  file="$WORK/$(basename "$url")"
  echo "    $name: $(basename "$url")"
  curl -fsSL --retry 3 -o "$file" "$url"
  actual="$(sha256 "$file")"
  if [ "$actual" != "$expected" ]; then
    echo "Refused $(basename "$url"): its fingerprint is $actual, expected $expected" >&2
    exit 1
  fi
  case "$file" in
    *.gz) gunzip -c "$file" >"$DEST/$name" ;;
    *.zip) unzip -q -o "$file" "$name" -d "$DEST" ;;
  esac
  chmod 755 "$DEST/$name"
done
cat >"$DEST/THIRD-PARTY.txt" <<'TXT'
Tools inside Synclassic.app:

ffmpeg and ffprobe 6.0 (https://ffmpeg.org), static builds by osxexperts.net (Apple silicon)
and evermeet.cx (Intel), distributed by github.com/eugeneware/ffmpeg-static (release b6.0).
Licensed under the GNU General Public License version 3 (with libx264, LAME and Opus).
Source code: https://ffmpeg.org/releases/ffmpeg-6.0.tar.xz

deno 2.4.5 (https://deno.com), MIT License, https://github.com/denoland/deno
TXT
echo "    tools are in $DEST"
