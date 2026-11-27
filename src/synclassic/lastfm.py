"""Last.fm: connect an account and scrobble plays from the iPod.

Uses the Last.fm API with your own API account (https://www.last.fm/api/account/create):
- connect: auth.getToken -> you approve in the browser -> auth.getSession gives a session key
  (it doesn't expire; your password never goes through Synclassic);
- scrobble: track.scrobble, up to 50 plays per request.
Requests are signed: md5 of the sorted parameters (name + value) followed by the shared secret.

The key, secret and session are stored only on this computer (readable only by you).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from synclassic.config import app_dir

API = "https://ws.audioscrobbler.com/2.0/"
AUTH_PAGE = "https://www.last.fm/api/auth/"
CREATE_ACCOUNT_PAGE = "https://www.last.fm/api/account/create"
BATCH = 50


class LastfmError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def credentials_file():
    return app_dir() / "lastfm.json"


def load_credentials() -> dict:
    try:
        return json.loads(credentials_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_credentials(data: dict) -> None:
    path = credentials_file()
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def builtin_key() -> tuple[str, str]:
    """Synclassic's own API account (see lastfm_app.py); empty when not filled in."""
    from synclassic import lastfm_app

    return lastfm_app.API_KEY.strip(), lastfm_app.SHARED_SECRET.strip()


def app_key() -> tuple[str, str]:
    """The API account to connect with: your own (Settings → Advanced) or Synclassic's built-in one."""
    data = load_credentials()
    if data.get("api_key") and data.get("secret"):
        return data["api_key"], data["secret"]
    return builtin_key()


def connected_user() -> str | None:
    data = load_credentials()
    return data.get("user") if data.get("session_key") else None


def disconnect() -> None:
    data = load_credentials()
    for key in ("session_key", "user"):
        data.pop(key, None)
    save_credentials(data)


def sign(params: dict[str, str], secret: str) -> str:
    text = "".join(f"{key}{params[key]}" for key in sorted(params) if key not in ("format", "callback"))
    return hashlib.md5((text + secret).encode("utf-8")).hexdigest()


class Client:
    def __init__(self, api_key: str, secret: str, session_key: str | None = None, session: requests.Session | None = None):
        if not api_key or not secret:
            raise LastfmError("Synclassic has no Last.fm API key yet: add one in lastfm_app.py, or your own in "
                              "Settings → Last.fm → Advanced.")
        self.api_key, self.secret, self.session_key = api_key.strip(), secret.strip(), session_key
        self.http = session or requests.Session()

    @classmethod
    def from_saved(cls) -> "Client":
        data = load_credentials()
        key, secret = app_key()
        return cls(data.get("api_key") or key, data.get("secret") or secret, data.get("session_key"))

    def call(self, method: str, params: dict | None = None, post: bool = False, signed: bool = True) -> dict:
        query = {"method": method, "api_key": self.api_key, **{k: str(v) for k, v in (params or {}).items()}}
        if self.session_key and post:
            query["sk"] = self.session_key
        if signed:
            query["api_sig"] = sign(query, self.secret)
        query["format"] = "json"
        try:
            response = self.http.post(API, data=query, timeout=30) if post else self.http.get(API, params=query, timeout=30)
        except requests.RequestException as error:
            raise LastfmError(f"Couldn't reach Last.fm: {error}") from error
        try:
            body = response.json()
        except ValueError:
            raise LastfmError(f"Last.fm sent an unexpected answer (HTTP {response.status_code}).") from None
        if "error" in body:
            raise LastfmError(_friendly(body.get("error"), body.get("message", "")), body.get("error"))
        return body

    # --- connecting ---------------------------------------------------------------------------

    def get_token(self) -> str:
        return self.call("auth.getToken")["token"]

    def approval_url(self, token: str) -> str:
        return f"{AUTH_PAGE}?api_key={self.api_key}&token={token}"

    def get_session(self, token: str) -> tuple[str, str]:
        session = self.call("auth.getSession", {"token": token})["session"]
        return session["key"], session.get("name", "")

    def connect(self, open_browser: Callable[[str], object], wait: float = 180, poll: float = 3,
                sleep: Callable[[float], None] = time.sleep) -> str:
        """Ask for approval in the browser and wait for it. Saves and returns the user name."""
        token = self.get_token()
        open_browser(self.approval_url(token))
        deadline = time.monotonic() + wait
        while True:
            try:
                key, user = self.get_session(token)
                break
            except LastfmError as error:
                if error.code != 14 or time.monotonic() > deadline:  # 14 = not approved yet
                    raise
                sleep(poll)
        self.session_key = key
        data = {"session_key": key, "user": user}
        if (self.api_key, self.secret) != builtin_key():  # only remember a key that isn't the built-in one
            data.update(api_key=self.api_key, secret=self.secret)
        save_credentials(data)
        return user

    # --- scrobbling ---------------------------------------------------------------------------

    def scrobble(self, plays: list[dict]) -> tuple[int, list[tuple[dict, str]]]:
        """Send plays (max 50). Returns (accepted, [(play, reason it was ignored)])."""
        if not self.session_key:
            raise LastfmError("Connect to Last.fm first (Settings).")
        params: dict[str, str] = {}
        for i, play in enumerate(plays):
            params[f"artist[{i}]"] = play["artist"]
            params[f"track[{i}]"] = play["track"]
            params[f"timestamp[{i}]"] = str(play["timestamp"])
            if play.get("album"):
                params[f"album[{i}]"] = play["album"]
            if play.get("album_artist"):
                params[f"albumArtist[{i}]"] = play["album_artist"]
            if play.get("duration"):
                params[f"duration[{i}]"] = str(play["duration"])
            params[f"chosenByUser[{i}]"] = "1"
        body = self.call("track.scrobble", params, post=True)
        results = body.get("scrobbles", {})
        items = results.get("scrobble", [])
        if isinstance(items, dict):
            items = [items]
        ignored = []
        for play, item in zip(plays, items):
            message = item.get("ignoredMessage") or {}
            if str(message.get("code", "0")) != "0":
                ignored.append((play, _ignored_reason(str(message.get("code")), message.get("#text", ""))))
        accepted = int((results.get("@attr") or {}).get("accepted", len(plays) - len(ignored)))
        return accepted, ignored


def _friendly(code, message: str) -> str:
    return {
        4: "Last.fm rejected the connection request. Check the API key and shared secret.",
        9: "Your Last.fm connection expired. Connect again in Settings.",
        10: "Last.fm doesn't recognise that API key. Check it in Settings.",
        13: "Last.fm rejected the request signature. Check the shared secret in Settings.",
        14: "Waiting for you to approve Synclassic on the Last.fm page.",
        15: "The Last.fm approval expired. Try connecting again.",
        26: "This Last.fm API key was suspended.",
        29: "Last.fm says: too many requests. Try again in a while.",
    }.get(code, f"Last.fm error {code}: {message}")


def _ignored_reason(code: str, text: str) -> str:
    return {
        "1": "artist ignored by Last.fm", "2": "track ignored by Last.fm",
        "3": "too old (Last.fm only takes plays from the last 2 weeks)", "4": "timestamp in the future",
        "5": "daily scrobble limit reached",
    }.get(code, text or f"ignored ({code})")


@dataclass
class ScrobbleResult:
    accepted: int = 0
    ignored: int = 0
    too_old: int = 0
    left: int = 0
    reasons: dict[str, int] | None = None
