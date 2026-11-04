"""Studio settings, read from the environment at call time.

Read lazily (not frozen at import) so tests can point STUDIO_DIR at a temp
directory without reloading modules.
"""
import os

_TRUE = ("1", "true", "yes", "on")


def enabled() -> bool:
    """Studio runs in self-host only; STUDIO_ENABLED=0 turns it off there too."""
    return os.environ.get("STUDIO_ENABLED", "1").strip().lower() in _TRUE


def studio_dir() -> str:
    """Root for the database and saved projects. Never swept by cleanup_jobs()."""
    return os.path.abspath(os.environ.get("STUDIO_DIR", "studio_data"))


def projects_dir() -> str:
    return os.path.join(studio_dir(), "projects")


def db_path() -> str:
    return os.path.join(studio_dir(), "studio.db")


def link_mode() -> str:
    """``link`` (default) hardlinks media files when the filesystem allows it,
    falling back to a copy; ``copy`` always copies."""
    mode = os.environ.get("STUDIO_LINK_MODE", "link").strip().lower()
    return mode if mode in ("link", "copy") else "link"
