"""Studio phase 5: backup/restore, overview, tags, bulk review, copy rewrite."""
import gzip
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from studio import backup, config, copywriter, db, scheduler, summary

from test_studio_projects import env  # noqa: F401  (fixture)


# ---- backup -------------------------------------------------------------------------

class FakeS3:
    def __init__(self, page_size=2):
        self.objects = {}           # key -> bytes
        self.page_size = page_size
        self.uploads = []

    def upload_file(self, path, bucket, key):
        with open(path, "rb") as f:
            self.objects[key] = f.read()
        self.uploads.append(key)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        chunk = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        out = {"Contents": [{"Key": k, "Size": len(self.objects[k])} for k in chunk], "IsTruncated": more}
        if more:
            out["NextContinuationToken"] = str(start + self.page_size)
        return out

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def download_file(self, bucket, key, path):
        with open(path, "wb") as f:
            f.write(self.objects[key])


@pytest.fixture
def bucket(monkeypatch):
    monkeypatch.setenv("STUDIO_BACKUP_BUCKET", "b")
    monkeypatch.setenv("STUDIO_BACKUP_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("STUDIO_BACKUP_SECRET_ACCESS_KEY", "s")
    monkeypatch.setenv("STUDIO_BACKUP_KEEP", "2")
    return FakeS3()


def test_backup_uploads_db_and_files_incrementally(env, bucket):
    client, fake, studio_dir = env
    client.post("/api/studio/projects", json={"job_id": fake.make_job()})
    (studio_dir / "secrets").mkdir(exist_ok=True)
    (studio_dir / "secrets" / "youtube-x.json").write_text('{"refresh_token": "t"}')

    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    first = backup.run(bucket, now=t0)
    assert first["db_key"] == "studio-backup/db/studio-20261101T000000Z.db.gz"
    assert first["uploaded_files"] >= 3   # 2 clips + metadata
    assert not any("secrets" in k for k in bucket.objects)

    raw = gzip.decompress(bucket.objects[first["db_key"]])
    path = studio_dir / "check.db"
    path.write_bytes(raw)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    conn.close()

    second = backup.run(bucket, now=t0 + timedelta(days=7))
    assert second["uploaded_files"] == 0 and second["skipped_files"] == first["uploaded_files"]
    backup.run(bucket, now=t0 + timedelta(days=14))
    dbs = sorted(k for k in bucket.objects if k.startswith("studio-backup/db/"))
    assert len(dbs) == 2 and dbs[0].endswith("20261108T000000Z.db.gz")  # keep=2


def test_restore_into_empty_dir(env, bucket, tmp_path, monkeypatch):
    client, fake, _ = env
    pid = client.post("/api/studio/projects", json={"job_id": fake.make_job()}).json()["project"]["id"]
    backup.run(bucket)
    bucket.objects["studio-backup/projects/../../escape.txt"] = b"x"

    target = tmp_path / "restored"
    monkeypatch.setenv("STUDIO_DIR", str(target))
    db.reset()
    out = backup.restore(bucket)
    assert out["files"] >= 3
    assert (target / "projects" / pid / "files" / "video_clip_1.mp4").is_file()
    assert not (tmp_path / "escape.txt").exists()
    assert client.get(f"/api/studio/projects/{pid}").status_code == 200
    with pytest.raises(RuntimeError):
        backup.restore(bucket)   # studio.db exists now


def test_backup_endpoints_and_recorded_outcome(env, monkeypatch, bucket):
    client, _fake, _ = env
    monkeypatch.delenv("STUDIO_BACKUP_BUCKET")
    assert client.post("/api/studio/backup/run").status_code == 400
    assert client.get("/api/studio/backup/status").json()["configured"] is False
    monkeypatch.setenv("STUDIO_BACKUP_BUCKET", "b")
    monkeypatch.setattr(backup, "client", lambda: bucket)
    record = scheduler.run_backup()
    assert record["ok"] is True and record["summary"]["db_key"]
    st = client.get("/api/studio/backup/status").json()
    assert st["configured"] is True and st["last"]["ok"] is True

    monkeypatch.setattr(backup, "run", lambda: (_ for _ in ()).throw(RuntimeError("AccessDenied")))
    assert scheduler.run_backup()["ok"] is False
    assert "AccessDenied" in client.get("/api/studio/backup/status").json()["last"]["error"]


# ---- overview -----------------------------------------------------------------------

def _posted(client, fake, vid, posted_at):
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=1)}).json()["project"]
    client.patch(f"/api/studio/projects/{p['id']}", json={"rights": "campaign"})
    client.patch(f"/api/studio/clips/{p['clips'][0]['id']}", json={"review_status": "approved"})
    chans = client.get("/api/studio/channels").json()["channels"]
    ch = chans[0] if chans else client.post("/api/studio/channels", json={
        "name": "YT", "platform": "youtube"}).json()["channel"]
    return client.post("/api/studio/posts", json={
        "clip_id": p["clips"][0]["id"], "channel_id": ch["id"],
        "url": f"https://youtu.be/{vid}", "posted_at": posted_at}).json()["post"]["id"], p


