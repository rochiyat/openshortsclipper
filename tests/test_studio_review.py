"""Studio phase 2: review queue, rights warning, channels, campaigns, payout."""
import sqlite3

import pytest

from studio import db, migrations
from studio.catalog import estimate_payout
from studio.models import Campaign

from test_studio_projects import env  # noqa: F401  (fixture)


def _save(client, fake, **kw):
    return client.post("/api/studio/projects", json={"job_id": fake.make_job(**kw)}).json()["project"]


# ---- migration --------------------------------------------------------------

def test_v1_database_upgrades_and_keeps_its_rows(tmp_path, monkeypatch):
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    conn = sqlite3.connect(studio_dir / "studio.db")
    conn.isolation_level = None
    v1_only = [(v, stmts) for v, stmts in migrations.MIGRATIONS if v == 1]
    monkeypatch.setattr(migrations, "MIGRATIONS", v1_only)
    assert migrations.apply(conn) == 1
    conn.execute("INSERT INTO projects (id, job_id, title, created_at, updated_at) "
                 "VALUES ('p1', 'j1', 'Old', 't', 't')")
    conn.close()
    monkeypatch.undo()

    monkeypatch.setenv("STUDIO_DIR", str(studio_dir))
    db.reset()
    try:
        assert db.schema_version() == migrations.LATEST
        with db.session() as s:
            from studio.models import Project
            p = s.get(Project, "p1")
            assert p.title == "Old" and p.campaign_id is None
    finally:
        db.reset()


# ---- review queue -----------------------------------------------------------

def test_review_queue_lists_drafts_with_project_info(env):
    client, fake, _ = env
    p = _save(client, fake)
    d = client.get("/api/studio/clips").json()
    assert d["total"] == 2
    assert d["counts"]["draft"] == 2
    first = d["clips"][0]
    assert first["project"]["id"] == p["id"]
    assert first["project"]["rights"] == "unknown"


def test_edit_copy_without_changing_status(env):
    client, fake, _ = env
    p = _save(client, fake)
    cid = p["clips"][0]["id"]
    r = client.patch(f"/api/studio/clips/{cid}",
                     json={"hook_text": "  Hook baru  ", "hashtags": "#belajar #sains", "caption": ""})
    assert r.status_code == 200
    c = r.json()["clip"]
    assert c["hook_text"] == "Hook baru"
    assert c["hashtags"] == "#belajar #sains"
    assert c["caption"] is None
    assert c["review_status"] == "draft"
    assert c["title"] == "Judul 1"  # untouched: not sent


