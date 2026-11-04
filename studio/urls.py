"""Recognise a pasted post URL: platform, the platform's own id, canonical URL.

Strict allowlist of hosts and path shapes. Short links (vm.tiktok.com,
fb.watch) are refused rather than followed: resolving them would be a
server-side fetch of an arbitrary redirect, and the full URL is one tap away
in every app ("Copy link" on the post itself).
"""
import re
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse


class ParsedPost(NamedTuple):
    platform: str
    external_id: str
    url: str


class PostUrlError(ValueError):
    pass


_YT_ID = r"[A-Za-z0-9_-]{11}"
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_TT_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_IG_HOSTS = {"instagram.com", "www.instagram.com"}
_TH_HOSTS = {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}
_FB_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}
_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
_SHORT_LINKS = {"vm.tiktok.com", "vt.tiktok.com", "fb.watch", "t.co", "instagr.am"}


def _youtube(host, path, query):
    if host == "youtu.be":
        m = re.match(rf"^/({_YT_ID})/?$", path)
        return m.group(1) if m else None
    if host not in _YT_HOSTS:
        return None
    m = re.match(rf"^/(?:shorts|live|embed)/({_YT_ID})/?$", path)
    if m:
        return m.group(1)
    if path.rstrip("/") == "/watch":
        v = (query.get("v") or [""])[0]
        return v if re.fullmatch(_YT_ID, v) else None
    return None


def parse_post_url(raw: str) -> ParsedPost:
    url = (raw or "").strip()
    if not url:
        raise PostUrlError("Paste the post's URL")
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PostUrlError("The URL must start with https://")
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path or "/"
    query = parse_qs(parsed.query)

    if host in _SHORT_LINKS:
        raise PostUrlError("That is a short link. Open the post and copy its full URL instead.")

    yt = _youtube(host, path, query)
    if yt:
        return ParsedPost("youtube", yt, f"https://www.youtube.com/shorts/{yt}")

    if host in _TT_HOSTS:
        m = re.match(r"^/@([A-Za-z0-9._-]+)/(?:video|photo)/(\d{8,25})/?$", path)
        if m:
            return ParsedPost("tiktok", m.group(2), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}")

    if host in _IG_HOSTS:
        m = re.match(r"^/(?:[A-Za-z0-9._]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]{5,40})/?$", path)
        if m:
            return ParsedPost("instagram", m.group(1), f"https://www.instagram.com/reel/{m.group(1)}/")

    if host in _TH_HOSTS:
        m = re.match(r"^/@([A-Za-z0-9._]+)/post/([A-Za-z0-9_-]{5,40})/?$", path)
        if m:
            return ParsedPost("threads", m.group(2), f"https://www.threads.net/@{m.group(1)}/post/{m.group(2)}")

    if host in _FB_HOSTS:
        m = (re.match(r"^/reel/(\d{5,25})/?$", path)
             or re.match(r"^/[^/]+/videos/(?:[^/]+/)?(\d{5,25})/?$", path))
        vid = m.group(1) if m else None
        if not vid and path.rstrip("/") == "/watch":
            v = (query.get("v") or [""])[0]
            vid = v if re.fullmatch(r"\d{5,25}", v) else None
        if vid:
            return ParsedPost("facebook", vid, f"https://www.facebook.com/reel/{vid}")

    if host in _X_HOSTS:
        m = re.match(r"^/([A-Za-z0-9_]{1,15})/status/(\d{5,25})/?", path)
        if m:
            return ParsedPost("x", m.group(2), f"https://x.com/{m.group(1)}/status/{m.group(2)}")

    raise PostUrlError(
        "Not a post URL this tracker recognises (YouTube, TikTok, Instagram, Threads, Facebook, X).")
