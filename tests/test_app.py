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
        assert r.status_code == 200 and "Connect Blotato" in r.text and "Connect Zernio" in r.text
        c.post("/setup/songs", data={"text": "⭐ What Was I Made For — Billie Eilish"})
        from app import songs
        songs.save([dict(songs.songs()[0], ig={"id": "a1", "title": "What Was I Made For?", "artist": "Billie Eilish"},
                         ig_note="Found", tt=None, tt_note="Not in TikTok's commercial music list right now")])
        r = c.get("/setup")
        assert r.status_code == 200 and "What Was I Made For?" in r.text and "commercial music list" in r.text
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


def test_restyle_remakes_unsent_and_swaps_scheduled():
    make_clip("clip01", 40)
    store.write_json("hooks.json", [{"hook": "Box 1: Top line Box 2: Bottom line", "caption": "c1"}])
    tz = ZoneInfo(TZ)
    day = date(2030, 1, 7)
    builder.build_day(day, store.settings(), now=datetime(2030, 1, 7, 9, 0, tzinfo=tz))
    plan = builder.plan(day)
    for r in plan:                       # pretend these were made before the style change
        r["style"] = 1
    builder.save_plan(day, plan)
    f1 = builder.batch_dir(day) / "reel_01.mp4"
    os.utime(f1, (0, 0))

    class Fake(FakeClient):
        def __init__(self):
            super().__init__()
            self.patched = []

        def upload(self, path):
            super().upload(path)
            return f"https://cdn/x{self.uploads}.mp4"

        def list_schedules(self):
            return [{"id": "sch_1", "draft": {"accountId": "1", "content": {"text": "t", "platform": "instagram",
                                                                         "mediaUrls": [plan_now()[0]["media_url"]]},
                                              "target": {"targetType": "instagram"}}}]

        def replace_media(self, sch, old, new):
            self.patched.append((sch["id"], old, new))

    def plan_now():
        return builder.plan(day)

    fake = Fake()
    s = store.settings()
    # schedule the 15:00 reel only (15:20 is outside a 5.2 h window at 9:50)
    s2 = dict(s, schedule_hours_ahead=5.2)
    runner.schedule_due(s2, client=fake, now_utc=datetime(2030, 1, 7, 9, 50, tzinfo=tz).astimezone(timezone.utc))
    p = plan_now()
    assert p[0]["platforms"]["instagram"]["status"] == "scheduled"
    assert p[1]["platforms"]["instagram"]["status"] == "pending"
    old_url = p[0]["media_url"]

    # "next" only remakes the soonest one
    n = runner.restyle(s, limit=1, client=fake, now_utc=datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc))
    assert n == 1 and fake.patched and fake.patched[0][:2] == ("sch_1", old_url)
    p = plan_now()
    assert p[0]["style"] == builder.STYLE_VERSION and p[0]["media_url"] == fake.patched[0][2] != old_url
    assert p[1]["style"] == 1
    # the rest, then nothing left to do
    assert runner.restyle(s, client=fake, now_utc=datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc)) == 1
    assert all(r["style"] == builder.STYLE_VERSION for r in plan_now())
    assert runner.restyle(s, client=fake, now_utc=datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc)) == 0
    assert f1.stat().st_mtime > 0 and f1.stat().st_size > 1000      # file was replaced
    # new reels are built in the current style and not remade
    assert not list(builder.batch_dir(day).glob("*_new.*")) and not list(builder.batch_dir(day).glob("*_mux.*"))


# ── Zernio + songs ─────────────────────────────────────────────────────────────

class FakeZernio:
    def __init__(self, ig_audio=None, tt_tracks=None):
        self.posts, self.uploads, self.replaced = [], 0, []
        self.ig_audio = ig_audio or {}
        self.tt_tracks = tt_tracks or []

    def upload(self, path):
        self.uploads += 1
        return f"https://z/cdn/{self.uploads}.mp4"

    def search_instagram_audio(self, account_id, query, audio_type="music"):
        return self.ig_audio.get((audio_type, query), self.ig_audio.get((audio_type, "*"), []))

    def tiktok_music(self, account_id):
        return self.tt_tracks

    def schedule(self, platform, account, reel, url, when, settings, song=None):
        from app import zernio
        self.posts.append((platform, reel["n"], song, zernio.Client("k").body(platform, account, reel, url, when,
                                                                              settings, song)))
        return f"zp-{len(self.posts)}"

    def replace_media(self, post_id, new_url):
        self.replaced.append((post_id, new_url))


