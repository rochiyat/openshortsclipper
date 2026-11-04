"""Read a post's public counters (views, likes, comments).

YouTube first: with YOUTUBE_DATA_API_KEY set, ``videos.list`` answers up to
50 videos per call at one quota unit, the most reliable source by far.
Without a key, or for other platforms, yt-dlp reads the public page.

Rules this follows (see CLAUDE.md): every outbound fetch runs inside
``ssrf_guard.guarded()``; yt-dlp goes direct, never through PROXY_URL;
YOUTUBE_COOKIES are tried first and an anonymous attempt second. A page that
answers without a view count is a degraded read and raises, it is never
recorded as 0 views.
"""
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"


@dataclass
class Counters:
    views: int
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    source: str = "ytdlp"


class FetchError(Exception):
    """Could not read the counters this time; try again later."""


class PostUnavailable(FetchError):
    """The platform says the post is gone, private or removed."""


_UNAVAILABLE = re.compile(
    r"video unavailable|private video|has been removed|no longer available|"
    r"this video is unavailable|account (?:has been )?terminated|post isn't available|"
    r"status code 404", re.I)


def _int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def youtube_api_key() -> str:
    return os.environ.get("YOUTUBE_DATA_API_KEY", "").strip()


def fetch_youtube_api(video_ids: Iterable[str], api_key: str, timeout: float = 15.0) -> Dict[str, Counters]:
    """Counters for each id YouTube returned. An id missing from the answer is
    deleted, private or never existed."""
    import httpx
    import ssrf_guard

    ids = [i for i in dict.fromkeys(video_ids) if i]
    out: Dict[str, Counters] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        try:
            with ssrf_guard.guarded():
                resp = httpx.get(YOUTUBE_API_URL, timeout=timeout, params={
                    "part": "statistics", "id": ",".join(chunk), "key": api_key,
                    "maxResults": 50})
        except httpx.HTTPError as e:
            raise FetchError(f"YouTube API unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            reason = ""
            try:
                errs = resp.json().get("error", {}).get("errors") or []
                reason = errs[0].get("reason", "") if errs else ""
            except ValueError:
                pass
            # Never echo the response body: it can carry the request URL (and key).
            raise FetchError(f"YouTube API answered {resp.status_code} {reason}".strip())
        for item in resp.json().get("items") or []:
            stats = item.get("statistics") or {}
            views = _int(stats.get("viewCount"))
            if views is None:
                continue  # hidden counters: treat like a missing read, not 0
            out[item.get("id")] = Counters(
                views=views, likes=_int(stats.get("likeCount")),
                comments=_int(stats.get("commentCount")), source="youtube_api")
    return out


class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def fetch_ytdlp(url: str, platform: str) -> Counters:
    """Counters from the public page via yt-dlp (no download)."""
    import yt_dlp
    import ssrf_guard

    cookies_env = os.environ.get("YOUTUBE_COOKIES") if platform == "youtube" else None
    ck_path = None
    errors = []
    try:
        if cookies_env:
            fd, ck_path = tempfile.mkstemp(prefix="studio_ck_", suffix=".txt")
            with os.fdopen(fd, "w") as f:
                f.write(cookies_env)
        for use_cookies in ([True, False] if ck_path else [False]):
            opts = {
                "skip_download": True, "quiet": True, "no_warnings": True,
                "noplaylist": True, "logger": _QuietLogger(), "socket_timeout": 20,
                # Counters only: a SABR-only video must not fail on formats.
                "ignore_no_formats_error": True,
                # No "proxy": the paid PROXY_URL is for downloads only and
                # never applies here.
            }
            if platform == "youtube":
                opts["extractor_args"] = {"youtube": {"player_client": ["default"]}}
            if use_cookies:
                opts["cookiefile"] = ck_path
            try:
                with ssrf_guard.guarded(), yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False) or {}
            except Exception as e:  # noqa: BLE001 — classified below
                msg = str(e)
                if _UNAVAILABLE.search(msg) and not re.search(r"not a bot|sign in to confirm", msg, re.I):
                    raise PostUnavailable(msg[:300]) from e
                errors.append(msg[:200])
                continue
            views = _int(info.get("view_count"))
            if views is None:
                errors.append("page answered without a view count")
                continue
            return Counters(views=views, likes=_int(info.get("like_count")),
                            comments=_int(info.get("comment_count")),
                            shares=_int(info.get("repost_count")), source="ytdlp")
        raise FetchError("; ".join(errors)[:500] or "no counters")
    finally:
        if ck_path:
            try:
                os.unlink(ck_path)
            except OSError:
                pass
