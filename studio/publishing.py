"""The publish queue: enqueue a clip for a channel, run due jobs, track results.

A job is processed by one worker at a time. Network calls happen outside any
DB transaction; each state change is its own short transaction.
"""
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog, db, publishers, storage, youtube_oauth
from .models import Clip, Post, PublishJob, new_id, now_iso
from .posts import PostConflict
from .projects import StudioError
from .urls import parse_post_url

MAX_ATTEMPTS = 3
RETRY_MINUTES = 10
AWAIT_GIVE_UP = timedelta(hours=24)
ACTIVE = ("queued", "running", "awaiting")

_worker_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def compose_copy(clip: Clip, channel) -> tuple:
    """(title, description) from the clip's reviewed copy and the channel preset."""
    preset = channel.preset
    title = (clip.title or clip.hook_text or "Short").strip()
    parts = [clip.caption or ""]
    tags = clip.hashtags or preset.get("default_hashtags")
    if tags:
        parts.append(tags)
    if preset.get("caption_suffix"):
        parts.append(preset["caption_suffix"])
    description = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return title, description


def enqueue(s: Session, data: dict, *, force: bool = False) -> PublishJob:
    clip_id = data.get("clip_id") or ""
    clip = s.get(Clip, clip_id) if storage.valid_id(clip_id) else None
    if clip is None:
        raise StudioError("Clip not found", 404)
    channel = catalog.get_channel(s, data.get("channel_id") or "")
    if channel.archived_at:
        raise StudioError("That channel is archived")
    if channel.publisher == "manual":
        raise StudioError("This channel publishes manually: record the post's URL instead")
    available = publishers.availability()[channel.publisher]
    if not available["configured"]:
        raise StudioError(f"Publishing is not set up on the server: {available['needs']}")
    cfg = channel.publisher_cfg
    if channel.publisher == "youtube_api":
        if channel.platform != "youtube":
            raise StudioError("Direct YouTube publishing needs a YouTube channel")
        if not youtube_oauth.is_connected(channel.id):
            raise StudioError("Connect this channel to YouTube first (channels tab)")
    if channel.publisher == "uploadpost":
        if channel.platform not in publishers.UPLOAD_POST_PLATFORMS:
            raise StudioError(f"Upload-Post publishing supports {', '.join(publishers.UPLOAD_POST_PLATFORMS)}")
        if not cfg.get("profile"):
            raise StudioError("Set the channel's Upload-Post profile first (channels tab)")

    pending = s.scalars(select(PublishJob).where(
        PublishJob.clip_id == clip.id, PublishJob.channel_id == channel.id,
        PublishJob.status.in_(ACTIVE))).first()
    if pending:
        raise PostConflict("already_queued", "This clip is already queued for that channel.",
                           job_id=pending.id)
    if not force:
        if clip.review_status not in ("approved", "posted"):
            raise PostConflict("not_approved",
                               f"This clip is still '{clip.review_status}'. Publish it anyway?")
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

    default_title, default_desc = compose_copy(clip, channel)
    title = (data.get("title") or "").strip()[:300] or default_title
    description = data.get("description")
    description = (description if description is not None else default_desc).strip()[:5000]

    when = _now()
    if data.get("scheduled_at"):
        try:
            when = datetime.fromisoformat(str(data["scheduled_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise StudioError("scheduled_at must be an ISO 8601 datetime")
        if when.tzinfo is None:
            raise StudioError("scheduled_at needs a time zone offset")
        if when > _now() + timedelta(days=180):
            raise StudioError("scheduled_at is more than 180 days away")

    ts = now_iso()
    job = PublishJob(
        id=new_id(), clip_id=clip.id, channel_id=channel.id, campaign_id=campaign_id,
        publisher=channel.publisher, title=title, description=description,
        scheduled_at=_iso(max(when, _now() - timedelta(minutes=1))), status="queued",
        attempts=0, created_at=ts, updated_at=ts)
    s.add(job)
    s.flush()
    return job


def get_job(s: Session, job_id: str) -> PublishJob:
    job = s.get(PublishJob, job_id) if storage.valid_id(job_id) else None
    if job is None:
        raise StudioError("Publish job not found", 404)
    return job


def list_jobs(s: Session, statuses=None, limit: int = 200):
    stmt = select(PublishJob)
    if statuses:
        stmt = stmt.where(PublishJob.status.in_(list(statuses)))
    return s.scalars(stmt.order_by(PublishJob.scheduled_at.desc()).limit(limit)).unique().all()


def cancel(job: PublishJob):
    if job.status not in ("queued", "failed"):
        raise StudioError(f"A {job.status} job cannot be canceled", 409)
    job.status = "canceled"
    job.updated_at = now_iso()


def retry(job: PublishJob):
    if job.status not in ("failed", "canceled"):
        raise StudioError(f"Only a failed or canceled job can be retried (this one is {job.status})", 409)
    job.status = "queued"
    job.attempts = 0
    job.next_attempt_at = None
    job.last_error = None
    job.scheduled_at = min(job.scheduled_at, now_iso())
    job.updated_at = now_iso()


def update_job(job: PublishJob, data: dict):
    if job.status != "queued":
        raise StudioError("Only a queued job can be edited", 409)
    if "title" in data and data["title"] is not None:
        job.title = data["title"].strip()[:300] or job.title
    if "description" in data and data["description"] is not None:
        job.description = data["description"].strip()[:5000]
    if data.get("scheduled_at"):
        try:
            when = datetime.fromisoformat(str(data["scheduled_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise StudioError("scheduled_at must be an ISO 8601 datetime")
        if when.tzinfo is None:
            raise StudioError("scheduled_at needs a time zone offset")
        job.scheduled_at = _iso(when)
    job.updated_at = now_iso()


# ---- the worker ------------------------------------------------------------------

def _record_post(s: Session, job: PublishJob, url: Optional[str]) -> Post:
    """The post for a finished job: live with its URL, or pending without one."""
    channel = job.channel
    clip = job.clip
    ts = now_iso()
    if url:
        parsed = parse_post_url(url)
        existing = s.scalars(select(Post).where(Post.platform == parsed.platform,
                                                Post.external_id == parsed.external_id)).first()
        if existing:
            return existing
        platform, external_id, url, status = parsed.platform, parsed.external_id, parsed.url, "live"
    else:
        platform, external_id, url, status = channel.platform, f"pending-{job.id}", "", "pending"
    post = Post(
        id=new_id(), clip_id=clip.id if clip else None, channel_id=channel.id,
        campaign_id=job.campaign_id, platform=platform, external_id=external_id, url=url,
        posted_at=ts, method=job.publisher, publish_request_id=job.request_id, status=status,
        claim_status="none", file_sha256=clip.file_sha256 if clip else None,
        created_at=ts, updated_at=ts)
    s.add(post)
    if clip is not None:
        clip.review_status = "posted"
        clip.reviewed_at = clip.reviewed_at or ts
        clip.updated_at = ts
    s.flush()
    return post


def _fail_or_retry(job: PublishJob, err: publishers.PublishError):
    job.last_error = str(err)[:500]
    if err.retryable and job.attempts < MAX_ATTEMPTS:
        job.status = "queued"
        job.next_attempt_at = _iso(_now() + timedelta(minutes=RETRY_MINUTES * job.attempts))
    else:
        job.status = "failed"
    job.updated_at = now_iso()


def _run_one(job_id: str):
    with db.session() as s:
        job = s.get(PublishJob, job_id)
        if job is None or job.status != "queued":
            return
        clip, channel = job.clip, job.channel
        if clip is None:
            job.status, job.last_error = "failed", "The clip was deleted"
            return
        path = storage.safe_join(storage.files_dir(clip.project_id), clip.file_name)
        if not path or not os.path.isfile(path):
            job.status, job.last_error = "failed", "The clip's file is missing"
            return
        req = publishers.PublishRequest(
            file_path=path, platform=channel.platform, title=job.title or "",
            description=job.description or "", channel_id=channel.id, config=channel.publisher_cfg)
        adapter = publishers.ADAPTERS[job.publisher]
        job.status = "running"
        job.attempts += 1
        job.updated_at = now_iso()

    try:
        result = adapter(req)
    except publishers.PublishError as e:
        error = e
        result = None
    except Exception as e:  # noqa: BLE001 — never kill the worker
        error = publishers.PublishError(f"{type(e).__name__}: {str(e)[:200]}", retryable=False)
        result = None

    with db.session() as s:
        job = s.get(PublishJob, job_id)
        if result is None:
            _fail_or_retry(job, error)
            return
        job.last_error = None
        job.updated_at = now_iso()
        if result.url:
            job.post_id = _record_post(s, job, result.url).id
            job.status = "done"
        else:
            job.request_id = result.request_id
            job.status = "awaiting"


def _poll_one(job_id: str):
    with db.session() as s:
        job = s.get(PublishJob, job_id)
        if job is None or job.status != "awaiting" or not job.request_id:
            return
        request_id, platform = job.request_id, job.channel.platform
        started = datetime.fromisoformat(job.updated_at)
    state = publishers.check_uploadpost(request_id, platform)
    with db.session() as s:
        job = s.get(PublishJob, job_id)
        if state["state"] == "done":
            job.post_id = _record_post(s, job, state["url"]).id
            job.status = "done"
        elif state["state"] == "failed":
            job.status, job.last_error = "failed", state["error"]
        elif _now() - started > AWAIT_GIVE_UP:
            job.status = "failed"
            job.last_error = "No result from Upload-Post after 24 hours; check the channel"
        else:
            return
        job.updated_at = now_iso()


def recover_interrupted():
    """A job left 'running' by a crash may or may not have uploaded. Never
    retry it blindly (a double post): fail it and say why."""
    with db.session() as s:
        for job in s.scalars(select(PublishJob).where(PublishJob.status == "running")).unique():
            job.status = "failed"
            job.last_error = ("Interrupted while uploading (server restart). Check the channel "
                              "before retrying, it may already be published.")
            job.updated_at = now_iso()


def tick(limit: int = 3) -> dict:
    """Run due jobs and poll async ones. Safe to call often; one at a time."""
    if not _worker_lock.acquire(blocking=False):
        return {"skipped": True}
    try:
        now = now_iso()
        with db.session() as s:
            due = [j.id for j in s.scalars(
                select(PublishJob).where(PublishJob.status == "queued",
                                         PublishJob.scheduled_at <= now)
                .order_by(PublishJob.scheduled_at)).unique()
                if not j.next_attempt_at or j.next_attempt_at <= now][:limit]
            awaiting = [j.id for j in s.scalars(
                select(PublishJob).where(PublishJob.status == "awaiting")).unique()]
        for job_id in due:
            _run_one(job_id)
        for job_id in awaiting:
            _poll_one(job_id)
        return {"ran": len(due), "polled": len(awaiting)}
    finally:
        _worker_lock.release()
