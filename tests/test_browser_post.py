"""Browser posting: who may use it, what it refuses, how a post reports back.

No browser runs here: the worker is replaced, because the real flow needs a
logged-in YouTube account. Its selectors are only proven by a real upload.
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import browser_post

JOB_ID = "11111111-2222-4333-8444-555555555555"
LOCAL = ("127.0.0.1", 50000)


@pytest.fixture
def api(tmp_path, monkeypatch):
    import app as app_module

    (tmp_path / "skills" / "youtube-connector" / "browser_profile").mkdir(parents=True)
    job_dir = tmp_path / "output" / JOB_ID
    job_dir.mkdir(parents=True)
    (job_dir / "clip.mp4").write_bytes(b"video")
    clip = {"video_url": f"/videos/{JOB_ID}/clip.mp4", "video_title_for_youtube_short": "Saved title"}
    monkeypatch.setitem(app_module.jobs, JOB_ID, {"status": "completed", "result": {"clips": [clip]}})
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    monkeypatch.setenv("BROWSER_POST_ENABLED", "1")
    monkeypatch.setenv("BROWSER_POST_PROFILE_ROOT", str(tmp_path / "skills"))
    monkeypatch.setattr(browser_post.importlib.util, "find_spec", lambda name: object())
    return app_module


def body(**over):
    return {"job_id": JOB_ID, "clip_index": 0, "platforms": ["youtube"], "provider": "browser", **over}


def wait_final(client, request_id):
    for _ in range(100):
        data = client.get(f"/api/social/post-status/{request_id}").json()
        if data["status"] != "in_progress":
            return data
        time.sleep(0.02)
    raise AssertionError("the post never reached a final state")


def test_status_lists_platforms_with_a_profile(api):
    r = TestClient(api.app, client=LOCAL).get("/api/social/browser/status")
    assert r.json() == {"enabled": True, "platforms": ["youtube"]}


def test_off_unless_switched_on(api, monkeypatch):
    monkeypatch.delenv("BROWSER_POST_ENABLED")
    client = TestClient(api.app, client=LOCAL)
    assert client.get("/api/social/browser/status").json() == {"enabled": False, "platforms": []}
    assert client.post("/api/social/post", json=body()).status_code == 403


def test_never_on_cloud(api, monkeypatch):
    monkeypatch.setattr(api, "BILLING_ENABLED", True)
    client = TestClient(api.app, client=LOCAL)
    assert client.post("/api/social/post", json=body()).status_code == 403


def test_refuses_another_machine(api):
    client = TestClient(api.app, client=("192.0.2.10", 50000))
    assert client.get("/api/social/browser/status").json()["enabled"] is False
    assert client.post("/api/social/post", json=body()).status_code == 403


def test_refuses_a_web_page_on_another_site(api):
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=body(), headers={"Origin": "https://attacker.example"})
    assert r.status_code == 403


def test_allows_the_local_dashboard(api, monkeypatch):
    monkeypatch.setattr(browser_post, "_run_worker", lambda job: {"success": True})
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=body(), headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200


@pytest.mark.parametrize("over", [
    {"platforms": ["tiktok"]},
    {"platforms": []},
    {"scheduled_date": "2030-01-01T00:00:00Z"},
    {"visibility": "secret"},
])
def test_refuses_what_it_cannot_do(api, over):
    client = TestClient(api.app, client=LOCAL)
    assert client.post("/api/social/post", json=body(**over)).status_code == 400


@pytest.mark.parametrize("index", [-1, 5])
def test_refuses_a_clip_that_does_not_exist(api, index):
    client = TestClient(api.app, client=LOCAL)
    assert client.post("/api/social/post", json=body(clip_index=index)).status_code == 404


def test_a_post_reports_success_with_its_link(api, monkeypatch):
    seen = {}

    def worker(job):
        seen.update(job)
        return {"success": True, "message": "Posted as private.", "url": "https://youtu.be/abc"}

    monkeypatch.setattr(browser_post, "_run_worker", worker)
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=body(title="A <b> title", description="Text", visibility="private"))
    assert r.status_code == 200, r.text

    final = wait_final(client, r.json()["request_id"])
    assert final["status"] == "completed"
    assert final["results"][0]["url"] == "https://youtu.be/abc"
    assert seen["title"] == "A b title"
    assert seen["description"] == "Text"
    assert seen["visibility"] == "private"
    assert os.path.isabs(seen["file_path"]) and seen["file_path"].endswith("clip.mp4")


def test_title_falls_back_to_the_clips_own(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(browser_post, "_run_worker", lambda job: seen.update(job) or {"success": True})
    client = TestClient(api.app, client=LOCAL)
    wait_final(client, client.post("/api/social/post", json=body()).json()["request_id"])
    assert seen["title"] == "Saved title"


def test_a_failed_post_reports_why(api, monkeypatch):
    monkeypatch.setattr(browser_post, "_run_worker", lambda job: {"success": False, "message": "Google is asking"})
    client = TestClient(api.app, client=LOCAL)
    final = wait_final(client, client.post("/api/social/post", json=body()).json()["request_id"])
    assert final["status"] == "failed"
    assert final["results"][0] == {"platform": "youtube", "success": False, "message": "Google is asking", "url": None}


def test_a_crashing_worker_still_ends_the_post(api, monkeypatch):
    def boom(job):
        raise RuntimeError("no browser")

    monkeypatch.setattr(browser_post, "_run_worker", boom)
    client = TestClient(api.app, client=LOCAL)
    final = wait_final(client, client.post("/api/social/post", json=body()).json()["request_id"])
    assert final["status"] == "failed"


def test_an_unknown_request_is_unknown(api):
    client = TestClient(api.app, client=LOCAL)
    assert client.get("/api/social/post-status/browser_nope").json()["status"] == "unknown"


def test_worker_output_that_is_not_json_is_a_failure(monkeypatch):
    class Done:
        stdout = b"not json"
        stderr = b"Executable doesn't exist"

    monkeypatch.setattr(browser_post.subprocess, "run", lambda *a, **k: Done())
    result = browser_post._run_worker({})
    assert result["success"] is False
    assert "Executable doesn't exist" in result["message"]


class FakeStudio:
    """Just enough of a Playwright page for the Done step."""

    def __init__(self, dialog_after_presses=None, dialog="", done_vanishes_after_presses=None):
        self.presses = 0
        self.dialog_after_presses = dialog_after_presses
        self.dialog = dialog
        self.done_vanishes_after_presses = done_vanishes_after_presses
        self.url = "https://studio.youtube.com/channel/x"

    def locator(self, selector):
        return self

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.presses += 1

    def is_visible(self):
        vanish = self.done_vanishes_after_presses
        return vanish is None or self.presses < vanish

    def count(self):
        return 0

    def evaluate(self, script):
        shown = self.dialog_after_presses is not None and self.presses >= self.dialog_after_presses
        return self.dialog if shown else ""

    def wait_for_timeout(self, ms):
        pass


SEEN_IN_STUDIO = ("Pemrosesan video\nVideo pribadi Anda sedang memproses definisi standar (SD).\n"
                  "1.12\nA Clip's Title, With Punctuation 2,200\n"
                  "Pemeriksaan selesai. Tidak ditemukan masalah.\nTutup")


def test_confirmation_dialog_is_recognised_in_any_language():
    title = "A Clip's Title, With Punctuation 2,200"
    assert browser_post._yt_confirms_post(SEEN_IN_STUDIO, title)
    assert not browser_post._yt_confirms_post(SEEN_IN_STUDIO, "Another video")
    assert not browser_post._yt_confirms_post("", title)
    assert not browser_post._yt_confirms_post(SEEN_IN_STUDIO, "")


def test_done_succeeds_on_the_dialog_with_the_upload_dialog_still_in_the_page():
    page = FakeStudio(dialog_after_presses=1, dialog=SEEN_IN_STUDIO)
    browser_post._yt_finish(page, "A Clip's Title, With Punctuation 2,200")
    assert page.presses == 1


def test_done_succeeds_when_the_upload_dialog_closes():
    page = FakeStudio(done_vanishes_after_presses=1)
    browser_post._yt_finish(page, "Any title")
    assert page.presses == 1


def test_done_is_pressed_again_when_the_first_press_does_nothing(monkeypatch):
    clock = iter(range(0, 10_000, 20))
    monkeypatch.setattr(browser_post.time, "monotonic", lambda: next(clock))
    page = FakeStudio(dialog_after_presses=2, dialog=SEEN_IN_STUDIO)
    browser_post._yt_finish(page, "A Clip's Title, With Punctuation 2,200")
    assert page.presses == 2


def test_an_unrelated_dialog_is_reported_not_taken_for_success(monkeypatch):
    clock = iter(range(0, 10_000, 20))
    monkeypatch.setattr(browser_post.time, "monotonic", lambda: next(clock))
    page = FakeStudio(dialog_after_presses=1, dialog="Daily upload limit reached")
    with pytest.raises(browser_post._PostFailed, match="Daily upload limit reached"):
        browser_post._yt_finish(page, "My title")


def test_upload_is_complete_only_on_the_final_chunk():
    class Response:
        def __init__(self, url, status):
            self.url, self.headers = url, {"x-goog-upload-status": status}

    assert browser_post._is_upload_complete(Response("https://upload.youtube.com/x", "final"))
    assert not browser_post._is_upload_complete(Response("https://upload.youtube.com/x", "active"))
    assert not browser_post._is_upload_complete(Response("https://example.com/x", "final"))
