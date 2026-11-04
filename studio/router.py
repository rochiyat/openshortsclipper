"""/api/studio: saved projects (self-host only)."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import catalog, db, posts, projects, review, scheduler, storage
from .models import Clip, Post, Project
from .projects import JobBridge, StudioError

router = APIRouter(prefix="/api/studio", tags=["studio"])

_bridge: Optional[JobBridge] = None


def set_bridge(bridge: JobBridge):
    global _bridge
    _bridge = bridge


def _get_bridge() -> JobBridge:
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Studio is not wired to the job system")
    return _bridge


def _raise(e: StudioError):
    raise HTTPException(status_code=e.status_code, detail=e.detail)


def _media_url(project_id: str, file_name: str) -> str:
    return f"/api/studio/media/{project_id}/{file_name}"


def _clip_out(c: Clip) -> dict:
    return {
        "id": c.id,
        "clip_index": c.clip_index,
        "file_name": c.file_name,
        "video_url": _media_url(c.project_id, c.file_name),
        "duration": c.duration,
        "start": c.start_s,
        "end": c.end_s,
        "title": c.title,
        "hook_text": c.hook_text,
        "caption": c.caption,
        "hashtags": c.hashtags,
        "file_sha256": c.file_sha256,
        "review_status": c.review_status,
        "reviewed_at": c.reviewed_at,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _review_counts(clips) -> dict:
    counts = {"draft": 0, "approved": 0, "rejected": 0, "posted": 0}
    for c in clips:
        counts[c.review_status] = counts.get(c.review_status, 0) + 1
    return counts


def _project_out(p: Project, with_clips: bool = False) -> dict:
    clips = list(p.clips or [])
    out = {
        "id": p.id,
        "job_id": p.job_id,
        "title": p.title,
        "source_url": p.source_url,
        "source_type": p.source_type,
        "has_source": bool(p.has_source),
        "rights": p.rights,
        "rights_note": p.rights_note,
        "status": p.status,
        "size_bytes": p.size_bytes,
        "campaign_id": p.campaign_id,
        "campaign_name": p.campaign.name if p.campaign else None,
        "clip_count": len(clips),
        "review_counts": _review_counts(clips),
        "cover_url": _media_url(p.id, clips[0].file_name) if clips else None,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }
    if with_clips:
        out["clips"] = [_clip_out(c) for c in clips]
    return out


class SaveProjectRequest(BaseModel):
    job_id: str
    title: Optional[str] = None
    include_source: bool = False


class UpdateProjectRequest(BaseModel):
    title: Optional[str] = None
    rights: Optional[str] = None
    rights_note: Optional[str] = None
    status: Optional[str] = None
    campaign_id: Optional[str] = None


class UpdateClipRequest(BaseModel):
    title: Optional[str] = None
    hook_text: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    review_status: Optional[str] = None
    force: bool = False


class ChannelRequest(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    handle: Optional[str] = None
    language: Optional[str] = None
    caption_preset: Optional[dict] = None
    watermark_text: Optional[str] = None
    publisher: Optional[str] = None
    archived: Optional[bool] = None


class CampaignRequest(BaseModel):
    name: Optional[str] = None
    platform_source: Optional[str] = None
    brief_url: Optional[str] = None
    cpm: Optional[float] = None
    currency: Optional[str] = None
    min_views: Optional[int] = None
    max_payout: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    donation_pct: Optional[float] = None
    status: Optional[str] = None
    notes: Optional[str] = None


def _sent(model: BaseModel, exclude=()) -> dict:
    """Only the fields the client actually sent: PATCH null means "clear",
    an absent field means "leave alone"."""
    return {k: getattr(model, k) for k in model.model_fields_set if k not in exclude}


@router.get("/health")
def health():
    return {"ok": True, "schema": db.schema_version()}


@router.post("/projects")
def save_project(req: SaveProjectRequest, s: Session = Depends(db.get_session)):
    try:
        existed = projects.find_by_job(s, req.job_id) is not None
        p = projects.save_job(s, _get_bridge(), req.job_id, title=req.title,
                              include_source=req.include_source)
    except StudioError as e:
        _raise(e)
    return {"project": _project_out(p, with_clips=True), "created": not existed}


@router.get("/projects")
def list_projects(q: str = "", status: str = "active",
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  s: Session = Depends(db.get_session)):
    rows, total = projects.list_projects(s, q=q, status=status, limit=limit, offset=offset)
    return {"projects": [_project_out(p) for p in rows], "total": total}


@router.get("/projects/by-job/{job_id}")
def project_by_job(job_id: str, s: Session = Depends(db.get_session)):
    p = projects.find_by_job(s, job_id)
    return {"project": _project_out(p) if p else None}


@router.get("/projects/{project_id}")
def get_project(project_id: str, s: Session = Depends(db.get_session)):
    try:
        p = projects.get_project(s, project_id)
    except StudioError as e:
        _raise(e)
    return {"project": _project_out(p, with_clips=True)}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, req: UpdateProjectRequest,
                   s: Session = Depends(db.get_session)):
    try:
        p = projects.get_project(s, project_id)
        extra = {"campaign_id": req.campaign_id} if "campaign_id" in req.model_fields_set else {}
        projects.update_project(p, title=req.title, rights=req.rights,
                                rights_note=req.rights_note, status=req.status, s=s, **extra)
    except StudioError as e:
        _raise(e)
    return {"project": _project_out(p, with_clips=True)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, s: Session = Depends(db.get_session)):
    try:
        p = projects.get_project(s, project_id)
        projects.delete_project(s, p)
    except StudioError as e:
        _raise(e)
    return {"deleted": project_id}


@router.post("/projects/{project_id}/reopen")
def reopen_project(project_id: str, s: Session = Depends(db.get_session)):
    try:
        p = projects.get_project(s, project_id)
        return projects.reopen(p, _get_bridge())
    except StudioError as e:
        _raise(e)


@router.get("/media/{project_id}/{file_path:path}")
def media(project_id: str, file_path: str):
    if not storage.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Not found")
    base = storage.files_dir(project_id)
    target = storage.safe_join(base, file_path)
    if not target or not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


# ---- review queue -------------------------------------------------------------

def _review_clip_out(c: Clip) -> dict:
    out = _clip_out(c)
    p = c.project
    out["project"] = {
        "id": p.id, "title": p.title, "rights": p.rights, "source_url": p.source_url,
        "campaign_id": p.campaign_id,
        "campaign_name": p.campaign.name if p.campaign else None,
    }
    return out


@router.get("/clips")
def list_clips(review_status: str = "draft", project_id: str = "", campaign_id: str = "",
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               s: Session = Depends(db.get_session)):
    rows, total = review.list_clips(s, review_status=review_status, project_id=project_id,
                                    campaign_id=campaign_id, limit=limit, offset=offset)
    return {"clips": [_review_clip_out(c) for c in rows], "total": total,
            "counts": review.status_counts(s)}


@router.patch("/clips/{clip_id}")
def update_clip(clip_id: str, req: UpdateClipRequest, s: Session = Depends(db.get_session)):
    try:
        c = review.get_clip(s, clip_id)
        review.update_clip(c, _sent(req, exclude=("force",)), force=req.force)
    except StudioError as e:
        _raise(e)
    return {"clip": _review_clip_out(c)}


# ---- channels -----------------------------------------------------------------

@router.get("/channels")
def list_channels(include_archived: bool = False, s: Session = Depends(db.get_session)):
    return {"channels": [catalog.channel_out(c)
                         for c in catalog.list_channels(s, include_archived)]}


@router.post("/channels")
def create_channel(req: ChannelRequest, s: Session = Depends(db.get_session)):
    try:
        c = catalog.create_channel(s, _sent(req))
    except StudioError as e:
        _raise(e)
    return {"channel": catalog.channel_out(c)}


@router.patch("/channels/{channel_id}")
def update_channel(channel_id: str, req: ChannelRequest, s: Session = Depends(db.get_session)):
    try:
        c = catalog.update_channel(catalog.get_channel(s, channel_id), _sent(req))
    except StudioError as e:
        _raise(e)
    return {"channel": catalog.channel_out(c)}


# ---- campaigns ----------------------------------------------------------------

@router.get("/campaigns")
def list_campaigns(status: str = "", s: Session = Depends(db.get_session)):
    rows = catalog.list_campaigns(s, status)
    stats = catalog.campaign_stats(s, [c.id for c in rows])
    return {"campaigns": [catalog.campaign_out(c, stats[c.id]) for c in rows]}


@router.post("/campaigns")
def create_campaign(req: CampaignRequest, s: Session = Depends(db.get_session)):
    try:
        c = catalog.create_campaign(s, _sent(req))
    except StudioError as e:
        _raise(e)
    return {"campaign": catalog.campaign_out(c, catalog.campaign_stats(s, [c.id])[c.id])}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, s: Session = Depends(db.get_session)):
    try:
        c = catalog.get_campaign(s, campaign_id)
    except StudioError as e:
        _raise(e)
    return {"campaign": catalog.campaign_out(c, catalog.campaign_stats(s, [c.id])[c.id])}


@router.patch("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, req: CampaignRequest, s: Session = Depends(db.get_session)):
    try:
        c = catalog.update_campaign(catalog.get_campaign(s, campaign_id), _sent(req))
    except StudioError as e:
        _raise(e)
    return {"campaign": catalog.campaign_out(c, catalog.campaign_stats(s, [c.id])[c.id])}


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str, s: Session = Depends(db.get_session)):
    try:
        catalog.delete_campaign(s, catalog.get_campaign(s, campaign_id))
    except StudioError as e:
        _raise(e)
    return {"deleted": campaign_id}


@router.get("/campaigns/{campaign_id}/estimate")
def estimate(campaign_id: str, views: int = Query(..., ge=0), s: Session = Depends(db.get_session)):
    """Payout for a given view count under this campaign's rules (calculator)."""
    try:
        c = catalog.get_campaign(s, campaign_id)
    except StudioError as e:
        _raise(e)
    return catalog.estimate_payout(views, c)