def test_parse_song_list():
    from app import songs
    items = songs.parse("What Was I Made For — Billie Eilish\n"
                        "⭐ Golden Hour - JVKE\n"
                        "https://www.instagram.com/reels/audio/123456 | Dancing On My Own — Calum Scott | fav\n"
                        "https://www.instagram.com/reels/audio/999\n"
                        "what was i made for — billie eilish\n\n")
    assert len(items) == 4                                     # duplicate dropped
    assert items[0]["title"] == "What Was I Made For" and items[0]["artist"] == "Billie Eilish"
    assert items[1]["fav"] and items[1]["artist"] == "JVKE"
    assert items[2]["link_id"] == "123456" and items[2]["title"] == "Dancing On My Own" and items[2]["fav"]
    assert items[3]["link_id"] == "999" and items[3]["title"] == ""


def test_match_songs_on_instagram_and_tiktok(monkeypatch):
    from app import songs
    fz = FakeZernio(
        ig_audio={("music", "What Was I Made For Billie Eilish"): [
                      {"audioId": "a1", "title": "What Was I Made For? [From The Motion Picture \"Barbie\"]",
                       "displayArtist": "Billie Eilish"},
                      {"audioId": "a2", "title": "What Was I Made For (Cover)", "displayArtist": "Someone"}],
                  ("music", "Dancing On My Own Calum Scott"): [{"audioId": "x", "title": "Dancing On My Own",
                                                                 "displayArtist": "Calum Scott"}],
                  ("original_sound", "Dancing On My Own Calum Scott"): [
                      {"audioId": "123456", "title": "Dancing On My Own", "igUsername": "pianoguy"}]},
        tt_tracks=[{"id": "full1", "name": "What Was I Made For?", "artist": "Billie Eilish", "rank": 3,
                    "clip": {"id": "clip1"}}])
    items = songs.parse("What Was I Made For — Billie Eilish\n"
                        "https://www.instagram.com/reels/audio/123456 | Dancing On My Own — Calum Scott\n"
                        "Nope — Nobody")
    accts = {"instagram": {"id": "ig", "via": "zernio"}, "tiktok": {"id": "tt", "via": "zernio"}}
    monkeypatch.setattr(songs.time, "sleep", lambda s: None)
    out = songs.resolve(items, fz, accts)
    assert out[0]["ig"]["id"] == "a1" and out[0]["tt"]["id"] == "clip1"     # clip id, not full track
    assert out[1]["ig"]["id"] == "123456"                                     # matched the link exactly
    assert out[2]["ig"] is None and "Not available" in out[2]["ig_note"] and out[2]["tt"] is None


