"""Browser-posting accounts: the saved list, its channels, and posting to them.

No browser runs here: the worker is replaced. Whether Studio really opens the
channel a post names is only proven by a real upload.
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import browser_accounts
import browser_post

JOB_ID = "11111111-2222-4333-8444-555555555555"
LOCAL = ("127.0.0.1", 50000)
CH_A = "UC" + "a" * 22
CH_B = "UC" + "b" * 22


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_POST_PROFILE_ROOT", str(tmp_path / "skills"))
    return tmp_path / "skills"


@pytest.fixture
def api(root, tmp_path, monkeypatch):
    import app as app_module

    job_dir = tmp_path / "output" / JOB_ID
    job_dir.mkdir(parents=True)
    (job_dir / "clip.mp4").write_bytes(b"video")
    clip = {"video_url": f"/videos/{JOB_ID}/clip.mp4", "video_title_for_youtube_short": "Saved title"}
    monkeypatch.setitem(app_module.jobs, JOB_ID, {"status": "completed", "result": {"clips": [clip]}})
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(app_module, "BILLING_ENABLED", False)
    monkeypatch.setenv("BROWSER_POST_ENABLED", "1")
    monkeypatch.setattr(browser_post.importlib.util, "find_spec", lambda name: object())
    return TestClient(app_module.app, client=LOCAL)


def wait_final(client, request_id):
    for _ in range(200):
        data = client.get(f"/api/social/post-status/{request_id}").json()
        if data["status"] != "in_progress":
            return data
        time.sleep(0.02)
    raise AssertionError("the post never reached a final state")


def wait_login(client, account_id):
    for _ in range(200):
        acc = next(a for a in client.get("/api/social/browser/accounts").json()["accounts"] if a["id"] == account_id)
        if acc["login"] and acc["login"]["status"] != "open":
            return acc
        time.sleep(0.02)
    raise AssertionError("the login never ended")


def make_account(client, label="Main", channels=(CH_A, CH_B)):
    acc = client.post("/api/social/browser/accounts", json={"label": label, "open_login": False}).json()["account"]
    r = client.patch(f"/api/social/browser/accounts/{acc['id']}",
                     json={"channels": [{"id": c, "name": f"name {c[-1]}"} for c in channels]})
    assert r.status_code == 200, r.text
    return r.json()["account"]


# --- registry ---------------------------------------------------------------

def test_a_legacy_profile_is_taken_in_once(root):
    (root / "youtube-connector" / "browser_profile").mkdir(parents=True)
    assert [a["id"] for a in browser_accounts.list_accounts()] == ["youtube-default"]
    browser_accounts.delete("youtube-default")
    assert not (root / "youtube-connector").exists()
    # Deleted stays deleted, even if the folder comes back.
    (root / "youtube-connector" / "browser_profile").mkdir(parents=True)
    assert browser_accounts.list_accounts() == []


def test_channels_accept_ids_and_urls_and_drop_duplicates(root):
    acc = browser_accounts.create("youtube", "x")
    out = browser_accounts.update(acc["id"], channels=[
        {"id": f"https://studio.youtube.com/channel/{CH_A}/videos"}, {"id": CH_A}, {"id": CH_B, "name": " B "}])
    assert out["channels"] == [{"id": CH_A, "name": CH_A}, {"id": CH_B, "name": "B"}]
    with pytest.raises(browser_accounts.AccountError):
        browser_accounts.update(acc["id"], channels=[{"id": "not a channel"}])


def test_a_login_adds_channels_and_keeps_names(root):
    acc = browser_accounts.create("youtube", "x")
    browser_accounts.update(acc["id"], channels=[{"id": CH_A, "name": "Mine"}])
    out = browser_accounts.record_login(acc["id"], [CH_A, CH_B, "junk"])
    assert out["channels"] == [{"id": CH_A, "name": "Mine"}, {"id": CH_B, "name": CH_B}]
    assert out["last_login_at"]


def test_delete_removes_the_profile(root):
    acc = browser_accounts.create("youtube", "x")
    profile = root / "browser-accounts" / acc["id"] / "browser_profile"
    assert profile.is_dir()
    browser_accounts.delete(acc["id"])
    assert not (root / "browser-accounts" / acc["id"]).exists()
    assert browser_accounts.list_accounts() == []


def test_a_profile_outside_the_root_is_never_deleted(root, tmp_path):
    acc = browser_accounts.create("youtube", "x")
    data = browser_accounts._read()
    data["accounts"][0]["profile"] = "../elsewhere"
    browser_accounts._write(data)
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(browser_accounts.AccountError):
        browser_accounts.delete(acc["id"])
    assert (tmp_path / "elsewhere").is_dir()


def test_only_supported_platforms(root):
    with pytest.raises(browser_accounts.AccountError):
        browser_accounts.create("myspace", "x")


def test_studio_channel_is_read_from_the_url():
    assert browser_post.studio_channel(f"https://studio.youtube.com/channel/{CH_A}/videos") == CH_A
    assert browser_post.studio_channel(f"https://www.youtube.com/channel/{CH_A}") == ""
    assert browser_post.studio_channel(f"https://evil.example/?u=https://studio.youtube.com/channel/{CH_A}") == ""


# --- API --------------------------------------------------------------------

def test_account_endpoints_answer_only_locally(api, root):
    import app as app_module
    other = TestClient(app_module.app, client=("192.0.2.10", 50000))
    assert other.get("/api/social/browser/accounts").status_code == 403
    assert other.post("/api/social/browser/accounts", json={"open_login": False}).status_code == 403
    acc = make_account(api)
    assert other.delete(f"/api/social/browser/accounts/{acc['id']}").status_code == 403
    assert api.post("/api/social/browser/accounts", json={"open_login": False},
                    headers={"Origin": "https://attacker.example"}).status_code == 403


def test_add_rename_and_delete_an_account(api, root):
    acc = make_account(api)
    r = api.patch(f"/api/social/browser/accounts/{acc['id']}", json={"label": "Renamed"})
    assert r.json()["account"]["label"] == "Renamed"
    assert api.delete(f"/api/social/browser/accounts/{acc['id']}").status_code == 200
    assert api.get("/api/social/browser/accounts").json()["accounts"] == []
    assert api.delete(f"/api/social/browser/accounts/{acc['id']}").status_code == 404


def test_adding_an_account_opens_a_login_window_and_records_channels(api, root, monkeypatch):
    jobs = []

    def worker(job, timeout=None):
        jobs.append(job)
        return {"success": True, "channels": [CH_A, CH_B]}

    monkeypatch.setattr(browser_post, "_run_worker", worker)
    acc = api.post("/api/social/browser/accounts", json={"label": "New"}).json()["account"]
    done = wait_login(api, acc["id"])
    assert jobs[0]["mode"] == "login"
    assert jobs[0]["profile_dir"].endswith(os.path.join(acc["id"], "browser_profile"))
    assert [c["id"] for c in done["channels"]] == [CH_A, CH_B]
    assert done["login"]["status"] == "done"


def test_a_login_is_refused_while_the_profile_is_busy(api, root, monkeypatch):
    acc = make_account(api)
    lock = browser_post._profile_lock(acc["id"])
    lock.acquire()
    try:
        assert api.post(f"/api/social/browser/accounts/{acc['id']}/login").status_code == 409
        assert api.delete(f"/api/social/browser/accounts/{acc['id']}").status_code == 409
    finally:
        lock.release()


def test_without_playwright_a_login_says_how_to_install(api, root, monkeypatch):
    monkeypatch.setattr(browser_post.importlib.util, "find_spec", lambda name: None)
    r = api.post("/api/social/browser/accounts", json={"label": "x"})
    assert r.status_code == 400 and "playwright install" in r.json()["detail"]
    assert browser_accounts.list_accounts() == []


def test_post_to_two_channels_of_one_account(api, root, monkeypatch):
    acc = make_account(api)
    seen = []

    def worker(job, timeout=None):
        seen.append(job)
        return {"success": True, "url": f"https://youtu.be/{job['channel_id'][-1]}"}

    monkeypatch.setattr(browser_post, "_run_worker", worker)
    r = api.post("/api/social/post", json={
        "job_id": JOB_ID, "clip_index": 0, "platforms": ["youtube"], "provider": "browser",
        "title": "Main title",
        "targets": [{"account_id": acc["id"], "channel_id": CH_A},
                    {"account_id": acc["id"], "channel_id": CH_B, "title": "Other <title>"}],
    })
    assert r.status_code == 200, r.text
    final = wait_final(api, r.json()["request_id"])
    assert final["status"] == "completed"
    assert [j["channel_id"] for j in seen] == [CH_A, CH_B]
    assert [j["title"] for j in seen] == ["Main title", "Other title"]
    assert [x["url"] for x in final["results"]] == ["https://youtu.be/a", "https://youtu.be/b"]
    assert final["results"][1]["target"] == "Main · name b"


def test_one_failed_destination_does_not_hide_the_others(api, root, monkeypatch):
    acc = make_account(api)
    monkeypatch.setattr(browser_post, "_run_worker", lambda job, timeout=None: (
        {"success": job["channel_id"] == CH_A, "message": "nope"}))
    r = api.post("/api/social/post", json={
        "job_id": JOB_ID, "clip_index": 0, "platforms": ["youtube"], "provider": "browser",
        "targets": [{"account_id": acc["id"], "channel_id": CH_A}, {"account_id": acc["id"], "channel_id": CH_B}],
    })
    final = wait_final(api, r.json()["request_id"])
    assert final["status"] == "completed"
    assert [x["success"] for x in final["results"]] == [True, False]


@pytest.mark.parametrize("targets", [
    [{"account_id": "youtube-nope"}],
    [{"account_id": "ACC", "channel_id": "UC" + "z" * 22}],  # not one of the account's channels
    [{"account_id": "ACC", "channel_id": CH_A}] * 11,
])
def test_refuses_destinations_it_cannot_reach(api, root, targets):
    acc = make_account(api)
    targets = [{**t, "account_id": acc["id"] if t["account_id"] == "ACC" else t["account_id"]} for t in targets]
    r = api.post("/api/social/post", json={
        "job_id": JOB_ID, "clip_index": 0, "platforms": ["youtube"], "provider": "browser", "targets": targets})
    assert r.status_code == 400


# --- channel check before the file is handed over ---------------------------

class FakeStudio:
    def __init__(self, url, config_channel=""):
        self.url = url
        self.config_channel = config_channel
        self.files = []
        self.visited = []

    def goto(self, url, **kw):
        self.visited.append(url)

    def evaluate(self, script):
        return self.config_channel

    def locator(self, selector):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def set_input_files(self, path):
        self.files.append(path)

    def wait_for_timeout(self, ms):
        pass


def test_upload_opens_on_the_named_channel():
    page = FakeStudio(f"https://studio.youtube.com/channel/{CH_A}/videos/upload?d=ud", CH_A)
    browser_post._yt_open_upload_dialog(page, "/x.mp4", CH_A)
    assert page.visited == [browser_post.YT_CHANNEL_UPLOAD_URL.format(CH_A)]
    assert page.files == ["/x.mp4"]


def test_upload_on_another_channel_is_refused_before_the_file_is_given():
    page = FakeStudio(f"https://studio.youtube.com/channel/{CH_A}/videos/upload?d=ud", CH_B)
    with pytest.raises(browser_post._PostFailed, match=CH_B):
        browser_post._yt_open_upload_dialog(page, "/x.mp4", CH_A)
    assert page.files == []


def test_upload_without_a_channel_keeps_the_old_page():
    page = FakeStudio("https://studio.youtube.com/channel/whatever")
    browser_post._yt_open_upload_dialog(page, "/x.mp4")
    assert page.visited == [browser_post.YT_UPLOAD_URL]
