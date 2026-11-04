"""Studio phase 4: publish queue, YouTube direct upload (OAuth), Upload-Post."""
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from studio import publishers, publishing, youtube_oauth

from test_studio_projects import env  # noqa: F401  (fixture)


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._payload).encode()

    def json(self):
        return self._payload


@pytest.fixture
def google(monkeypatch):
    """A fake Google: token endpoint, channels.list and the resumable upload."""
    import httpx
    calls = {"token": [], "init": [], "put": []}
    state = {"upload_status": 200, "video_id": "VVVVVVVVVV1"}

    def fake_post(url, data=None, timeout=None, **kw):
        assert url == youtube_oauth.TOKEN_URL
        calls["token"].append(dict(data))
        return Resp(200, {"access_token": "at-" + data["grant_type"], "expires_in": 3600,
                          "refresh_token": "rt-1" if data["grant_type"] == "authorization_code" else None})

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        assert url == youtube_oauth.CHANNELS_URL
        return Resp(200, {"items": [{"id": "UC123", "snippet": {"title": "Sepotong Ilmu"}}]})

    class Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, params=None, headers=None, content=None, **kw):
            calls["init"].append({"url": url, "params": params, "headers": headers,
                                  "meta": json.loads(content)})
            return Resp(200, {}, {"location": "https://upload.example/session-1"})

        def put(self, url, content=None, headers=None, **kw):
            body = b"".join(content)
            calls["put"].append({"url": url, "size": len(body), "headers": headers})
            if state["upload_status"] != 200:
                return Resp(state["upload_status"], {"error": {"errors": [{"reason": "quotaExceeded"}]}})
            return Resp(200, {"id": state["video_id"]})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("STUDIO_YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("STUDIO_YOUTUBE_CLIENT_SECRET", "csecret")
    return calls, state


def _setup(client, fake, publisher="youtube_api", platform="youtube", config_=None):
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=2)}).json()["project"]
    camp = client.post("/api/studio/campaigns", json={"name": "Camp", "cpm": 1}).json()["campaign"]
    client.patch(f"/api/studio/projects/{p['id']}", json={"rights": "campaign", "campaign_id": camp["id"]})
    for c in p["clips"]:
        client.patch(f"/api/studio/clips/{c['id']}", json={
            "review_status": "approved", "caption": f"Caption {c['clip_index']}", "hashtags": "#belajar"})
    ch = client.post("/api/studio/channels", json={
        "name": "Ch", "platform": platform, "publisher": publisher,
        "publisher_config": config_ or {},
        "caption_preset": {"default_hashtags": "#fyp", "caption_suffix": "Follow!"}}).json()["channel"]
    return p, ch, camp


def _connect(client, ch):
    r = client.get("/api/studio/youtube/connect",
                   params={"channel_id": ch["id"], "origin": "http://localhost:5175"})
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert q["redirect_uri"] == ["http://localhost:5175/api/studio/youtube/callback"]
    assert "youtube.upload" in q["scope"][0] and q["access_type"] == ["offline"]
    cb = client.get("/api/studio/youtube/callback", params={"state": q["state"][0], "code": "c0de"})
    assert cb.status_code == 200 and "Sepotong Ilmu" in cb.text
    return q["state"][0]


# ---- YouTube OAuth ----------------------------------------------------------------

def test_youtube_connect_stores_token_privately(env, google):
    client, fake, studio_dir = env
    _p, ch, _ = _setup(client, fake)
    state = _connect(client, ch)
    path = studio_dir / "secrets" / f"youtube-{ch['id']}.json"
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["refresh_token"] == "rt-1"
    got = client.get("/api/studio/channels").json()["channels"][0]
    assert got["youtube_connected"] is True
    assert got["publisher_config"]["youtube_channel_title"] == "Sepotong Ilmu"
    assert "rt-1" not in json.dumps(got) and "at-" not in json.dumps(got)
    # The state is single-use.
    again = client.get("/api/studio/youtube/callback", params={"state": state, "code": "c0de"})
    assert again.status_code == 400


def test_youtube_connect_needs_client_config_and_the_right_publisher(env, monkeypatch):
    client, fake, _ = env
    monkeypatch.delenv("STUDIO_YOUTUBE_CLIENT_ID", raising=False)
    _p, ch, _ = _setup(client, fake)
    r = client.get("/api/studio/youtube/connect", params={"channel_id": ch["id"]})
    assert r.status_code == 400 and "STUDIO_YOUTUBE_CLIENT_ID" in r.json()["detail"]
    manual = client.post("/api/studio/channels", json={"name": "M", "platform": "youtube"}).json()["channel"]
    assert client.get("/api/studio/youtube/connect", params={"channel_id": manual["id"]}).status_code == 400


