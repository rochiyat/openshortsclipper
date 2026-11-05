"""Studio projects: save a finished job, survive the job dir's cleanup, reopen.

Runs against a small FastAPI app with a fake job system (JobBridge), so it
needs neither the ML stack nor app.py's globals. One test at the end checks the
real wiring in app.py.
"""
import json
import os
import shutil
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import studio
from studio import db


class FakeJobs:
    """A job store shaped like app.py's: output/{job_id}/ + a metadata JSON."""

    def __init__(self, output_dir):
        self.output_dir = str(output_dir)
        self.jobs = {}

    def make_job(self, n_clips=2, source=True, status="completed"):
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(self.output_dir, job_id)
        os.makedirs(job_dir)
        shorts = []
        for i in range(n_clips):
            name = f"video_clip_{i + 1}.mp4"
            with open(os.path.join(job_dir, name), "wb") as f:
                f.write(f"clip-{i}-{job_id}".encode())
            shorts.append({
                "start": 10.0 * i, "end": 10.0 * i + 25.5,
                "video_title_for_youtube_short": f"Judul {i + 1}",
                "viral_hook_text": f"Hook {i + 1} 🤯",
                "video_description_for_tiktok": f"Caption {i + 1} #fyp",
            })
        meta = {"shorts": shorts}
        if source:
            meta["source_video"] = "source.mp4"
            with open(os.path.join(job_dir, "source.mp4"), "wb") as f:
                f.write(b"x" * 1000)
        with open(os.path.join(job_dir, "video_metadata.json"), "w") as f:
            json.dump(meta, f)
        with open(os.path.join(job_dir, ".resume.json"), "w") as f:
            f.write("{}")
        self.jobs[job_id] = self._record(job_id, status)
        return job_id

    def _record(self, job_id, status="completed"):
        job_dir = os.path.join(self.output_dir, job_id)
        with open(os.path.join(job_dir, "video_metadata.json")) as f:
            meta = json.load(f)
        clips = [dict(c, video_url=f"/videos/{job_id}/video_clip_{i + 1}.mp4")
                 for i, c in enumerate(meta["shorts"])]
        return {"status": status, "output_dir": job_dir, "result": {"clips": clips},
                "source_url": "https://www.youtube.com/watch?v=abc",
                "source_video": meta.get("source_video")}

    def purge(self, job_id):
        """What cleanup_jobs() does once the retention window passes."""
        shutil.rmtree(os.path.join(self.output_dir, job_id))
        self.jobs.pop(job_id, None)

    def describe(self, job_id):
        return self.jobs.get(job_id)

    def recover(self):
        for job_id in os.listdir(self.output_dir):
            meta = os.path.join(self.output_dir, job_id, "video_metadata.json")
            if job_id not in self.jobs and os.path.isfile(meta):
                self.jobs[job_id] = self._record(job_id)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_DIR", str(tmp_path / "studio"))
    db.reset()
    out = tmp_path / "output"
    out.mkdir()
    fake = FakeJobs(out)
    app = FastAPI()
    studio.setup(app, studio.JobBridge(output_dir=fake.output_dir,
                                       describe=fake.describe, recover=fake.recover))
    with TestClient(app) as client:
        yield client, fake, tmp_path / "studio"
    db.reset()


