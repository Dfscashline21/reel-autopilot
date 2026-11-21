import os
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="ra-test-")
os.environ["APP_PASSWORD"] = "pw"
os.environ["COOKIE_SECURE"] = "0"

from app import builder, captions, media, runner, store  # noqa: E402

TZ = "America/Los_Angeles"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store.save_settings(timezone=TZ, times=["15:00", "15:20"], reel_seconds=6,
                        accounts={"instagram": {"id": "1", "enabled": True},
                                  "youtube": {"id": "2", "enabled": True},
                                  "tiktok": {"id": "3", "enabled": False}},
                        blotato_api_key="k", youtube_daily_cap=1)
    yield


def make_clip(cid, seconds=24):
    out = store.path("source", "clips", f"{cid}.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=540x960:rate=30:duration={seconds}", "-vf", "scale=1080:1920",
                    "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    items = store.clips()
    items.append({"id": cid, "name": cid, "raw": "", "status": "ready", "duration": seconds, "excluded": []})
    store.save_clips(items)


# ── captions ──────────────────────────────────────────────────────────────────

def test_captions_per_platform():
    s = store.save_settings(link="https://x.co", notice="Not medical advice.", hashtags="#a #b #c #d #e #f",
                            tiktok_hashtags="#t1 #t2", banned_words="guaranteed")
    r = {"hook": "Hi", "caption": "Guaranteed results, see https://x.co/p"}
    ig = captions.for_platform("instagram", r, s)
    assert ig.count("#") == 5 and "Not medical advice." in ig and "Link in bio." in ig
    assert "guaranteed" not in ig.lower()
    tt = captions.for_platform("tiktok", r, s)
    assert "http" not in tt and "Not medical" not in tt and "#t1 #t2" in tt
    assert captions.for_platform("youtube", r, s).endswith("#shorts")


# ── footage ───────────────────────────────────────────────────────────────────

def test_windows_do_not_overlap_and_respect_excludes():
    s = store.settings()
    w = media.window_seconds(s)
    c = {"id": "clip01", "duration": 60, "excluded": [round(w, 1)]}
    starts = media.windows(c, s)
    assert round(w, 1) not in starts
    assert all(b - a >= w - 0.01 for a, b in zip(starts, starts[1:]))


def test_pick_windows_prefers_unused_and_spreads_clips():
    make_clip("clip01", 30)
    make_clip("clip02", 30)
    h = {"counter": 5, "windows": {"clip01@0.0": {"last": 5, "variants": [1]}}, "hooks": {}}
    picked = builder.pick_windows(2, store.settings(), h)
    keys = [p[0] for p in picked]
    assert "clip01@0.0" not in keys
    assert {p[1] for p in picked} == {"clip01", "clip02"}


def test_reused_window_gets_a_new_variant():
    h = {"windows": {"clip01@0.0": {"last": 1, "variants": [0, 11]}}}
    v = builder.pick_variant("clip01@0.0", 0, h)
    assert v not in (0, 11)


# ── build + schedule ──────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self):
        self.posts, self.uploads = [], 0

    def upload(self, path):
        self.uploads += 1
        assert os.path.getsize(path) > 1000
        return "https://cdn/x.mp4"

    def schedule(self, platform, account, reel, url, when, settings):
        self.posts.append((platform, reel["n"], when))
        return f"sub-{len(self.posts)}"


def test_build_day_and_schedule_once():
    make_clip("clip01", 40)
    store.write_json("hooks.json", [{"hook": "First hook", "caption": "c1"}, {"hook": "Second hook", "caption": "c2"}])
    tz = ZoneInfo(TZ)
    day = date(2030, 1, 7)
    now = datetime(2030, 1, 7, 9, 0, tzinfo=tz)
    items = builder.build_day(day, store.settings(), now=now)
    assert len(items) == 2
    assert len({i["clip"] for i in items}) == 2                 # no shared footage
    assert {i["hook"] for i in items} == {"First hook", "Second hook"}
    assert items[0]["platforms"] == {"instagram": {"status": "pending"}, "youtube": {"status": "pending"}}
    assert items[1]["platforms"]["youtube"]["status"] == "off"  # YouTube cap of 1
    assert (builder.batch_dir(day) / "reel_01.mp4").exists()
    hist = store.read_json("history.json", {})
    assert len(hist["windows"]) == 2

    fake = FakeClient()
    s = store.settings()
    # 10:00 local: 15:00 is 5 h away (inside the 6 h window), 15:20 is too.
    n = runner.schedule_due(s, client=fake, now_utc=datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc))
    assert n == 3 and fake.uploads == 2
    # Re-running schedules nothing twice.
    assert runner.schedule_due(s, client=fake, now_utc=datetime(2030, 1, 7, 10, 30, tzinfo=tz).astimezone(timezone.utc)) == 0
    plan = builder.plan(day)
    assert all(st["status"] in ("scheduled", "off") for r in plan for st in r["platforms"].values())


def test_out_of_window_waits_and_passed_is_missed():
    make_clip("clip01", 40)
    store.write_json("hooks.json", [{"hook": "H1"}, {"hook": "H2"}])
    tz = ZoneInfo(TZ)
    day = date(2030, 1, 8)
    builder.build_day(day, store.settings(), now=datetime(2030, 1, 8, 6, 0, tzinfo=tz))
    fake = FakeClient()
    assert runner.schedule_due(store.settings(), client=fake,
                               now_utc=datetime(2030, 1, 8, 7, 0, tzinfo=tz).astimezone(timezone.utc)) == 0
    runner.schedule_due(store.settings(), client=fake,
                        now_utc=datetime(2030, 1, 8, 15, 10, tzinfo=tz).astimezone(timezone.utc))
    plan = builder.plan(day)
    assert plan[0]["platforms"]["instagram"]["status"] == "missed"
    assert plan[1]["platforms"]["instagram"]["status"] == "scheduled"


def test_build_skips_times_already_gone():
    make_clip("clip01", 40)
    store.write_json("hooks.json", [{"hook": "H1"}])
    tz = ZoneInfo(TZ)
    items = builder.build_day(date(2030, 1, 9), store.settings(), now=datetime(2030, 1, 9, 15, 5, tzinfo=tz))
    assert [i["local_time"] for i in items] == ["15:20"]


def test_ready_lists_what_is_missing():
    store.save_settings(accounts={})
    missing = runner.ready(store.settings())
    assert "at least one connected platform" in missing and "hooks" in missing


# ── web ───────────────────────────────────────────────────────────────────────

def test_login_and_pages():
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as c:
        assert c.get("/setup", follow_redirects=False).status_code == 303
        assert c.post("/login", data={"pw": "nope"}, follow_redirects=False).headers["location"].startswith("/login")
        c.post("/login", data={"pw": "pw"})
        r = c.get("/setup")
        assert r.status_code == 200 and "Connect Blotato" in r.text
        c.post("/setup/hooks", data={"text": "Hook one | Cap one | comment\nHook two\nhook TWO"})
        assert [h["hook"] for h in store.hooks()] == ["Hook one", "Hook two"]
        c.post("/setup/schedule", data={"timezone": TZ, "times": "9:05, 15:00", "reel_seconds": 13})
        assert store.settings()["times"] == ["09:05", "15:00"]
        r = c.post("/start", follow_redirects=False)
        assert "processed%20clip" in r.headers["location"]          # no ready clip yet
        assert c.get("/files/../settings.json").status_code == 404
