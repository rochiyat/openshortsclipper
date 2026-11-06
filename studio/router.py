"""/api/studio: saved projects (self-host only)."""
import json
import os
from typing import Optional

import html
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import (campaign_ai, catalog, copywriter, db, posts, projects, publishers, publishing,
               revenue, review, scheduler, storage, summary, youtube_oauth)
from .models import Clip, Post, Project, PublishJob, now_iso
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


def _selection_notes(c: Clip) -> tuple:
    """(predicted_score, why) from the pipeline's record of the clip, kept in the
    metadata column, so projects saved before this was shown have them too."""
    try:
        meta = json.loads(c.metadata_json or "{}")
    except ValueError:
        return None, None
    if not isinstance(meta, dict):
        return None, None
    score = meta.get("predicted_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    why = meta.get("why")
    return score, (why if isinstance(why, str) and why.strip() else None)


def _clip_out(c: Clip) -> dict:
    predicted_score, why = _selection_notes(c)
    return {
        "predicted_score": predicted_score,
        "why": why,
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
        "tags": p.tags,
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
    tags: Optional[list] = None


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
    publisher_config: Optional[dict] = None
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
    brief: Optional[dict] = None  # campaign_ai.clean_brief shape; null clears it


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
def list_projects(q: str = "", status: str = "active", tag: str = "",
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  s: Session = Depends(db.get_session)):
    rows, total = projects.list_projects(s, q=q, status=status, tag=tag, limit=limit, offset=offset)
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
                                rights_note=req.rights_note, status=req.status, tags=req.tags,
                                s=s, **extra)
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
    url: Optional[str] = None
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
    if not p.url:
        raise HTTPException(status_code=409, detail="Attach the post's URL first")
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


# ---- publishing ---------------------------------------------------------------------

class PublishRequestBody(BaseModel):
    clip_id: str
    channel_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_at: Optional[str] = None
    campaign_id: Optional[str] = None
    force: bool = False


class UpdatePublishJobRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_at: Optional[str] = None


def _job_out(j: PublishJob) -> dict:
    return {
        "id": j.id, "publisher": j.publisher, "status": j.status,
        "title": j.title, "description": j.description,
        "scheduled_at": j.scheduled_at, "attempts": j.attempts,
        "next_attempt_at": j.next_attempt_at, "last_error": j.last_error,
        "post_id": j.post_id, "created_at": j.created_at, "updated_at": j.updated_at,
        "channel": {"id": j.channel.id, "name": j.channel.name, "platform": j.channel.platform}
        if j.channel else None,
        "campaign": {"id": j.campaign.id, "name": j.campaign.name} if j.campaign else None,
        "clip": {"id": j.clip.id, "title": j.clip.title,
                 "video_url": _media_url(j.clip.project_id, j.clip.file_name)} if j.clip else None,
    }


@router.get("/publishers")
def publisher_availability():
    return publishers.availability()


@router.get("/publish/preview")
def publish_preview(clip_id: str, channel_id: str, s: Session = Depends(db.get_session)):
    """The title and description a publish would use, for the form to prefill."""
    try:
        clip = review.get_clip(s, clip_id)
        channel = catalog.get_channel(s, channel_id)
    except StudioError as e:
        _raise(e)
    title, description = publishing.compose_copy(clip, channel)
    return {"title": title, "description": description}


@router.post("/publish")
def publish(req: PublishRequestBody, s: Session = Depends(db.get_session)):
    try:
        job = publishing.enqueue(s, _sent(req, exclude=("force",)), force=req.force)
    except StudioError as e:
        _raise(e)
    out = _job_out(job)
    s.commit()
    if job.scheduled_at <= now_iso():
        scheduler.kick_publisher()
    return {"job": out}


@router.get("/publish/jobs")
def publish_jobs(status: str = "", s: Session = Depends(db.get_session)):
    statuses = [x for x in status.split(",") if x] or None
    return {"jobs": [_job_out(j) for j in publishing.list_jobs(s, statuses)]}


@router.patch("/publish/jobs/{job_id}")
def update_publish_job(job_id: str, req: UpdatePublishJobRequest, s: Session = Depends(db.get_session)):
    try:
        job = publishing.get_job(s, job_id)
        publishing.update_job(job, _sent(req))
    except StudioError as e:
        _raise(e)
    return {"job": _job_out(job)}


@router.post("/publish/jobs/{job_id}/cancel")
def cancel_publish_job(job_id: str, s: Session = Depends(db.get_session)):
    try:
        job = publishing.get_job(s, job_id)
        publishing.cancel(job)
    except StudioError as e:
        _raise(e)
    return {"job": _job_out(job)}


@router.post("/publish/jobs/{job_id}/retry")
def retry_publish_job(job_id: str, s: Session = Depends(db.get_session)):
    try:
        job = publishing.get_job(s, job_id)
        publishing.retry(job)
    except StudioError as e:
        _raise(e)
    out = _job_out(job)
    s.commit()
    scheduler.kick_publisher()
    return {"job": out}


# ---- YouTube connect (OAuth) -------------------------------------------------------

def _origin(value: str) -> Optional[str]:
    """scheme://host[:port] of a dashboard origin, or None."""
    parsed = urlparse(value or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/"):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


@router.get("/youtube/connect")
def youtube_connect(channel_id: str, request: Request, origin: str = "",
                    s: Session = Depends(db.get_session)):
    """The Google consent URL. ``origin`` is the dashboard's own origin, so the
    redirect works behind the dev proxy; it must match a redirect URI you
    registered in Google Cloud (STUDIO_YOUTUBE_REDIRECT_URI overrides it)."""
    try:
        channel = catalog.get_channel(s, channel_id)
    except StudioError as e:
        _raise(e)
    if channel.publisher != "youtube_api" or channel.platform != "youtube":
        raise HTTPException(status_code=400, detail="Set this channel's publisher to 'youtube direct' first")
    base = _origin(origin) or str(request.base_url)
    try:
        url = youtube_oauth.start(channel.id, youtube_oauth.redirect_uri(base))
    except youtube_oauth.OAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"url": url, "redirect_uri": youtube_oauth.redirect_uri(base)}


def _oauth_page(title: str, message: str, ok: bool) -> HTMLResponse:
    color = "#3fb68b" if ok else "#e5534b"
    body = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<body style="font-family:system-ui,sans-serif;background:#0b0d10;color:#e8e6e1;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px">
<main style="max-width:420px;text-align:center"><h1 style="font-size:20px;color:{color}">{html.escape(title)}</h1>
<p style="line-height:1.5">{html.escape(message)}</p><p style="opacity:.7">You can close this tab and return to OpenShorts.</p></main>
<script>try{{window.opener&&window.opener.postMessage({{studio:'youtube-connected',ok:{str(ok).lower()}}},'*')}}catch(e){{}}</script>"""
    return HTMLResponse(body, status_code=200 if ok else 400)


@router.get("/youtube/callback")
def youtube_callback(state: str = "", code: str = "", error: str = ""):
    if error:
        return _oauth_page("YouTube not connected", f"Google answered: {error[:100]}", False)
    try:
        channel_id = youtube_oauth.finish(state, code)
    except youtube_oauth.OAuthError as e:
        return _oauth_page("YouTube not connected", str(e), False)
    title = None
    try:
        info = youtube_oauth.my_channel(channel_id)
        title = info.get("title")
        with db.session() as s:
            ch = catalog.get_channel(s, channel_id)
            cfg = ch.publisher_cfg
            cfg.update({"youtube_channel_id": info.get("id"), "youtube_channel_title": title})
            import json as _json
            ch.publisher_config = _json.dumps(cfg, ensure_ascii=False)
    except (youtube_oauth.OAuthError, StudioError) as e:
        print(f"⚠️ Studio YouTube channel lookup: {e}")
    return _oauth_page("YouTube connected",
                       f"Uploads will go to {title}." if title else "Uploads are ready.", True)


@router.post("/channels/{channel_id}/youtube/disconnect")
def youtube_disconnect(channel_id: str, s: Session = Depends(db.get_session)):
    try:
        ch = catalog.get_channel(s, channel_id)
    except StudioError as e:
        _raise(e)
    youtube_oauth.disconnect(ch.id)
    return {"channel": catalog.channel_out(ch)}


# ---- phase 5: overview, tags, bulk review, copy rewrite, backup ------------------

class BulkReviewRequest(BaseModel):
    clip_ids: list
    review_status: str
    force: bool = False


class RewriteRequest(BaseModel):
    language: Optional[str] = None
    channel_id: Optional[str] = None


@router.get("/summary")
def overview(s: Session = Depends(db.get_session)):
    return summary.overview(s)


@router.get("/tags")
def tags(s: Session = Depends(db.get_session)):
    return {"tags": projects.all_tags(s)}


@router.post("/clips/bulk")
def bulk_review(req: BulkReviewRequest, s: Session = Depends(db.get_session)):
    try:
        return review.bulk_status(s, req.clip_ids, req.review_status, force=req.force)
    except StudioError as e:
        _raise(e)


@router.post("/clips/{clip_id}/rewrite")
def rewrite_clip(clip_id: str, req: RewriteRequest, request: Request,
                 s: Session = Depends(db.get_session)):
    """Suggested copy in the channel's language (default Indonesian). Not saved."""
    try:
        clip = review.get_clip(s, clip_id)
        channel = catalog.get_channel(s, req.channel_id) if req.channel_id else None
    except StudioError as e:
        _raise(e)
    language = (req.language or (channel.language if channel else None) or "id").strip()
    current = {"title": clip.title, "hook_text": clip.hook_text, "caption": clip.caption,
               "hashtags": clip.hashtags}
    s.commit()  # release the DB before a slow model call
    try:
        suggestion = copywriter.rewrite(current, language, channel.platform if channel else None,
                                        gemini_key=request.headers.get("X-Gemini-Key"))
    except copywriter.CopyError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"language": language, "suggestion": suggestion}