def test_zernio_posts_get_songs_and_youtube_stays_on_blotato():
    from app import songs
    make_clip("clip01", 40)
    store.write_json("hooks.json", [{"hook": "Box 1: Top Box 2: Bottom", "caption": "c1 #a #b"}])
    store.save_settings(accounts={"instagram": {"id": "ig1", "enabled": True, "via": "zernio"},
                                  "tiktok": {"id": "tt1", "enabled": True, "via": "zernio"},
                                  "youtube": {"id": "2", "enabled": True, "via": "blotato"}},
                        zernio_api_key="zk", youtube_daily_cap=5)
    songs.save([{"line": "A", "title": "A", "artist": "", "link_id": None, "fav": False,
                 "ig": {"id": "igA", "title": "A", "artist": ""}, "tt": None},
                {"line": "B", "title": "B", "artist": "", "link_id": None, "fav": True,
                 "ig": {"id": "igB", "title": "B", "artist": ""}, "tt": {"id": "ttB", "title": "B", "artist": ""}}])
    tz = ZoneInfo(TZ)
    day = date(2030, 1, 7)
    builder.build_day(day, store.settings(), now=datetime(2030, 1, 7, 9, 0, tzinfo=tz))
    fz, fb = FakeZernio(), FakeClient()
    n = runner.schedule_due(store.settings(), client=fb, zclient=fz,
                            now_utc=datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc))
    assert n == 6 and fz.uploads == 2 and fb.uploads == 2                    # one upload per reel per service
    assert {p for p, *_ in fb.posts} == {"youtube"}
    ig = [x for x in fz.posts if x[0] == "instagram"]
    tt = [x for x in fz.posts if x[0] == "tiktok"]
    assert builder.plan(day)[0]["set"] == builder.plan(day)[1]["set"]
    assert len({x[2]["id"] for x in ig}) == 1                                 # one set, one song
    body = ig[0][3]["platforms"][0]["platformSpecificData"]
    assert body["audioConfiguration"]["videoVolume"] == 0 and body["shareToFeed"] is True
    assert [x[2]["id"] for x in tt] == ["ttB", "ttB"]                         # only B is on TikTok
    tbody = tt[0][3]["tiktokSettings"]
    assert tbody["musicSoundInfo"]["musicSoundId"] == "ttB" and tbody["videoOriginalSoundVolume"] == 0
    assert tbody["content_preview_confirmed"] and tbody["express_consent_given"]
    plan = builder.plan(day)
    assert plan[0]["platforms"]["instagram"]["via"] == "zernio" and plan[0]["platforms"]["instagram"]["song"]
    assert plan[0]["zernio_url"] and plan[0]["media_url"]

    # Remake swaps the new video onto the Zernio posts
    for r in plan:
        r["style"] = 1
    builder.save_plan(day, plan)

    class FB(FakeClient):
        def list_schedules(self):
            return [{"id": f"s{r['n']}", "draft": {"content": {"mediaUrls": [r["media_url"]]}}}
                    for r in builder.plan(day)]

        def replace_media(self, sch, old, new):
            pass
    fz2 = FakeZernio()
    assert runner.restyle(store.settings(), client=FB(), zclient=fz2,
                          now_utc=datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc)) == 2
    assert sorted(p for p, _ in fz2.replaced) == ["zp-1", "zp-2", "zp-3", "zp-4"]


def test_song_fallback_and_random_without_repeats():
    import random
    from app import songs
    fz = FakeZernio(tt_tracks=[{"id": f"t{i}", "name": f"T{i}", "artist": "x", "rank": i, "clip": {"id": f"c{i}"}}
                               for i in range(1, 15)])
    songs.save([])
    s = dict(store.settings(), song_fallback="trending")
    assert songs.pick("tiktok", s, fz, {"id": "tt"}, day="2030-01-01")["id"] in {f"c{i}" for i in range(1, 11)}
    assert songs.pick("tiktok", dict(s, song_fallback="none"), fz, {"id": "tt"}) is None
    songs.save([{"line": x, "fav": x == "F", "tt": {"id": x}, "ig": {"id": x}} for x in "FPQR"])
    rng = random.Random(7)
    day1 = [songs.pick("tiktok", s, day="2030-01-02", rng=rng)["id"] for _ in range(4)]
    assert sorted(day1) == ["F", "P", "Q", "R"]                   # no repeats until all are used
    day1 += [songs.pick("tiktok", s, day="2030-01-02", rng=rng)["id"] for _ in range(4)]
    assert sorted(day1[4:]) == ["F", "P", "Q", "R"]               # then a second round
    ig = [songs.pick("instagram", s, day="2030-01-02", rng=rng)["id"] for _ in range(4)]
    assert sorted(ig) == ["F", "P", "Q", "R"]                     # each platform counts separately
    day2 = [songs.pick("tiktok", s, day="2030-01-03", rng=rng)["id"] for _ in range(4)]
    assert sorted(day2) == ["F", "P", "Q", "R"]                   # a new day starts fresh
    first = [songs.pick("tiktok", s, day=f"2031-01-{d:02d}", rng=rng)["id"] for d in range(1, 29)]
    assert first.count("F") > 28 / 4                              # a favorite comes up more often
    # reels in the same set share a song; other sets that day get different ones
    sets = [songs.pick("instagram", s, day="2032-01-01", rng=rng, set_key=k)["id"] for k in (1, 1, 1, 2, 2, 3)]
    assert sets[0] == sets[1] == sets[2] and sets[3] == sets[4] and len({sets[0], sets[3], sets[5]}) == 3


