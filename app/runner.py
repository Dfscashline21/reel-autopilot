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
            sets, needed = builder.sets_needed(settings)
            ready = len(builder._ready_clips())
            if ready < needed:
                store.event("warn", f"{ready} videos ready; the rotation needs {needed} ({sets} sets a day, "
                                    f"each video resting {settings.get('video_rest_days', 7)} days). "
                                    "Some sets will be skipped until more videos are uploaded.")
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


def restyle(settings, limit=None, client=None, now_utc=None):
    """Remake upcoming reels made in an older text style, soonest first.

    Reels not yet in Blotato are just re-rendered. Reels already scheduled in Blotato are
    re-rendered, uploaded again, and the new video is swapped onto the scheduled post, so its
    time and caption stay the same. Reels within 15 minutes of posting are left alone.
    """
    tz = ZoneInfo(settings["timezone"])
    now_utc = now_utc or datetime.now(timezone.utc)
    soon = now_utc + timedelta(minutes=15)
    root = store.DATA_DIR / "batches"
    todo = []
    for d in sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        for r in builder.plan(day) or []:
            if r.get("skipped") or r.get("style", 1) >= builder.STYLE_VERSION:
                continue
            if reel_when(r, tz)[1] <= soon:
                continue
            todo.append((reel_when(r, tz)[1], day, r["n"]))
    todo.sort()
    if limit:
        todo = todo[:limit]
    if not todo:
        store.event("info", "No upcoming reels need remaking; they already use the current style.")
        return 0
    client = client or (blotato.Client(settings["blotato_api_key"]) if settings.get("blotato_api_key") else None)
    schedules = None
    done = 0
    for _, day, n in todo:
        items = builder.plan(day) or []
        r = next((x for x in items if x["n"] == n), None)
        if r is None:
            continue
        local = reel_when(r, tz)[0]
        label = f"{local:%a %-I:%M %p} \"{r['hook'][:40]}\""
        in_blotato = any(st.get("status") == "scheduled" for st in r["platforms"].values())
        try:
            if not in_blotato:
                builder.rerender(day, r, settings)
                builder.save_plan(day, items)
                done += 1
                store.event("build", f"Remade {label} in the new style.")
                continue
            if schedules is None:
                schedules = client.list_schedules()
            old_url = r.get("media_url")
            matches = [s for s in schedules
                       if old_url and old_url in ((s.get("draft") or {}).get("content") or {}).get("mediaUrls", [])]
            if not matches:
                store.event("warn", f"Couldn't find {label} among Blotato's scheduled posts, so it was left as is.")
                continue
            builder.rerender(day, r, settings)
            new_url = client.upload(str(builder.batch_dir(day) / r["file"]))
            failed = []
            for sch in matches:
                try:
                    client.replace_media(sch, old_url, new_url)
                except blotato.BlotatoError as e:
                    failed.append(str(e)[:120])
                time.sleep(1)
            r["media_url"] = new_url
            builder.save_plan(day, items)
            if failed:
                store.event("error", f"Remade {label}, but Blotato refused the swap on {len(failed)} of "
                                     f"{len(matches)} posts: {failed[0]}")
            else:
                done += 1
                store.event("scheduled", f"Remade {label} and swapped it into {len(matches)} scheduled "
                                         f"Blotato post(s).")
        except (blotato.BlotatoError, RuntimeError, OSError) as e:
            store.event("error", f"Couldn't remake {label}: {str(e)[:200]}")
    return done


def restyle_now(limit=None):
    """Run restyle without clashing with the autopilot's own tick."""
    with _tick_lock:
        try:
            restyle(store.settings(), limit=limit)
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Remaking reels failed: {str(e)[:250]}")


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
