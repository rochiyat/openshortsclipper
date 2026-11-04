"""/api/studio: saved projects (self-host only)."""
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import db, projects, storage
from .models import Clip, Project
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
        "file_sha256": c.file_sha256,
        "review_status": c.review_status,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


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
        "clip_count": len(clips),
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
        projects.update_project(p, title=req.title, rights=req.rights,
                                rights_note=req.rights_note, status=req.status)
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
