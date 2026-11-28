# iPod database fixtures

`original-empty.b64` and `captured-album-index-36.b64` are Base64-encoded iTunesDB files
copied from iOpenPod's test fixtures (https://github.com/TheRealSavi/iOpenPod,
`tests/fixtures/iTunesDB/`, GPL-3.0-or-later). The first is an empty database written by
iOpenPod; the second is an anonymized 60-song database reconstructed from a real iPod
(fabricated names and IDs). Synclassic uses them to check that unchanged databases
round-trip byte-for-byte.

`artworkdb-original-empty.b64`, `artworkdb-original-with-unknown-data.b64` and
`artworkdb-nano6-original-1x.b64` are ArtworkDB files from iOpenPod's
`tests/fixtures/ArtworkDB/` (an empty database, one with deliberate unknown data, and one
with a single image in four sizes). Synclassic uses them to check that unchanged artwork
databases round-trip byte-for-byte.

`hash58-vectors.json` comes from iOpenPod's `tests/fixtures/writing/hash58-original.json`
(GPL-3.0-or-later): hash58 signatures captured from the original iOpenPod implementation,
which used libgpod's literal lookup tables. Synclassic's port (which computes the tables)
must reproduce them byte for byte.
