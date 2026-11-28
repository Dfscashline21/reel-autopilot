"""Web app: login, setup wizard, home dashboard. Run: uvicorn app.main:app"""
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import blotato, builder, media, runner, songs, store, zernio

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
store.DATA_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(_app):
    media.start_worker()
    runner.start()
    yield


app = FastAPI(title="Reel Autopilot", docs_url=None, redoc_url=None, lifespan=lifespan)
_preview = {"running": False, "error": None}
OPEN_PATHS = {"/login", "/health", "/static/style.css"}


def password():
    return (os.environ.get("APP_PASSWORD") or "").strip()


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path in OPEN_PATHS or request.session.get("ok"):
        return await call_next(request)
    return RedirectResponse("/login", status_code=303)


# Added after require_login so it wraps it (the last middleware added runs first).
app.add_middleware(SessionMiddleware, secret_key=store.secret_key(), max_age=60 * 60 * 24 * 30,
                   https_only=os.environ.get("COOKIE_SECURE", "1") == "1", same_site="lax")


def back(anchor="", msg=""):
    q = f"?msg={quote(msg)}" if msg else ""
    return RedirectResponse(f"/setup{q}{('#' + anchor) if anchor else ''}", status_code=303)


# ── login ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/static/style.css")
def css():
    return FileResponse(BASE / "static" / "style.css", media_type="text/css")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "login.html", {"error": error, "configured": bool(password())})


@app.post("/login")
def login(request: Request, pw: str = Form("")):
    if password() and hmac.compare_digest(pw.strip(), password()):
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── files (thumbnails, previews, reels) ───────────────────────────────────────

@app.get("/files/{rel:path}")
def files(rel: str):
    p = (store.DATA_DIR / rel).resolve()
    allowed = [store.DATA_DIR / d for d in ("source/thumbs", "previews", "batches")]
    if not any(str(p).startswith(str(a.resolve())) for a in allowed) or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    # Revalidate every time: previews and reels are rebuilt under the same names.
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


# ── home ──────────────────────────────────────────────────────────────────────

