"""SQLite engine for Studio (sync SQLAlchemy 2, WAL).

Only the API process writes here; the per-job main.py child never touches it.
Endpoints are plain ``def`` so FastAPI runs them in its threadpool.
"""
import os
import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from . import config, migrations

_lock = threading.Lock()
_engine = None
_engine_path = None
_Session = None


def _on_connect(dbapi_conn, _record):
    # The sqlite3 driver opens implicit transactions; let migrations.apply()
    # and SQLAlchemy manage them explicitly instead.
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def _build(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(config.projects_dir(), exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
        future=True,
    )
    event.listen(engine, "connect", _on_connect)
    raw = engine.raw_connection()
    try:
        raw.driver_connection.isolation_level = None  # autocommit; BEGIN by hand
        migrations.apply(raw.driver_connection)
        raw.driver_connection.isolation_level = ""
    finally:
        raw.close()
    return engine


def init():
    """Create (or migrate) the database for the current STUDIO_DIR. Idempotent."""
    global _engine, _engine_path, _Session
    path = config.db_path()
    with _lock:
        if _engine is not None and _engine_path == path:
            return _engine
        if _engine is not None:
            _engine.dispose()
        _engine = _build(path)
        _engine_path = path
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
        return _engine


def reset():
    """Drop the cached engine (tests switch STUDIO_DIR between cases)."""
    global _engine, _engine_path, _Session
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = _engine_path = _Session = None


def schema_version() -> int:
    engine = init()
    with engine.connect() as conn:
        return conn.exec_driver_sql("SELECT version FROM schema_version").scalar_one()


@contextmanager
def session():
    init()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session():
    """FastAPI dependency."""
    with session() as s:
        yield s
