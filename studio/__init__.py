"""Studio: saved projects for self-host, stored in SQLite under STUDIO_DIR.

Wired from app.py with ``studio.setup(app, bridge)`` (self-host only) and
``studio.init()`` in the lifespan. See docs/plans/studio-roadmap.md.
"""
from .config import enabled
from .db import init
from .projects import JobBridge

__all__ = ["enabled", "init", "setup", "JobBridge"]


def setup(app, bridge: JobBridge):
    """Mount /api/studio on ``app`` and hand it the job system."""
    from .router import router, set_bridge
    set_bridge(bridge)
    app.include_router(router)
