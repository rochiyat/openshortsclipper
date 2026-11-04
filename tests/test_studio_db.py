"""Studio SQLite: migrations are idempotent, PRAGMAs are on, ORM matches DDL."""
import sqlite3

import pytest
from sqlalchemy import inspect

from studio import db, migrations
from studio.models import Base


@pytest.fixture
def studio_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_DIR", str(tmp_path / "studio"))
    db.reset()
    yield tmp_path / "studio"
    db.reset()


def test_init_creates_db_at_latest_version(studio_dir):
    db.init()
    assert (studio_dir / "studio.db").is_file()
    assert (studio_dir / "projects").is_dir()
    assert db.schema_version() == migrations.LATEST


def test_migrations_are_idempotent(studio_dir):
    db.init()
    db.reset()
    db.init()  # second boot on the same file must not fail or re-run DDL
    conn = sqlite3.connect(studio_dir / "studio.db")
    try:
        assert migrations.apply(conn) == migrations.LATEST
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    finally:
        conn.close()


def test_wal_and_foreign_keys_are_on(studio_dir):
    engine = db.init()
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_orm_columns_match_migration_ddl(studio_dir):
    engine = db.init()
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        on_disk = {c["name"] for c in insp.get_columns(table.name)}
        mapped = {c.name for c in table.columns}
        assert mapped == on_disk, table.name