def _snap(client, pid, when, views):
    r = client.post(f"/api/studio/posts/{pid}/snapshots",
                    json={"views": views, "captured_at": when.isoformat()})
    assert r.status_code == 200, r.text


def test_daily_gains_and_overview(env, monkeypatch):
    client, fake, _ = env
    monkeypatch.setenv("STUDIO_TZ", "+07:00")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    d = lambda n: now - timedelta(days=n)  # noqa: E731

    fresh, p1 = _posted(client, fake, "AAAAAAAAAA1", d(3).date().isoformat())
    _snap(client, fresh, d(3), 1000)
    _snap(client, fresh, d(2), 5000)
    _snap(client, fresh, d(1), 9000)
    _snap(client, fresh, d(0), 12000)
    # Recorded long after posting: its first read is not a one-day spike.
    late, _ = _posted(client, fake, "BBBBBBBBBB2", d(60).date().isoformat())
    _snap(client, late, d(1), 500_000)
    _snap(client, late, d(0), 500_300)

    camp = client.post("/api/studio/campaigns", json={"name": "C", "cpm": 2}).json()["campaign"]
    client.patch(f"/api/studio/posts/{fresh}", json={"campaign_id": camp["id"]})

    o = client.get("/api/studio/summary").json()
    assert o["views_today"] == 3000 + 300
    assert o["views_7d"] == 12000 + 300
    assert o["total_views"] == 12000 + 500_300
    assert len(o["daily"]) == 14 and o["daily"][-1]["views"] == 3300
    assert o["top_posts"][0]["post_id"] == fresh and o["top_posts"][0]["gain_7d"] == 12000
    assert o["payouts"][0]["payout"] == 24.0
    assert o["posts"]["live"] == 2


def test_attention_items(env):
    client, fake, _ = env
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=1)}).json()["project"]
    client.patch(f"/api/studio/clips/{p['clips'][0]['id']}", json={"review_status": "approved", "force": True})
    kinds = {a["kind"] for a in client.get("/api/studio/summary").json()["attention"]}
    assert "rights_unknown" in kinds


# ---- tags & bulk ---------------------------------------------------------------------

def test_tags_normalised_filtered_and_counted(env):
    client, fake, _ = env
    a = client.post("/api/studio/projects", json={"job_id": fake.make_job()}).json()["project"]
    b = client.post("/api/studio/projects", json={"job_id": fake.make_job()}).json()["project"]
    r = client.patch(f"/api/studio/projects/{a['id']}", json={"tags": ["#Podcast", "sains", "podcast", " "]})
    assert r.json()["project"]["tags"] == ["podcast", "sains"]
    client.patch(f"/api/studio/projects/{b['id']}", json={"tags": ["podcast_2"]})
    assert client.patch(f"/api/studio/projects/{b['id']}", json={"tags": "podcast"}).status_code == 422
    assert client.patch(f"/api/studio/projects/{b['id']}", json={"tags": [f"t{i}" for i in range(21)]}).status_code == 400

    got = client.get("/api/studio/projects", params={"tag": "podcast"}).json()
    assert [p["id"] for p in got["projects"]] == [a["id"]]  # "podcast" does not match "podcast_2"
    assert client.get("/api/studio/projects", params={"tag": "podcast_2"}).json()["total"] == 1
    assert client.get("/api/studio/tags").json()["tags"][0] == {"tag": "podcast", "count": 1}


