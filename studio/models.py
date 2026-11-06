"""ORM mapping for the tables created in migrations.py.

Own metadata, unrelated to cloud.models. The DDL lives in migrations.py; keep
these columns in sync with it (tests/test_studio_db.py checks that).
"""
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PROJECT_STATUSES = ("active", "archived")
RIGHTS = ("unknown", "campaign", "permission", "cc", "own")
REVIEW_STATUSES = ("draft", "approved", "rejected", "posted")
# What a reviewer may set by hand; "posted" comes from recording a post (phase 3).
REVIEWABLE_STATUSES = ("draft", "approved", "rejected")
PLATFORMS = ("youtube", "tiktok", "instagram", "threads", "facebook", "x")
PUBLISHERS = ("manual", "uploadpost", "youtube_api")
CAMPAIGN_STATUSES = ("active", "ended", "archived")
POST_STATUSES = ("live", "removed", "failed", "pending")
CLAIM_STATUSES = ("none", "submitted", "approved", "paid", "rejected")
PUBLISH_STATUSES = ("queued", "running", "awaiting", "done", "failed", "canceled")
SNAPSHOT_SOURCES = ("youtube_api", "ytdlp", "manual", "uploadpost")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column(String, default="upload")
    has_source: Mapped[int] = mapped_column(Integer, default=0)
    rights: Mapped[str] = mapped_column(String, default="unknown")
    rights_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    tags_json: Mapped[str] = mapped_column("tags", Text, default="[]")
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)

    @property
    def tags(self) -> list:
        try:
            value = json.loads(self.tags_json or "[]")
        except ValueError:
            return []
        return [t for t in value if isinstance(t, str)] if isinstance(value, list) else []

    campaign: Mapped["Campaign | None"] = relationship(lazy="joined")
    clips: Mapped[list["Clip"]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
        order_by="Clip.clip_index", passive_deletes=True)


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    clip_index: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    review_status: Mapped[str] = mapped_column(String, default="draft")
    reviewed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)

    project: Mapped[Project] = relationship(back_populates="clips")

    @property
    def meta(self) -> dict:
        try:
            return json.loads(self.metadata_json or "{}")
        except ValueError:
            return {}


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


def _json_dict(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


class Channel(Base):
    """One of the user's own social accounts, with its posting defaults."""
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    platform: Mapped[str] = mapped_column(String)
    handle: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    caption_preset: Mapped[str | None] = mapped_column(Text, nullable=True)
    watermark_text: Mapped[str | None] = mapped_column(String, nullable=True)
    publisher: Mapped[str] = mapped_column(String, default="manual")
    publisher_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)
    archived_at: Mapped[str | None] = mapped_column(String, nullable=True)

    @property
    def preset(self) -> dict:
        return _json_dict(self.caption_preset)

    @property
    def publisher_cfg(self) -> dict:
        return _json_dict(self.publisher_config)


class Campaign(Base):
    """A clipping program that pays per view (CPM)."""
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    platform_source: Mapped[str | None] = mapped_column(String, nullable=True)
    brief_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cpm: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String, default="USD")
    min_views: Mapped[int] = mapped_column(Integer, default=0)
    max_payout: Mapped[float | None] = mapped_column(Float, nullable=True)
    starts_at: Mapped[str | None] = mapped_column(String, nullable=True)
    ends_at: Mapped[str | None] = mapped_column(String, nullable=True)
    donation_pct: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String, default="active")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    brief_json: Mapped[str | None] = mapped_column("brief", Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)

    @property
    def brief(self) -> dict:
        return _json_dict(self.brief_json)


class Post(Base):
    """A clip published on one of the user's channels."""
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    clip_id: Mapped[str | None] = mapped_column(
        ForeignKey("clips.id", ondelete="SET NULL"), nullable=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String)
    external_id: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    posted_at: Mapped[str] = mapped_column(String)
    method: Mapped[str] = mapped_column(String, default="manual")
    publish_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="live")
    submitted_to_campaign_at: Mapped[str | None] = mapped_column(String, nullable=True)
    claim_status: Mapped[str] = mapped_column(String, default="none")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    last_checked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)

    clip: Mapped["Clip | None"] = relationship(lazy="joined")
    channel: Mapped[Channel] = relationship(lazy="joined")
    campaign: Mapped["Campaign | None"] = relationship(lazy="joined")


class ViewSnapshot(Base):
    __tablename__ = "view_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    captured_at: Mapped[str] = mapped_column(String, default=now_iso)
    views: Mapped[int] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String)


class PublishJob(Base):
    """One clip to publish on one channel, now or at ``scheduled_at``."""
    __tablename__ = "publish_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    clip_id: Mapped[str | None] = mapped_column(
        ForeignKey("clips.id", ondelete="SET NULL"), nullable=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    publisher: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    post_id: Mapped[str | None] = mapped_column(
        ForeignKey("posts.id", ondelete="SET NULL"), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)

    clip: Mapped["Clip | None"] = relationship(lazy="joined")
    channel: Mapped[Channel] = relationship(lazy="joined")
    campaign: Mapped["Campaign | None"] = relationship(lazy="joined")
