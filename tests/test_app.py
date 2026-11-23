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
    assert len({i["clip"] for i in items}) == 2                 # different pieces of the video
    assert {i["hook"] for i in items} == {"First hook"}         # one set: same hook
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


def test_adding_a_time_builds_only_the_new_slot():
    make_clip("clip01", 60)
    store.write_json("hooks.json", [{"hook": "H1"}, {"hook": "H2"}, {"hook": "H3"}])
    tz = ZoneInfo(TZ)
    day = date(2030, 1, 10)
    now = datetime(2030, 1, 10, 9, 0, tzinfo=tz)
    first = builder.build_day(day, store.settings(), now=now)
    assert [r["local_time"] for r in first] == ["15:00", "15:20"]
    again = builder.build_day(day, store.settings(), now=now)
    assert again == []                                   # nothing new: no rebuild
    store.save_settings(times=["12:00", "15:00", "15:20"])
    added = builder.build_day(day, store.settings(), now=now)
    assert [r["local_time"] for r in added] == ["12:00"]
    plan = builder.plan(day)
    assert [r["local_time"] for r in plan] == ["12:00", "15:00", "15:20"]
    assert len({r["file"] for r in plan}) == 3           # no file overwritten
    assert len({r["clip"] for r in plan}) == 3           # no shared footage


def test_instagram_trial_reel_setting():
    from app import blotato
    sent = []
    c = blotato.Client("k")
    c._req = lambda m, p, **kw: (sent.append(kw["json"]), {"postSubmissionId": "x"})[1]
    reel = {"hook": "H", "caption": "C", "tag": ""}
    when = datetime(2030, 1, 1, tzinfo=timezone.utc)
    s = dict(store.settings())
    c.schedule("instagram", {"id": "1"}, reel, "u", when, s)
    assert sent[-1]["post"]["target"] == {"targetType": "instagram", "mediaType": "reel", "shareToFeed": True}
    s["ig_trial"] = "SS_PERFORMANCE"
    c.schedule("instagram", {"id": "1"}, reel, "u", when, s)
    t = sent[-1]["post"]["target"]
    assert t["trial"] == {"graduationStrategy": "SS_PERFORMANCE"} and "shareToFeed" not in t


def test_sets_share_hook_and_video_and_videos_rest_7_days():
    for c in ("clipA", "clipB", "clipC"):
        make_clip(c, 60)
    store.write_json("hooks.json", [{"hook": "H1", "caption": "c1"}, {"hook": "H2", "caption": "c2"},
                                    {"hook": "H3", "caption": "c3"}])
    store.save_settings(times=["07:00", "07:02", "07:04", "07:06", "07:08", "07:10"],
                        reels_per_set=3, video_rest_days=7)
    tz = ZoneInfo(TZ)
    d1 = date(2030, 2, 1)
    items = builder.build_day(d1, store.settings(), now=datetime(2030, 2, 1, 0, 0, tzinfo=tz))
    assert len(items) == 6
    a, b = items[:3], items[3:]
    for grp in (a, b):
        assert len({r["hook"] for r in grp}) == 1 and len({r["caption"] for r in grp}) == 1
        assert len({r["clip"].split("@")[0] for r in grp}) == 1          # one video per set
        assert len({r["clip"] for r in grp}) == 3                        # three different pieces
    assert a[0]["hook"] == "H1" and b[0]["hook"] == "H2"
    used_d1 = {a[0]["clip"].split("@")[0], b[0]["clip"].split("@")[0]}
    assert len(used_d1) == 2
    # Day 2: next hooks in rotation; only the one unused video is rested, so one set is skipped.
    d2 = date(2030, 2, 2)
    items2 = builder.build_day(d2, store.settings(), now=datetime(2030, 2, 2, 0, 0, tzinfo=tz))
    assert len(items2) == 3 and items2[0]["hook"] == "H3"
    assert items2[0]["clip"].split("@")[0] not in used_d1
    assert any("Not enough rested videos" in e["text"] for e in store.events())
    # Day 8: day-1 videos have rested 7 days and can be used again.
    d8 = date(2030, 2, 8)
    items8 = builder.build_day(d8, store.settings(), now=datetime(2030, 2, 8, 0, 0, tzinfo=tz))
    assert {r["clip"].split("@")[0] for r in items8} == used_d1
    assert builder.sets_needed(store.settings()) == (2, 14)


def test_two_box_hooks():
    from app import builder
    from app.captions import youtube_title
    h = 'Clip 1: "Aren\'t you embarrassed you\'re still replaying it…" Clip 2: Yes, actually, I am. 🙃'
    assert builder.split_boxes(h) == ["Aren't you embarrassed you're still replaying it…", "Yes, actually, I am. 🙃"]
    assert builder.split_boxes("Box 1: Things fun moms do… Box 2: 1. Dance") == ["Things fun moms do…", "1. Dance"]
    assert builder.split_boxes("A plain hook") is None
    assert youtube_title({"hook": "Box 1: One Box 2: Two"}) == "One Two"


def test_hook_dates_and_favorites(tmp_path, monkeypatch):
    from datetime import date
    from app import builder, store
    from app.main import parse_hooks, format_hooks
    items = parse_hooks("A | cap a\nTurkey | cap t | 2026-11-26\n⭐ Winner | cap w\nB | cap b | mytag | fav")
    assert items[1]["dates"] == ["2026-11-26"] and items[1]["tag"] == ""
    assert items[2] == {"hook": "Winner", "caption": "cap w", "tag": "", "fav": True}
    assert items[3]["tag"] == "mytag" and items[3]["fav"]
    assert parse_hooks(format_hooks(items)) == items
    monkeypatch.setattr(store, "hooks", lambda: items)
    h = {"counter": 100, "hooks": {"A": 70, "Winner": 90, "B": 99}}
    # pinned hook leads on its day and never shows up on other days
    assert [x["hook"] for x in builder.pick_hooks(2, h, date(2026, 11, 26))] == ["Turkey", "A"]
    assert "Turkey" not in [x["hook"] for x in builder.pick_hooks(3, h, date(2026, 11, 27))]
    # favorite used at 90 counts as waiting longer than a plain hook used at 85
    h["hooks"]["A"] = 85
    assert builder.pick_hooks(1, h, date(2026, 11, 27))[0]["hook"] == "Winner"
    assert builder.pick_hooks(1, h, date(2026, 11, 27), skip={"Winner"})[0]["hook"] == "A"
