"""Publisher adapters: put a saved clip on a channel.

``youtube_api``: direct upload with the channel's own OAuth token (see
youtube_oauth.py). Note: Google locks videos uploaded through an API project
that has not passed its audit to *private*; until yours is audited, publish
then switch the video to public in YouTube Studio.

``uploadpost``: Upload-Post (TikTok / Instagram / YouTube) with the server key
UPLOAD_POST_API_KEY. Uploads are asynchronous: the job waits for the status
endpoint, and a post whose public URL is not reported (a TikTok draft, for
one) is recorded as pending until you paste the URL.
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from .urls import PostUrlError, parse_post_url

UPLOAD_POST_URL = "https://api.upload-post.com/api/upload"
UPLOAD_POST_STATUS_URL = "https://api.upload-post.com/api/uploadposts/status"
UPLOAD_POST_PLATFORMS = ("tiktok", "instagram", "youtube")
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX = 5000


@dataclass
class PublishRequest:
    file_path: str
    platform: str
    title: str
    description: str
    channel_id: str
    config: dict


@dataclass
class PublishResult:
    url: Optional[str] = None           # public post URL when known
    request_id: Optional[str] = None    # async vendor request still running


class PublishError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def youtube_title(title: str) -> str:
    t = " ".join(str(title or "").split())
    if len(t) <= YOUTUBE_TITLE_MAX:
        return t or "Short"
    cut = t[:YOUTUBE_TITLE_MAX]
    space = cut.rfind(" ")
    return (cut[:space] if space > 40 else cut).rstrip(" ,.;:-")


def _clean_description(text: str) -> str:
    # YouTube rejects angle brackets in descriptions.
    return re.sub(r"[<>]", "", str(text or ""))[:YOUTUBE_DESCRIPTION_MAX]


def _status_error(service: str, resp) -> PublishError:
    reason = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            errs = err.get("errors") or []
            reason = (errs[0].get("reason") if errs else "") or err.get("status") or ""
        elif isinstance(err, str):
            reason = err
        elif isinstance(body, dict):
            reason = str(body.get("message") or "")[:200]
    except ValueError:
        pass
    code = resp.status_code
    # 4xx is ours to fix (auth, quota, bad metadata); 429 and 5xx may pass.
    retryable = code == 429 or code >= 500
    if code == 403 and reason in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded"):
        retryable = True
    return PublishError(f"{service} answered {code} {reason}".strip(), retryable=retryable)


def _chunks(path: str, size: int = 4 * 1024 * 1024):
    with open(path, "rb") as f:
        while True:
            block = f.read(size)
            if not block:
                return
            yield block


# ---- YouTube (direct) -----------------------------------------------------------

def publish_youtube(req: PublishRequest) -> PublishResult:
    import httpx
    import ssrf_guard
    from . import youtube_oauth

    try:
        token = youtube_oauth.access_token(req.channel_id)
    except youtube_oauth.OAuthError as e:
        raise PublishError(str(e), retryable=False)
    privacy = req.config.get("privacy") or "public"
    metadata = {
        "snippet": {"title": youtube_title(req.title),
                    "description": _clean_description(req.description),
                    "categoryId": "27"},  # Education; editable in YouTube Studio
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    size = os.path.getsize(req.file_path)
    try:
        with ssrf_guard.guarded(), httpx.Client(timeout=httpx.Timeout(30, write=600, read=600)) as client:
            init = client.post(
                YOUTUBE_UPLOAD_URL,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Type": "video/mp4",
                         "X-Upload-Content-Length": str(size)},
                content=json.dumps(metadata))
            if init.status_code != 200 or not init.headers.get("location"):
                raise _status_error("YouTube", init)
            up = client.put(init.headers["location"], content=_chunks(req.file_path),
                            headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
            if up.status_code not in (200, 201):
                raise _status_error("YouTube", up)
            video_id = (up.json() or {}).get("id")
    except httpx.HTTPError as e:
        raise PublishError(f"YouTube upload interrupted: {type(e).__name__}") from e
    if not video_id:
        raise PublishError("YouTube accepted the upload without a video id", retryable=False)
    return PublishResult(url=f"https://www.youtube.com/shorts/{video_id}")


# ---- Upload-Post ----------------------------------------------------------------

def upload_post_key() -> str:
    return os.environ.get("UPLOAD_POST_API_KEY", "").strip()


def publish_uploadpost(req: PublishRequest) -> PublishResult:
    import httpx
    import ssrf_guard

    key = upload_post_key()
    if not key:
        raise PublishError("Set UPLOAD_POST_API_KEY on the server to publish through Upload-Post",
                           retryable=False)
    profile = req.config.get("profile")
    if not profile:
        raise PublishError("Set the channel's Upload-Post profile first", retryable=False)
    if req.platform not in UPLOAD_POST_PLATFORMS:
        raise PublishError(f"Upload-Post publishing supports {', '.join(UPLOAD_POST_PLATFORMS)}",
                           retryable=False)
    data = {"user": profile, "title": req.description or req.title,
            "platform[]": [req.platform], "async_upload": "true"}
    if req.platform == "tiktok":
        data["tiktok_title"] = req.description or req.title
        data["post_mode"] = os.environ.get("TIKTOK_POST_MODE", "MEDIA_UPLOAD").strip()
    elif req.platform == "instagram":
        data["instagram_title"] = req.description or req.title
        data["media_type"] = "REELS"
    elif req.platform == "youtube":
        data["youtube_title"] = youtube_title(req.title)
        data["youtube_description"] = _clean_description(req.description)
        data["privacyStatus"] = req.config.get("privacy") or "public"
    try:
        with open(req.file_path, "rb") as f, ssrf_guard.guarded(), httpx.Client(timeout=180) as client:
            resp = client.post(UPLOAD_POST_URL, headers={"Authorization": f"Apikey {key}"}, data=data,
                               files={"video": (os.path.basename(req.file_path), f, "video/mp4")})
    except httpx.HTTPError as e:
        raise PublishError(f"Upload-Post unreachable: {type(e).__name__}") from e
    if resp.status_code not in (200, 201, 202):
        raise _status_error("Upload-Post", resp)
    out = resp.json() if resp.content else {}
    url = _find_post_url(out, req.platform)
    if url:
        return PublishResult(url=url)
    if out.get("request_id"):
        return PublishResult(request_id=str(out["request_id"]))
    raise PublishError("Upload-Post accepted the upload without a request id", retryable=False)


def _find_post_url(payload, platform: str) -> Optional[str]:
    """The first URL anywhere in a vendor payload that parses as a post of
    ``platform``. The field name varies (post_url, url, ...), the shape does not."""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and node.startswith("http"):
            try:
                parsed = parse_post_url(node)
            except PostUrlError:
                continue
            if parsed.platform == platform:
                return parsed.url
    return None


def check_uploadpost(request_id: str, platform: str) -> dict:
    """{"state": "running" | "done" | "failed", "url": str | None, "error": str | None}"""
    import httpx
    import ssrf_guard

    key = upload_post_key()
    if not key:
        return {"state": "failed", "url": None, "error": "UPLOAD_POST_API_KEY is not set"}
    try:
        with ssrf_guard.guarded():
            resp = httpx.get(UPLOAD_POST_STATUS_URL, params={"request_id": request_id},
                             headers={"Authorization": f"Apikey {key}"}, timeout=20)
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {"state": "running", "url": None, "error": None}
    if not isinstance(data, dict):
        return {"state": "running", "url": None, "error": None}
    results = [r for r in (data.get("results") or []) if isinstance(r, dict)]
    mine = [r for r in results if r.get("platform") == platform] or results
    if any(r.get("success") is False for r in mine) or data.get("status") == "failed":
        msg = next((r.get("message") or r.get("error") for r in mine if r.get("success") is False), None)
        return {"state": "failed", "url": None, "error": " ".join(str(msg or "failed").split())[:300]}
    status = str(data.get("status") or "").lower()
    if status in ("completed", "complete", "success", "done", "finished") or any(
            r.get("success") is True for r in mine):
        return {"state": "done", "url": _find_post_url(mine or data, platform), "error": None}
    return {"state": "running", "url": None, "error": None}


ADAPTERS = {"youtube_api": publish_youtube, "uploadpost": publish_uploadpost}


def availability() -> dict:
    from . import youtube_oauth
    return {
        "youtube_api": {"configured": youtube_oauth.configured(),
                        "needs": "STUDIO_YOUTUBE_CLIENT_ID + STUDIO_YOUTUBE_CLIENT_SECRET"},
        "uploadpost": {"configured": bool(upload_post_key()), "needs": "UPLOAD_POST_API_KEY",
                       "platforms": list(UPLOAD_POST_PLATFORMS)},
    }