def upcoming(settings, limit=15):
    tz = ZoneInfo(settings["timezone"])
    now = datetime.now(tz)
    rows = []
    root = store.DATA_DIR / "batches"
    if root.exists():
        for d in sorted(p.name for p in root.iterdir() if p.is_dir()):
            for r in builder.plan(datetime.fromisoformat(d).date()) or []:
                local, _ = runner.reel_when(r, tz)
                if local < now.replace(hour=0, minute=0, second=0):
                    continue
                rows.append({**r, "when": local, "past": local < now, "day": d})
    return rows[:limit]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    s = store.settings()
    if not s.get("active"):
        return RedirectResponse("/setup", status_code=303)
    unused, per_day = builder.low_footage(s)
    sets, needed = builder.sets_needed(s)
    return templates.TemplateResponse(request, "home.html", {
        "sets": sets, "videos_needed": needed, "videos_ready": len(builder._ready_clips()),
        "s": s, "rows": upcoming(s), "events": store.events(25), "names": blotato.NAMES,
        "failed": runner.failed_reels(s), "songs": songs.songs(),
        "tomorrow": (datetime.now(ZoneInfo(s["timezone"])) + timedelta(days=1)).date().isoformat(),
        "unused": unused, "per_day": per_day, "days_left": (unused // per_day) if per_day else 0,
        "disk": runner.disk_free_gb()})


@app.post("/pause")
def pause():
    store.save_settings(paused=True)
    store.event("info", "Paused. Reels already handed to Blotato will still post.")
    return RedirectResponse("/", status_code=303)


@app.post("/resume")
def resume():
    store.save_settings(paused=False)
    store.event("info", "Resumed.")
    threading.Thread(target=runner.tick, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.post("/run-now")
def run_now():
    threading.Thread(target=runner.tick, daemon=True).start()
    store.event("info", "Running the autopilot now.")
    return RedirectResponse("/", status_code=303)


@app.post("/reels/restyle")
def restyle(which: str = Form("all")):
    limit = 1 if which == "next" else None
    threading.Thread(target=runner.restyle_now, kwargs={"limit": limit}, daemon=True).start()
    store.event("info", "Remaking the next upcoming reel in the new style." if limit else
                        "Remaking upcoming reels in the new style. This can take a few minutes.")
    return RedirectResponse("/", status_code=303)


@app.post("/reels/clear")
def clear_reels(platform: str = Form(""), mode: str = Form("remove")):
    if platform not in blotato.NAMES:
        return RedirectResponse("/", status_code=303)
    resend = mode == "resend"
    threading.Thread(target=runner.clear_now, args=(platform,), kwargs={"resend": resend}, daemon=True).start()
    store.event("info", f"Clearing upcoming {blotato.NAMES[platform]} reels"
                        + (" to send them again." if resend else "."))
    return RedirectResponse("/", status_code=303)


@app.post("/zernio/check-failed")
def check_failed():
    s = store.settings()
    if not s.get("zernio_api_key"):
        store.event("warn", "Connect Zernio first (setup step 1).")
        return RedirectResponse("/#failed", status_code=303)
    try:
        n = runner.sync_failed(s)
        store.event("info", f"Checked Zernio: {n} newly failed post(s)." if n else "Checked Zernio: no new failed posts.")
    except Exception as e:  # noqa: BLE001
        store.event("error", f"Couldn't check Zernio: {str(e)[:200]}")
    return RedirectResponse("/#failed", status_code=303)


@app.post("/reel/{day}/{n}/repost")
def repost_reel(day: str, n: int, platform: str = Form(...), new_date: str = Form(...),
                new_time: str = Form(...), song: str = Form("random")):
    s = store.settings()
    key = "ig" if platform == "instagram" else "tt"
    choice = song
    if song not in ("random", "none"):
        choice = next((x[key] for x in songs.songs() if x.get(key) and x[key]["id"] == song), "random")
    try:
        runner.repost(s, datetime.fromisoformat(day).date(), n, platform,
                      datetime.fromisoformat(new_date).date(), new_time[:5], choice)
    except (ValueError, OSError) as e:
        store.event("error", f"Couldn't repost: {e}")
    return RedirectResponse("/#failed", status_code=303)


@app.post("/reel/{day}/{n}/queue")
def queue_reel(day: str, n: int, platform: str = Form(...)):
    try:
        runner.queue_repost(store.settings(), datetime.fromisoformat(day).date(), n, platform)
    except (ValueError, OSError) as e:
        store.event("error", f"Couldn't add to the queue: {e}")
    return RedirectResponse("/#failed", status_code=303)


@app.post("/failed/queue-all")
def queue_all_failed():
    s = store.settings()
    done = 0
    for f in runner.failed_reels(s):
        try:
            runner.queue_repost(s, datetime.fromisoformat(f["day"]).date(), f["n"], f["platform"])
            done += 1
        except (ValueError, OSError) as e:
            store.event("error", f"Couldn't add \"{f['hook'][:40]}\" to the queue: {e}")
    store.event("info", f"Added {done} failed post(s) to the back of the queue.")
    return RedirectResponse("/#failed", status_code=303)


@app.post("/reel/{day}/{n}/skip")
def skip(day: str, n: int):
    d = datetime.fromisoformat(day).date()
    items = builder.plan(d) or []
    for r in items:
        if r["n"] == n:
            if any(st["status"] == "scheduled" for st in r["platforms"].values()):
                store.event("warn", f"\"{r['hook'][:40]}\" is already scheduled. To stop it there, use "
                                    "Clear scheduled reels for that platform, or remove it in Zernio or Blotato.")
            r["skipped"] = True
            for p, st in r["platforms"].items():
                if st["status"] == "pending":
                    r["platforms"][p] = {"status": "skipped"}
    builder.save_plan(d, items)
    return RedirectResponse("/", status_code=303)


# ── setup ─────────────────────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
def setup(request: Request, msg: str = ""):
    s = store.settings()
    clips = store.clips()
    previews = []
    pdir = store.DATA_DIR / "previews"
    if pdir.exists():
        info = {r["file"]: r for r in (store.read_json("previews/plan.json", []) or [])}
        names = {c["id"]: c["name"] for c in store.clips()}
        for f in sorted(pdir.glob("reel_*.mp4")):
            r = info.get(f.name, {})
            previews.append({"file": f.name, "v": int(f.stat().st_mtime), "hook": r.get("hook", ""),
                             "video": names.get((r.get("clip") or "").split("@")[0], "")})
    hooks_text = format_hooks(store.hooks())
    return templates.TemplateResponse(request, "setup.html", {
        "s": s, "clips": clips, "windows": {c["id"]: len(media.windows(c, s)) for c in clips},
        "music": [p.name for p in media.music_tracks()], "hooks_text": hooks_text,
        "hook_count": len(store.hooks()), "previews": previews, "preview": _preview,
        "missing": runner.ready(s), "names": blotato.NAMES, "msg": msg,
        "songs": songs.songs(), "songs_text": songs.format_list(songs.songs()),
        "zones": sorted(z for z in available_timezones() if "/" in z and not z.startswith("Etc"))})


@app.post("/setup/blotato")
def setup_blotato(api_key: str = Form("")):
    key = api_key.strip() or store.settings()["blotato_api_key"]
    try:
        accts = blotato.Client(key).accounts()
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/setup?msg=Blotato said: {str(e)[:120]}#blotato", status_code=303)
    old = store.settings().get("accounts") or {}
    merged = {p: a for p, a in old.items() if a.get("via") == "zernio"}   # Zernio keeps its platforms
    for p, a in accts.items():
        if p in merged:
            continue
        a["via"] = "blotato"
        a["enabled"] = old.get(p, {}).get("enabled", p != "twitter")
        merged[p] = a
    store.save_settings(blotato_api_key=key, accounts=merged)
    store.event("info", f"Blotato connected: {', '.join(blotato.NAMES[p] for p in accts) or 'no accounts yet'}.")
    return back("blotato")


def _refresh_zernio(key):
    accts = zernio.Client(key).accounts()
    old = store.settings().get("accounts") or {}
    merged = {p: a for p, a in old.items() if p not in accts}
    for p, a in accts.items():
        a["enabled"] = old.get(p, {}).get("enabled", True)
        merged[p] = a
    store.save_settings(zernio_api_key=key, accounts=merged)
    return accts


@app.post("/setup/zernio")
def setup_zernio(api_key: str = Form("")):
    key = api_key.strip() or store.settings()["zernio_api_key"]
    try:
        accts = _refresh_zernio(key)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/setup?msg=Zernio said: {str(e)[:120]}#zernio", status_code=303)
    store.event("info", f"Zernio connected: {', '.join(blotato.NAMES[p] for p in accts) or 'no accounts yet'}. "
                        "Instagram and TikTok now post through Zernio.")
    if songs.songs():
        _check_songs_later()
    return back("zernio")


def _public_base(request: Request):
    host = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{host}" if host else str(request.base_url).rstrip("/")


@app.get("/setup/zernio/instagram-facebook")
def zernio_instagram_facebook(request: Request):
    """Send the person to Zernio to connect Instagram through Facebook Login (needed for music)."""
    s = store.settings()
    if not s.get("zernio_api_key"):
        return back("zernio", "Connect Zernio with your API key first.")
    ig = (s.get("accounts") or {}).get("instagram") or {}
    old = ig.get("id") if ig.get("via") == "zernio" else None
    try:
        url = zernio.Client(s["zernio_api_key"]).instagram_facebook_url(
            _public_base(request) + "/setup/zernio/return", ig.get("profile") or None, old)
    except Exception as e:  # noqa: BLE001
        return back("zernio", f"Zernio said: {str(e)[:150]}")
    return RedirectResponse(url, status_code=303)


@app.get("/setup/zernio/return")
def zernio_return():
    """Zernio sends the person back here after connecting; pick up the new account."""
    s = store.settings()
    try:
        accts = _refresh_zernio(s["zernio_api_key"])
    except Exception as e:  # noqa: BLE001
        return back("zernio", f"Zernio said: {str(e)[:150]}")
    ig = accts.get("instagram") or {}
    if ig.get("login") in ("facebook_login", ""):     # "" = Zernio didn't say; the song check will tell
        store.event("info", f"Instagram reconnected{(' · ' + ig['username']) if ig.get('username') else ''}.")
        if songs.songs():
            _check_songs_later()
        return back("songs", "Instagram reconnected. Checking your songs; refresh in a minute.")
    return back("zernio", "Instagram still isn't connected through Facebook. Make sure your Instagram is linked "
                          "to a Facebook Page, then try again.")


def _check_songs_later():
    def run():
        s = store.settings()
        try:
            items = songs.resolve(songs.songs(), zernio.Client(s["zernio_api_key"]), s.get("accounts") or {})
            songs.save(items)
            ig = sum(1 for x in items if x.get("ig"))
            tt = sum(1 for x in items if x.get("tt"))
            store.event("info", f"Songs checked: {ig} of {len(items)} found on Instagram, {tt} on TikTok.")
        except Exception as e:  # noqa: BLE001
            store.event("error", f"Checking songs failed: {str(e)[:200]}")
    threading.Thread(target=run, daemon=True).start()


@app.post("/setup/songs")
async def setup_songs(text: str = Form("")):
    old = {(x.get("link_id") or x["line"]): x for x in songs.songs()}
    items = songs.parse(text)
    for x in items:          # keep earlier lookups until the new check finishes
        prev = old.get(x.get("link_id") or x["line"])
        if prev:
            for k in ("ig", "ig_note", "tt", "tt_note"):
                if k in prev:
                    x[k] = prev[k]
    songs.save(items)
    if items and store.settings().get("zernio_api_key"):
        _check_songs_later()
        return back("songs", f"Saved {len(items)} songs. Checking them on Instagram and TikTok; refresh in a minute.")
    return back("songs", f"Saved {len(items)} songs.")


@app.post("/setup/songs/fallback")
def setup_song_fallback(fallback: str = Form("trending")):
    store.save_settings(song_fallback=fallback if fallback in ("trending", "none") else "trending")
    return back("songs")


@app.post("/setup/platforms")
async def setup_platforms(request: Request):
    form = await request.form()
    accts = store.settings().get("accounts") or {}
    for p in accts:
        accts[p]["enabled"] = form.get(f"on_{p}") == "1"
    trial = form.get("ig_trial", "off")
    store.save_settings(accounts=accts,
                        ig_trial=trial if trial in ("off", "MANUAL", "SS_PERFORMANCE") else "off")
    return back("blotato")


@app.post("/setup/clips")
def setup_clips(files: list[UploadFile] = File(...)):
    for f in files:
        if not f.filename:
            continue
        fd, tmp = tempfile.mkstemp(dir=store.dir_("source", "incoming"))
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(f.file, out, 4 * 1024 * 1024)
        media.add_clip(tmp, Path(f.filename).name)
    return back("clips")


@app.post("/clips/{cid}/delete")
def clip_delete(cid: str):
    media.remove_clip(cid)
    return back("clips")


@app.get("/clips/{cid}", response_class=HTMLResponse)
def clip_page(request: Request, cid: str):
    c = next((x for x in store.clips() if x["id"] == cid), None)
    if not c:
        return back("clips")
    w = media.window_seconds()
    starts = [round(i * w, 1) for i in range(int((c.get("duration") or 0) // w))]
    return templates.TemplateResponse(request, "clip.html", {"c": c, "starts": starts, "w": w})


@app.post("/clips/{cid}/windows")
async def clip_windows(request: Request, cid: str):
    form = await request.form()
    keep = {float(v) for v in form.getlist("keep")}
    items = store.clips()
    for c in items:
        if c["id"] == cid:
            w = media.window_seconds()
            starts = [round(i * w, 1) for i in range(int((c.get("duration") or 0) // w))]
            c["excluded"] = [s for s in starts if s not in keep]
    store.save_clips(items)
    return back("clips")


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FAV = {"fav", "favorite", "favourite", "\u2b50", "\u2b50\ufe0f"}


def _hook_item(hook, caption="", extras=(), dates=(), fav=False):
    """One hook: the text on screen, its caption, an optional tag, pinned dates and the favorite mark."""
    hook = hook.strip()
    if hook.startswith("\u2b50"):            # "⭐ hook text" marks a favorite too
        hook, fav = hook.lstrip("\u2b50\ufe0f ").strip(), True
    tag, dates = "", list(dates)
    for x in (e.strip() for e in extras):
        days = [d.strip() for d in x.split(",")]
        if not x:
            continue
        if all(_DATE.match(d) for d in days):
            dates += days
        elif x.lower() in _FAV:
            fav = True
        elif not tag:
            tag = x
    item = {"hook": hook, "caption": caption.strip(), "tag": tag}
    if dates:
        item["dates"] = sorted(set(dates))
    if fav:
        item["fav"] = True
    return item


def parse_hooks(text):
    """Hooks, one per line: hook | caption | tag, plus optional extra fields in any order:
    a date (2026-11-26, or several separated by commas) pins the hook to those days only,
    and "fav" or a star marks a favorite that comes around about twice as often."""
    text = text.strip()
    if text.startswith("["):
        data = json.loads(text)
        out = []
        for d in data:
            if not str(d.get("hook", "")).strip():
                continue
            dates = d.get("dates") or ([d["date"]] if d.get("date") else [])
            out.append(_hook_item(str(d.get("hook", "")), str(d.get("caption", "")), [str(d.get("tag", ""))],
                                  [str(x) for x in dates], bool(d.get("fav"))))
        return out
    out, seen = [], set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        item = _hook_item(parts[0], parts[1] if len(parts) > 1 else "", parts[2:])
        key = (item["hook"].lower(), tuple(item.get("dates", [])))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def format_hooks(items):
    """The saved hooks as text in the same format parse_hooks reads."""
    lines = []
    for h in items:
        extra = [h.get("tag", "")] + [",".join(h.get("dates", []))] + (["fav"] if h.get("fav") else [])
        fields = [h.get("hook", ""), h.get("caption", "")] + [x for x in extra if x]
        while len(fields) > 1 and not fields[-1]:
            fields.pop()
        lines.append(" | ".join(fields))
    return "\n".join(lines)


@app.post("/setup/hooks")
async def setup_hooks(text: str = Form(""), file: UploadFile | None = File(None)):
    if file is not None and file.filename:
        text = (await file.read()).decode("utf-8", "replace")
    try:
        items = parse_hooks(text)
    except (ValueError, AttributeError) as e:
        return RedirectResponse(f"/setup?msg=Could not read hooks: {str(e)[:80]}#hooks", status_code=303)
    store.write_json("hooks.json", items)
    return back("hooks")


@app.post("/setup/music")
def setup_music(files: list[UploadFile] = File(...)):
    for f in files:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(f.filename or "").name)
        if not name or Path(name).suffix.lower() not in (".mp3", ".m4a", ".wav", ".aac"):
            continue
        with open(store.path("source", "music", name), "wb") as out:
            shutil.copyfileobj(f.file, out)
    return back("music")


@app.post("/music/{name}/delete")
def music_delete(name: str):
    p = store.DATA_DIR / "source" / "music" / Path(name).name
    p.unlink(missing_ok=True)
    return back("music")


@app.post("/setup/schedule")
def setup_schedule(timezone: str = Form("America/Los_Angeles"), times: str = Form(""),
                   link: str = Form(""), hashtags: str = Form(""), tiktok_hashtags: str = Form(""),
                   banned_words: str = Form(""), notice: str = Form(""), youtube_daily_cap: int = Form(3),
                   reel_seconds: int = Form(13), days_ahead: int = Form(2),
                   reels_per_set: int = Form(3), video_rest_days: int = Form(7)):
    ts = sorted({t if len(t) == 5 else f"0{t}" for t in re.findall(r"\b\d{1,2}:\d{2}\b", times)})
    try:
        ZoneInfo(timezone)
    except Exception:  # noqa: BLE001
        timezone = "America/Los_Angeles"
    store.save_settings(timezone=timezone, times=ts, link=link.strip(), hashtags=hashtags.strip(),
                        tiktok_hashtags=tiktok_hashtags.strip(), banned_words=banned_words.strip(),
                        notice=notice.strip(), youtube_daily_cap=max(0, youtube_daily_cap),
                        reel_seconds=min(30, max(6, reel_seconds)), days_ahead=min(7, max(0, days_ahead)),
                        reels_per_set=min(10, max(1, reels_per_set)),
                        video_rest_days=min(60, max(0, video_rest_days)))
    return back("schedule")


@app.post("/setup/preview")
def setup_preview():
    if not _preview["running"]:
        def job():
            _preview.update(running=True, error=None)
            try:
                builder.build_previews(3)
            except Exception as e:  # noqa: BLE001
                _preview["error"] = str(e)[:300]
            finally:
                _preview["running"] = False
        threading.Thread(target=job, daemon=True).start()
    return back("preview")


@app.post("/start")
def start():
    s = store.settings()
    missing = runner.ready(s)
    if missing:
        return RedirectResponse(f"/setup?msg=Still needed: {', '.join(missing)}#start", status_code=303)
    store.save_settings(active=True, paused=False)
    store.event("info", "Autopilot started.")
    threading.Thread(target=runner.tick, daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.post("/stop")
def stop():
    store.save_settings(active=False)
    store.event("info", "Autopilot stopped. Reels already in Zernio or Blotato will still post.")
    return RedirectResponse("/setup", status_code=303)
