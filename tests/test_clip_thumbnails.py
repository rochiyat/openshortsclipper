"""A clip's thumbnails: templates, who may generate, and how one reaches a post.

Gemini is replaced: these cover the wiring, not how a thumbnail looks.
"""
import io
import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import browser_post
import thumbnail

JOB_ID = "11111111-2222-4333-8444-666666666666"
LOCAL = ("127.0.0.1", 50000)
THUMB = "thumb_abc123_1.jpg"


def image_bytes(size=(40, 40)):
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def api(tmp_path, monkeypatch):
    import app as app_module

    (tmp_path / "skills" / "youtube-connector" / "browser_profile").mkdir(parents=True)
    job_dir = tmp_path / "output" / JOB_ID
    job_dir.mkdir(parents=True)
    (job_dir / "clip.mp4").write_bytes(b"video")
    clip = {"video_url": f"/videos/{JOB_ID}/clip.mp4", "video_title_for_youtube_short": "Saved title",
            "video_description_for_instagram": "About the clip"}
    monkeypatch.setitem(app_module.jobs, JOB_ID, {"status": "completed", "result": {"clips": [clip]}})
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    monkeypatch.setenv("BROWSER_POST_ENABLED", "1")
    monkeypatch.setenv("BROWSER_POST_PROFILE_ROOT", str(tmp_path / "skills"))
    monkeypatch.setattr(browser_post.importlib.util, "find_spec", lambda name: object())

    async def key(request):
        return "test-key"

    monkeypatch.setattr(app_module, "resolve_gemini", key)
    monkeypatch.setattr(app_module, "extract_face_frames", lambda *a, **k: [])
    return app_module


@pytest.fixture
def generated(api, monkeypatch):
    """Replaces the model; records what the endpoint asked of it."""
    calls = []

    def fake(api_key, title, session_id, **kwargs):
        style = kwargs.get("style_reference_path")
        calls.append({"title": title, "style_exists": bool(style and os.path.exists(style)), **kwargs})
        os.makedirs(kwargs["output_dir"], exist_ok=True)
        for i in range(kwargs["count"]):
            with open(os.path.join(kwargs["output_dir"], f"thumb_abc123_{i + 1}.jpg"), "wb") as f:
                f.write(image_bytes())
        return [{"url": "x"}] * kwargs["count"]

    monkeypatch.setattr(api, "generate_thumbnail", fake)
    return calls


def url(index=0):
    return f"/api/clip/{JOB_ID}/{index}/thumbnails"


def test_list_offers_the_templates_without_their_prompts(api):
    data = TestClient(api.app).get(url()).json()
    assert data["thumbnails"] == []
    assert {t["id"] for t in data["templates"]} == set(thumbnail.CLIP_TEMPLATE_IDS)
    assert all("prompt" not in t for t in data["templates"])


def test_default_is_three_vertical_thumbnails(api, generated):
    r = TestClient(api.app).post(url(), data={})
    assert r.status_code == 200, r.text
    assert r.json()["generated"] == 3
    call = generated[0]
    assert call["count"] == 3 and call["vertical"] is True
    assert call["title"] == "Saved title"
    assert len(r.json()["thumbnails"]) == 3
    assert r.json()["thumbnails"][0]["url"].startswith(f"/videos/{JOB_ID}/clip_thumbs/0/thumb_")


def test_a_template_reaches_the_prompt(api, generated):
    TestClient(api.app).post(url(), data={"template": "number"})
    assert "number" in generated[0]["extra_prompt"]


def test_campaign_must_avoid_reaches_the_prompt(api, generated):
    import video_requirements

    video_requirements.save(os.path.join(api.OUTPUT_DIR, JOB_ID), {"must_avoid": "klaim medis"})
    TestClient(api.app).post(url(), data={})
    assert "klaim medis" in generated[0]["extra_prompt"]