@router.get("/backup/status")
def backup_status():
    return scheduler.backup_status()


@router.post("/backup/run", status_code=202)
def backup_run():
    from . import backup
    if not backup.configured():
        raise HTTPException(status_code=400, detail="Set STUDIO_BACKUP_BUCKET and its access keys first")
    started = scheduler.trigger_backup()
    return {"started": started, **scheduler.backup_status()}


# ---- campaign briefs, recommendations, revenue --------------------------------
# A brief or a campaign list is either pasted, or read from a page through a
# browser account logged in on this machine (browser_post.read_page). Reading
# acts as that login, so it answers only locally and only for allowed hosts.

class PageRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    account_id: Optional[str] = None  # a "konten" browser account; default the first


def read_hosts() -> tuple:
    raw = os.environ.get("CAMPAIGN_READ_HOSTS", "konten.com")
    return tuple(h.strip().lower().lstrip(".") for h in raw.split(",") if h.strip())


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in read_hosts())


def _page_text(req: PageRequest, request: Request) -> tuple:
    """(text, url, title) from pasted text, or the page read in the browser."""
    text = (req.text or "").strip()
    url = (req.url or "").strip()
    if text:
        return text, url, ""
    if not url:
        raise HTTPException(status_code=400, detail="Paste the text or give the page's URL")
    import browser_accounts
    import browser_post
    if not (browser_post.enabled() and browser_post.is_local_request(request)):
        raise HTTPException(status_code=403, detail=(
            "Reading a page needs browser posting on this machine (BROWSER_POST_ENABLED=1); "
            "paste the text instead"))
    if urlparse(url).scheme != "https" or not _host_allowed(url):
        raise HTTPException(status_code=400, detail=(
            f"Only pages on {', '.join(read_hosts())} are read (CAMPAIGN_READ_HOSTS); paste the text instead"))
    try:
        import security_utils
        security_utils.assert_public_url(url)
    except Exception:
        raise HTTPException(status_code=400, detail="That URL does not point to a public site")
    accounts = [a for a in browser_accounts.list_accounts() if a["platform"] == "konten" and a["has_profile"]]
    account = next((a for a in accounts if a["id"] == req.account_id), None) if req.account_id \
        else (accounts[0] if accounts else None)
    if account is None:
        raise HTTPException(status_code=400, detail="Add a konten.com account and log in to it first")
    if not browser_post.playwright_installed():
        raise HTTPException(status_code=400, detail="Playwright is not installed on this machine")
    page = browser_post.read_page(account["id"], url)
    if not page.get("success"):
        raise HTTPException(status_code=502, detail=page.get("message") or "The page could not be read")
    if not (page.get("text") or "").strip():
        raise HTTPException(status_code=502, detail="The page showed no text: log in to the account again")
    return page["text"], page.get("url") or url, page.get("title") or ""