def test_callback_escapes_google_error(env):
    client, _fake, _ = env
    r = client.get("/api/studio/youtube/callback", params={"error": "<script>x</script>"})
    assert r.status_code == 400 and "<script>x" not in r.text


# ---- queue + YouTube upload -------------------------------------------------------

def test_publish_now_uploads_to_youtube_and_records_the_post(env, google, monkeypatch):
    client, fake, _ = env
    calls, _state = google
    p, ch, camp = _setup(client, fake, config_={"privacy": "unlisted"})
    _connect(client, ch)
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)

    preview = client.get("/api/studio/publish/preview",
                         params={"clip_id": p["clips"][0]["id"], "channel_id": ch["id"]}).json()
    assert preview["title"] == "Judul 1"
    assert preview["description"] == "Caption 0\n\n#belajar\n\nFollow!"

    r = client.post("/api/studio/publish", json={"clip_id": p["clips"][0]["id"], "channel_id": ch["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["job"]["status"] == "queued"
    assert publishing.tick() == {"ran": 1, "polled": 0}

    meta = calls["init"][0]["meta"]
    assert meta["snippet"]["title"] == "Judul 1"
    assert meta["status"]["privacyStatus"] == "unlisted"
    assert calls["init"][0]["headers"]["Authorization"] == "Bearer at-authorization_code"
    assert calls["put"][0]["size"] == len(f"clip-0-{p['job_id']}".encode())

    job = client.get("/api/studio/publish/jobs").json()["jobs"][0]
    assert job["status"] == "done" and job["post_id"]
    post = client.get("/api/studio/posts").json()["posts"][0]
    assert post["url"] == "https://www.youtube.com/shorts/VVVVVVVVVV1"
    assert post["method"] == "youtube_api" and post["status"] == "live"
    assert post["campaign"]["id"] == camp["id"]
    posted = client.get("/api/studio/clips", params={"review_status": "posted"}).json()["clips"]
    assert [c["id"] for c in posted] == [p["clips"][0]["id"]]


def test_scheduled_job_waits_until_due(env, google, monkeypatch):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    _connect(client, ch)
    later = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    r = client.post("/api/studio/publish", json={
        "clip_id": p["clips"][0]["id"], "channel_id": ch["id"], "scheduled_at": later})
    assert r.status_code == 200
    assert publishing.tick() == {"ran": 0, "polled": 0}
    naive = client.post("/api/studio/publish", json={
        "clip_id": p["clips"][1]["id"], "channel_id": ch["id"], "scheduled_at": "2026-12-01T09:00"})
    assert naive.status_code == 400 and "time zone" in naive.json()["detail"]


def test_enqueue_guards(env, google, monkeypatch):
    client, fake, _ = env
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)
    p, ch, _ = _setup(client, fake)
    clip = p["clips"][0]["id"]
    # Not connected yet.
    r = client.post("/api/studio/publish", json={"clip_id": clip, "channel_id": ch["id"]})
    assert r.status_code == 400 and "Connect" in r.json()["detail"]
    _connect(client, ch)
    assert client.post("/api/studio/publish", json={"clip_id": clip, "channel_id": ch["id"]}).status_code == 200
    again = client.post("/api/studio/publish", json={"clip_id": clip, "channel_id": ch["id"], "force": True})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_queued"
    # Draft clips ask first.
    client.patch(f"/api/studio/clips/{p['clips'][1]['id']}", json={"review_status": "draft"})
    r = client.post("/api/studio/publish", json={"clip_id": p["clips"][1]["id"], "channel_id": ch["id"]})
    assert r.json()["detail"]["code"] == "not_approved"
    # Manual channels never publish.
    manual = client.post("/api/studio/channels", json={"name": "M", "platform": "youtube"}).json()["channel"]
    assert client.post("/api/studio/publish", json={"clip_id": clip, "channel_id": manual["id"]}).status_code == 400


def test_duplicate_file_on_channel_after_a_publish(env, google, monkeypatch):
    client, fake, _ = env
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)
    p, ch, _ = _setup(client, fake)
    _connect(client, ch)
    clip = p["clips"][0]["id"]
    client.post("/api/studio/publish", json={"clip_id": clip, "channel_id": ch["id"]})
    publishing.tick()
    r = client.post("/api/studio/publish", json={"clip_id": clip, "channel_id": ch["id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_on_channel"


def test_quota_error_retries_then_fails(env, google, monkeypatch):
    client, fake, _ = env
    _calls, state = google
    state["upload_status"] = 403
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)
    p, ch, _ = _setup(client, fake)
    _connect(client, ch)
    jid = client.post("/api/studio/publish", json={
        "clip_id": p["clips"][0]["id"], "channel_id": ch["id"]}).json()["job"]["id"]
    publishing.tick()
    job = client.get("/api/studio/publish/jobs").json()["jobs"][0]
    assert job["status"] == "queued" and job["attempts"] == 1
    assert "quotaExceeded" in job["last_error"] and job["next_attempt_at"]
    assert publishing.tick()["ran"] == 0   # backoff: not due yet
    for _ in range(publishing.MAX_ATTEMPTS):
        from studio import db
        from studio.models import PublishJob
        with db.session() as s:
            s.get(PublishJob, jid).next_attempt_at = None
        publishing.tick()
    job = client.get("/api/studio/publish/jobs").json()["jobs"][0]
    assert job["status"] == "failed" and job["attempts"] == publishing.MAX_ATTEMPTS
    assert client.get("/api/studio/posts").json()["total"] == 0
    # Retry puts it back in the queue.
    state["upload_status"] = 200
    assert client.post(f"/api/studio/publish/jobs/{jid}/retry").json()["job"]["status"] == "queued"
    publishing.tick()
    assert client.get("/api/studio/publish/jobs").json()["jobs"][0]["status"] == "done"


def test_cancel_and_edit_queued_job(env, google, monkeypatch):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    _connect(client, ch)
    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    jid = client.post("/api/studio/publish", json={
        "clip_id": p["clips"][0]["id"], "channel_id": ch["id"], "scheduled_at": later}).json()["job"]["id"]
    r = client.patch(f"/api/studio/publish/jobs/{jid}", json={"title": "Judul baru"})
    assert r.json()["job"]["title"] == "Judul baru"
    assert client.post(f"/api/studio/publish/jobs/{jid}/cancel").json()["job"]["status"] == "canceled"
    assert client.patch(f"/api/studio/publish/jobs/{jid}", json={"title": "x"}).status_code == 409


def test_interrupted_upload_is_never_retried_blindly(env, google):
    client, fake, _ = env
    from studio import db
    from studio.models import PublishJob
    p, ch, _ = _setup(client, fake)
    _connect(client, ch)
    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    jid = client.post("/api/studio/publish", json={
        "clip_id": p["clips"][0]["id"], "channel_id": ch["id"], "scheduled_at": later}).json()["job"]["id"]
    with db.session() as s:
        s.get(PublishJob, jid).status = "running"
    publishing.recover_interrupted()
    job = client.get("/api/studio/publish/jobs").json()["jobs"][0]
    assert job["status"] == "failed" and "may already be published" in job["last_error"]


def test_youtube_title_is_cut_on_a_word():
    long = "Kenapa " * 30
    t = publishers.youtube_title(long)
    assert len(t) <= 100 and not t.endswith(" ") and t.startswith("Kenapa Kenapa")
    assert publishers.youtube_title("") == "Short"


# ---- Upload-Post ---------------------------------------------------------------------

@pytest.fixture
def uploadpost(monkeypatch):
    import httpx
    sent = []
    state = {"status": {"status": "pending", "results": []}}

    class Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, data=None, files=None, **kw):
            sent.append({"url": url, "headers": headers, "data": data})
            return Resp(200, {"success": True, "request_id": "req-1"})

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(httpx, "get", lambda url, params=None, headers=None, timeout=None: Resp(200, state["status"]))
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "upk")
    return sent, state


def test_uploadpost_needs_key_and_profile(env, monkeypatch):
    client, fake, _ = env
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    p, ch, _ = _setup(client, fake, publisher="uploadpost", platform="tiktok",
                      config_={"profile": "akun"})
    r = client.post("/api/studio/publish", json={"clip_id": p["clips"][0]["id"], "channel_id": ch["id"]})
    assert r.status_code == 400 and "UPLOAD_POST_API_KEY" in r.json()["detail"]
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    noprof = client.post("/api/studio/channels", json={
        "name": "T2", "platform": "tiktok", "publisher": "uploadpost"}).json()["channel"]
    r = client.post("/api/studio/publish", json={"clip_id": p["clips"][0]["id"], "channel_id": noprof["id"]})
    assert r.status_code == 400 and "profile" in r.json()["detail"]
    bad = client.post("/api/studio/channels", json={
        "name": "T3", "platform": "tiktok", "publisher": "uploadpost", "publisher_config": {"profile": "a b"}})
    assert bad.status_code == 400


def test_uploadpost_tiktok_draft_becomes_pending_then_live(env, uploadpost, monkeypatch):
    client, fake, _ = env
    sent, state = uploadpost
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)
    p, ch, _ = _setup(client, fake, publisher="uploadpost", platform="tiktok", config_={"profile": "akun"})
    client.post("/api/studio/publish", json={"clip_id": p["clips"][0]["id"], "channel_id": ch["id"]})
    assert publishing.tick() == {"ran": 1, "polled": 0}
    assert sent[0]["headers"]["Authorization"] == "Apikey upk"
    assert sent[0]["data"]["user"] == "akun" and sent[0]["data"]["platform[]"] == ["tiktok"]
    assert sent[0]["data"]["post_mode"] == "MEDIA_UPLOAD"
    assert client.get("/api/studio/publish/jobs").json()["jobs"][0]["status"] == "awaiting"

    publishing.tick()   # still pending at the vendor
    assert client.get("/api/studio/publish/jobs").json()["jobs"][0]["status"] == "awaiting"

    # Done, but a TikTok draft has no public URL yet.
    state["status"] = {"status": "completed", "results": [{"platform": "tiktok", "success": True}]}
    publishing.tick()
    assert client.get("/api/studio/publish/jobs").json()["jobs"][0]["status"] == "done"
    post = client.get("/api/studio/posts").json()["posts"][0]
    assert post["status"] == "pending" and post["url"] == ""

    # Pending posts are not polled for views...
    from studio import posts as posts_mod
    assert posts_mod.run_snapshots(sleep=lambda s: None)["checked"] == 0
    # ...until the URL is attached.
    bad = client.patch(f"/api/studio/posts/{post['id']}", json={"url": "https://youtu.be/AAAAAAAAAA1"})
    assert bad.status_code == 400
    r = client.patch(f"/api/studio/posts/{post['id']}",
                     json={"url": "https://www.tiktok.com/@akun/video/7301234567890123456"})
    assert r.status_code == 200 and r.json()["post"]["status"] == "live"
    assert r.json()["post"]["external_id"] == "7301234567890123456"