def test_an_uploaded_template_is_the_style_reference(api, generated):
    r = TestClient(api.app).post(url(), files={"template_file": ("t.png", image_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    assert generated[0]["style_exists"] is True
    # Inputs are cleaned up, so they can never be served afterwards.
    folder = api._clip_thumbs_path(JOB_ID, 0)
    assert not [n for n in os.listdir(folder) if n.startswith(".")]


def test_a_template_that_is_not_an_image_costs_nothing(api, generated):
    r = TestClient(api.app).post(url(), files={"template_file": ("t.png", b"not an image", "image/png")})
    assert r.status_code == 400
    assert generated == []


def test_a_private_template_link_is_refused(api, generated):
    r = TestClient(api.app).post(url(), data={"template_url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400
    assert generated == []


@pytest.mark.parametrize("data", [{"template": "nope"}])
def test_an_unknown_template_is_refused(api, generated, data):
    assert TestClient(api.app).post(url(), data=data).status_code == 400


def test_a_clip_that_does_not_exist(api, generated):
    assert TestClient(api.app).post(url(5), data={}).status_code == 404


def test_free_plan_cannot_generate_on_cloud(api, generated, monkeypatch):
    class Free:
        plan = "free"

    async def user(request):
        return Free()

    monkeypatch.setattr(api, "BILLING_ENABLED", True)
    monkeypatch.setattr(api, "_user_from_request", user)
    assert TestClient(api.app).post(url(), data={}).status_code == 403
    assert generated == []


def post_body(**over):
    return {"job_id": JOB_ID, "clip_index": 0, "platforms": ["youtube"], "provider": "browser", **over}


def wait_final(client, request_id):
    for _ in range(100):
        data = client.get(f"/api/social/post-status/{request_id}").json()
        if data["status"] != "in_progress":
            return data
        time.sleep(0.02)
    raise AssertionError("the post never reached a final state")


def test_the_chosen_thumbnail_reaches_the_worker(api, monkeypatch):
    folder = api._clip_thumbs_path(JOB_ID, 0)
    os.makedirs(folder)
    with open(os.path.join(folder, THUMB), "wb") as f:
        f.write(image_bytes())
    seen = {}
    monkeypatch.setattr(browser_post, "_run_worker", lambda job: seen.update(job) or {"success": True})
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=post_body(thumbnail=THUMB))
    assert r.status_code == 200, r.text
    wait_final(client, r.json()["request_id"])
    assert os.path.isabs(seen["thumbnail_path"]) and seen["thumbnail_path"].endswith(THUMB)


def test_no_thumbnail_is_fine(api, monkeypatch):
    seen = {}
    monkeypatch.setattr(browser_post, "_run_worker", lambda job: seen.update(job) or {"success": True})
    client = TestClient(api.app, client=LOCAL)
    wait_final(client, client.post("/api/social/post", json=post_body()).json()["request_id"])
    assert seen["thumbnail_path"] is None


@pytest.mark.parametrize("name,status", [
    ("../../secret.jpg", 400),
    ("clip.mp4", 400),
    ("thumb_abc123_9.jpg", 404),
])
def test_a_thumbnail_outside_the_clip_is_refused(api, name, status):
    client = TestClient(api.app, client=LOCAL)
    assert client.post("/api/social/post", json=post_body(thumbnail=name)).status_code == status


class FakeUploader:
    def __init__(self, present=True):
        self.present = present
        self.files = None
        self.first = self

    def locator(self, selector):
        return self

    def wait_for(self, state=None, timeout=None):
        if not self.present:
            raise browser_post.PlaywrightTimeoutError("no input")

    def set_input_files(self, path):
        self.files = path

    def wait_for_timeout(self, ms):
        pass


def test_studio_receives_the_thumbnail():
    page = FakeUploader()
    assert browser_post._yt_set_thumbnail(page, "x.jpg") is True
    assert page.files == "x.jpg"


def test_a_channel_without_the_upload_still_posts():
    assert browser_post._yt_set_thumbnail(FakeUploader(present=False), "x.jpg") is False


def test_vertical_thumbnails_are_cropped_to_nine_sixteen(tmp_path):
    out = tmp_path / "t.jpg"
    thumbnail.finalize_thumbnail(Image.new("RGB", (1024, 1024)), str(out), thumbnail.CLIP_THUMB_SIZE)
    with Image.open(out) as img:
        assert img.size == thumbnail.CLIP_THUMB_SIZE
    assert out.stat().st_size <= thumbnail.THUMB_MAX_BYTES


def test_instructions_keep_a_vertical_headline_out_of_the_sides():
    text = thumbnail.clip_thumbnail_instructions("auto")
    assert '"top"' in text and '"bottom"' in text


@pytest.mark.parametrize("link,video", [
    ("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk"),
    ("https://youtu.be/abcdefghijk", "abcdefghijk"),
    ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk"),
])
def test_youtube_links_resolve_to_their_thumbnail(link, video):
    assert thumbnail._YOUTUBE_ID_RE.match(link).group(1) == video


def test_preview_image_is_found_whatever_the_attribute_order():
    for html in ('<meta property="og:image" content="https://x.example/a.jpg">',
                 '<meta content="https://x.example/a.jpg" name="twitter:image">'):
        found = thumbnail._OG_IMAGE_RE.search(html)
        assert (found.group(1) or found.group(2)) == "https://x.example/a.jpg"
