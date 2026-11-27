"""The autopilot: every few minutes, keep reels built ahead and hand due ones to Zernio or Blotato."""
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import blotato, builder, media, songs, store, zernio

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
    on = [a for a in (settings.get("accounts") or {}).values() if a.get("enabled")]
    if any(a.get("via", "blotato") == "blotato" for a in on) and not settings.get("blotato_api_key"):
        missing.append("Blotato API key")
    if any(a.get("via") == "zernio" for a in on) and not settings.get("zernio_api_key"):
        missing.append("Zernio API key")
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


NAMES = blotato.NAMES


def _clients(settings, client=None, zclient=None):
    """{"blotato": Client|None, "zernio": Client|None}; tests pass fakes."""
    return {"blotato": client or (blotato.Client(settings["blotato_api_key"])
                                  if settings.get("blotato_api_key") else None),
            "zernio": zclient or (zernio.Client(settings["zernio_api_key"])
                                  if settings.get("zernio_api_key") else None)}


def _url_key(via):
    return "zernio_url" if via == "zernio" else "media_url"


def schedule_due(settings, client=None, now_utc=None, zclient=None):
    """Upload + schedule every reel whose time is within the hand-off window.
    Instagram and TikTok connected through Zernio get a song from the list, with the
    video's own sound muted."""
    tz = ZoneInfo(settings["timezone"])
    now_utc = now_utc or datetime.now(timezone.utc)
    horizon = now_utc + timedelta(hours=float(settings.get("schedule_hours_ahead", 6)))
    accounts = settings.get("accounts") or {}
    clients = _clients(settings, client, zclient)
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
            for p in pending:
                acct = accounts.get(p) or {}
                if not acct.get("enabled"):
                    r["platforms"][p] = {"status": "off"}
                    changed = True
                    continue
                via = acct.get("via", "blotato")
                c = clients.get(via)
                if c is None:
                    r["platforms"][p] = {"status": "failed", "error": f"no {via.title()} API key"}
                    changed = True
                    continue
                uk = _url_key(via)
                if not r.get(uk):
                    try:
                        r[uk] = c.upload(str(builder.batch_dir(day) / r["file"]))
                        changed = True
                    except (blotato.BlotatoError, zernio.ZernioError) as e:
                        store.event("error", f"Upload to {via.title()} failed for \"{r['hook'][:40]}\": {e}")
                        continue          # stays pending; tried again next round
                    except OSError as e:
                        store.event("error", f"Could not read reel file: {e}")
                        break
                try:
                    if via == "zernio":
                        choice = r.get("song_choice")
                        if choice == "none":
                            song = None
                        elif isinstance(choice, dict):
                            song = choice              # picked when reposting
                        else:
                            song = songs.pick(p, settings, c, acct, day=r.get("local_date"), set_key=r.get("set"))
                        sid = c.schedule(p, acct, r, r[uk], when, settings, song)
                        st = {"status": "scheduled", "id": sid, "via": "zernio"}
                        if song:
                            st["song"] = " — ".join(x for x in (song.get("title"), song.get("artist")) if x) \
                                or "song from your link"
                        r["platforms"][p] = st
                    else:
                        sid = c.schedule(p, acct, r, r[uk], when, settings)
                        r["platforms"][p] = {"status": "scheduled", "id": sid}
                    done += 1
                except (blotato.BlotatoError, zernio.ZernioError) as e:
                    r["platforms"][p] = {"status": "failed", "error": str(e)}
                    store.event("error", f"{NAMES[p]} refused \"{r['hook'][:40]}\": {e}")
                changed = True
                builder.save_plan(day, items)
                time.sleep(1)
            ok = [NAMES[p] + (f" (♪ {st['song']})" if st.get("song") else "")
                  for p, st in r["platforms"].items() if st["status"] == "scheduled"]
            if ok and any(p in pending for p, st in r["platforms"].items() if st["status"] == "scheduled"):
                store.event("scheduled", f"{local:%a %-I:%M %p}: \"{r['hook'][:50]}\" on {', '.join(ok)}.")
        if changed:
            builder.save_plan(day, items)
    return done