# ---- posts & views --------------------------------------------------------------

class CreatePostRequest(BaseModel):
    clip_id: str
    channel_id: str
    url: str
    posted_at: Optional[str] = None
    campaign_id: Optional[str] = None
    notes: Optional[str] = None
    force: bool = False


class UpdatePostRequest(BaseModel):
    status: Optional[str] = None
    claim_status: Optional[str] = None
    submitted_to_campaign_at: Optional[str] = None
    posted_at: Optional[str] = None
    campaign_id: Optional[str] = None
    notes: Optional[str] = None


class ManualSnapshotRequest(BaseModel):
    views: int
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    captured_at: Optional[str] = None


def _post_out(p: Post, snaps=None) -> dict:
    summary = posts.summarize(snaps or [])
    est = (catalog.estimate_payout(summary["views"] or 0, p.campaign)
           if p.campaign and summary["views"] is not None else None)
    return {
        "id": p.id, "url": p.url, "platform": p.platform, "external_id": p.external_id,
        "posted_at": p.posted_at, "method": p.method, "status": p.status,
        "claim_status": p.claim_status, "submitted_to_campaign_at": p.submitted_to_campaign_at,
        "notes": p.notes, "last_checked_at": p.last_checked_at, "last_error": p.last_error,
        "channel": {"id": p.channel.id, "name": p.channel.name, "handle": p.channel.handle}
        if p.channel else None,
        "campaign": {"id": p.campaign.id, "name": p.campaign.name} if p.campaign else None,
        "clip": {"id": p.clip.id, "title": p.clip.title, "project_id": p.clip.project_id,
                 "video_url": _media_url(p.clip.project_id, p.clip.file_name)} if p.clip else None,
        "stats": summary,
        "estimate": est,
        "created_at": p.created_at,
    }