def test_zernio_instagram_trial_and_no_song():
    from app import zernio
    reel = {"hook": "H", "caption": "C", "tag": ""}
    when = datetime(2030, 1, 1, tzinfo=timezone.utc)
    s = dict(store.settings(), ig_trial="SS_PERFORMANCE")
    b = zernio.Client("k").body("instagram", {"id": "1"}, reel, "u", when, s)
    psd = b["platforms"][0]["platformSpecificData"]
    assert psd["trialParams"] == {"graduationStrategy": "SS_PERFORMANCE"} and "shareToFeed" not in psd
    assert "audioConfiguration" not in psd and b["scheduledFor"] == "2030-01-01T00:00:00Z"
    t = zernio.Client("k").body("tiktok", {"id": "2"}, reel, "u", when, s)
    assert "musicSoundInfo" not in t["tiktokSettings"] and "videoOriginalSoundVolume" not in t["tiktokSettings"]


def test_clear_scheduled_reels_by_platform():
    make_clip("clip01", 40)
    store.write_json("hooks.json", [{"hook": "H", "caption": "c"}])
    store.save_settings(accounts={"instagram": {"id": "ig1", "enabled": True, "via": "zernio"},
                                  "youtube": {"id": "2", "enabled": True, "via": "blotato"}},
                        zernio_api_key="zk", youtube_daily_cap=5, song_fallback="none")
    tz = ZoneInfo(TZ)
    day = date(2030, 1, 7)
    builder.build_day(day, store.settings(), now=datetime(2030, 1, 7, 9, 0, tzinfo=tz))
    now = datetime(2030, 1, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc)

    class FZ(FakeZernio):
        def __init__(self):
            super().__init__()
            self.deleted = []

        def delete_post(self, pid):
            self.deleted.append(pid)

    class FB(FakeClient):
        def __init__(self):
            super().__init__()
            self.deleted = []

        def upload(self, path):
            super().upload(path)
            return f"https://cdn/y{self.uploads}.mp4"

        def list_schedules(self):
            return [{"id": f"s{r['n']}", "draft": {"content": {"mediaUrls": [r["media_url"]], "platform": "youtube"}}}
                    for r in builder.plan(day)]

        def delete_schedule(self, sid):
            self.deleted.append(sid)

    fz, fb = FZ(), FB()
    assert runner.schedule_due(store.settings(), client=fb, zclient=fz, now_utc=now) == 4
    # Remove YouTube only: cancelled in Blotato, Instagram untouched
    assert runner.clear_platform(store.settings(), "youtube", client=fb, zclient=fz, now_utc=now) == (2, 0)
    assert sorted(fb.deleted) == ["s1", "s2"] and fz.deleted == []
    plan = builder.plan(day)
    assert {r["platforms"]["youtube"]["status"] for r in plan} == {"cleared"}
    assert {r["platforms"]["instagram"]["status"] for r in plan} == {"scheduled"}
    # Re-send Instagram: cancelled in Zernio, then sent again on the next round
    assert runner.clear_platform(store.settings(), "instagram", resend=True, client=fb, zclient=fz,
                                 now_utc=now) == (2, 0)
    assert sorted(fz.deleted) == ["zp-1", "zp-2"]
    assert {r["platforms"]["instagram"]["status"] for r in builder.plan(day)} == {"pending"}
    assert runner.schedule_due(store.settings(), client=fb, zclient=fz, now_utc=now) == 2
    plan = builder.plan(day)
    assert {r["platforms"]["instagram"]["status"] for r in plan} == {"scheduled"}
    assert {r["platforms"]["youtube"]["status"] for r in plan} == {"cleared"}      # stays cleared
    # Send again brings back reels cleared earlier
    assert runner.clear_platform(store.settings(), "youtube", resend=True, client=fb, zclient=fz,
                                 now_utc=now) == (2, 0)
    assert {r["platforms"]["youtube"]["status"] for r in builder.plan(day)} == {"pending"}
    assert runner.schedule_due(store.settings(), client=fb, zclient=fz, now_utc=now) == 2
    assert {r["platforms"]["youtube"]["status"] for r in builder.plan(day)} == {"scheduled"}
    # Nothing within 10 minutes of posting is touched
    late = datetime(2030, 1, 7, 14, 55, tzinfo=tz).astimezone(timezone.utc)
    assert runner.clear_platform(store.settings(), "instagram", client=fb, zclient=fz, now_utc=late) == (1, 0)