def restyle(settings, limit=None, client=None, now_utc=None, zclient=None):
    """Remake upcoming reels made in an older text style, soonest first.

    Reels not yet handed off are just re-rendered. Reels already scheduled in Zernio or
    Blotato are re-rendered, uploaded again, and the new video is swapped onto the scheduled
    posts, so their time, caption and music stay the same. Reels within 15 minutes of posting are left alone.
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
    clients = _clients(settings, client, zclient)
    schedules = None
    done = 0
    for _, day, n in todo:
        items = builder.plan(day) or []
        r = next((x for x in items if x["n"] == n), None)
        if r is None:
            continue
        local = reel_when(r, tz)[0]
        label = f"{local:%a %-I:%M %p} \"{r['hook'][:40]}\""
        sched = {p: st for p, st in r["platforms"].items() if st.get("status") == "scheduled"}
        try:
            if not sched:
                builder.rerender(day, r, settings)
                r.pop("media_url", None)
                r.pop("zernio_url", None)
                builder.save_plan(day, items)
                done += 1
                store.event("build", f"Remade {label} in the new style.")
                continue
            z_posts = [st["id"] for st in sched.values() if st.get("via") == "zernio"]
            b_sched = any(st.get("via", "blotato") == "blotato" for st in sched.values())
            matches = []
            old_url = r.get("media_url")
            if b_sched:
                if clients["blotato"] is None:
                    raise blotato.BlotatoError("no Blotato API key")
                if schedules is None:
                    schedules = clients["blotato"].list_schedules()
                matches = [s for s in schedules
                           if old_url and old_url in ((s.get("draft") or {}).get("content") or {}).get("mediaUrls", [])]
            if not matches and not z_posts:
                store.event("warn", f"Couldn't find {label} among the scheduled posts, so it was left as is.")
                continue
            builder.rerender(day, r, settings)
            path = str(builder.batch_dir(day) / r["file"])
            failed, swapped = [], 0
            if matches:
                new_url = clients["blotato"].upload(path)
                for sch in matches:
                    try:
                        clients["blotato"].replace_media(sch, old_url, new_url)
                        swapped += 1
                    except blotato.BlotatoError as e:
                        failed.append(str(e)[:120])
                    time.sleep(1)
                r["media_url"] = new_url
            if z_posts:
                if clients["zernio"] is None:
                    raise zernio.ZernioError("no Zernio API key")
                new_z = clients["zernio"].upload(path)
                for pid in z_posts:
                    try:
                        clients["zernio"].replace_media(pid, new_z)
                        swapped += 1
                    except zernio.ZernioError as e:
                        failed.append(str(e)[:120])
                    time.sleep(1)
                r["zernio_url"] = new_z
            builder.save_plan(day, items)
            total = len(matches) + len(z_posts)
            if failed:
                store.event("error", f"Remade {label}, but the swap was refused on {len(failed)} of "
                                     f"{total} posts: {failed[0]}")
            else:
                done += 1
                store.event("scheduled", f"Remade {label} and swapped it into {total} scheduled post(s).")
        except (blotato.BlotatoError, zernio.ZernioError, RuntimeError, OSError) as e:
            store.event("error", f"Couldn't remake {label}: {str(e)[:200]}")
    return done


def clear_platform(settings, platform, resend=False, client=None, zclient=None, now_utc=None):
    """Take one platform's upcoming reels off the schedule.

    Posts already handed to Zernio or Blotato are cancelled there. With resend, the reels go
    back to pending, so the autopilot sends them again with the current setup (for example
    through Zernio with a song); without it they're marked cleared and won't post on that
    platform. Resend also brings back reels cleared earlier. Other platforms are untouched.
    Reels within 10 minutes of posting are left alone.
    Returns (cleared, failed)."""
    tz = ZoneInfo(settings["timezone"])
    now_utc = now_utc or datetime.now(timezone.utc)
    soon = now_utc + timedelta(minutes=10)
    clients = _clients(settings, client, zclient)
    root = store.DATA_DIR / "batches"
    schedules = None
    cleared, failed = 0, 0
    for d in sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        items = builder.plan(day) or []
        changed = False
        for r in items:
            st = r["platforms"].get(platform)
            ok = ("scheduled", "pending", "failed") + (("cleared",) if resend else ())
            if not st or r.get("skipped") or st.get("status") not in ok:
                continue
            if reel_when(r, tz)[1] <= soon:
                continue
            label = f"{reel_when(r, tz)[0]:%a %-I:%M %p} \"{r['hook'][:40]}\""
            if st["status"] == "scheduled":
                try:
                    if st.get("via") == "zernio":
                        if clients["zernio"] is None:
                            raise zernio.ZernioError("no Zernio API key")
                        clients["zernio"].delete_post(st["id"])
                    else:
                        if clients["blotato"] is None:
                            raise blotato.BlotatoError("no Blotato API key")
                        if schedules is None:
                            schedules = clients["blotato"].list_schedules()
                        url = r.get("media_url")
                        found = [x for x in schedules
                                 if url and url in ((x.get("draft") or {}).get("content") or {}).get("mediaUrls", [])
                                 and platform in (((x.get("draft") or {}).get("content") or {}).get("platform"),
                                                  ((x.get("draft") or {}).get("target") or {}).get("targetType"))]
                        if not found:
                            raise blotato.BlotatoError("couldn't find it among Blotato's scheduled posts")
                        for x in found:
                            clients["blotato"].delete_schedule(x["id"])
                    time.sleep(0.5)
                except (blotato.BlotatoError, zernio.ZernioError) as e:
                    failed += 1
                    store.event("error", f"Couldn't clear {NAMES[platform]} for {label}: {str(e)[:150]}")
                    continue
            if resend:
                r["platforms"][platform] = {"status": "pending"}
                # a new hand-off uploads again if it moves to the other service
            else:
                r["platforms"][platform] = {"status": "cleared"}
            cleared += 1
            changed = True
        if changed:
            builder.save_plan(day, items)
    what = "cleared and will be sent again with the current setup" if resend else "cleared"
    store.event("warn" if failed else "info",
                f"{NAMES[platform]}: {cleared} upcoming reel(s) {what}" + (f"; {failed} couldn't be cleared." if failed else "."))
    return cleared, failed


def clear_now(platform, resend=False):
    with _tick_lock:
        try:
            clear_platform(store.settings(), platform, resend=resend)
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Clearing {platform} failed: {str(e)[:250]}")


def restyle_now(limit=None):
    """Run restyle without clashing with the autopilot's own tick."""
    with _tick_lock:
        try:
            restyle(store.settings(), limit=limit)
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Remaking reels failed: {str(e)[:250]}")


