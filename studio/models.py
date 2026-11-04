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
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)

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
