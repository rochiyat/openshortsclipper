"""Channels (the user's own accounts) and campaigns (pay-per-view programs)."""
import json
import re
from datetime import date
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import storage
from .models import (CAMPAIGN_STATUSES, PLATFORMS, PUBLISHERS, Campaign, Channel,
                     Clip, Project, now_iso)
from .projects import StudioError

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_PRESET_KEYS = ("default_hashtags", "caption_suffix")


def _text(value, limit, field, required=False):
    if value is None:
        if required:
            raise StudioError(f"{field} is required")
        return None
    value = str(value).strip()
    if required and not value:
        raise StudioError(f"{field} is required")
    return value[:limit] or None


def _http_url(value, field):
    value = _text(value, 2000, field)
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise StudioError(f"{field} must be an http(s) URL")
    return value


def _date(value, field):
    value = _text(value, 10, field)
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise StudioError(f"{field} must be a date (YYYY-MM-DD)")


def _number(value, field, minimum=0.0, maximum=None, integer=False):
    if value is None:
        return None
    try:
        num = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise StudioError(f"{field} must be a number")
    if num != num or num < minimum or (maximum is not None and num > maximum):  # NaN too
        bound = f"between {minimum:g} and {maximum:g}" if maximum is not None else f">= {minimum:g}"
        raise StudioError(f"{field} must be {bound}")
    return num


# ---- channels ---------------------------------------------------------------

def channel_out(c: Channel) -> dict:
    return {
        "id": c.id, "name": c.name, "platform": c.platform, "handle": c.handle,
        "language": c.language, "caption_preset": c.preset,
        "watermark_text": c.watermark_text, "publisher": c.publisher,
        "archived": c.archived_at is not None,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }


def _apply_channel(c: Channel, data: dict, creating: bool):
    if creating or "name" in data:
        c.name = _text(data.get("name"), 120, "name", required=True)
    if creating or "platform" in data:
        platform = (data.get("platform") or "").strip().lower()
        if platform not in PLATFORMS:
            raise StudioError(f"platform must be one of: {', '.join(PLATFORMS)}")
        c.platform = platform
    if "handle" in data:
        handle = _text(data.get("handle"), 120, "handle")
        c.handle = handle.lstrip("@") if handle else None
    if "language" in data:
        lang = _text(data.get("language"), 16, "language")
        if lang and not _LANG_RE.match(lang):
            raise StudioError("language must be a code like 'id' or 'en'")
        c.language = lang
    if "caption_preset" in data:
        preset = data.get("caption_preset") or {}
        if not isinstance(preset, dict):
            raise StudioError("caption_preset must be an object")
        clean = {k: _text(preset.get(k), 2000, k) for k in _PRESET_KEYS if preset.get(k)}
        c.caption_preset = json.dumps({k: v for k, v in clean.items() if v}, ensure_ascii=False)
    if "watermark_text" in data:
        c.watermark_text = _text(data.get("watermark_text"), 120, "watermark_text")
    if creating or "publisher" in data:
        publisher = (data.get("publisher") or "manual").strip().lower()
        if publisher not in PUBLISHERS:
            raise StudioError(f"publisher must be one of: {', '.join(PUBLISHERS)}")
        c.publisher = publisher
    if "archived" in data:
        c.archived_at = (c.archived_at or now_iso()) if data.get("archived") else None


def list_channels(s: Session, include_archived=False):
    stmt = select(Channel).order_by(Channel.platform, Channel.name)
    if not include_archived:
        stmt = stmt.where(Channel.archived_at.is_(None))
    return s.scalars(stmt).all()


def get_channel(s: Session, channel_id: str) -> Channel:
    c = s.get(Channel, channel_id) if storage.valid_id(channel_id) else None
    if c is None:
        raise StudioError("Channel not found", 404)
    return c


def create_channel(s: Session, data: dict) -> Channel:
    ts = now_iso()
    c = Channel(created_at=ts, updated_at=ts)
    _apply_channel(c, data, creating=True)
    s.add(c)
    s.flush()
    return c


def update_channel(c: Channel, data: dict) -> Channel:
    _apply_channel(c, data, creating=False)
    c.updated_at = now_iso()
    return c


# ---- campaigns --------------------------------------------------------------

def estimate_payout(views, campaign: Campaign) -> dict:
    """Payout for one post's views under a campaign's rules.

    Below ``min_views`` nothing is claimable; ``max_payout`` caps a single
    post. The donation share is taken from the capped payout.
    """
    views = max(0, int(views or 0))
    eligible = views if views >= (campaign.min_views or 0) else 0
    payout = eligible / 1000.0 * float(campaign.cpm or 0)
    if campaign.max_payout is not None:
        payout = min(payout, float(campaign.max_payout))
    donation = payout * float(campaign.donation_pct or 0) / 100.0
    return {
        "views": views,
        "eligible_views": eligible,
        "payout": round(payout, 2),
        "donation": round(donation, 2),
        "net": round(payout - donation, 2),
        "currency": campaign.currency,
    }