def test_bulk_review(env):
    client, fake, _ = env
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=3)}).json()["project"]
    ids = [c["id"] for c in p["clips"]]
    r = client.post("/api/studio/clips/bulk", json={"clip_ids": ids, "review_status": "approved"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "rights_unknown"
    r = client.post("/api/studio/clips/bulk", json={"clip_ids": ids + ["nope"], "review_status": "approved", "force": True})
    assert r.json() == {"changed": 3, "skipped_posted": 0, "not_found": 1}
    r = client.post("/api/studio/clips/bulk", json={"clip_ids": ids, "review_status": "posted"})
    assert r.status_code == 400
    counts = client.get("/api/studio/clips").json()["counts"]
    assert counts["approved"] == 3


# ---- copy rewrite ----------------------------------------------------------------------

def test_prompt_carries_language_and_style():
    prompt = copywriter.build_prompt({"title": "Why we forget"}, "id", "tiktok")
    assert "Indonesian" in prompt and "santai" in prompt and "for tiktok" in prompt
    assert "Why we forget" in prompt


def test_rewrite_endpoint_uses_channel_language(env, monkeypatch):
    client, fake, _ = env
    seen = {}

    def fake_rewrite(current, language, platform, gemini_key=None):
        seen.update(language=language, platform=platform, key=gemini_key, current=current)
        return {"title": "Kenapa Kita Lupa?", "hook_text": "Otakmu menghapus ini 🤯",
                "caption": "Simak sampai habis.", "hashtags": "#belajar #sains"}

    monkeypatch.setattr(copywriter, "rewrite", fake_rewrite)
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=1)}).json()["project"]
    ch = client.post("/api/studio/channels", json={"name": "EN", "platform": "youtube", "language": "en"}).json()["channel"]
    cid = p["clips"][0]["id"]
    r = client.post(f"/api/studio/clips/{cid}/rewrite", json={"channel_id": ch["id"]},
                    headers={"X-Gemini-Key": "gk"})
    assert r.status_code == 200 and seen == {"language": "en", "platform": "youtube", "key": "gk",
                                             "current": seen["current"]}
    assert seen["current"]["title"] == "Judul 1"
    # Default language is Indonesian; nothing is saved.
    r = client.post(f"/api/studio/clips/{cid}/rewrite", json={})
    assert r.json()["language"] == "id"
    assert client.get(f"/api/studio/projects/{p['id']}").json()["project"]["clips"][0]["title"] == "Judul 1"


def test_rewrite_errors_and_cleaning(env, monkeypatch):
    client, fake, _ = env
    import llm_backend
    monkeypatch.setattr(llm_backend, "active", lambda: False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=1)}).json()["project"]
    r = client.post(f"/api/studio/clips/{p['clips'][0]['id']}/rewrite", json={})
    assert r.status_code == 502 and "Gemini" in r.json()["detail"]

    monkeypatch.setattr(llm_backend, "active", lambda: True)
    monkeypatch.setattr(llm_backend, "generate_json", lambda prompt, schema: (
        {"title": "x" * 300, "hook_text": " a  b ", "caption": " c ", "hashtags": "#a   #b"}, None))
    out = copywriter.rewrite({"title": "t"}, "id")
    assert len(out["title"]) == 100 and out["hook_text"] == "a b" and out["hashtags"] == "#a #b"
