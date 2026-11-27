"""Synclassic's own Last.fm API account (built into the app).

Last.fm requires every app to identify itself with an API key. With Synclassic's key filled in
here, anyone using Synclassic connects with one click (approving in the browser) and never sees
API keys. Like other open-source players, the key ships with the app.

To fill it in (once, as Synclassic's developer): create an API account at
https://www.last.fm/api/account/create (Application name: Synclassic; leave the callback URL
empty), then paste the "API key" and "Shared secret" below.

People can still use their own API account instead (Settings → Last.fm → Advanced).
"""

API_KEY = "afa0c0bc29f96936079bdef034b5053e"
SHARED_SECRET = "1a637c636304a21e7939291b2f38d5c5"