def campaign_out(c: Campaign, stats: Optional[dict] = None) -> dict:
    out = {
        "id": c.id, "name": c.name, "platform_source": c.platform_source,
        "brief_url": c.brief_url, "cpm": c.cpm, "currency": c.currency,
        "min_views": c.min_views, "max_payout": c.max_payout,
        "starts_at": c.starts_at, "ends_at": c.ends_at,
        "donation_pct": c.donation_pct, "status": c.status, "notes": c.notes,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }
    if stats is not None:
        out["stats"] = stats
    return out


def _apply_campaign(c: Campaign, data: dict, creating: bool):
    if creating or "name" in data:
        c.name = _text(data.get("name"), 160, "name", required=True)
    if "platform_source" in data:
        c.platform_source = _text(data.get("platform_source"), 120, "platform_source")
    if "brief_url" in data:
        c.brief_url = _http_url(data.get("brief_url"), "brief_url")
    if creating or "cpm" in data:
        c.cpm = _number(data.get("cpm") or 0, "cpm", 0, 1_000_000)
    if creating or "currency" in data:
        cur = (data.get("currency") or "USD").strip().upper()
        if not _CURRENCY_RE.match(cur):
            raise StudioError("currency must be a 3-letter code like USD or IDR")
        c.currency = cur
    if creating or "min_views" in data:
        c.min_views = _number(data.get("min_views") or 0, "min_views", 0, 10**12, integer=True)
    if "max_payout" in data:
        c.max_payout = _number(data.get("max_payout"), "max_payout", 0, 10**12)
    if "starts_at" in data:
        c.starts_at = _date(data.get("starts_at"), "starts_at")
    if "ends_at" in data:
        c.ends_at = _date(data.get("ends_at"), "ends_at")
    if c.starts_at and c.ends_at and c.ends_at < c.starts_at:
        raise StudioError("ends_at cannot be before starts_at")
    if creating or "donation_pct" in data:
        c.donation_pct = _number(data.get("donation_pct") or 0, "donation_pct", 0, 100)
    if creating or "status" in data:
        status = (data.get("status") or "active").strip().lower()
        if status not in CAMPAIGN_STATUSES:
            raise StudioError(f"status must be one of: {', '.join(CAMPAIGN_STATUSES)}")
        c.status = status
    if "notes" in data:
        c.notes = _text(data.get("notes"), 4000, "notes")


def campaign_stats(s: Session, campaign_ids) -> dict:
    """Projects and clips per review status for each campaign id."""
    ids = list(campaign_ids)
    stats = {cid: {"projects": 0, "clips": {"draft": 0, "approved": 0, "rejected": 0, "posted": 0}}
             for cid in ids}
    if not ids:
        return stats
    for cid, n in s.execute(select(Project.campaign_id, func.count(Project.id))
                            .where(Project.campaign_id.in_(ids))
                            .group_by(Project.campaign_id)):
        stats[cid]["projects"] = n
    for cid, status, n in s.execute(
            select(Project.campaign_id, Clip.review_status, func.count(Clip.id))
            .join(Clip, Clip.project_id == Project.id)
            .where(Project.campaign_id.in_(ids))
            .group_by(Project.campaign_id, Clip.review_status)):
        stats[cid]["clips"][status] = n
    return stats


def list_campaigns(s: Session, status: str = ""):
    stmt = select(Campaign).order_by(Campaign.created_at.desc())
    if status in CAMPAIGN_STATUSES:
        stmt = stmt.where(Campaign.status == status)
    return s.scalars(stmt).all()


def get_campaign(s: Session, campaign_id: str) -> Campaign:
    c = s.get(Campaign, campaign_id) if storage.valid_id(campaign_id) else None
    if c is None:
        raise StudioError("Campaign not found", 404)
    return c


def create_campaign(s: Session, data: dict) -> Campaign:
    ts = now_iso()
    c = Campaign(created_at=ts, updated_at=ts)
    _apply_campaign(c, data, creating=True)
    s.add(c)
    s.flush()
    return c


def update_campaign(c: Campaign, data: dict) -> Campaign:
    _apply_campaign(c, data, creating=False)
    c.updated_at = now_iso()
    return c


def delete_campaign(s: Session, c: Campaign):
    """Projects keep existing; their campaign link is cleared (ON DELETE SET NULL)."""
    s.delete(c)
    s.flush()
