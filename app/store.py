"""Everything the app keeps lives under DATA_DIR (a Railway volume in production).

    data/
      settings.json          what the person entered in setup
      hooks.json             [{"hook", "caption", "tag"}]
      clips.json             normalized clips + excluded windows
      history.json           footage windows already used
      events.json            recent activity shown on the home page
      secret.key             signs the login cookie
      source/raw/            uploads as received
      source/clips/          normalized 1080x1920 clips
      source/music/          music tracks
      batches/YYYY-MM-DD/    reel_01.mp4 ..., thumbs, plan.json
      previews/              sample reels from setup
"""
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_lock = threading.RLock()

DEFAULT_SETTINGS = {
    "blotato_api_key": "",
    "accounts": {},            # platform -> {"id", "username", "enabled", "page_id"}
    "timezone": "America/Los_Angeles",
    "times": ["15:00", "15:20", "15:40", "16:00", "16:20"],
    "link": "",
    "hashtags": "",            # added to every Instagram/Facebook/YouTube caption
    "tiktok_hashtags": "",     # TikTok gets its own (plain) tags
    "banned_words": "",        # comma separated; removed from every caption
    "notice": "",              # e.g. "Not medical advice." added to captions (not TikTok)
    "youtube_daily_cap": 3,
    "reel_seconds": 13,        # target length of a finished reel
    "days_ahead": 2,           # how many days of reels to keep built
    "schedule_hours_ahead": 6, # hand a reel to Blotato this long before it posts
    "active": False,           # set by the Start button
    "paused": False,
}


def path(*parts) -> Path:
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def dir_(*parts) -> Path:
    p = DATA_DIR.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(name, default):
    p = DATA_DIR / name
    with _lock:
        try:
            return json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default


def write_json(name, obj):
    p = path(name)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=1)
        os.replace(tmp, p)


def settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(read_json("settings.json", {}))
    # A key set on the host wins over nothing; the setup page can still change it.
    if not s["blotato_api_key"] and os.environ.get("BLOTATO_API_KEY"):
        s["blotato_api_key"] = os.environ["BLOTATO_API_KEY"].strip()
    return s


def save_settings(**changes) -> dict:
    with _lock:
        s = read_json("settings.json", {})
        s.update(changes)
        write_json("settings.json", s)
    return settings()


def hooks() -> list:
    return read_json("hooks.json", [])


def clips() -> list:
    return read_json("clips.json", [])


def save_clips(items):
    write_json("clips.json", items)


def secret_key() -> str:
    p = path("secret.key")
    if not p.exists():
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip()


def event(kind: str, text: str):
    """Add a line to the activity feed (newest first, last 200 kept)."""
    with _lock:
        ev = read_json("events.json", [])
        ev.insert(0, {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "kind": kind, "text": text})
        write_json("events.json", ev[:200])
    print(f"[{kind}] {text}", flush=True)


def events(n=30) -> list:
    return read_json("events.json", [])[:n]
