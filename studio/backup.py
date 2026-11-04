"""Back up Studio to S3-compatible storage (Cloudflare R2, AWS S3, MinIO...).

What goes up:
* ``db/studio-<UTC timestamp>.db.gz``: a consistent copy of studio.db taken
  with SQLite's online backup API (safe while the app writes), gzipped. The
  newest STUDIO_BACKUP_KEEP copies are kept, older ones deleted.
* ``projects/<project id>/files/...``: every saved clip file, uploaded only
  when the remote copy is missing or has a different size. Remote files of a
  deleted project are left alone (a backup must survive a mistaken delete).

What never goes up: ``secrets/`` (YouTube OAuth tokens). After a restore,
connect the YouTube channels again.

Restore (into an empty STUDIO_DIR):  python -m studio.backup restore
"""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from typing import Optional

from . import config


def settings() -> dict:
    env = os.environ.get
    return {
        "bucket": (env("STUDIO_BACKUP_BUCKET") or "").strip(),
        "endpoint": (env("STUDIO_BACKUP_ENDPOINT") or "").strip() or None,
        "region": (env("STUDIO_BACKUP_REGION") or "auto").strip(),
        "access_key": (env("STUDIO_BACKUP_ACCESS_KEY_ID") or env("AWS_ACCESS_KEY_ID") or "").strip(),
        "secret_key": (env("STUDIO_BACKUP_SECRET_ACCESS_KEY") or env("AWS_SECRET_ACCESS_KEY") or "").strip(),
        "prefix": ((env("STUDIO_BACKUP_PREFIX") or "studio-backup").strip().strip("/") + "/"),
        "keep": max(1, int(env("STUDIO_BACKUP_KEEP") or 8)),
        "interval_days": max(0, int(env("STUDIO_BACKUP_INTERVAL_DAYS") or 7)),
    }


def configured() -> bool:
    cfg = settings()
    return bool(cfg["bucket"] and cfg["access_key"] and cfg["secret_key"])


def client():
    import boto3
    from botocore.config import Config
    cfg = settings()
    return boto3.client(
        "s3", endpoint_url=cfg["endpoint"], region_name=cfg["region"],
        aws_access_key_id=cfg["access_key"], aws_secret_access_key=cfg["secret_key"],
        config=Config(retries={"max_attempts": 5, "mode": "standard"}))


def _list(s3, bucket: str, prefix: str) -> dict:
    """key -> size for every object under ``prefix``."""
    out, token = {}, None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents") or []:
            out[obj["Key"]] = obj["Size"]
        if not page.get("IsTruncated"):
            return out
        token = page.get("NextContinuationToken")


def snapshot_db(dest_path: str):
    """Consistent copy of studio.db (WAL included), even while the app writes."""
    src = sqlite3.connect(config.db_path())
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def run(s3=None, now: Optional[datetime] = None) -> dict:
    """One backup pass. Returns a summary; raises on configuration or S3 errors."""
    if not configured():
        raise RuntimeError("Set STUDIO_BACKUP_BUCKET and the access keys first")
    cfg = settings()
    s3 = s3 or client()
    bucket, prefix = cfg["bucket"], cfg["prefix"]
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    summary = {"db_key": None, "uploaded_files": 0, "uploaded_bytes": 0,
               "skipped_files": 0, "deleted_db_backups": 0, "finished_at": None}

    with tempfile.TemporaryDirectory(prefix="studio-backup-") as tmp:
        raw = os.path.join(tmp, "studio.db")
        snapshot_db(raw)
        packed = raw + ".gz"
        with open(raw, "rb") as fin, gzip.open(packed, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        db_key = f"{prefix}db/studio-{stamp}.db.gz"
        s3.upload_file(packed, bucket, db_key)
        summary["db_key"] = db_key

    remote = _list(s3, bucket, f"{prefix}projects/")
    root = config.projects_dir()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]  # staging dirs
        for name in filenames:
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            key = f"{prefix}projects/{rel}"
            size = os.path.getsize(path)
            if remote.get(key) == size:
                summary["skipped_files"] += 1
                continue
            s3.upload_file(path, bucket, key)
            summary["uploaded_files"] += 1
            summary["uploaded_bytes"] += size

    db_backups = sorted(k for k in _list(s3, bucket, f"{prefix}db/") if k.endswith(".db.gz"))
    for old in db_backups[:-cfg["keep"]]:
        s3.delete_object(Bucket=bucket, Key=old)
        summary["deleted_db_backups"] += 1
    summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return summary


def restore(s3=None, db_key: Optional[str] = None) -> dict:
    """Download the newest (or the given) database backup and every project
    file into STUDIO_DIR. Refuses to overwrite an existing studio.db."""
    if os.path.exists(config.db_path()):
        raise RuntimeError(f"{config.db_path()} already exists; move it away first")
    cfg = settings()
    s3 = s3 or client()
    bucket, prefix = cfg["bucket"], cfg["prefix"]
    if not db_key:
        keys = sorted(k for k in _list(s3, bucket, f"{prefix}db/") if k.endswith(".db.gz"))
        if not keys:
            raise RuntimeError("No database backup found")
        db_key = keys[-1]
    os.makedirs(config.studio_dir(), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studio-restore-") as tmp:
        packed = os.path.join(tmp, "db.gz")
        s3.download_file(bucket, db_key, packed)
        with gzip.open(packed, "rb") as fin, open(config.db_path(), "wb") as fout:
            shutil.copyfileobj(fin, fout)
    files = 0
    root = os.path.realpath(config.projects_dir())
    for key in _list(s3, bucket, f"{prefix}projects/"):
        rel = key[len(f"{prefix}projects/"):]
        target = os.path.realpath(os.path.join(root, rel))
        if not rel or os.path.commonpath([root, target]) != root:
            continue  # never write outside the projects dir, whatever the bucket holds
        os.makedirs(os.path.dirname(target), exist_ok=True)
        s3.download_file(bucket, key, target)
        files += 1
    return {"db_key": db_key, "files": files}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("run", "restore"):
        print("usage: python -m studio.backup run | restore [db-key]")
        return 2
    try:
        if argv[0] == "run":
            from . import db
            db.init()
            print(run())
        else:
            print(restore(db_key=argv[1] if len(argv) > 1 else None))
    except Exception as e:  # noqa: BLE001 — CLI: one line, non-zero exit
        print(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
