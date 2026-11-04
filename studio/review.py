"""Review queue: every saved clip goes draft -> approved / rejected before posting."""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from . import storage
from .models import REVIEWABLE_STATUSES, Clip, Project, now_iso
from .projects import StudioError


class RightsUnconfirmed(StudioError):
    """Approving a clip whose project has no recorded rights. The UI asks the
    user to confirm, then retries with ``force``."""
    status_code = 409

    def __init__(self):
        super().__init__({
            "code": "rights_unknown",
            "message": "This project's rights are 'unknown'. Set them (campaign, "
                       "permission, cc or own) before posting, or confirm to approve anyway.",
        })


def list_clips(s: Session, *, review_status: str = "draft", project_id: str = "",
               campaign_id: str = "", limit: int = 100, offset: int = 0):
    stmt = (select(Clip).join(Project, Clip.project_id == Project.id)
            .options(joinedload(Clip.project).joinedload(Project.campaign))
            .where(Project.status == "active"))
    count = (select(func.count(Clip.id)).join(Project, Clip.project_id == Project.id)
             .where(Project.status == "active"))
    filters = []
    if review_status:
        filters.append(Clip.review_status == review_status)
    if project_id:
        filters.append(Clip.project_id == project_id)
    if campaign_id == "none":
        filters.append(Project.campaign_id.is_(None))
    elif campaign_id:
        filters.append(Project.campaign_id == campaign_id)
    for f in filters:
        stmt, count = stmt.where(f), count.where(f)
    limit = max(1, min(int(limit), 500))
    rows = s.scalars(stmt.order_by(Project.created_at.desc(), Clip.clip_index)
                     .limit(limit).offset(max(0, int(offset)))).unique().all()
    return rows, s.scalar(count) or 0


def status_counts(s: Session) -> dict:
    counts = {"draft": 0, "approved": 0, "rejected": 0, "posted": 0}
    for status, n in s.execute(
            select(Clip.review_status, func.count(Clip.id))
            .join(Project, Clip.project_id == Project.id)
            .where(Project.status == "active")
            .group_by(Clip.review_status)):
        counts[status] = n
    return counts


def get_clip(s: Session, clip_id: str) -> Clip:
    c = s.get(Clip, clip_id) if storage.valid_id(clip_id) else None
    if c is None:
        raise StudioError("Clip not found", 404)
    return c


def _text(value, limit):
    value = (value or "").strip()
    return value[:limit] or None


def bulk_status(s: Session, clip_ids, review_status: str, *, force: bool = False) -> dict:
    """Move many clips at once. Posted clips are skipped, never changed; an
    approval touching a project with unknown rights needs ``force`` (all or none)."""
    if review_status not in REVIEWABLE_STATUSES:
        raise StudioError(f"review_status must be one of: {', '.join(REVIEWABLE_STATUSES)}")
    ids = [i for i in dict.fromkeys(clip_ids or []) if storage.valid_id(i)][:500]
    clips = s.scalars(select(Clip).where(Clip.id.in_(ids))).unique().all() if ids else []
    if review_status == "approved" and not force and any(
            c.project.rights == "unknown" and c.review_status != "posted" for c in clips):
        raise RightsUnconfirmed()
    changed, skipped = 0, 0
    ts = now_iso()
    for c in clips:
        if c.review_status == "posted":
            skipped += 1
            continue
        if c.review_status != review_status:
            c.review_status = review_status
            c.reviewed_at = ts if review_status != "draft" else None
            c.updated_at = ts
            changed += 1
    return {"changed": changed, "skipped_posted": skipped, "not_found": len(ids) - len(clips)}


def update_clip(clip: Clip, data: dict, *, force: bool = False) -> Clip:
    """Edit the clip's copy and/or move it through review.

    ``data`` holds only the fields the caller sent. A clip already marked
    ``posted`` keeps that status: it is set by recording a post, not by hand.
    """
    if "title" in data:
        clip.title = _text(data["title"], 300)
    if "hook_text" in data:
        clip.hook_text = _text(data["hook_text"], 300)
    if "caption" in data:
        clip.caption = _text(data["caption"], 5000)
    if "hashtags" in data:
        clip.hashtags = _text(data["hashtags"], 1000)
    status: Optional[str] = data.get("review_status")
    if status is not None and status != clip.review_status:
        if status not in REVIEWABLE_STATUSES:
            raise StudioError(f"review_status must be one of: {', '.join(REVIEWABLE_STATUSES)}")
        if clip.review_status == "posted":
            raise StudioError("This clip is already posted", 409)
        if status == "approved" and clip.project.rights == "unknown" and not force:
            raise RightsUnconfirmed()
        clip.review_status = status
        clip.reviewed_at = now_iso() if status != "draft" else None
    clip.updated_at = now_iso()
    return clip