def _gemini_key(request: Request) -> Optional[str]:
    return request.headers.get("X-Gemini-Key")


@router.post("/campaign-ai/brief")
def analyse_brief(req: PageRequest, request: Request):
    """A brief read by the model: campaign fields, rules, and video requirements.
    Nothing is saved; the dashboard fills the campaign form with it."""
    text, url, title = _page_text(req, request)
    try:
        out = campaign_ai.extract_brief(text, url, _gemini_key(request))
    except campaign_ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if url and "brief_url" not in out["campaign"]:
        out["campaign"]["brief_url"] = url
    return {**out, "page_title": title, "source": "page" if not (req.text or "").strip() else "text"}


@router.post("/campaign-ai/list")
def read_campaign_list(req: PageRequest, request: Request):
    """Campaigns listed on a page (or pasted), to pick from and add. Not saved."""
    text, url, _title = _page_text(req, request)
    try:
        return {"campaigns": campaign_ai.extract_list(text, url, _gemini_key(request))}
    except campaign_ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))


class RecommendRequest(BaseModel):
    prompt: str
    campaign_ids: Optional[list] = None   # saved campaigns; default every active one
    candidates: Optional[list] = None     # unsaved ones (from /campaign-ai/list)


def _creator_profile(s: Session) -> dict:
    channels = [{"name": c.name, "platform": c.platform, "language": c.language}
                for c in catalog.list_channels(s)]
    rows = s.scalars(select(Post).where(Post.status == "live")).unique().all()
    snaps = posts.snapshots_for(s, [p.id for p in rows])
    views = [(p.platform, posts.summarize(snaps[p.id])["views"]) for p in rows]
    return campaign_ai.creator_profile(channels, views)


