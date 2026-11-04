"""Studio phase 3: post URLs, recording posts, view snapshots, reports, schedule."""
import sys
import types
from datetime import datetime, time as dtime, timedelta, timezone

import pytest

from studio import metrics, posts, scheduler
from studio.urls import PostUrlError, parse_post_url

from test_studio_projects import env  # noqa: F401  (fixture)


# ---- URL parsing --------------------------------------------------------------

@pytest.mark.parametrize("url,platform,ext_id", [
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ?si=abc123", "youtube", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "youtube", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("youtube.com/shorts/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
    ("https://www.tiktok.com/@akun.id/video/7301234567890123456?lang=id", "tiktok", "7301234567890123456"),
    ("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=xyz", "instagram", "C1a2B3c4D5e"),
    ("https://www.instagram.com/p/C1a2B3c4D5e/", "instagram", "C1a2B3c4D5e"),
    ("https://www.threads.net/@akun/post/C9xYz12345", "threads", "C9xYz12345"),
    ("https://www.facebook.com/reel/1234567890123", "facebook", "1234567890123"),
    ("https://x.com/akun/status/1790000000000000000", "x", "1790000000000000000"),
])
def test_parse_post_url(url, platform, ext_id):
    parsed = parse_post_url(url)
    assert (parsed.platform, parsed.external_id) == (platform, ext_id)
    assert parsed.url.startswith("https://")


def test_youtube_urls_are_canonical():
    assert parse_post_url("https://youtu.be/dQw4w9WgXcQ").url == "https://www.youtube.com/shorts/dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    "", "https://vm.tiktok.com/ZMabc123/", "https://fb.watch/abc/",
    "https://www.youtube.com/@channel", "https://www.youtube.com/watch?v=short",
    "https://evil.example/shorts/dQw4w9WgXcQ", "https://youtube.com.evil.example/shorts/dQw4w9WgXcQ",
    "javascript:alert(1)", "ftp://youtube.com/shorts/dQw4w9WgXcQ",
])
def test_parse_post_url_rejects(url):
    with pytest.raises(PostUrlError):
        parse_post_url(url)


# ---- helpers --------------------------------------------------------------------

YT = "https://www.youtube.com/shorts/{}"


def _setup(client, fake, rights="campaign", campaign=True):
    p = client.post("/api/studio/projects", json={"job_id": fake.make_job(n_clips=3)}).json()["project"]
    camp = None
    if campaign:
        camp = client.post("/api/studio/campaigns", json={
            "name": "Camp", "cpm": 2, "min_views": 1000, "donation_pct": 10}).json()["campaign"]
    client.patch(f"/api/studio/projects/{p['id']}", json={
        "rights": rights, "campaign_id": camp["id"] if camp else None})
    for c in p["clips"]:
        client.patch(f"/api/studio/clips/{c['id']}", json={"review_status": "approved"})
    ch = client.post("/api/studio/channels", json={"name": "YT ID", "platform": "youtube"}).json()["channel"]
    return p, ch, camp


def _post(client, clip_id, channel_id, vid, **kw):
    return client.post("/api/studio/posts", json={
        "clip_id": clip_id, "channel_id": channel_id, "url": YT.format(vid), **kw})


# ---- recording posts ------------------------------------------------------------

def test_record_post_marks_clip_posted_and_inherits_campaign(env):
    client, fake, _ = env
    p, ch, camp = _setup(client, fake)
    r = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1")
    assert r.status_code == 200, r.text
    post = r.json()["post"]
    assert post["platform"] == "youtube" and post["external_id"] == "AAAAAAAAAA1"
    assert post["campaign"]["id"] == camp["id"]
    assert post["stats"]["views"] is None
    clips = client.get("/api/studio/clips", params={"review_status": "posted"}).json()
    assert [c["id"] for c in clips["clips"]] == [p["clips"][0]["id"]]


