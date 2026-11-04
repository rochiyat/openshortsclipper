"""The Studio overview: what grew, what earns, what needs attention.

Daily gains come from the view snapshots: a post's gain on a day is its last
read that day minus its last read before that day, in the schedule's time zone.
A post's very first read counts as a gain only when it was posted that day or
the day before; a post recorded weeks late would otherwise show all its
lifetime views as one day's spike.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import posts, scheduler
from .models import Campaign, Clip, Post, Project, PublishJob


def _local_day(iso: str, tz) -> date:
    return datetime.fromisoformat(iso).astimezone(tz).date()


def daily_gains(post_rows: List[Post], snaps: Dict[str, list], tz) -> Dict[str, Dict[date, int]]:
    """post id -> {local day: views gained that day}."""
    out: Dict[str, Dict[date, int]] = {}
    for p in post_rows:
        per_day: Dict[date, int] = {}
        for s in snaps.get(p.id, []):  # chronological
            per_day[_local_day(s.captured_at, tz)] = s.views
        gains: Dict[date, int] = {}
        prev = None
        posted_day = _local_day(p.posted_at, tz)
        for day in sorted(per_day):
            views = per_day[day]
            if prev is None:
                if (day - posted_day).days <= 1:
                    gains[day] = views
            else:
                gains[day] = max(0, views - prev)  # a recount down is not a loss
            prev = views
        out[p.id] = gains
    return out


def overview(s: Session, days: int = 14, now: datetime = None) -> dict:
    tz = scheduler.zone()
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(tz).date()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

    post_rows = s.scalars(select(Post).where(Post.status.in_(("live", "removed")))).unique().all()
    snaps = posts.snapshots_for(s, [p.id for p in post_rows])
    gains = daily_gains(post_rows, snaps, tz)

    per_day = defaultdict(int)
    for g in gains.values():
        for day, v in g.items():
            per_day[day] += v
    last7 = {today - timedelta(days=i) for i in range(7)}
    prev7 = {today - timedelta(days=i) for i in range(7, 14)}

    top = []
    for p in post_rows:
        gain7 = sum(v for d, v in gains[p.id].items() if d in last7)
        latest = snaps[p.id][-1].views if snaps[p.id] else None
        if gain7 or latest:
            top.append({"post_id": p.id, "title": p.clip.title if p.clip else None,
                        "channel": p.channel.name if p.channel else None, "platform": p.platform,
                        "url": p.url, "gain_7d": gain7, "views": latest})
    top.sort(key=lambda x: (x["gain_7d"], x["views"] or 0), reverse=True)

    payouts = []
    for c in s.scalars(select(Campaign).where(Campaign.status == "active")).all():
        report = posts.campaign_report(s, c)
        if report["posts"]:
            payouts.append({"campaign_id": c.id, "name": c.name, "currency": c.currency,
                            "posts": len(report["posts"]), **report["totals"]})

    review_counts = dict(s.execute(
        select(Clip.review_status, func.count(Clip.id)).join(Project, Clip.project_id == Project.id)
        .where(Project.status == "active").group_by(Clip.review_status)).all())
    job_counts = dict(s.execute(select(PublishJob.status, func.count(PublishJob.id))
                                .group_by(PublishJob.status)).all())
    post_counts = dict(s.execute(select(Post.status, func.count(Post.id))
                                 .group_by(Post.status)).all())

    attention = []
    if job_counts.get("failed"):
        attention.append({"kind": "publish_failed", "count": job_counts["failed"],
                          "message": "publish jobs failed"})
    if post_counts.get("pending"):
        attention.append({"kind": "post_pending", "count": post_counts["pending"],
                          "message": "posts waiting for their public URL"})
    read_errors = s.scalar(select(func.count(Post.id)).where(
        Post.status == "live", Post.last_error.is_not(None))) or 0
    if read_errors:
        attention.append({"kind": "read_errors", "count": read_errors,
                          "message": "live posts whose views could not be read last time"})
    unknown_rights = s.scalar(select(func.count(Clip.id)).join(Project, Clip.project_id == Project.id)
                              .where(Project.status == "active", Project.rights == "unknown",
                                     Clip.review_status.in_(("approved", "posted")))) or 0
    if unknown_rights:
        attention.append({"kind": "rights_unknown", "count": unknown_rights,
                          "message": "approved or posted clips from sources with unknown rights"})

    return {
        "time_zone": str(tz),
        "today": today.isoformat(),
        "views_7d": sum(per_day[d] for d in last7),
        "views_prev_7d": sum(per_day[d] for d in prev7),
        "views_today": per_day[today],
        "total_views": sum(x[-1].views for x in snaps.values() if x),
        "daily": [{"day": d.isoformat(), "views": per_day[d]} for d in window],
        "top_posts": top[:5],
        "payouts": payouts,
        "review": {k: review_counts.get(k, 0) for k in ("draft", "approved", "rejected", "posted")},
        "queue": {k: job_counts.get(k, 0) for k in ("queued", "running", "awaiting", "failed")},
        "posts": {k: post_counts.get(k, 0) for k in ("live", "pending", "removed")},
        "attention": attention,
        "snapshots": scheduler.status(),
    }
