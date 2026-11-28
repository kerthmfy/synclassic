"""Log in to Spotify with your own developer app (Authorization Code + PKCE).

No client secret is needed. The token is stored only on this computer.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import pathlib
import secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

from synclassic.config import app_dir

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8898
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"
# playlist-modify-private: only to make your iPod's On-The-Go playlists in your account (otg_to_spotify).
SCOPES = "playlist-read-private playlist-read-collaborative user-library-read playlist-modify-private"


class SpotifyAuthError(RuntimeError):
    pass


def token_file() -> pathlib.Path:
    return app_dir() / "spotify_token.json"


def load_token() -> dict | None:
    path = token_file()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_token(token: dict) -> None:
    path = token_file()
    tmp = path.with_suffix(".tmp")
    # Create the file readable only by the current user.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(token, handle)
    tmp.replace(path)


def forget_token() -> bool:
    path = token_file()
    if path.exists():
        path.unlink()
        return True
    return False


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _stamp(token: dict, client_id: str) -> dict:
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
    token["client_id"] = client_id
    return token


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802 (http.server naming)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = dict(urllib.parse.parse_qsl(parsed.query))
        type(self).result = params
        ok = "code" in params
        body = (
            "<h2>Synclassic is connected to Spotify.</h2><p>You can close this tab.</p>"
            if ok
            else f"<h2>Spotify login failed</h2><p>{params.get('error', 'unknown error')}</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):  # keep the terminal quiet
        pass


def login(client_id: str, open_browser: bool = True, timeout: float = 300) -> dict:
    """Run the browser login and return the saved token."""
    client_id = client_id.strip()
    if not client_id:
        raise SpotifyAuthError("A Spotify Client ID is required.")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": state,
        }
    )

    _CallbackHandler.result = {}
    try:
        server = http.server.HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _CallbackHandler)
    except OSError as error:
        raise SpotifyAuthError(f"Port {REDIRECT_PORT} is busy, so the login page can't call back: {error}") from error
    server.timeout = 1
    thread = threading.Thread(target=lambda: _serve_until_result(server, timeout), daemon=True)
    thread.start()

    print("Opening Spotify in your browser. If it doesn't open, visit this link:\n")
    print(url + "\n")
    if open_browser:
        webbrowser.open(url)
    thread.join()
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise SpotifyAuthError("Timed out waiting for the Spotify login.")
    if result.get("state") != state:
        raise SpotifyAuthError("Login response didn't match this request (state mismatch). Try again.")
    if "code" not in result:
        raise SpotifyAuthError(f"Spotify login was not completed: {result.get('error', 'unknown error')}")

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise SpotifyAuthError(f"Spotify refused the login: {response.text[:300]}")
    token = _stamp(response.json(), client_id)
    save_token(token)
    return token


def _serve_until_result(server: http.server.HTTPServer, timeout: float) -> None:
    deadline = time.time() + timeout
    while not _CallbackHandler.result and time.time() < deadline:
        server.handle_request()


def refresh(token: dict) -> dict:
    client_id = token.get("client_id", "")
    if not token.get("refresh_token") or not client_id:
        raise SpotifyAuthError("Spotify login expired. Run 'synclassic login' again.")
    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": client_id},
        timeout=30,
    )
    if response.status_code != 200:
        raise SpotifyAuthError("Spotify login expired. Run 'synclassic login' again.")
    new = response.json()
    # Spotify may or may not send a new refresh token.
    new.setdefault("refresh_token", token["refresh_token"])
    new = _stamp(new, client_id)
    save_token(new)
    return new


def valid_token() -> dict | None:
    """The saved token, refreshed if needed. None when not logged in."""
    token = load_token()
    if not token:
        return None
    if time.time() >= token.get("expires_at", 0):
        token = refresh(token)
    return token
