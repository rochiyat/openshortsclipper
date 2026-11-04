"""Save a finished job as a project, list projects, reopen one into output/.

The app hands Studio a ``JobBridge`` instead of Studio importing app.py: no
circular import, and tests drive it with a fake job directory.
"""
import json
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import storage
from .models import PROJECT_STATUSES, RIGHTS, Clip, Project, new_id, now_iso


@dataclass
class JobBridge:
    """What Studio needs from the job system.

    ``describe(job_id)`` returns None for an unknown job, else a dict with
    ``status``, ``output_dir``, ``result`` (the job result as /api/status
    returns it, clips carrying ``video_url``), ``source_url`` and
    ``source_video`` (file name of a kept source download, if any).
    ``recover()`` rehydrates finished jobs from ``output_dir`` after files were
    copied back.
    """
    output_dir: str
    describe: Callable[[str], Optional[dict]]
    recover: Callable[[], None]


class StudioError(Exception):
    status_code = 400

    def __init__(self, detail, status_code=None):
        super().__init__(detail)
        self.detail = detail
        if status_code:
            self.status_code = status_code


_locks: dict = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _source_type(url: Optional[str]) -> str:
    if not url:
        return "upload"
    host = (urlparse(url).hostname or "").lower()
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    return "url"


def _clip_file_name(clip: dict) -> Optional[str]:
    url = (clip or {}).get("video_url") or ""
    name = os.path.basename(url.split("?", 1)[0].split("#", 1)[0])
    return name or None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _default_title(clips, source_url):
    for c in clips:
        t = c.get("video_title_for_youtube_short") or c.get("title")
        if t:
            return str(t)[:200]
    if source_url:
        return source_url[:200]
    return "Project " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def save_job(s: Session, bridge: JobBridge, job_id: str, *, title: Optional[str] = None,
             include_source: bool = False) -> Project:
    """Snapshot a completed job into Studio. Saving the same job again re-syncs
    its files (e.g. after more edits) and keeps the project's id, title and the
    clips' review state."""
    if not storage.valid_id(job_id):
        raise StudioError("Invalid job id")
    with _lock_for(f"job:{job_id}"):
        info = bridge.describe(job_id)
        if not info:
            raise StudioError("Job not found", 404)
        if info.get("status") != "completed":
            raise StudioError("Only a completed job can be saved", 409)
        src_dir = info.get("output_dir") or ""
        if not os.path.isdir(src_dir):
            raise StudioError("The job's files are no longer on disk", 410)
        clips = ((info.get("result") or {}).get("clips")) or []
        if not clips:
            raise StudioError("This job has no clips to save", 409)

        project = s.scalars(
            select(Project).options(selectinload(Project.clips))
            .where(Project.job_id == job_id)).first()
        is_new = project is None
        project_id = project.id if project else new_id()

        skip = set()
        source_video = info.get("source_video")
        if source_video and not include_source:
            skip.add(source_video)

        os.makedirs(storage.project_root(project_id), exist_ok=True)
        staged = storage.staging_path(project_id)
        try:
            size = storage.mirror(src_dir, staged, skip=skip)
            rows = []
            for i, clip in enumerate(clips):
                if not isinstance(clip, dict):
                    continue
                name = _clip_file_name(clip)
                path = storage.safe_join(staged, name) if name else None
                if not path or not os.path.isfile(path):
                    continue
                rows.append((i, name, path, clip))
            if not rows:
                raise StudioError("None of the job's clip files are on disk", 410)
            hashes = {i: storage.sha256_file(path) for i, _n, path, _c in rows}
            storage.replace_dir(staged, storage.files_dir(project_id))
        except Exception:
            shutil.rmtree(staged, ignore_errors=True)
            if is_new:
                storage.remove_project(project_id)
            raise

        ts = now_iso()
        source_url = info.get("source_url")
        if is_new:
            project = Project(
                id=project_id, job_id=job_id,
                title=(title or "").strip()[:200] or _default_title(clips, source_url),
                source_url=source_url, source_type=_source_type(source_url),
                created_at=ts)
            s.add(project)
        elif title and title.strip():
            project.title = title.strip()[:200]
        project.has_source = int(bool(source_video and include_source))
        project.size_bytes = size
        project.updated_at = ts

        existing = {c.clip_index: c for c in project.clips}
        keep = set()
        for i, name, _path, clip in rows:
            keep.add(i)
            start, end = _num(clip.get("start")), _num(clip.get("end"))
            row = existing.get(i)
            if row is None:
                row = Clip(
                    id=new_id(), clip_index=i, created_at=ts,
                    title=clip.get("video_title_for_youtube_short") or clip.get("title"),
                    hook_text=clip.get("viral_hook_text") or clip.get("hook_text"),
                    caption=(clip.get("video_description_for_tiktok")
                             or clip.get("video_description_for_instagram")),
                )
                project.clips.append(row)
            row.file_name = name
            row.start_s, row.end_s = start, end
            row.duration = (end - start) if (start is not None and end is not None) else None
            row.file_sha256 = hashes[i]
            row.metadata_json = json.dumps(clip, ensure_ascii=False, default=str)
            row.updated_at = ts
        for idx, row in existing.items():
            if idx not in keep:
                project.clips.remove(row)
        s.flush()
        return project


