"""Connect a Studio channel to YouTube (OAuth 2.0, your own Google Cloud project).

Setup (once): in Google Cloud Console enable "YouTube Data API v3", create an
OAuth client of type "Web application", add the redirect URI
``<dashboard or API origin>/api/studio/youtube/callback`` and set
STUDIO_YOUTUBE_CLIENT_ID / STUDIO_YOUTUBE_CLIENT_SECRET.

Tokens live in ``{STUDIO_DIR}/secrets/youtube-<channel_id>.json`` (mode 0600),
never in studio.db, never in an API response.
"""
import json
import os
import secrets
import threading
import time
from typing import Optional
from urllib.parse import urlencode

from . import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly")

_STATE_TTL = 15 * 60
_states: dict = {}
_states_lock = threading.Lock()
_token_lock = threading.Lock()


class OAuthError(Exception):
    pass


def client_id() -> str:
    return os.environ.get("STUDIO_YOUTUBE_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("STUDIO_YOUTUBE_CLIENT_SECRET", "").strip()


def configured() -> bool:
    return bool(client_id() and client_secret())


def redirect_uri(request_base: str) -> str:
    explicit = os.environ.get("STUDIO_YOUTUBE_REDIRECT_URI", "").strip()
    return explicit or f"{request_base.rstrip('/')}/api/studio/youtube/callback"


def _secrets_dir() -> str:
    path = os.path.join(config.studio_dir(), "secrets")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _token_path(channel_id: str) -> str:
    from .storage import valid_id
    if not valid_id(channel_id):
        raise OAuthError("invalid channel id")
    return os.path.join(_secrets_dir(), f"youtube-{channel_id}.json")


def _write_token(channel_id: str, data: dict):
    path = _token_path(channel_id)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read_token(channel_id: str) -> Optional[dict]:
    try:
        with open(_token_path(channel_id)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def is_connected(channel_id: str) -> bool:
    tok = _read_token(channel_id)
    return bool(tok and tok.get("refresh_token"))


def disconnect(channel_id: str):
    try:
        os.unlink(_token_path(channel_id))
    except OSError:
        pass


def start(channel_id: str, redirect: str) -> str:
    """The Google consent URL for this channel; the state is single-use."""
    if not configured():
        raise OAuthError("Set STUDIO_YOUTUBE_CLIENT_ID and STUDIO_YOUTUBE_CLIENT_SECRET first")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _states_lock:
        for k in [k for k, v in _states.items() if v["exp"] < now]:
            del _states[k]
        _states[state] = {"channel_id": channel_id, "redirect": redirect, "exp": now + _STATE_TTL}
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id(), "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
    })


def _post_token(data: dict) -> dict:
    import httpx
    import ssrf_guard
    try:
        with ssrf_guard.guarded():
            resp = httpx.post(TOKEN_URL, data=data, timeout=20)
    except httpx.HTTPError as e:
        raise OAuthError(f"Google token endpoint unreachable: {type(e).__name__}") from e
    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass
    if resp.status_code != 200:
        # error + error_description only: never the request (it holds the secret).
        raise OAuthError(f"Google refused the token request: {body.get('error', resp.status_code)}")
    return body


def finish(state: str, code: str) -> str:
    """Exchange the code, store the tokens, return the Studio channel id."""
    with _states_lock:
        entry = _states.pop(state or "", None)
    if not entry or entry["exp"] < time.time():
        raise OAuthError("This connect link expired. Start again from the channel.")
    tokens = _post_token({
        "code": code, "client_id": client_id(), "client_secret": client_secret(),
        "redirect_uri": entry["redirect"], "grant_type": "authorization_code"})
    if not tokens.get("refresh_token"):
        raise OAuthError("Google sent no refresh token. Remove the app's access at "
                         "myaccount.google.com/permissions and connect again.")
    _write_token(entry["channel_id"], {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens.get("access_token"),
        "expires_at": time.time() + int(tokens.get("expires_in") or 0) - 60,
        "scope": tokens.get("scope"),
    })
    return entry["channel_id"]


def access_token(channel_id: str) -> str:
    """A valid access token, refreshed when it is about to expire."""
    with _token_lock:
        tok = _read_token(channel_id)
        if not tok or not tok.get("refresh_token"):
            raise OAuthError("This channel is not connected to YouTube")
        if tok.get("access_token") and float(tok.get("expires_at") or 0) > time.time():
            return tok["access_token"]
        fresh = _post_token({
            "client_id": client_id(), "client_secret": client_secret(),
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        tok["access_token"] = fresh.get("access_token")
        tok["expires_at"] = time.time() + int(fresh.get("expires_in") or 0) - 60
        if fresh.get("refresh_token"):
            tok["refresh_token"] = fresh["refresh_token"]
        _write_token(channel_id, tok)
        return tok["access_token"]


def my_channel(channel_id: str) -> dict:
    """{id, title} of the YouTube channel the token belongs to."""
    import httpx
    import ssrf_guard
    token = access_token(channel_id)
    with ssrf_guard.guarded():
        resp = httpx.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                         headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if resp.status_code != 200:
        raise OAuthError(f"YouTube answered {resp.status_code} for the channel lookup")
    items = resp.json().get("items") or []
    if not items:
        raise OAuthError("That Google account has no YouTube channel")
    return {"id": items[0].get("id"), "title": (items[0].get("snippet") or {}).get("title")}
