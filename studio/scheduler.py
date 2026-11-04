"""Daily view snapshots, near the end of the day in the user's time zone.

One run per day at STUDIO_SNAPSHOT_AT (default 23:50) in STUDIO_TZ (default
Asia/Jakarta), the moment clipping programs usually tally a day's views. If the
API was down at that time, the missed run happens at the next start-up.
STUDIO_SNAPSHOTS=0 turns the schedule off (manual "refresh" still works).
"""
import asyncio
import json
import os
import re
import threading
from datetime import datetime, time as dtime, timedelta, timezone, tzinfo
from typing import Optional

from . import db, posts
from .models import Setting, now_iso

_LAST_RUN_KEY = "snapshots.last_run"
_task: Optional[asyncio.Task] = None
_publish_task: Optional[asyncio.Task] = None
PUBLISH_TICK_SECONDS = 30
_lock = threading.Lock()
_state = {"running": False, "last_summary": None}


def enabled() -> bool:
    return os.environ.get("STUDIO_SNAPSHOTS", "1").strip().lower() in ("1", "true", "yes", "on")


def zone() -> tzinfo:
    """STUDIO_TZ as an IANA name or a fixed offset like +07:00.

    Slim images may ship without tzdata; Jakarta has no DST, so falling back
    to UTC+7 is exact for the default."""
    name = os.environ.get("STUDIO_TZ", "Asia/Jakarta").strip() or "Asia/Jakarta"
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", name)
    if m:
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        return timezone(-delta if m.group(1) == "-" else delta)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError, missing tzdata
        return timezone(timedelta(hours=7), "UTC+07")


def run_at() -> dtime:
    raw = os.environ.get("STUDIO_SNAPSHOT_AT", "23:50").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return dtime(int(m.group(1)), int(m.group(2)))
    return dtime(23, 50)


def last_due(now: datetime, at: dtime, tz: tzinfo) -> datetime:
    """The most recent scheduled moment at or before ``now``."""
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    if candidate > local:
        candidate -= timedelta(days=1)
    return candidate


def next_due(now: datetime, at: dtime, tz: tzinfo) -> datetime:
    return last_due(now, at, tz) + timedelta(days=1)


def _read_last_run() -> Optional[datetime]:
    with db.session() as s:
        row = s.get(Setting, _LAST_RUN_KEY)
        if not row or not row.value:
            return None
        try:
            return datetime.fromisoformat(json.loads(row.value))
        except (ValueError, TypeError):
            return None


def _write_last_run(when: str):
    with db.session() as s:
        row = s.get(Setting, _LAST_RUN_KEY)
        if row is None:
            s.add(Setting(key=_LAST_RUN_KEY, value=json.dumps(when)))
        else:
            row.value = json.dumps(when)


def status() -> dict:
    last = _read_last_run()
    tz, at = zone(), run_at()
    now = datetime.now(timezone.utc)
    return {
        "enabled": enabled(),
        "running": _state["running"],
        "last_run": last.isoformat() if last else None,
        "next_run": next_due(now, at, tz).isoformat(),
        "time_zone": str(tz),
        "run_at": at.strftime("%H:%M"),
        "last_summary": _state["last_summary"],
    }


def run_once(post_ids=None) -> Optional[dict]:
    """Run a snapshot pass unless one is already going. Blocking."""
    if not _lock.acquire(blocking=False):
        return None
    _state["running"] = True
    try:
        summary = posts.run_snapshots(post_ids)
        if post_ids is None:
            _write_last_run(now_iso())
        _state["last_summary"] = summary
        return summary
    finally:
        _state["running"] = False
        _lock.release()


def trigger_background() -> bool:
    """Start a full pass on a thread; False when one is already running."""
    if _state["running"]:
        return False
    threading.Thread(target=run_once, name="studio-snapshots", daemon=True).start()
    return True


async def _loop():
    tz, at = zone(), run_at()
    while True:
        try:
            now = datetime.now(timezone.utc)
            due = last_due(now, at, tz)
            last = _read_last_run()
            if last is None or last < due:
                await asyncio.to_thread(run_once)
            wait = (next_due(datetime.now(timezone.utc), at, tz)
                    - datetime.now(timezone.utc)).total_seconds()
            # Wake at least every 5 minutes: a suspended laptop or a clock
            # change must not push the run to the next day.
            await asyncio.sleep(max(5.0, min(wait, 300.0)))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — keep the schedule alive
            print(f"⚠️ Studio snapshot loop: {type(e).__name__}: {e}")
            await asyncio.sleep(300)


async def _publish_loop():
    from . import publishing
    await asyncio.to_thread(publishing.recover_interrupted)
    while True:
        try:
            await asyncio.to_thread(publishing.tick)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — keep the queue alive
            print(f"⚠️ Studio publish loop: {type(e).__name__}: {e}")
        await asyncio.sleep(PUBLISH_TICK_SECONDS)


def kick_publisher():
    """Process due publish jobs now, off the request thread."""
    from . import publishing
    threading.Thread(target=publishing.tick, name="studio-publish", daemon=True).start()


def start():
    """Start the daily snapshot loop and the publish queue once per process
    (called from the app lifespan)."""
    global _task, _publish_task
    loop = asyncio.get_running_loop()
    if _publish_task is None or _publish_task.done():
        _publish_task = loop.create_task(_publish_loop())
    if enabled() and (_task is None or _task.done()):
        _task = loop.create_task(_loop())