@router.post("/posts")
def create_post(req: CreatePostRequest, s: Session = Depends(db.get_session)):
    try:
        p = posts.create_post(s, _sent(req, exclude=("force",)), force=req.force)
    except StudioError as e:
        _raise(e)
    return {"post": _post_out(p)}


@router.get("/posts")
def list_posts(channel_id: str = "", platform: str = "", campaign_id: str = "", status: str = "",
               limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
               s: Session = Depends(db.get_session)):
    rows, total = posts.list_posts(s, channel_id=channel_id, platform=platform,
                                   campaign_id=campaign_id, status=status,
                                   limit=limit, offset=offset)
    snaps = posts.snapshots_for(s, [p.id for p in rows])
    return {"posts": [_post_out(p, snaps[p.id]) for p in rows], "total": total}


@router.patch("/posts/{post_id}")
def update_post(post_id: str, req: UpdatePostRequest, s: Session = Depends(db.get_session)):
    try:
        p = posts.update_post(s, posts.get_post(s, post_id), _sent(req))
    except StudioError as e:
        _raise(e)
    return {"post": _post_out(p, posts.snapshots_for(s, [p.id])[p.id])}


@router.delete("/posts/{post_id}")
def delete_post(post_id: str, s: Session = Depends(db.get_session)):
    try:
        posts.delete_post(s, posts.get_post(s, post_id))
    except StudioError as e:
        _raise(e)
    return {"deleted": post_id}


