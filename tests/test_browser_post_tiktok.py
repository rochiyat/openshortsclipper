"""Browser posting to TikTok, and to several platforms at once.

No browser runs here: only a real upload proves the selectors. These cover what the
flow must never do: post twice, post a garbled caption, or answer TikTok's questions.
"""
import time

import pytest
from fastapi.testclient import TestClient

import browser_post

JOB_ID = "11111111-2222-4333-8444-777777777777"
LOCAL = ("127.0.0.1", 50000)


@pytest.fixture
def api(tmp_path, monkeypatch):
    import app as app_module

    for platform in ("youtube", "tiktok"):
        (tmp_path / "skills" / f"{platform}-connector" / "browser_profile").mkdir(parents=True)
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
    return {"job_id": JOB_ID, "clip_index": 0, "platforms": ["tiktok"], "provider": "browser", **over}


def wait_final(client, request_id):
    for _ in range(100):
        data = client.get(f"/api/social/post-status/{request_id}").json()
        if data["status"] != "in_progress":
            return data
        time.sleep(0.02)
    raise AssertionError("the post never reached a final state")


def test_status_lists_tiktok_with_a_profile(api):
    data = TestClient(api.app, client=LOCAL).get("/api/social/browser/status").json()
    assert data["platforms"] == ["youtube", "tiktok"]


@pytest.mark.parametrize("platforms,visibility,status", [
    (["tiktok"], "friends", 200),
    (["tiktok"], "private", 200),
    (["tiktok"], "unlisted", 400),
    (["youtube"], "friends", 400),
    (["youtube", "tiktok"], "private", 200),
    (["youtube", "tiktok"], "unlisted", 400),
    (["youtube", "tiktok"], "friends", 400),
])
def test_visibility_must_suit_every_platform(api, monkeypatch, platforms, visibility, status):
    monkeypatch.setattr(browser_post, "_run_worker", lambda job: {"success": True})
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=body(platforms=platforms, visibility=visibility))
    assert r.status_code == status


def test_both_platforms_post_in_turn(api, monkeypatch):
    order = []
    monkeypatch.setattr(browser_post, "_run_worker",
                        lambda job: order.append(job["platform"]) or {"success": True, "message": "ok"})
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=body(platforms=["youtube", "tiktok", "youtube"], visibility="private"))
    final = wait_final(client, r.json()["request_id"])
    assert order == ["youtube", "tiktok"]
    assert final["status"] == "completed"
    assert [x["platform"] for x in final["results"]] == ["youtube", "tiktok"]


def test_one_platform_failing_still_says_the_other_went_out(api, monkeypatch):
    def worker(job):
        if job["platform"] == "youtube":
            return {"success": False, "message": "no"}
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(browser_post, "_run_worker", worker)
    client = TestClient(api.app, client=LOCAL)
    r = client.post("/api/social/post", json=body(platforms=["youtube", "tiktok"], visibility="private"))
    final = wait_final(client, r.json()["request_id"])
    assert final["status"] == "failed"
    assert [x["success"] for x in final["results"]] == [False, True]


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def count(self):
        return self.page.counts.get(self.selector, 0)

    def is_visible(self):
        return self.count() > 0

    def nth(self, i):
        return self

    def inner_text(self):
        if self.selector == browser_post.TT_CAPTION:
            return self.page.caption_text if self.page.caption_text is not None else self.page.typed
        return self.page.dialog

    def wait_for(self, state=None, timeout=None):
        pass

    def get_attribute(self, name):
        return self.page.post_attrs.get(name)

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)
        if self.selector == browser_post.TT_POST and self.page.leaves_after_post:
            self.page.url = "https://www.tiktok.com/tiktokstudio/content"
        if self.selector == browser_post.TT_TOUR_BUTTONS:
            self.page.counts[browser_post.TT_TOUR] = 0


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key == "Delete":
            self.page.typed = ""
        elif key == "Enter":
            self.page.typed += "\n"

    def insert_text(self, text):
        self.page.typed += text


class FakePage:
    def __init__(self, url="https://www.tiktok.com/tiktokstudio/upload", leaves_after_post=True):
        self.url = url
        self.counts = {}
        self.clicks = []
        self.typed = "name_of_the_file_clip_9"
        self.caption_text = None
        self.dialog = ""
        self.post_attrs = {}
        self.leaves_after_post = leaves_after_post
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_timeout(self, ms):
        pass


@pytest.fixture
def fast_clock(monkeypatch):
    clock = iter(range(0, 100_000, 30))
    monkeypatch.setattr(browser_post.time, "monotonic", lambda: next(clock))


def test_caption_replaces_the_file_name_keeping_its_lines():
    page = FakePage()
    browser_post._tt_fill_caption(page, "First line\n\n#tag #other")
    assert page.typed == "First line\n\n#tag #other"


def test_a_garbled_caption_is_never_posted():
    page = FakePage()
    page.caption_text = "First line"
    with pytest.raises(browser_post._PostFailed, match="nothing was posted"):
        browser_post._tt_fill_caption(page, "First line #tag")
    assert browser_post.TT_POST not in page.clicks


def test_the_feature_tour_is_closed_before_clicking():
    page = FakePage()
    page.counts = {browser_post.TT_TOUR: 1, browser_post.TT_TOUR_BUTTONS: 1}
    browser_post._tt_fill_caption(page, "Text")
    assert page.clicks[0] == browser_post.TT_TOUR_BUTTONS


@pytest.mark.parametrize("visibility,value", [("public", "0"), ("friends", "2"), ("private", "1")])
def test_visibility_uses_tiktoks_own_values(visibility, value):
    page = FakePage()
    browser_post._tt_set_visibility(page, visibility)
    assert page.clicks[-1] == f"""[role="option"][id='option-"{value}"']"""


def test_post_is_pressed_once_and_the_page_leaving_is_success():
    page = FakePage()
    browser_post._tt_post(page)
    assert page.clicks == [browser_post.TT_POST]


def test_no_confirmation_warns_against_posting_twice(fast_clock):
    page = FakePage(leaves_after_post=False)
    page.counts[browser_post.TT_DIALOG] = 1
    page.dialog = "Your video may violate the guidelines"
    with pytest.raises(browser_post._PostFailed, match="violate the guidelines") as failure:
        browser_post._tt_post(page)
    assert "twice" in str(failure.value)
    assert page.clicks == [browser_post.TT_POST]  # the dialog is never answered


def test_waits_until_post_is_enabled_and_gives_up(fast_clock):
    page = FakePage()
    page.post_attrs = {"data-disabled": "true"}
    page.counts[browser_post.TT_POST] = 1
    with pytest.raises(browser_post._PostFailed, match="nothing was posted"):
        browser_post._tt_wait_until_postable(page)


def test_ready_when_post_is_enabled():
    page = FakePage()
    page.post_attrs = {"data-disabled": "false", "aria-disabled": "false", "data-loading": "false"}
    page.counts[browser_post.TT_POST] = 1
    browser_post._tt_wait_until_postable(page)


def test_a_login_page_is_reported():
    with pytest.raises(browser_post._PostFailed, match="log in"):
        browser_post._tt_check_blocked(FakePage(url="https://www.tiktok.com/login?redirect=x"))


def test_a_captcha_is_reported_not_worked_around():
    page = FakePage()
    page.counts[browser_post.TT_CHALLENGE] = 1
    with pytest.raises(browser_post._PostFailed, match="verify"):
        browser_post._tt_check_blocked(page)
    assert page.clicks == []