def test_approve_needs_rights_or_force(env):
    client, fake, _ = env
    p = _save(client, fake)
    cid = p["clips"][0]["id"]

    r = client.patch(f"/api/studio/clips/{cid}", json={"review_status": "approved"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "rights_unknown"
    assert client.get(f"/api/studio/projects/{p['id']}").json()["project"]["clips"][0]["review_status"] == "draft"

    r = client.patch(f"/api/studio/clips/{cid}", json={"review_status": "approved", "force": True})
    assert r.status_code == 200 and r.json()["clip"]["review_status"] == "approved"
    assert r.json()["clip"]["reviewed_at"]

    client.patch(f"/api/studio/projects/{p['id']}", json={"rights": "campaign"})
    cid2 = p["clips"][1]["id"]
    r = client.patch(f"/api/studio/clips/{cid2}", json={"review_status": "approved"})
    assert r.status_code == 200


def test_reject_and_back_to_draft(env):
    client, fake, _ = env
    p = _save(client, fake)
    cid = p["clips"][0]["id"]
    assert client.patch(f"/api/studio/clips/{cid}", json={"review_status": "rejected"}).status_code == 200
    counts = client.get("/api/studio/clips").json()["counts"]
    assert counts["rejected"] == 1 and counts["draft"] == 1
    c = client.patch(f"/api/studio/clips/{cid}", json={"review_status": "draft"}).json()["clip"]
    assert c["review_status"] == "draft" and c["reviewed_at"] is None


def test_posted_cannot_be_set_by_hand(env):
    client, fake, _ = env
    cid = _save(client, fake)["clips"][0]["id"]
    assert client.patch(f"/api/studio/clips/{cid}", json={"review_status": "posted"}).status_code == 400


def test_archived_projects_leave_the_queue(env):
    client, fake, _ = env
    p = _save(client, fake)
    client.patch(f"/api/studio/projects/{p['id']}", json={"status": "archived"})
    assert client.get("/api/studio/clips").json()["total"] == 0


# ---- channels ---------------------------------------------------------------

def test_channel_crud(env):
    client, _fake, _ = env
    r = client.post("/api/studio/channels", json={
        "name": "Akun ID", "platform": "TikTok", "handle": "@akun.id", "language": "id",
        "caption_preset": {"default_hashtags": "#fyp", "caption_suffix": "Follow!", "junk": "x"},
        "watermark_text": "@akun.id"})
    assert r.status_code == 200, r.text
    ch = r.json()["channel"]
    assert ch["platform"] == "tiktok" and ch["handle"] == "akun.id"
    assert ch["caption_preset"] == {"default_hashtags": "#fyp", "caption_suffix": "Follow!"}
    assert ch["publisher"] == "manual"

    assert client.post("/api/studio/channels", json={"name": "x", "platform": "myspace"}).status_code == 400
    assert client.post("/api/studio/channels", json={"platform": "youtube"}).status_code == 400
    assert client.post("/api/studio/channels",
                       json={"name": "x", "platform": "youtube", "language": "bahasa"}).status_code == 400

    r = client.patch(f"/api/studio/channels/{ch['id']}", json={"language": "en", "archived": True})
    assert r.json()["channel"]["archived"] is True
    assert client.get("/api/studio/channels").json()["channels"] == []
    assert len(client.get("/api/studio/channels", params={"include_archived": True}).json()["channels"]) == 1


# ---- campaigns --------------------------------------------------------------

def test_campaign_crud_validation_and_project_link(env):
    client, fake, _ = env
    r = client.post("/api/studio/campaigns", json={
        "name": "Podcast X", "cpm": 1.5, "currency": "usd", "min_views": 10000,
        "brief_url": "https://example.com/brief", "starts_at": "2026-11-01",
        "ends_at": "2026-11-30", "donation_pct": 20})
    assert r.status_code == 200, r.text
    camp = r.json()["campaign"]
    assert camp["currency"] == "USD" and camp["stats"]["projects"] == 0

    bad = [
        {"name": "x", "cpm": -1},
        {"name": "x", "donation_pct": 101},
        {"name": "x", "brief_url": "javascript:alert(1)"},
        {"name": "x", "starts_at": "2026-12-01", "ends_at": "2026-11-01"},
        {"name": "x", "currency": "RUPIAH"},
        {"name": "x", "starts_at": "kemarin"},
    ]
    for body in bad:
        assert client.post("/api/studio/campaigns", json=body).status_code == 400, body

    p = _save(client, fake)
    r = client.patch(f"/api/studio/projects/{p['id']}", json={"campaign_id": camp["id"]})
    assert r.json()["project"]["campaign_name"] == "Podcast X"
    assert client.patch(f"/api/studio/projects/{p['id']}",
                        json={"campaign_id": "nope-nope"}).status_code == 404
    # A PATCH without campaign_id leaves the link alone.
    client.patch(f"/api/studio/projects/{p['id']}", json={"title": "Renamed"})
    assert client.get(f"/api/studio/projects/{p['id']}").json()["project"]["campaign_id"] == camp["id"]

    stats = client.get(f"/api/studio/campaigns/{camp['id']}").json()["campaign"]["stats"]
    assert stats["projects"] == 1 and stats["clips"]["draft"] == 2
    queue = client.get("/api/studio/clips", params={"campaign_id": camp["id"]}).json()
    assert queue["total"] == 2
    assert client.get("/api/studio/clips", params={"campaign_id": "none"}).json()["total"] == 0

    # Deleting the campaign unlinks, never deletes, the project.
    assert client.delete(f"/api/studio/campaigns/{camp['id']}").status_code == 200
    proj = client.get(f"/api/studio/projects/{p['id']}").json()["project"]
    assert proj["campaign_id"] is None

    # Explicit null clears the link.
    camp2 = client.post("/api/studio/campaigns", json={"name": "B"}).json()["campaign"]
    client.patch(f"/api/studio/projects/{p['id']}", json={"campaign_id": camp2["id"]})
    client.patch(f"/api/studio/projects/{p['id']}", json={"campaign_id": None})
    assert client.get(f"/api/studio/projects/{p['id']}").json()["project"]["campaign_id"] is None


def test_estimate_endpoint(env):
    client, _fake, _ = env
    camp = client.post("/api/studio/campaigns",
                       json={"name": "A", "cpm": 2, "min_views": 1000, "donation_pct": 10}).json()["campaign"]
    d = client.get(f"/api/studio/campaigns/{camp['id']}/estimate", params={"views": 50000}).json()
    assert d == {"views": 50000, "eligible_views": 50000, "payout": 100.0,
                 "donation": 10.0, "net": 90.0, "currency": "USD"}


@pytest.mark.parametrize("views,min_views,cpm,cap,pct,payout,donation", [
    (9_999, 10_000, 1.0, None, 0, 0.0, 0.0),        # below the claim threshold
    (10_000, 10_000, 1.0, None, 0, 10.0, 0.0),      # threshold is inclusive
    (1_000_000, 0, 1.5, 500.0, 20, 500.0, 100.0),   # capped, then donation share
    (12_345, 0, 0.8, None, 25, 9.88, 2.47),
    (-5, 0, 1.0, None, 0, 0.0, 0.0),                # nonsense input is clamped
])
def test_payout_formula(views, min_views, cpm, cap, pct, payout, donation):
    c = Campaign(cpm=cpm, min_views=min_views, max_payout=cap, donation_pct=pct, currency="USD")
    out = estimate_payout(views, c)
    assert out["payout"] == pytest.approx(payout)
    assert out["donation"] == pytest.approx(donation)
    assert out["net"] == pytest.approx(round(payout - donation, 2))
