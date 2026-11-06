"""Where the money is: earnings per campaign, and what to do next to earn more.

Plain arithmetic over the user's own posts and view snapshots, no model: every
recommendation names the numbers it comes from, so it can be checked. Payouts
use catalog.estimate_payout (the campaign's CPM, minimum and cap). Amounts in
different currencies are never added together.
"""
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, posts
from .models import Campaign, Post

NEAR_MINIMUM = 0.6        # a post past 60% of the minimum is worth a push
ENDING_SOON_DAYS = 3
STALE_HOURS = 48          # views older than this are refreshed before trusting them
WEAK_SHARE = 0.25         # earning under a quarter of the best campaign per post
MIN_POSTS_TO_COMPARE = 3
DURATION_BUCKETS = ((0, 20, "under 20 s"), (20, 35, "20-35 s"), (35, 60, "35-60 s"), (60, 10**6, "over 60 s"))


def _days_left(ends_at: Optional[str], today: date) -> Optional[int]:
    if not ends_at:
        return None
    try:
        return (date.fromisoformat(ends_at[:10]) - today).days
    except ValueError:
        return None


def _bucket(duration) -> Optional[str]:
    if duration is None:
        return None
    for lo, hi, label in DURATION_BUCKETS:
        if lo <= duration < hi:
            return label
    return None


def _median(values) -> Optional[int]:
    values = [v for v in values if v is not None]
    return int(statistics.median(values)) if values else None


def _money(amount: float, currency: str) -> str:
    return f"{amount:,.2f} {currency}"