@router.get("/posts/{post_id}/snapshots")
def post_snapshots(post_id: str, s: Session = Depends(db.get_session)):
    try:
        p = posts.get_post(s, post_id)
    except StudioError as e:
        _raise(e)
    snaps = posts.snapshots_for(s, [p.id])[p.id]
    return {"post_id": p.id, "snapshots": [
        {"captured_at": x.captured_at, "views": x.views, "likes": x.likes,
         "comments": x.comments, "shares": x.shares, "source": x.source} for x in snaps]}


@router.post("/posts/{post_id}/snapshots")
def add_manual_snapshot(post_id: str, req: ManualSnapshotRequest, s: Session = Depends(db.get_session)):
    """Type the numbers in by hand when a platform cannot be read."""
    try:
        p = posts.get_post(s, post_id)
        posts.manual_snapshot(s, p, _sent(req))
    except StudioError as e:
        _raise(e)
    return {"post": _post_out(p, posts.snapshots_for(s, [p.id])[p.id])}


@router.post("/posts/{post_id}/refresh")
def refresh_post(post_id: str, s: Session = Depends(db.get_session)):
    """Read this post's counters now (blocking, one fetch)."""
    try:
        p = posts.get_post(s, post_id)
    except StudioError as e:
        _raise(e)
    s.commit()
    summary = scheduler.run_once([p.id])
    if summary is None:
        raise HTTPException(status_code=409, detail="A snapshot run is already in progress")
    s.expire_all()
    p = posts.get_post(s, post_id)
    return {"post": _post_out(p, posts.snapshots_for(s, [p.id])[p.id]), "summary": summary}


@router.get("/snapshots/status")
def snapshots_status():
    return scheduler.status()


@router.post("/snapshots/run", status_code=202)
def snapshots_run():
    started = scheduler.trigger_background()
    return {"started": started, **scheduler.status()}


@router.get("/campaigns/{campaign_id}/report")
def campaign_report(campaign_id: str, s: Session = Depends(db.get_session)):
    try:
        c = catalog.get_campaign(s, campaign_id)
    except StudioError as e:
        _raise(e)
    return posts.campaign_report(s, c)


@router.get("/export/posts.csv")
def export_posts_csv(s: Session = Depends(db.get_session)):
    return Response(posts.export_csv(s), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="studio-posts.csv"'})