def test_same_url_twice_is_refused(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1")
    r = client.post("/api/studio/posts", json={"clip_id": p["clips"][1]["id"], "channel_id": ch["id"],
                                               "url": "https://youtu.be/AAAAAAAAAA1", "force": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_recorded"


def test_same_file_on_same_channel_needs_confirmation(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    clip = p["clips"][0]["id"]
    _post(client, clip, ch["id"], "AAAAAAAAAA1")
    r = _post(client, clip, ch["id"], "BBBBBBBBBB2")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "duplicate_on_channel"
    assert "AAAAAAAAAA1" in r.json()["detail"]["url"]
    assert _post(client, clip, ch["id"], "BBBBBBBBBB2", force=True).status_code == 200
    # Another channel is not a duplicate.
    ch2 = client.post("/api/studio/channels", json={"name": "YT EN", "platform": "youtube"}).json()["channel"]
    assert _post(client, clip, ch2["id"], "CCCCCCCCCC3").status_code == 200


def test_unapproved_clip_needs_confirmation(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    client.patch(f"/api/studio/clips/{p['clips'][2]['id']}", json={"review_status": "draft"})
    r = _post(client, p["clips"][2]["id"], ch["id"], "DDDDDDDDDD4")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_approved"
    assert _post(client, p["clips"][2]["id"], ch["id"], "DDDDDDDDDD4", force=True).status_code == 200


def test_platform_must_match_channel_and_channel_must_be_active(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    tt = client.post("/api/studio/channels", json={"name": "TT", "platform": "tiktok"}).json()["channel"]
    assert _post(client, p["clips"][0]["id"], tt["id"], "AAAAAAAAAA1").status_code == 400
    client.patch(f"/api/studio/channels/{ch['id']}", json={"archived": True})
    assert _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").status_code == 400
    bad = client.post("/api/studio/posts", json={"clip_id": p["clips"][0]["id"], "channel_id": tt["id"],
                                                 "url": "https://vm.tiktok.com/ZMabc/"})
    assert bad.status_code == 400 and "short link" in bad.json()["detail"]


def test_posted_at_validation(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    future = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    assert _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1", posted_at=future).status_code == 400
    r = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1", posted_at="2026-11-01")
    assert r.json()["post"]["posted_at"].startswith("2026-11-01T12:00:00")


def test_delete_post_returns_clip_to_approved(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    pid = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").json()["post"]["id"]
    assert client.delete(f"/api/studio/posts/{pid}").status_code == 200
    clip = client.get("/api/studio/clips", params={"review_status": "approved"}).json()["clips"]
    assert p["clips"][0]["id"] in [c["id"] for c in clip]


def test_posts_survive_project_deletion(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1")
    client.delete(f"/api/studio/projects/{p['id']}")
    listed = client.get("/api/studio/posts").json()
    assert listed["total"] == 1 and listed["posts"][0]["clip"] is None


def test_claim_status_and_patch(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    pid = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").json()["post"]["id"]
    r = client.patch(f"/api/studio/posts/{pid}", json={"claim_status": "submitted"})
    assert r.json()["post"]["claim_status"] == "submitted"
    assert r.json()["post"]["submitted_to_campaign_at"]
    assert client.patch(f"/api/studio/posts/{pid}", json={"claim_status": "maybe"}).status_code == 400
    assert client.patch(f"/api/studio/posts/{pid}", json={"campaign_id": None}).json()["post"]["campaign"] is None


# ---- fetchers -------------------------------------------------------------------

class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_youtube_api_batches_and_parses(monkeypatch):
    import httpx
    calls = []

    def fake_get(url, timeout, params):
        calls.append(params["id"].split(","))
        items = [{"id": vid, "statistics": {"viewCount": "1500", "likeCount": "20", "commentCount": "3"}}
                 for vid in params["id"].split(",") if vid != "GONEGONEGON"]
        return _Resp(200, {"items": items})

    monkeypatch.setattr(httpx, "get", fake_get)
    ids = [f"{i:011d}" for i in range(60)] + ["GONEGONEGON"]
    out = metrics.fetch_youtube_api(ids, "k")
    assert [len(c) for c in calls] == [50, 11]
    assert out["00000000001"].views == 1500 and out["00000000001"].source == "youtube_api"
    assert "GONEGONEGON" not in out


def test_youtube_api_error_never_echoes_the_body(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, timeout, params: _Resp(
        403, {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "key=SECRET"}}))
    with pytest.raises(metrics.FetchError) as e:
        metrics.fetch_youtube_api(["AAAAAAAAAA1"], "SECRET")
    assert "quotaExceeded" in str(e.value) and "SECRET" not in str(e.value)


def _fake_ytdlp(monkeypatch, behaviour):
    """behaviour(opts) -> info dict or raises."""
    seen = []

    class YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            seen.append(dict(self.opts))
            return behaviour(self.opts)

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=YDL))
    return seen


def test_ytdlp_tries_cookies_first_then_anonymous(monkeypatch):
    monkeypatch.setenv("YOUTUBE_COOKIES", "# Netscape HTTP Cookie File\n")

    def behaviour(opts):
        if "cookiefile" in opts:
            raise RuntimeError("Sign in to confirm you're not a bot")
        return {"view_count": 42, "like_count": 5}

    seen = _fake_ytdlp(monkeypatch, behaviour)
    c = metrics.fetch_ytdlp(YT.format("AAAAAAAAAA1"), "youtube")
    assert c.views == 42 and c.source == "ytdlp"
    assert ["cookiefile" in o for o in seen] == [True, False]
    assert all(o["noplaylist"] and o["skip_download"] and "proxy" not in o for o in seen)


def test_ytdlp_missing_view_count_is_an_error_not_zero(monkeypatch):
    monkeypatch.delenv("YOUTUBE_COOKIES", raising=False)
    _fake_ytdlp(monkeypatch, lambda opts: {"title": "x"})
    with pytest.raises(metrics.FetchError) as e:
        metrics.fetch_ytdlp(YT.format("AAAAAAAAAA1"), "youtube")
    assert not isinstance(e.value, metrics.PostUnavailable)


def test_ytdlp_unavailable(monkeypatch):
    monkeypatch.delenv("YOUTUBE_COOKIES", raising=False)

    def behaviour(opts):
        raise RuntimeError("ERROR: [youtube] AAAAAAAAAA1: Video unavailable")

    _fake_ytdlp(monkeypatch, behaviour)
    with pytest.raises(metrics.PostUnavailable):
        metrics.fetch_ytdlp(YT.format("AAAAAAAAAA1"), "youtube")


# ---- snapshot runs --------------------------------------------------------------

def test_run_snapshots_via_api_key(env, monkeypatch):
    client, fake, _ = env
    p, ch, camp = _setup(client, fake)
    a = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").json()["post"]["id"]
    b = _post(client, p["clips"][1]["id"], ch["id"], "GONEGONEGON").json()["post"]["id"]
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "k")
    monkeypatch.setattr(metrics, "fetch_youtube_api", lambda ids, key: {
        "AAAAAAAAAA1": metrics.Counters(views=250_000, likes=900, source="youtube_api")})
    monkeypatch.setattr(metrics, "fetch_ytdlp", lambda *a: pytest.fail("API path must not scrape"))

    summary = posts.run_snapshots(sleep=lambda s: None)
    assert summary["checked"] == 2 and summary["ok"] == 1 and summary["removed"] == 1

    listed = {x["id"]: x for x in client.get("/api/studio/posts").json()["posts"]}
    assert listed[a]["stats"]["views"] == 250_000
    assert listed[a]["estimate"]["payout"] == 500.0   # 250k / 1000 * 2
    assert listed[b]["status"] == "removed"

    report = client.get(f"/api/studio/campaigns/{camp['id']}/report").json()
    assert report["totals"]["views"] == 250_000
    assert report["totals"]["payout"] == 500.0 and report["totals"]["donation"] == 50.0

    # Removed posts are no longer polled.
    assert posts.run_snapshots(sleep=lambda s: None)["checked"] == 1


def test_run_snapshots_without_key_scrapes_and_isolates_failures(env, monkeypatch):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1")
    _post(client, p["clips"][1]["id"], ch["id"], "BBBBBBBBBB2")
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    sleeps = []

    def fake_ytdlp(url, platform):
        if "BBBBBBBBBB2" in url:
            raise metrics.FetchError("bot check")
        return metrics.Counters(views=1234)

    monkeypatch.setattr(metrics, "fetch_ytdlp", fake_ytdlp)
    summary = posts.run_snapshots(sleep=sleeps.append)
    assert summary["ok"] == 1 and summary["failed"] == 1 and len(sleeps) == 1
    by_url = {x["external_id"]: x for x in client.get("/api/studio/posts").json()["posts"]}
    assert by_url["AAAAAAAAAA1"]["stats"]["views"] == 1234
    assert by_url["BBBBBBBBBB2"]["stats"]["views"] is None      # never a fake 0
    assert by_url["BBBBBBBBBB2"]["last_error"] == "bot check"
    assert by_url["BBBBBBBBBB2"]["status"] == "live"


def test_manual_snapshot_and_delta(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    pid = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").json()["post"]["id"]
    now = datetime.now(timezone.utc)
    client.post(f"/api/studio/posts/{pid}/snapshots",
                json={"views": 1000, "captured_at": (now - timedelta(days=1)).isoformat()})
    r = client.post(f"/api/studio/posts/{pid}/snapshots", json={"views": 4000, "likes": 50})
    stats = r.json()["post"]["stats"]
    assert stats["views"] == 4000 and stats["delta_24h"] == 3000 and stats["source"] == "manual"
    series = client.get(f"/api/studio/posts/{pid}/snapshots").json()["snapshots"]
    assert [x["views"] for x in series] == [1000, 4000]
    assert client.post(f"/api/studio/posts/{pid}/snapshots", json={"views": -1}).status_code == 400


def test_refresh_endpoint_reads_one_post(env, monkeypatch):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    pid = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").json()["post"]["id"]
    monkeypatch.delenv("YOUTUBE_DATA_API_KEY", raising=False)
    monkeypatch.setattr(metrics, "fetch_ytdlp", lambda url, platform: metrics.Counters(views=77))
    r = client.post(f"/api/studio/posts/{pid}/refresh")
    assert r.status_code == 200 and r.json()["post"]["stats"]["views"] == 77


def test_csv_export(env):
    client, fake, _ = env
    p, ch, _ = _setup(client, fake)
    pid = _post(client, p["clips"][0]["id"], ch["id"], "AAAAAAAAAA1").json()["post"]["id"]
    client.post(f"/api/studio/posts/{pid}/snapshots", json={"views": 50_000})
    r = client.get("/api/studio/export/posts.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("posted_at,platform,channel,url")
    assert "50000" in lines[1] and "100.0" in lines[1]


# ---- schedule ---------------------------------------------------------------------

def test_schedule_math_in_jakarta():
    wib = timezone(timedelta(hours=7))
    at = dtime(23, 50)
    # 16:49 UTC = 23:49 WIB: today's run is not due yet, yesterday's was.
    now = datetime(2026, 11, 4, 16, 49, tzinfo=timezone.utc)
    assert scheduler.last_due(now, at, wib) == datetime(2026, 11, 3, 23, 50, tzinfo=wib)
    assert scheduler.next_due(now, at, wib) == datetime(2026, 11, 4, 23, 50, tzinfo=wib)
    now = datetime(2026, 11, 4, 16, 51, tzinfo=timezone.utc)  # 23:51 WIB
    assert scheduler.last_due(now, at, wib) == datetime(2026, 11, 4, 23, 50, tzinfo=wib)


def test_zone_and_time_config(monkeypatch):
    monkeypatch.setenv("STUDIO_TZ", "+07:00")
    assert scheduler.zone().utcoffset(None) == timedelta(hours=7)
    monkeypatch.setenv("STUDIO_TZ", "Not/AZone")
    assert scheduler.zone().utcoffset(None) == timedelta(hours=7)  # falls back to WIB
    monkeypatch.setenv("STUDIO_SNAPSHOT_AT", "25:99")
    assert scheduler.run_at() == dtime(23, 50)
    monkeypatch.setenv("STUDIO_SNAPSHOT_AT", "21:05")
    assert scheduler.run_at() == dtime(21, 5)


def test_run_once_records_last_run_and_status(env, monkeypatch):
    client, _fake, _ = env
    monkeypatch.setattr(posts, "run_snapshots", lambda ids=None: {"checked": 0})
    assert client.get("/api/studio/snapshots/status").json()["last_run"] is None
    assert scheduler.run_once() == {"checked": 0}
    st = client.get("/api/studio/snapshots/status").json()
    assert st["last_run"] and st["run_at"] == "23:50" and st["running"] is False