def test_health(env):
    client, _fake, _dir = env
    r = client.get("/api/studio/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_save_copies_clips_and_metadata(env):
    client, fake, studio_dir = env
    job_id = fake.make_job()
    r = client.post("/api/studio/projects", json={"job_id": job_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    p = body["project"]
    assert p["title"] == "Judul 1"
    assert p["source_type"] == "youtube"
    assert p["has_source"] is False
    assert [c["review_status"] for c in p["clips"]] == ["draft", "draft"]
    assert p["clips"][0]["hook_text"] == "Hook 1 🤯"
    assert p["clips"][1]["duration"] == pytest.approx(25.5)
    files = studio_dir / "projects" / p["id"] / "files"
    assert (files / "video_clip_1.mp4").is_file()
    assert (files / "video_metadata.json").is_file()
    assert not (files / "source.mp4").exists()      # source left out by default
    assert not (files / ".resume.json").exists()    # dot files never copied


def test_clips_carry_their_viral_score_and_why(env):
    client, fake, _dir = env
    job_id = fake.make_job()
    clips = fake.jobs[job_id]["result"]["clips"]
    clips[0].update(predicted_score=91, why="Opens on a surprise.")
    clips[1].update(predicted_score="high", why="  ")
    p = client.post("/api/studio/projects", json={"job_id": job_id}).json()["project"]
    assert (p["clips"][0]["predicted_score"], p["clips"][0]["why"]) == (91, "Opens on a surprise.")
    # An unusable value shows nothing rather than a broken badge.
    assert (p["clips"][1]["predicted_score"], p["clips"][1]["why"]) == (None, None)
    reread = client.get(f"/api/studio/projects/{p['id']}").json()["project"]
    assert reread["clips"][0]["predicted_score"] == 91


def test_a_clip_without_scores_still_lists(env):
    client, fake, _dir = env
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job()}).json()["project"]
    assert p["clips"][0]["predicted_score"] is None and p["clips"][0]["why"] is None


def test_include_source_keeps_the_source_video(env):
    client, fake, studio_dir = env
    job_id = fake.make_job()
    p = client.post("/api/studio/projects",
                    json={"job_id": job_id, "include_source": True}).json()["project"]
    assert p["has_source"] is True
    assert (studio_dir / "projects" / p["id"] / "files" / "source.mp4").is_file()


def test_project_survives_job_cleanup_and_restart(env):
    client, fake, _dir = env
    job_id = fake.make_job()
    pid = client.post("/api/studio/projects", json={"job_id": job_id}).json()["project"]["id"]
    fake.purge(job_id)
    db.reset()  # fresh engine, as after a container restart

    listed = client.get("/api/studio/projects").json()
    assert listed["total"] == 1 and listed["projects"][0]["id"] == pid
    detail = client.get(f"/api/studio/projects/{pid}").json()["project"]
    r = client.get(detail["clips"][0]["video_url"])
    assert r.status_code == 200
    assert r.content == f"clip-0-{job_id}".encode()


def test_reopen_restores_a_purged_job(env):
    client, fake, _dir = env
    job_id = fake.make_job()
    pid = client.post("/api/studio/projects", json={"job_id": job_id}).json()["project"]["id"]
    fake.purge(job_id)

    r = client.post(f"/api/studio/projects/{pid}/reopen")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["job_id"] == job_id
    assert len(data["result"]["clips"]) == 2
    assert os.path.isfile(os.path.join(fake.output_dir, job_id, "video_clip_2.mp4"))
    assert fake.jobs[job_id]["status"] == "completed"


def test_reopen_refuses_a_running_job(env):
    client, fake, _dir = env
    job_id = fake.make_job()
    pid = client.post("/api/studio/projects", json={"job_id": job_id}).json()["project"]["id"]
    fake.jobs[job_id]["status"] = "processing"
    assert client.post(f"/api/studio/projects/{pid}/reopen").status_code == 409


def test_saving_again_resyncs_and_keeps_review_state(env):
    client, fake, studio_dir = env
    job_id = fake.make_job()
    first = client.post("/api/studio/projects", json={"job_id": job_id}).json()["project"]
    with db.session() as s:
        from studio.models import Clip
        clip = s.get(Clip, first["clips"][0]["id"])
        clip.review_status = "approved"
    # The user edits the clip after saving: a new file replaces the old one.
    job_dir = os.path.join(fake.output_dir, job_id)
    with open(os.path.join(job_dir, "subtitled_1_video_clip_1.mp4"), "wb") as f:
        f.write(b"captioned")
    fake.jobs[job_id]["result"]["clips"][0]["video_url"] = \
        f"/videos/{job_id}/subtitled_1_video_clip_1.mp4"

    r = client.post("/api/studio/projects", json={"job_id": job_id})
    again = r.json()
    assert again["created"] is False
    p = again["project"]
    assert p["id"] == first["id"]
    assert p["clips"][0]["file_name"] == "subtitled_1_video_clip_1.mp4"
    assert p["clips"][0]["review_status"] == "approved"
    assert p["clips"][0]["file_sha256"] != first["clips"][0]["file_sha256"]
    assert client.get("/api/studio/projects").json()["total"] == 1
    leftovers = [n for n in os.listdir(studio_dir / "projects" / p["id"]) if n != "files"]
    assert leftovers == []


def test_save_rejects_unfinished_unknown_and_bad_ids(env):
    client, fake, _dir = env
    running = fake.make_job(status="processing")
    assert client.post("/api/studio/projects", json={"job_id": running}).status_code == 409
    assert client.post("/api/studio/projects", json={"job_id": str(uuid.uuid4())}).status_code == 404
    assert client.post("/api/studio/projects", json={"job_id": "../etc"}).status_code == 400


def test_by_job_lookup(env):
    client, fake, _dir = env
    job_id = fake.make_job()
    assert client.get(f"/api/studio/projects/by-job/{job_id}").json()["project"] is None
    pid = client.post("/api/studio/projects", json={"job_id": job_id}).json()["project"]["id"]
    assert client.get(f"/api/studio/projects/by-job/{job_id}").json()["project"]["id"] == pid


def test_patch_search_archive_and_delete(env):
    client, fake, studio_dir = env
    a = client.post("/api/studio/projects", json={"job_id": fake.make_job(), "title": "Podcast A"}).json()["project"]
    b = client.post("/api/studio/projects", json={"job_id": fake.make_job(), "title": "Kajian B"}).json()["project"]

    r = client.patch(f"/api/studio/projects/{a['id']}", json={"rights": "campaign", "title": "Podcast A2"})
    assert r.status_code == 200 and r.json()["project"]["rights"] == "campaign"
    assert client.patch(f"/api/studio/projects/{a['id']}", json={"rights": "stolen"}).status_code == 400
    assert client.patch(f"/api/studio/projects/{a['id']}", json={"title": "  "}).status_code == 400

    found = client.get("/api/studio/projects", params={"q": "kajian"}).json()
    assert [p["id"] for p in found["projects"]] == [b["id"]]

    client.patch(f"/api/studio/projects/{b['id']}", json={"status": "archived"})
    assert client.get("/api/studio/projects").json()["total"] == 1
    assert client.get("/api/studio/projects", params={"status": "archived"}).json()["total"] == 1

    assert client.delete(f"/api/studio/projects/{a['id']}").status_code == 200
    assert not (studio_dir / "projects" / a["id"]).exists()
    assert client.get(f"/api/studio/projects/{a['id']}").status_code == 404


@pytest.mark.parametrize("path", [
    "../studio.db",
    "..%2F..%2Fstudio.db",
    "%2e%2e/%2e%2e/studio.db",
    "/etc/passwd",
])
def test_media_never_leaves_the_project_dir(env, path):
    client, fake, _dir = env
    pid = client.post("/api/studio/projects", json={"job_id": fake.make_job()}).json()["project"]["id"]
    assert client.get(f"/api/studio/media/{pid}/{path}").status_code == 404
    assert client.get(f"/api/studio/media/..%2F{pid}/video_clip_1.mp4").status_code == 404


def test_app_wires_studio_in_self_host(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_DIR", str(tmp_path / "studio"))
    db.reset()
    import app as app_module
    assert app_module.STUDIO_ENABLED is True
    client = TestClient(app_module.app)  # no lifespan: no worker, no disk sweep
    assert client.get("/api/studio/health").json()["ok"] is True
    r = client.post("/api/studio/projects", json={"job_id": str(uuid.uuid4())})
    assert r.status_code == 404
    db.reset()