def insights(s: Session, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    stale_before = now - timedelta(hours=STALE_HOURS)

    campaigns = s.scalars(select(Campaign).where(Campaign.status != "archived")).all()
    all_posts = s.scalars(select(Post).where(Post.status.in_(("live", "removed")))).unique().all()
    snaps = posts.snapshots_for(s, [p.id for p in all_posts])

    rows = []
    for p in all_posts:
        sm = posts.summarize(snaps[p.id])
        est = catalog.estimate_payout(sm["views"] or 0, p.campaign) if p.campaign else None
        rows.append({"post": p, "views": sm["views"], "captured_at": sm["captured_at"], "est": est})

    per_campaign = []
    totals = defaultdict(lambda: {"payout": 0.0, "net": 0.0, "unclaimed": 0.0})
    recs = []
    by_campaign = defaultdict(list)
    for r in rows:
        if r["post"].campaign_id:
            by_campaign[r["post"].campaign_id].append(r)

    for c in campaigns:
        mine = by_campaign.get(c.id, [])
        payout = sum(r["est"]["payout"] for r in mine)
        net = sum(r["est"]["net"] for r in mine)
        eligible = [r for r in mine if r["est"]["eligible_views"] > 0]
        unsubmitted = [r for r in eligible if r["post"].claim_status in (None, "none")]
        unclaimed = sum(r["est"]["payout"] for r in unsubmitted)
        near = [r for r in mine if c.min_views and r["views"] is not None
                and NEAR_MINIMUM * c.min_views <= r["views"] < c.min_views]
        capped = [r for r in mine if c.max_payout is not None and r["est"]["payout"] >= float(c.max_payout) > 0]
        days_left = _days_left(c.ends_at, today)
        entry = {
            "campaign_id": c.id, "name": c.name, "status": c.status, "currency": c.currency,
            "cpm": c.cpm, "min_views": c.min_views, "max_payout": c.max_payout,
            "days_left": days_left, "posts": len(mine), "eligible_posts": len(eligible),
            "views": sum(r["views"] or 0 for r in mine), "payout": round(payout, 2), "net": round(net, 2),
            "payout_per_post": round(payout / len(mine), 2) if mine else None,
            "unsubmitted_posts": len(unsubmitted), "unclaimed_payout": round(unclaimed, 2),
            "near_minimum_posts": len(near), "capped_posts": len(capped),
        }
        per_campaign.append(entry)
        if c.status == "active":
            totals[c.currency]["payout"] += payout
            totals[c.currency]["net"] += net
            totals[c.currency]["unclaimed"] += unclaimed

        if unsubmitted:
            recs.append({"kind": "submit", "priority": 1, "campaign_id": c.id,
                         "message": f"Submit {len(unsubmitted)} eligible post{'s' if len(unsubmitted) != 1 else ''} "
                                    f"to {c.name}: about {_money(unclaimed, c.currency)} not claimed yet.",
                         "post_ids": [r["post"].id for r in unsubmitted]})
        if near and c.status == "active":
            recs.append({"kind": "near_minimum", "priority": 2, "campaign_id": c.id,
                         "message": f"{len(near)} post{'s' if len(near) != 1 else ''} in {c.name} "
                                    f"{'are' if len(near) != 1 else 'is'} past {int(NEAR_MINIMUM * 100)}% of the "
                                    f"{c.min_views:,}-view minimum: pin a comment, answer comments, "
                                    "or share it to get it over the line.",
                         "post_ids": [r["post"].id for r in near]})
        if c.status == "active" and days_left is not None and 0 <= days_left <= ENDING_SOON_DAYS:
            recs.append({"kind": "ending_soon", "priority": 1 if not mine else 2, "campaign_id": c.id,
                         "message": f"{c.name} ends in {days_left} day{'s' if days_left != 1 else ''}"
                                    + (" and you have no post in it yet." if not mine
                                       else ": post what is ready now, views after the end may not count.")})
        if c.status == "active" and days_left is not None and days_left < 0:
            recs.append({"kind": "ended", "priority": 3, "campaign_id": c.id,
                         "message": f"{c.name} ended {-days_left} day{'s' if days_left != -1 else ''} ago: "
                                    "mark it ended so it stops counting as active."})
        if capped:
            recs.append({"kind": "capped", "priority": 3, "campaign_id": c.id,
                         "message": f"{len(capped)} post{'s' if len(capped) != 1 else ''} in {c.name} hit the "
                                    f"{_money(float(c.max_payout), c.currency)} cap per post: more clips earn "
                                    "more than more views on one clip."})

    # Campaigns compared per post, in the same currency only.
    by_currency = defaultdict(list)
    for e in per_campaign:
        if e["status"] == "active" and e["posts"] >= MIN_POSTS_TO_COMPARE and e["payout_per_post"] is not None:
            by_currency[e["currency"]].append(e)
    for currency, group in by_currency.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda e: e["payout_per_post"], reverse=True)
        best = group[0]
        if best["payout_per_post"] <= 0:
            continue
        recs.append({"kind": "focus", "priority": 2, "campaign_id": best["campaign_id"],
                     "message": f"{best['name']} earns the most per post "
                                f"({_money(best['payout_per_post'], currency)} over {best['posts']} posts): "
                                "put your next clips there first."})
        for weak in group[1:]:
            if weak["payout_per_post"] < WEAK_SHARE * best["payout_per_post"]:
                recs.append({"kind": "weak", "priority": 3, "campaign_id": weak["campaign_id"],
                             "message": f"{weak['name']} earns {_money(weak['payout_per_post'], currency)} per post, "
                                        f"under a quarter of {best['name']}: consider pausing it."})

    # Channels and clip lengths by median views.
    channel_views = defaultdict(list)
    length_views = defaultdict(list)
    for r in rows:
        p = r["post"]
        if r["views"] is None:
            continue
        if p.channel:
            channel_views[(p.channel.name, p.platform)].append(r["views"])
        bucket = _bucket(p.clip.duration if p.clip else None)
        if bucket:
            length_views[bucket].append(r["views"])
    channels = sorted(({"channel": k[0], "platform": k[1], "posts": len(v), "median_views": _median(v)}
                       for k, v in channel_views.items()), key=lambda x: x["median_views"] or 0, reverse=True)
    lengths = sorted(({"length": k, "posts": len(v), "median_views": _median(v)}
                      for k, v in length_views.items()), key=lambda x: x["median_views"] or 0, reverse=True)
    if len(channels) >= 2 and channels[0]["posts"] >= MIN_POSTS_TO_COMPARE and channels[0]["median_views"]:
        top, low = channels[0], channels[-1]
        if low["median_views"] is not None and top["median_views"] >= 2 * max(1, low["median_views"]):
            recs.append({"kind": "channel", "priority": 3,
                         "message": f"{top['channel']} ({top['platform']}) gets a median of "
                                    f"{top['median_views']:,} views per post, {low['channel']} "
                                    f"{low['median_views']:,}: post campaign clips to {top['channel']} first."})
    if len(lengths) >= 2 and lengths[0]["posts"] >= MIN_POSTS_TO_COMPARE:
        recs.append({"kind": "length", "priority": 3,
                     "message": f"Clips {lengths[0]['length']} do best (median {lengths[0]['median_views']:,} "
                                f"views over {lengths[0]['posts']} posts)."})

    stale = [r for r in rows if r["post"].status == "live"
             and (r["captured_at"] is None or datetime.fromisoformat(r["captured_at"]) < stale_before)]
    if stale:
        recs.append({"kind": "stale", "priority": 2,
                     "message": f"{len(stale)} live post{'s' if len(stale) != 1 else ''} have no view count from "
                                f"the last {STALE_HOURS} h: refresh views before trusting these numbers.",
                     "post_ids": [r["post"].id for r in stale][:200]})

    recs.sort(key=lambda r: r["priority"])
    return {
        "generated_at": now.isoformat(),
        "totals": {cur: {k: round(v, 2) for k, v in t.items()} for cur, t in totals.items()},
        "campaigns": sorted(per_campaign, key=lambda e: (e["status"] != "active", -(e["payout"] or 0))),
        "channels": channels,
        "lengths": lengths,
        "recommendations": recs,
    }
