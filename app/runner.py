"""The autopilot: every few minutes, keep reels built ahead and hand due ones to Blotato."""
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import blotato, builder, media, store

TICK_SECONDS = 300
LEAD = timedelta(minutes=5)
KEEP_DAYS = 7
_tick_lock = threading.Lock()


def reel_when(reel, tz):
    local = datetime.fromisoformat(f"{reel['local_date']}T{reel['local_time']}").replace(tzinfo=tz)
    return local, local.astimezone(timezone.utc)


def ready(settings):
    """What's missing before Start can work (empty list = ready)."""
    missing = []
    if not settings.get("blotato_api_key"):
        missing.append("Blotato API key")
    if not any(a.get("enabled") for a in (settings.get("accounts") or {}).values()):
        missing.append("at least one connected platform")
    if not any(c.get("status") == "ready" for c in store.clips()):
        missing.append("at least one processed clip")
    if not store.hooks():
        missing.append("hooks")
    if not settings.get("times"):
        missing.append("posting times")
    return missing


def build_ahead(settings, now=None):
    tz = ZoneInfo(settings["timezone"])
    now = now or datetime.now(tz)
    built = []
    for k in range(int(settings.get("days_ahead", 2)) + 1):
        day = now.date() + timedelta(days=k)
        unused, per_day = builder.low_footage(settings)
        items = builder.build_day(day, settings, now=now)   # only slots the day is missing
        if not items:
            if builder.plan(day) is None:
                builder.save_plan(day, [])
            continue
        if items:
            built.append(day)
            store.event("build", f"Built {len(items)} reels for {day:%a %b %-d}.")
            if unused < per_day * 3:
                store.event("warn", f"Footage is running low: {unused} unused windows left. "
                                    "Upload new clips soon; older footage is being reused with new looks.")
    return built


def schedule_due(settings, client=None, now_utc=None):
    """Upload + schedule every reel whose time is within the hand-off window."""
    tz = ZoneInfo(settings["timezone"])
    now_utc = now_utc or datetime.now(timezone.utc)
    horizon = now_utc + timedelta(hours=float(settings.get("schedule_hours_ahead", 6)))
    accounts = settings.get("accounts") or {}
    client = client or blotato.Client(settings["blotato_api_key"])
    done = 0
    root = store.DATA_DIR / "batches"
    if not root.exists():
        return 0
    for d in sorted(p.name for p in root.iterdir() if p.is_dir()):
        day = date.fromisoformat(d)
        items = builder.plan(day)
        if not items:
            continue
        changed = False
        for r in items:
            pending = [p for p, st in r["platforms"].items() if st["status"] == "pending"]
            if r.get("skipped") or not pending:
                continue
            local, when = reel_when(r, tz)
            if when < now_utc + LEAD:
                for p in pending:
                    r["platforms"][p] = {"status": "missed", "error": "its time passed before it could be scheduled"}
                store.event("warn", f"Missed {local:%a %-I:%M %p}: \"{r['hook'][:50]}\".")
                changed = True
                continue
            if when > horizon:
                continue
            try:
                if not r.get("media_url"):
                    r["media_url"] = client.upload(str(builder.batch_dir(day) / r["file"]))
                    changed = True
                for p in pending:
                    acct = accounts.get(p) or {}
                    if not acct.get("enabled"):
                        r["platforms"][p] = {"status": "off"}
                        continue
                    try:
                        sid = client.schedule(p, acct, r, r["media_url"], when, settings)
                        r["platforms"][p] = {"status": "scheduled", "id": sid}
                        done += 1
                    except blotato.BlotatoError as e:
                        r["platforms"][p] = {"status": "failed", "error": str(e)}
                        store.event("error", f"{blotato.NAMES[p]} refused \"{r['hook'][:40]}\": {e}")
                    changed = True
                    builder.save_plan(day, items)
                    time.sleep(1)
                ok = [blotato.NAMES[p] for p, st in r["platforms"].items() if st["status"] == "scheduled"]
                if ok:
                    store.event("scheduled", f"{local:%a %-I:%M %p}: \"{r['hook'][:50]}\" on {', '.join(ok)}.")
            except blotato.BlotatoError as e:
                store.event("error", f"Upload failed for \"{r['hook'][:40]}\": {e}")
            except OSError as e:
                store.event("error", f"Could not read reel file: {e}")
        if changed:
            builder.save_plan(day, items)
    return done


def cleanup(settings, now=None):
    tz = ZoneInfo(settings["timezone"])
    today = (now or datetime.now(tz)).date()
    root = store.DATA_DIR / "batches"
    if not root.exists():
        return
    for p in root.iterdir():
        try:
            old = date.fromisoformat(p.name) < today - timedelta(days=KEEP_DAYS)
        except ValueError:
            continue
        if old:
            for f in p.glob("*.mp4"):
                f.unlink()


def tick():
    if not _tick_lock.acquire(blocking=False):
        return
    try:
        s = store.settings()
        if not s.get("active") or s.get("paused"):
            return
        if ready(s):
            return
        try:
            build_ahead(s)
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Build failed: {str(e)[:250]}")
        try:
            schedule_due(s)
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Scheduling failed: {str(e)[:250]}")
        cleanup(s)
    finally:
        _tick_lock.release()


def _loop():
    time.sleep(10)
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Autopilot error: {str(e)[:250]}")
        time.sleep(TICK_SECONDS)


def start():
    threading.Thread(target=_loop, daemon=True, name="autopilot").start()


def disk_free_gb():
    try:
        return round(shutil.disk_usage(store.DATA_DIR).free / 1e9, 1)
    except OSError:
        return None