def _saved_for_ranking(c) -> dict:
    b = c.brief or {}
    return {"id": c.id, "name": c.name, "cpm": c.cpm, "currency": c.currency, "min_views": c.min_views,
            "max_payout": c.max_payout, "ends_at": c.ends_at, "platforms": b.get("platforms") or [],
            "niche": b.get("niche") or "", "language": b.get("language") or "",
            "summary": b.get("summary") or c.notes or "", "saved": True}


@router.post("/campaign-ai/recommend")
def recommend(req: RecommendRequest, request: Request, s: Session = Depends(db.get_session)):
    """Campaigns ranked for what the creator wrote: the model's fit and reasons,
    with the payout a typical post of theirs would earn computed from their own views."""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Describe your channels and what you like to clip")
    if req.campaign_ids:
        saved = [c for c in catalog.list_campaigns(s) if c.id in set(map(str, req.campaign_ids))]
    else:
        saved = catalog.list_campaigns(s, "active")
    items = [_saved_for_ranking(c) for c in saved]
    for n, c in enumerate(req.candidates or []):
        if not isinstance(c, dict) or not c.get("name"):
            continue
        cleaned = campaign_ai.clean_brief({**c, "is_brief": True})
        items.append({"id": f"new-{n}", "name": cleaned["name"] or str(c.get("name"))[:160],
                      "cpm": cleaned["cpm"], "currency": cleaned["currency"], "min_views": cleaned["min_views"],
                      "max_payout": cleaned["max_payout"], "ends_at": cleaned["ends_at"],
                      "platforms": cleaned["platforms"], "niche": cleaned["niche"],
                      "summary": cleaned["summary"] or str(c.get("summary") or "")[:400],
                      "url": str(c.get("url") or "")[:500], "saved": False})
    if not items:
        raise HTTPException(status_code=400, detail="No campaign to rank: add or read some first")
    profile = _creator_profile(s)
    s.commit()  # release the DB before a slow model call
    try:
        ranked = campaign_ai.rank(prompt, items, profile, _gemini_key(request))
    except campaign_ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ranked": ranked, "profile": profile}


@router.post("/campaign-ai/check/{clip_id}")
def check_clip(clip_id: str, request: Request, campaign_id: str = "", s: Session = Depends(db.get_session)):
    """The clip's copy and length against its campaign's brief (the project's
    campaign unless one is named)."""
    try:
        clip = review.get_clip(s, clip_id)
        cid = campaign_id or (clip.project.campaign_id if clip.project else None)
        if not cid:
            raise StudioError("Link the project to a campaign first")
        campaign = catalog.get_campaign(s, cid)
    except StudioError as e:
        _raise(e)
    if not campaign.brief:
        raise HTTPException(status_code=400, detail="This campaign has no brief yet: analyse it first")
    _score, why = _selection_notes(clip)
    data = {"title": clip.title, "hook_text": clip.hook_text, "caption": clip.caption,
            "hashtags": clip.hashtags, "duration": clip.duration, "why_picked": why}
    brief = campaign.brief
    s.commit()
    try:
        return {"campaign_id": campaign.id, **campaign_ai.check_clip(brief, data, _gemini_key(request))}
    except campaign_ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/revenue")
def revenue_insights(s: Session = Depends(db.get_session)):
    """Earnings per campaign and what to do next, from recorded posts and views."""
    return revenue.insights(s)