def _all_reels():
    root = store.DATA_DIR / "batches"
    for d in sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        try:
            day = date.fromisoformat(d)
        except ValueError:
            continue
        yield day, builder.plan(day) or []


def sync_failed(settings, zclient=None):
    """Ask Zernio which posts failed and mark those reels failed here, with Zernio's reason.
    Returns how many reels were newly marked."""
    c = zclient or (zernio.Client(settings["zernio_api_key"]) if settings.get("zernio_api_key") else None)
    if c is None:
        return 0
    failed = {}
    for post in c.failed_posts():
        plats = post.get("platforms") or []
        bad = [x for x in plats if x.get("status") == "failed"] or (plats if post.get("status") == "failed" else [])
        for x in bad:
            msg = x.get("errorMessage") or post.get("errorMessage") or post.get("error") or "failed in Zernio"
            failed[(str(post.get("_id")), x.get("platform"))] = str(msg)
        if not plats and post.get("status") == "failed":
            failed[(str(post.get("_id")), None)] = str(post.get("errorMessage") or "failed in Zernio")
    if not failed:
        return 0
    marked = 0
    for day, items in _all_reels():
        changed = False
        for r in items:
            for p, st in r["platforms"].items():
                if st.get("via") != "zernio" or st.get("status") != "scheduled":
                    continue
                msg = failed.get((str(st.get("id")), p)) or failed.get((str(st.get("id")), None))
                if msg:
                    st["status"], st["error"] = "failed", msg
                    marked += 1
                    changed = True
                    store.event("error", f"{NAMES[p]} post failed in Zernio for \"{r['hook'][:40]}\": {msg[:150]}")
        if changed:
            builder.save_plan(day, items)
    return marked


