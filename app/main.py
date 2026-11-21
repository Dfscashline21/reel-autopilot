"""Web app: login, setup wizard, home dashboard. Run: uvicorn app.main:app"""
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import blotato, builder, media, runner, store

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


def back(anchor=""):
    return RedirectResponse(f"/setup{('#' + anchor) if anchor else ''}", status_code=303)


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
    return FileResponse(p)


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
    return templates.TemplateResponse(request, "home.html", {
        "s": s, "rows": upcoming(s), "events": store.events(25), "names": blotato.NAMES,
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


@app.post("/reel/{day}/{n}/skip")
def skip(day: str, n: int):
    d = datetime.fromisoformat(day).date()
    items = builder.plan(d) or []
    for r in items:
        if r["n"] == n:
            if any(st["status"] == "scheduled" for st in r["platforms"].values()):
                store.event("warn", f"\"{r['hook'][:40]}\" is already in Blotato. To stop it, open Blotato: "
                                    "Posts, Scheduled, Move to drafts.")
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
        previews = sorted(p.name for p in pdir.glob("reel_*.mp4"))
    hooks_text = "\n".join(" | ".join(x for x in (h.get("hook", ""), h.get("caption", ""), h.get("tag", "")) if x)
                           for h in store.hooks())
    return templates.TemplateResponse(request, "setup.html", {
        "s": s, "clips": clips, "windows": {c["id"]: len(media.windows(c, s)) for c in clips},
        "music": [p.name for p in media.music_tracks()], "hooks_text": hooks_text,
        "hook_count": len(store.hooks()), "previews": previews, "preview": _preview,
        "missing": runner.ready(s), "names": blotato.NAMES, "msg": msg,
        "zones": sorted(z for z in available_timezones() if "/" in z and not z.startswith("Etc"))})


@app.post("/setup/blotato")
def setup_blotato(api_key: str = Form("")):
    key = api_key.strip() or store.settings()["blotato_api_key"]
    try:
        accts = blotato.Client(key).accounts()
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/setup?msg=Blotato said: {str(e)[:120]}#blotato", status_code=303)
    old = store.settings().get("accounts") or {}
    for p, a in accts.items():
        a["enabled"] = old.get(p, {}).get("enabled", p != "twitter")
    store.save_settings(blotato_api_key=key, accounts=accts)
    store.event("info", f"Blotato connected: {', '.join(blotato.NAMES[p] for p in accts) or 'no accounts yet'}.")
    return back("blotato")


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


def parse_hooks(text):
    text = text.strip()
    if text.startswith("["):
        data = json.loads(text)
        return [{"hook": str(d.get("hook", "")).strip(), "caption": str(d.get("caption", "")).strip(),
                 "tag": str(d.get("tag", "")).strip()} for d in data if str(d.get("hook", "")).strip()]
    out, seen = [], set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0] or parts[0].lower() in seen:
            continue
        seen.add(parts[0].lower())
        out.append({"hook": parts[0], "caption": parts[1] if len(parts) > 1 else "",
                    "tag": parts[2] if len(parts) > 2 else ""})
    return out


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
                   reel_seconds: int = Form(13), days_ahead: int = Form(2)):
    ts = sorted({t if len(t) == 5 else f"0{t}" for t in re.findall(r"\b\d{1,2}:\d{2}\b", times)})
    try:
        ZoneInfo(timezone)
    except Exception:  # noqa: BLE001
        timezone = "America/Los_Angeles"
    store.save_settings(timezone=timezone, times=ts, link=link.strip(), hashtags=hashtags.strip(),
                        tiktok_hashtags=tiktok_hashtags.strip(), banned_words=banned_words.strip(),
                        notice=notice.strip(), youtube_daily_cap=max(0, youtube_daily_cap),
                        reel_seconds=min(30, max(6, reel_seconds)), days_ahead=min(7, max(0, days_ahead)))
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
    store.event("info", "Autopilot stopped. Reels already in Blotato will still post.")
    return RedirectResponse("/setup", status_code=303)