def test_uploadpost_status_with_url_goes_live(env, uploadpost, monkeypatch):
    client, fake, _ = env
    _sent, state = uploadpost
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)
    p, ch, _ = _setup(client, fake, publisher="uploadpost", platform="youtube", config_={"profile": "akun"})
    client.post("/api/studio/publish", json={"clip_id": p["clips"][0]["id"], "channel_id": ch["id"]})
    publishing.tick()
    state["status"] = {"status": "completed", "results": [
        {"platform": "youtube", "success": True, "post_url": "https://youtube.com/shorts/UUUUUUUUUU1"}]}
    publishing.tick()
    post = client.get("/api/studio/posts").json()["posts"][0]
    assert post["status"] == "live" and post["external_id"] == "UUUUUUUUUU1"


def test_uploadpost_platform_failure(env, uploadpost, monkeypatch):
    client, fake, _ = env
    _sent, state = uploadpost
    monkeypatch.setattr("studio.scheduler.kick_publisher", lambda: None)
    p, ch, _ = _setup(client, fake, publisher="uploadpost", platform="instagram", config_={"profile": "akun"})
    client.post("/api/studio/publish", json={"clip_id": p["clips"][0]["id"], "channel_id": ch["id"]})
    publishing.tick()
    state["status"] = {"status": "failed", "results": [
        {"platform": "instagram", "success": False, "message": "Reconnect   Instagram"}]}
    publishing.tick()
    job = client.get("/api/studio/publish/jobs").json()["jobs"][0]
    assert job["status"] == "failed" and job["last_error"] == "Reconnect Instagram"
    assert client.get("/api/studio/posts").json()["total"] == 0


def test_availability_endpoint(env, monkeypatch):
    client, _fake, _ = env
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    monkeypatch.delenv("STUDIO_YOUTUBE_CLIENT_ID", raising=False)
    d = client.get("/api/studio/publishers").json()
    assert d["youtube_api"]["configured"] is False and d["uploadpost"]["configured"] is False