def list_projects(s: Session, *, q: str = "", status: str = "active",
                  limit: int = 50, offset: int = 0):
    stmt = select(Project).options(selectinload(Project.clips))
    count = select(func.count(Project.id))
    if status in ("active", "archived"):
        stmt = stmt.where(Project.status == status)
        count = count.where(Project.status == status)
    if q:
        like = f"%{q.strip()}%"
        cond = or_(Project.title.ilike(like), Project.source_url.ilike(like))
        stmt = stmt.where(cond)
        count = count.where(cond)
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    rows = s.scalars(stmt.order_by(Project.created_at.desc()).limit(limit).offset(offset)).all()
    return rows, s.scalar(count) or 0


def get_project(s: Session, project_id: str) -> Project:
    if not storage.valid_id(project_id):
        raise StudioError("Project not found", 404)
    p = s.scalars(select(Project).options(selectinload(Project.clips))
                  .where(Project.id == project_id)).first()
    if p is None:
        raise StudioError("Project not found", 404)
    return p


def find_by_job(s: Session, job_id: str) -> Optional[Project]:
    if not storage.valid_id(job_id):
        return None
    return s.scalars(select(Project).where(Project.job_id == job_id)).first()


def update_project(project: Project, *, title=None, rights=None, rights_note=None,
                   status=None) -> Project:
    if title is not None:
        title = title.strip()[:200]
        if not title:
            raise StudioError("Title cannot be empty")
        project.title = title
    if rights is not None:
        if rights not in RIGHTS:
            raise StudioError(f"rights must be one of: {', '.join(RIGHTS)}")
        project.rights = rights
    if rights_note is not None:
        project.rights_note = rights_note.strip()[:1000] or None
    if status is not None:
        if status not in PROJECT_STATUSES:
            raise StudioError(f"status must be one of: {', '.join(PROJECT_STATUSES)}")
        project.status = status
    project.updated_at = now_iso()
    return project


def delete_project(s: Session, project: Project):
    pid = project.id
    s.delete(project)
    s.flush()
    storage.remove_project(pid)


def _job_files_present(info: Optional[dict]) -> bool:
    if not info or info.get("status") != "completed":
        return False
    out = info.get("output_dir") or ""
    clips = ((info.get("result") or {}).get("clips")) or []
    if not os.path.isdir(out) or not clips:
        return False
    for clip in clips:
        name = _clip_file_name(clip) if isinstance(clip, dict) else None
        path = storage.safe_join(out, name) if name else None
        if not path or not os.path.isfile(path):
            return False
    return True


def reopen(project: Project, bridge: JobBridge) -> dict:
    """Make the project's job live again in output/ so the Clip Generator and
    its editors work on it. Copies back only what is missing; never touches a
    job that is still queued or processing."""
    job_id = project.job_id
    with _lock_for(f"job:{job_id}"):
        info = bridge.describe(job_id)
        if info and info.get("status") in ("queued", "processing"):
            raise StudioError("This job is still running", 409)
        if not _job_files_present(info):
            src = storage.files_dir(project.id)
            if not os.path.isdir(src):
                raise StudioError("The project's files are missing", 410)
            dst = os.path.join(bridge.output_dir, job_id)
            os.makedirs(dst, exist_ok=True)
            for root, dirs, files in os.walk(src):
                rel = os.path.relpath(root, src)
                out_root = dst if rel == "." else os.path.join(dst, rel)
                os.makedirs(out_root, exist_ok=True)
                for name in files:
                    target = os.path.join(out_root, name)
                    if not os.path.exists(target):
                        storage.place(os.path.join(root, name), target)
            bridge.recover()
            info = bridge.describe(job_id)
        dst = (info or {}).get("output_dir") or os.path.join(bridge.output_dir, job_id)
        try:
            os.utime(dst)  # restart the retention clock for the reopened job
        except OSError:
            pass
        if not _job_files_present(info):
            raise StudioError("Could not restore the project", 500)
        return {"job_id": job_id, "result": info.get("result"), "project_state": None,
                "has_source": bool(project.has_source)}
