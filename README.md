# Synclassic

Your Spotify playlists on a classic iPod, with the same names and the same song order.

Synclassic reads your playlists (Spotify links, your Spotify account, `.m3u8` files or CSV
exports), finds each song on YouTube Music, downloads it as a tagged **M4A** (or MP3) file,
and writes one `.m3u8` playlist per Spotify playlist. Run it again and it only downloads
what's new.

> **Status:** Step 1 of the roadmap (download + playlists). Syncing straight to the iPod
> (built for the iPod Classic 5.5G) comes next. Until then you can import the `.m3u8` files
> into an iPod manager such as iOpenPod.

## Install on a Mac

You need [Homebrew](https://brew.sh). Then in Terminal:

```bash
brew install python@3.12 ffmpeg deno
```

```bash
git clone https://github.com/kerthmfy/synclassic.git
cd synclassic
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

```bash
synclassic doctor
```

Open the app:

```bash
synclassic-gui
```

`ffmpeg` converts and repackages audio (and its `ffplay` plays previews). `deno` is needed
by the YouTube downloader. Next time, open Terminal, `cd synclassic`, and run
`source .venv/bin/activate` before using `synclassic` or `synclassic-gui`.

## The app

- **＋ Add playlists**: paste a Spotify link, tick playlists from your Spotify account
  (including Liked Songs), or import an `.m3u8` / `.csv` file.
- **⟳ Update all**: checks every playlist for changes and downloads what's new. **■ Stop**
  pauses after the current song; updating again continues where it left off.
- **Needs attention**: songs that weren't found or might be the wrong version.
  Double-click a song to see the closest YouTube Music results, **play a preview**, search
  with your own words or paste a link, then **Download this version**.
- Right-click a song or playlist for more (search again, show in Finder, rename, remove).
- **Settings**: music folder, M4A/MP3, YouTube sign-in browser, and the Spotify login.

Everything below also works from Terminal with the `synclassic` command.

## Add playlists

Any of these work, and you can mix them in one command:

```bash
synclassic add https://open.spotify.com/playlist/XXXXXXXXXXXXXXXXXXXXXX
```

```bash
synclassic add liked
```

```bash
synclassic add "~/Downloads/Road Trip.m3u8" ~/Downloads/export.csv
```

| Input | Login needed? | Notes |
| --- | --- | --- |
| Spotify playlist / album / track link | No | Playlists over 100 songs are read in full by the full-playlist reader (below). |
| `liked` (your Liked Songs) | Yes | |
| Your own playlists, any length | Yes | `synclassic add --all-mine` adds Liked Songs plus every playlist you own. |
| `.m3u8` / `.m3u` file | No | Songs that already exist on your Mac are used as-is; others are downloaded. |
| `.csv` export (Exportify, TuneMyMusic) | No | A file with several playlists becomes several playlists. |

## Long playlists without logging in

Spotify only shares the first 100 songs of a playlist with apps that aren't logged in. For
longer public playlists, Synclassic opens Spotify's own web page in a small window, scrolls
through the song list automatically and reads every row (about 15 seconds for 150 songs).
You can watch it work and cancel at any time. It reads the page only; nothing is kept.

It's on by default: turn it off in Settings or with `synclassic config full_playlist_reader off`.
If Spotify changes its page and the reader can't finish, Synclassic keeps the first 100 songs and
tells you so.

## Log in to Spotify (optional)

Logging in is needed for Liked Songs and for playlists longer than 100 songs.

1. Open the [Spotify developer dashboard](https://developer.spotify.com/dashboard) and click **Create app**.
2. Add this exact Redirect URI: `http://127.0.0.1:8898/callback`, and tick **Web API**.
3. Copy the app's **Client ID**, then run `synclassic login` and paste it.

Spotify rules since February 2026: the developer account needs **Premium**, and personal apps
can only read the songs of playlists you **own or collaborate on**. For playlists you only
follow, Synclassic falls back to the public reader (first 100 songs). Tip: in Spotify, add
those songs to a playlist of your own.

## Keep everything up to date

```bash
synclassic update
```

This checks every playlist for added, removed and re-ordered songs, downloads only what's new,
and rewrites the playlists. `synclassic list` shows how each playlist is doing.

## Pick the right version yourself

When Synclassic isn't sure it found the right recording (or found nothing), it tells you. Then run:

```bash
synclassic review
```

For each of those songs you get the closest YouTube Music results, with the length difference
and a match score. You can:

- `p 2`: listen to choice 2 first (press Enter to stop)
- `2`: download choice 2
- `s some other words`: search with your own words
- `u <link>`: paste a YouTube / YouTube Music link you found
- `k`: keep the current file, `n`: skip, `q`: quit

To redo any single song: `synclassic show "Playlist name"` shows each song's number, then
run `synclassic pick <number>`.

## Where files go

```
~/Music/Synclassic/
  Music/<Album artist>/<Album>/<Track>.m4a    each song stored once, even if in many playlists
  Playlists/<Playlist name>.m3u8              in the same order as Spotify
```

Change it with `synclassic config music_dir /path/to/folder`.

## Settings

```bash
synclassic config
```

| Setting | Default | |
| --- | --- | --- |
| `format` | `m4a` | `m4a` (AAC) or `mp3` |
| `mp3_quality` | `v0` | `v0` or `320` (when `format` is `mp3`) |
| `music_dir` | `~/Music/Synclassic` | |
| `cookies_from_browser` | off | e.g. `safari`: YouTube Music Premium members get 256 kbps AAC |
| `download_pause` | `2` | seconds between downloads |
| `full_playlist_reader` | `on` | read public playlists over 100 songs from Spotify's web page |

Album art: Spotify's 640 × 640 cover is used whenever the song came from Spotify (Spotify
pages often show a 64 px thumbnail; Synclassic always asks for the full-size one), otherwise
YouTube Music's album art at up to 1000 px. Covers under 300 px are only used when nothing
sharper exists.

Audio quality: YouTube Music serves AAC in M4A, which is what the iPod plays, so in `m4a`
mode the audio is copied without re-encoding whenever possible.

## Other commands

`rename`, `remove` (files are kept), `clean` (delete songs no playlist uses), `logout`,
`spotify-playlists`. Run `synclassic --help` for everything.

## Notes

- Downloading from YouTube is against YouTube's Terms of Service. Use Synclassic for your own
  personal listening.
- Settings, login and the library database live in `~/Library/Application Support/Synclassic`.

## Development

```bash
pip install -e '.[dev]'
```

```bash
pytest
```

Licensed under GPL-3.0-or-later.
