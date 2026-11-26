# iPod database fixtures

`original-empty.b64` and `captured-album-index-36.b64` are Base64-encoded iTunesDB files
copied from iOpenPod's test fixtures (https://github.com/TheRealSavi/iOpenPod,
`tests/fixtures/iTunesDB/`, GPL-3.0-or-later). The first is an empty database written by
iOpenPod; the second is an anonymized 60-song database reconstructed from a real iPod
(fabricated names and IDs). Synclassic uses them to check that unchanged databases
round-trip byte-for-byte.
