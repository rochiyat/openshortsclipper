"""Files of saved projects: snapshot a job directory, resolve paths safely.

Layout: ``{STUDIO_DIR}/projects/{project_id}/files/<same names as output/{job_id}>``.
Same file names as the job directory, so a reopen copies them back verbatim
and the job's own metadata keeps resolving.
"""
import hashlib
import os
import re
import shutil
import uuid

from . import config

# Media is written once by ffmpeg under a new name, so a hardlink is safe.
# Everything else (metadata JSON, subtitle files) can be rewritten in place by
# the pipeline, which would silently change the saved copy through a link.
_LINKABLE = {".mp4", ".mov", ".webm", ".mkv", ".m4a", ".mp3", ".wav", ".aac",
             ".jpg", ".jpeg", ".png", ".webp"}

_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,79}$")


def valid_id(value: str) -> bool:
    return bool(value) and bool(_ID_RE.match(value))


def project_root(project_id: str) -> str:
    if not valid_id(project_id):
        raise ValueError("invalid project id")
    return os.path.join(config.projects_dir(), project_id)


def files_dir(project_id: str) -> str:
    return os.path.join(project_root(project_id), "files")


def safe_join(base: str, name: str):
    """``base/name`` if it stays inside ``base`` (no traversal), else None."""
    if not name or "\x00" in name:
        return None
    base_real = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base_real, name))
    if os.path.commonpath([base_real, target]) != base_real or target == base_real:
        return None
    return target


def place(src: str, dst: str, mode=None):
    mode = mode or config.link_mode()
    ext = os.path.splitext(src)[1].lower()
    if mode == "link" and ext in _LINKABLE:
        try:
            os.link(src, dst)
            return
        except OSError:
            pass  # other filesystem, or links unsupported: copy instead
    shutil.copy2(src, dst)


def mirror(src_dir: str, dst_dir: str, skip=None, mode=None) -> int:
    """Copy ``src_dir`` into a fresh ``dst_dir`` (which must not exist).

    Skips dot entries (resume manifest, owner/marker files). ``skip`` is a set
    of top-level names to leave out. Returns the bytes placed.
    """
    mode = mode or config.link_mode()
    skip = skip or set()
    total = 0
    os.makedirs(dst_dir)
    for root, dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and not (rel == "." and d in skip)]
        out_root = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        os.makedirs(out_root, exist_ok=True)
        for name in files:
            if name.startswith(".") or (rel == "." and name in skip):
                continue
            src = os.path.join(root, name)
            if os.path.islink(src) or not os.path.isfile(src):
                continue
            place(src, os.path.join(out_root, name), mode)
            total += os.path.getsize(src)
    return total


def replace_dir(staged: str, final: str):
    """Swap ``staged`` into place at ``final``, removing the previous one."""
    old = None
    if os.path.exists(final):
        old = f"{final}.old-{uuid.uuid4().hex[:8]}"
        os.rename(final, old)
    os.rename(staged, final)
    if old:
        shutil.rmtree(old, ignore_errors=True)


def staging_path(project_id: str) -> str:
    return os.path.join(project_root(project_id), f".staging-{uuid.uuid4().hex[:8]}")


def remove_project(project_id: str):
    shutil.rmtree(project_root(project_id), ignore_errors=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total
