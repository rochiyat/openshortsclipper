"""Posts (a clip published on a channel) and their view snapshots.

Views are never overwritten: every read is a row in view_snapshots, so the
growth curve and the numbers a campaign paid on stay inspectable.
"""
import csv
import io
import os
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import catalog, db, metrics, storage
from .models import (CLAIM_STATUSES, POST_STATUSES, Campaign, Clip, Post,
                     ViewSnapshot, new_id, now_iso)
from .projects import StudioError
from .urls import PostUrlError, parse_post_url


class PostConflict(StudioError):
    status_code = 409

    def __init__(self, code: str, message: str, **extra):
        super().__init__({"code": code, "message": message, **extra})


def _parse_when(value, field) -> Optional[str]:
    """ISO date or datetime -> UTC ISO string (a bare date is noon UTC)."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            dt = datetime.combine(date.fromisoformat(text), datetime.min.time()).replace(
                hour=12, tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise StudioError(f"{field} must be a date or datetime (ISO 8601)")
    dt = dt.astimezone(timezone.utc)
    if dt > datetime.now(timezone.utc) + timedelta(days=1):
        raise StudioError(f"{field} cannot be in the future")
    return dt.isoformat(timespec="seconds")


def get_post(s: Session, post_id: str) -> Post:
    p = s.get(Post, post_id) if storage.valid_id(post_id) else None
    if p is None:
        raise StudioError("Post not found", 404)
    return p


def create_post(s: Session, data: dict, *, force: bool = False) -> Post:
    """Record that a clip went live on a channel (manual URL paste).

    Refuses (409) a URL already recorded. Asks for confirmation (409 unless
    ``force``) when the clip is not approved yet, or when the same file is
    already posted on that channel (a reupload most platforms penalise).
    """
    try:
        parsed = parse_post_url(data.get("url") or "")
    except PostUrlError as e:
        raise StudioError(str(e))

    clip = s.get(Clip, data.get("clip_id") or "") if storage.valid_id(data.get("clip_id") or "") else None
    if clip is None:
        raise StudioError("Clip not found", 404)
    channel = catalog.get_channel(s, data.get("channel_id") or "")
    if channel.archived_at:
        raise StudioError("That channel is archived")
    if channel.platform != parsed.platform:
        raise StudioError(f"That is a {parsed.platform} URL but the channel is on {channel.platform}")

    existing = s.scalars(select(Post).where(Post.platform == parsed.platform,
                                            Post.external_id == parsed.external_id)).first()
    if existing:
        raise PostConflict("already_recorded", "This post is already recorded.", post_id=existing.id)

    if not force:
        if clip.review_status not in ("approved", "posted"):
            raise PostConflict("not_approved",
                               f"This clip is still '{clip.review_status}'. Record the post anyway?")
        if clip.file_sha256:
            dup = s.scalars(select(Post).where(Post.file_sha256 == clip.file_sha256,
                                               Post.channel_id == channel.id)).first()
            if dup:
                raise PostConflict("duplicate_on_channel",
                                   f"The same video file is already posted on {channel.name}.",
                                   post_id=dup.id, url=dup.url)

    if "campaign_id" in data:
        campaign_id = data.get("campaign_id") or None
        if campaign_id:
            catalog.get_campaign(s, campaign_id)
    else:
        campaign_id = clip.project.campaign_id

    ts = now_iso()
    post = Post(
        id=new_id(), clip_id=clip.id, channel_id=channel.id, campaign_id=campaign_id,
        platform=parsed.platform, external_id=parsed.external_id, url=parsed.url,
        posted_at=_parse_when(data.get("posted_at"), "posted_at") or ts,
        method="manual", status="live", claim_status="none",
        notes=(data.get("notes") or "").strip()[:2000] or None,
        file_sha256=clip.file_sha256, created_at=ts, updated_at=ts)
    s.add(post)
    clip.review_status = "posted"
    clip.reviewed_at = clip.reviewed_at or ts
    clip.updated_at = ts
    s.flush()
    return post


def update_post(s: Session, post: Post, data: dict) -> Post:
    if "status" in data:
        if data["status"] not in POST_STATUSES:
            raise StudioError(f"status must be one of: {', '.join(POST_STATUSES)}")
        post.status = data["status"]
        if post.status == "live":
            post.last_error = None
    if "claim_status" in data:
        if data["claim_status"] not in CLAIM_STATUSES:
            raise StudioError(f"claim_status must be one of: {', '.join(CLAIM_STATUSES)}")
        post.claim_status = data["claim_status"]
        if post.claim_status == "submitted" and not post.submitted_to_campaign_at:
            post.submitted_to_campaign_at = now_iso()
    if "submitted_to_campaign_at" in data:
        post.submitted_to_campaign_at = _parse_when(data["submitted_to_campaign_at"],
                                                    "submitted_to_campaign_at")
    if "posted_at" in data and data["posted_at"]:
        post.posted_at = _parse_when(data["posted_at"], "posted_at")
    if "notes" in data:
        post.notes = (data["notes"] or "").strip()[:2000] or None
    if "campaign_id" in data:
        cid = data["campaign_id"] or None
        post.campaign = catalog.get_campaign(s, cid) if cid else None
    post.updated_at = now_iso()
    return post


def delete_post(s: Session, post: Post):
    """Forget a post; its clip goes back to 'approved' when nothing else is live."""
    clip = post.clip
    s.delete(post)
    s.flush()
    if clip is not None and clip.review_status == "posted":
        others = s.scalar(select(func.count(Post.id)).where(Post.clip_id == clip.id))
        if not others:
            clip.review_status = "approved"
            clip.updated_at = now_iso()


# ---- snapshots ----------------------------------------------------------------

def add_snapshot(s: Session, post: Post, counters: metrics.Counters,
                 captured_at: Optional[str] = None) -> ViewSnapshot:
    captured_at = captured_at or now_iso()
    # Two reads in the same second (a manual refresh during the nightly run)
    # update one row instead of failing the whole batch on the unique key.
    snap = s.scalars(select(ViewSnapshot).where(ViewSnapshot.post_id == post.id,
                                                ViewSnapshot.captured_at == captured_at)).first()
    if snap is None:
        snap = ViewSnapshot(id=new_id(), post_id=post.id, captured_at=captured_at)
        s.add(snap)
    snap.views = max(0, int(counters.views))
    snap.likes, snap.comments, snap.shares = counters.likes, counters.comments, counters.shares
    snap.source = counters.source
    post.last_checked_at = snap.captured_at
    post.last_error = None
    s.flush()
    return snap


def manual_snapshot(s: Session, post: Post, data: dict) -> ViewSnapshot:
    views = catalog._number(data.get("views"), "views", 0, 10**13, integer=True)
    if views is None:
        raise StudioError("views is required")
    extra = {k: catalog._number(data.get(k), k, 0, 10**13, integer=True)
             for k in ("likes", "comments", "shares")}
    when = _parse_when(data.get("captured_at"), "captured_at")
    return add_snapshot(s, post, metrics.Counters(views=views, source="manual", **extra), when)


def snapshots_for(s: Session, post_ids: Iterable[str]) -> Dict[str, List[ViewSnapshot]]:
    ids = list(post_ids)
    out: Dict[str, List[ViewSnapshot]] = {i: [] for i in ids}
    if not ids:
        return out
    for snap in s.scalars(select(ViewSnapshot).where(ViewSnapshot.post_id.in_(ids))
                          .order_by(ViewSnapshot.captured_at)):
        out[snap.post_id].append(snap)
    return out


def summarize(snaps: List[ViewSnapshot]) -> dict:
    """Latest counters plus the growth over roughly the last day."""
    if not snaps:
        return {"views": None, "likes": None, "comments": None, "captured_at": None,
                "delta_24h": None, "source": None}
    latest = snaps[-1]
    latest_at = datetime.fromisoformat(latest.captured_at)
    cutoff = latest_at - timedelta(hours=20)
    before = [x for x in snaps[:-1] if datetime.fromisoformat(x.captured_at) <= cutoff]
    return {
        "views": latest.views, "likes": latest.likes, "comments": latest.comments,
        "captured_at": latest.captured_at, "source": latest.source,
        "delta_24h": (latest.views - before[-1].views) if before else None,
    }


def track_days() -> int:
    try:
        return max(1, int(os.environ.get("STUDIO_TRACK_DAYS", "90")))
    except ValueError:
        return 90


def _due_posts(s: Session, post_ids: Optional[Iterable[str]] = None) -> List[Post]:
    stmt = select(Post)
    if post_ids is not None:
        stmt = stmt.where(Post.id.in_(list(post_ids)))
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=track_days())).isoformat(timespec="seconds")
        stmt = stmt.where(Post.status == "live", Post.posted_at >= since)
    return list(s.scalars(stmt.order_by(Post.posted_at)).unique().all())


def collect(posts: List[dict], *, sleep: Callable[[float], None] = time.sleep) -> Dict[str, object]:
    """Fetch counters for plain post dicts {id, platform, external_id, url}.

    Returns post id -> Counters | PostUnavailable | FetchError. Network only,
    no DB: the caller writes the results in one short transaction.
    """
    results: Dict[str, object] = {}
    youtube = [p for p in posts if p["platform"] == "youtube"]
    others = [p for p in posts if p["platform"] != "youtube"]

    key = metrics.youtube_api_key()
    if youtube and key:
        try:
            by_vid = metrics.fetch_youtube_api([p["external_id"] for p in youtube], key)
        except metrics.FetchError as e:
            by_vid = None
            api_error = e
        for p in youtube:
            if by_vid is None:
                results[p["id"]] = api_error
            elif p["external_id"] in by_vid:
                results[p["id"]] = by_vid[p["external_id"]]
            else:
                results[p["id"]] = metrics.PostUnavailable("not returned by the YouTube API")
        youtube = []

    for i, p in enumerate(youtube + others):
        if i:
            sleep(random.uniform(1.5, 4.0))  # pace page reads; this is not a race
        try:
            results[p["id"]] = metrics.fetch_ytdlp(p["url"], p["platform"])
        except metrics.FetchError as e:
            results[p["id"]] = e
        except Exception as e:  # noqa: BLE001 — one bad post never stops the batch
            results[p["id"]] = metrics.FetchError(f"{type(e).__name__}: {str(e)[:200]}")
    return results


def run_snapshots(post_ids: Optional[Iterable[str]] = None, *,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    """Snapshot every live post still being tracked (or just ``post_ids``)."""
    with db.session() as s:
        plain = [{"id": p.id, "platform": p.platform, "external_id": p.external_id, "url": p.url}
                 for p in _due_posts(s, post_ids)]
    results = collect(plain, sleep=sleep) if plain else {}
    summary = {"checked": len(plain), "ok": 0, "failed": 0, "removed": 0,
               "finished_at": None, "errors": []}
    captured_at = now_iso()
    with db.session() as s:
        for pid, res in results.items():
            post = s.get(Post, pid)
            if post is None:
                continue
            if isinstance(res, metrics.Counters):
                add_snapshot(s, post, res, captured_at)
                summary["ok"] += 1
            elif isinstance(res, metrics.PostUnavailable):
                post.status = "removed"
                post.last_error = str(res)[:500]
                post.last_checked_at = captured_at
                summary["removed"] += 1
            else:
                post.last_error = str(res)[:500]
                post.last_checked_at = captured_at
                summary["failed"] += 1
                if len(summary["errors"]) < 5:
                    summary["errors"].append({"post_id": pid, "error": str(res)[:200]})
    summary["finished_at"] = now_iso()
    return summary


# ---- listing & reports ----------------------------------------------------------

def list_posts(s: Session, *, channel_id="", platform="", campaign_id="", status="",
               limit=200, offset=0):
    stmt = select(Post)
    count = select(func.count(Post.id))
    filters = []
    if channel_id:
        filters.append(Post.channel_id == channel_id)
    if platform:
        filters.append(Post.platform == platform)
    if campaign_id == "none":
        filters.append(Post.campaign_id.is_(None))
    elif campaign_id:
        filters.append(Post.campaign_id == campaign_id)
    if status:
        filters.append(Post.status == status)
    for f in filters:
        stmt, count = stmt.where(f), count.where(f)
    limit = max(1, min(int(limit), 1000))
    rows = s.scalars(stmt.order_by(Post.posted_at.desc()).limit(limit)
                     .offset(max(0, int(offset)))).unique().all()
    return rows, s.scalar(count) or 0


def campaign_report(s: Session, campaign: Campaign) -> dict:
    posts = s.scalars(select(Post).where(Post.campaign_id == campaign.id)
                      .order_by(Post.posted_at)).unique().all()
    snaps = snapshots_for(s, [p.id for p in posts])
    rows, totals = [], {"views": 0, "eligible_views": 0, "payout": 0.0, "donation": 0.0, "net": 0.0}
    for p in posts:
        summary = summarize(snaps[p.id])
        est = catalog.estimate_payout(summary["views"] or 0, campaign)
        rows.append({"post_id": p.id, "url": p.url, "platform": p.platform,
                     "channel": p.channel.name if p.channel else None,
                     "clip_title": p.clip.title if p.clip else None,
                     "posted_at": p.posted_at, "status": p.status, "claim_status": p.claim_status,
                     "views": summary["views"], "captured_at": summary["captured_at"], **{
                         k: est[k] for k in ("eligible_views", "payout", "donation", "net")}})
        totals["views"] += summary["views"] or 0
        for k in ("eligible_views", "payout", "donation", "net"):
            totals[k] += est[k]
    for k in ("payout", "donation", "net"):
        totals[k] = round(totals[k], 2)
    return {"campaign_id": campaign.id, "currency": campaign.currency,
            "posts": rows, "totals": totals}


def export_csv(s: Session) -> str:
    posts, _ = list_posts(s, limit=1000)
    snaps = snapshots_for(s, [p.id for p in posts])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["posted_at", "platform", "channel", "url", "clip_title", "campaign", "status",
                "claim_status", "views", "likes", "comments", "views_captured_at",
                "est_payout", "currency"])
    for p in posts:
        sm = summarize(snaps[p.id])
        est = catalog.estimate_payout(sm["views"] or 0, p.campaign) if p.campaign else None
        w.writerow([p.posted_at, p.platform, p.channel.name if p.channel else "", p.url,
                    p.clip.title if p.clip else "", p.campaign.name if p.campaign else "",
                    p.status, p.claim_status, sm["views"] if sm["views"] is not None else "",
                    sm["likes"] if sm["likes"] is not None else "",
                    sm["comments"] if sm["comments"] is not None else "",
                    sm["captured_at"] or "", est["payout"] if est else "",
                    est["currency"] if est else ""])
    return buf.getvalue()