def failed_reels(settings):
    """Reels whose post failed or was missed on some platform and whose video is still here."""
    tz = ZoneInfo(settings["timezone"])
    rows = []
    for day, items in _all_reels():
        for r in items:
            if not (builder.batch_dir(day) / r["file"]).exists():
                continue
            for p, st in r["platforms"].items():
                if st.get("status") in ("failed", "missed"):
                    rows.append({"day": day.isoformat(), "n": r["n"], "hook": r["hook"], "file": r["file"],
                                 "thumb": r.get("thumb"), "platform": p, "status": st["status"],
                                 "error": st.get("error", ""), "song": st.get("song", ""),
                                 "when": reel_when(r, tz)[0], "time": r["local_time"]})
    return rows


def repost(settings, day, n, platform, new_date, new_time, song_choice="random", now_utc=None):
    """Copy a failed reel to another day and time, on that one platform, optionally with a
    chosen song ("random", "none" or a song from the list). Returns the new reel."""
    tz = ZoneInfo(settings["timezone"])
    now_utc = now_utc or datetime.now(timezone.utc)
    when = datetime.fromisoformat(f"{new_date.isoformat()}T{new_time}").replace(tzinfo=tz)
    if when.astimezone(timezone.utc) < now_utc + timedelta(minutes=15):
        raise ValueError("Pick a time at least 15 minutes from now.")
    items = builder.plan(day) or []
    r = next((x for x in items if x["n"] == n), None)
    if r is None or platform not in r["platforms"]:
        raise ValueError("That reel isn't here any more.")
    src = builder.batch_dir(day)
    if not (src / r["file"]).exists():
        raise ValueError("That reel's video has already been cleaned up.")
    target = items if new_date == day else (builder.plan(new_date) or [])
    n2 = max([x["n"] for x in target] + [0, n if new_date == day else 0]) + 1
    out = builder.batch_dir(new_date)
    out.mkdir(parents=True, exist_ok=True)
    fname, thumb = f"repost_{n2:02d}.mp4", f"repost_{n2:02d}.jpg"
    shutil.copy2(src / r["file"], out / fname)
    if r.get("thumb") and (src / r["thumb"]).exists():
        shutil.copy2(src / r["thumb"], out / thumb)
    new = {k: v for k, v in r.items() if k not in ("media_url", "zernio_url")}
    new.update({"n": n2, "file": fname, "thumb": thumb, "local_date": new_date.isoformat(), "local_time": new_time,
                "set": f"repost-{n2}", "repost": True, "repost_of": f"{day.isoformat()}#{n}", "skipped": False,
                "platforms": {platform: {"status": "pending"}}})
    if song_choice == "none":
        new["song_choice"] = "none"
    elif isinstance(song_choice, dict):
        new["song_choice"] = song_choice
    r["platforms"][platform] = dict(r["platforms"][platform], status="reposted",
                                    reposted_to=f"{new_date.isoformat()} {new_time}")
    target.append(new)
    target.sort(key=lambda x: x["local_time"])
    builder.save_plan(new_date, target)
    if new_date != day:
        builder.save_plan(day, items)
    store.event("info", f"Reposting \"{r['hook'][:40]}\" on {NAMES[platform]} {when:%a %b %-d, %-I:%M %p}"
                        + (f" with {song_choice.get('title') or 'the chosen song'}." if isinstance(song_choice, dict)
                           else " with no song." if song_choice == "none" else " with a random song from your list."))
    return new


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
        if s.get("zernio_api_key"):
            try:
                sync_failed(s)
            except Exception as e:  # noqa: BLE001
                store.event("warn", f"Couldn't check Zernio for failed posts: {str(e)[:200]}")
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
