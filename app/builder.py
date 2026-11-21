"""Turns footage windows + hooks into finished reels, and a day of reels into a batch."""
import itertools
import shutil
import subprocess
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from . import media, store

W, H, FPS = media.W, media.H, media.FPS
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]

SPEEDS = [1.0, 1.08, 0.95, 1.12, 1.04]
ZOOMS = [1.0, 1.03, 1.06, 1.09]
GRADES = [
    "eq=contrast=1.06:saturation=1.10",
    "eq=contrast=1.10:saturation=0.75:brightness=-0.02",
    "eq=contrast=1.05:saturation=1.20,colorbalance=rs=.06:bs=-.06",
    "eq=contrast=1.08:saturation=0.90,colorbalance=rs=-.05:bs=.08",
    "eq=contrast=1.14:saturation=0.60:brightness=-0.03",
    "eq=contrast=1.05:saturation=1.12:gamma=1.06",
    "eq=contrast=1.12:saturation=0.85,colorbalance=gs=.04",
]
MIRRORS = [False, True]
# Every combination is a distinct look; a reused window always gets one it hasn't had.
VARIANTS = list(itertools.product(range(len(SPEEDS)), range(len(ZOOMS)), range(len(GRADES)), MIRRORS))


def _font(size):
    for f in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def overlay_png(text, out_path):
    """Hook text, white with a black outline, on a dark band in the lower third."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    band = Image.new("RGBA", (W, 760), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for y in range(760):
        bd.line([(0, y), (W, y)], fill=(0, 0, 0, int(200 * min(1, y / 260))))
    img.alpha_composite(band, (0, H - 760))
    d = ImageDraw.Draw(img)
    size, maxw = 84, W - 160

    def wrap(font):
        lines, cur = [], ""
        for word in text.split():
            t = (cur + " " + word).strip()
            if d.textlength(t, font=font) <= maxw:
                cur = t
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
        return [ln for ln in lines if ln]

    font = _font(size)
    lines = wrap(font)
    while len(lines) > 5 and size > 52:
        size -= 6
        font = _font(size)
        lines = wrap(font)
    lh = int(size * 1.25)
    y = H - 560 - (len(lines) * lh) // 2
    for ln in lines:
        x = (W - d.textlength(ln, font=font)) / 2
        d.text((x, y), ln, font=font, fill="white", stroke_width=7, stroke_fill="black")
        y += lh
    img.save(out_path)


def render(clip_id, start, hook, music, music_offset, variant, out_path, reel_seconds=13):
    sp_i, zm_i, gr_i, mirror = VARIANTS[variant % len(VARIANTS)]
    speed, zoom, grade = SPEEDS[sp_i], ZOOMS[zm_i], GRADES[gr_i]
    length = float(reel_seconds)
    src_len = round(length * speed, 2)
    src = store.DATA_DIR / "source" / "clips" / f"{clip_id}.mp4"
    png = str(out_path).rsplit(".", 1)[0] + "_hook.png"
    overlay_png(hook, png)
    zc = f"scale={int(W * zoom)}:-2,crop={W}:{H}" if zoom > 1 else f"scale={W}:{H}"
    flip = ",hflip" if mirror else ""
    vf = (f"[0:v]setpts=PTS/{speed},fps={FPS},{zc}{flip},{grade},vignette=PI/{4 + variant % 3},format=yuv420p[bg];"
          f"[1:v]format=rgba,fade=t=in:st=0.25:d=0.35:alpha=1[ov];[bg][ov]overlay=0:0[v]")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(src_len), "-i", str(src),
           "-loop", "1", "-framerate", str(FPS), "-i", png]
    if music:
        cmd += ["-i", str(music)]
        vf += (f";[2:a]atrim=start={music_offset}:duration={length},asetpts=PTS-STARTPTS,volume=0.35,"
               f"afade=t=in:d=0.5,afade=t=out:st={max(0, length - 1.2)}:d=1.2[a]")
    cmd += ["-filter_complex", vf, "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"] if music else ["-an"]
    cmd += ["-t", str(length), "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-movflags", "+faststart", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    thumb = str(out_path).rsplit(".", 1)[0] + ".jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", str(out_path), "-frames:v", "1",
                    "-vf", "scale=270:-2", thumb], capture_output=True)
    try:
        import os
        os.remove(png)
    except OSError:
        pass
    return thumb


# ── choosing footage and hooks ────────────────────────────────────────────────

def _history():
    h = store.read_json("history.json", {})
    h.setdefault("counter", 0)
    h.setdefault("windows", {})   # key -> {"last": counter, "variants": [..]}
    h.setdefault("hooks", {})     # hook text -> last counter
    h.setdefault("clips", {})     # clip id -> last date used (YYYY-MM-DD)
    return h


def pick_windows(n, settings=None, history=None):
    """n footage windows: never used first, then least recently used; spread across clips."""
    h = history or _history()
    pool = media.all_windows(settings)
    if not pool:
        return []
    def rank(w):
        rec = h["windows"].get(w[0])
        return (-1 if rec is None else rec["last"])
    pool.sort(key=rank)
    chosen, used_clips = [], set()
    for w in pool:                       # first pass: one per clip
        if len(chosen) == n:
            break
        if w[1] not in used_clips:
            chosen.append(w)
            used_clips.add(w[1])
    for w in pool:                       # fill the rest in rank order
        if len(chosen) == n:
            break
        if w not in chosen:
            chosen.append(w)
    while len(chosen) < n:               # tiny libraries: cycle (variants still differ)
        chosen.append(pool[len(chosen) % len(pool)])
    return chosen[:n]


def pick_variant(key, salt, history):
    used = set(history["windows"].get(key, {}).get("variants", []))
    for k in range(len(VARIANTS)):
        v = (salt * 37 + k * 11) % len(VARIANTS)
        if v not in used:
            return v
    return salt % len(VARIANTS)


def pick_hooks(n, history=None):
    h = history or _history()
    items = [x for x in store.hooks() if (x.get("hook") or "").strip()]
    items.sort(key=lambda x: h["hooks"].get(x["hook"], -1))
    if not items:
        return []
    return [items[i % len(items)] for i in range(n)]


def low_footage(settings=None):
    """(unused windows, reels per day)."""
    s = settings or store.settings()
    h = _history()
    unused = sum(1 for w in media.all_windows(s) if w[0] not in h["windows"])
    return unused, len(s.get("times") or [])


# ── a day's batch ─────────────────────────────────────────────────────────────

def batch_dir(day):
    return store.DATA_DIR / "batches" / day.isoformat()


def plan(day):
    return store.read_json(f"batches/{day.isoformat()}/plan.json", None)


def save_plan(day, items):
    store.write_json(f"batches/{day.isoformat()}/plan.json", items)


def _ready_clips():
    return [c for c in store.clips() if c.get("status") == "ready"]


def sets_needed(settings):
    """(sets a day, videos the rotation needs) for the current schedule."""
    per = max(1, int(settings.get("reels_per_set", 3)))
    n = len(settings.get("times") or [])
    sets = -(-n // per) if n else 0
    rest = max(0, int(settings.get("video_rest_days", 7)))
    return sets, sets * max(1, rest)


def pick_clip(day, settings, history, taken, rest=True):
    """The video for a new set: rested at least video_rest_days, not used today,
    most unused footage first. None when nothing is eligible."""
    rest_days = int(settings.get("video_rest_days", 7)) if rest else 0
    best = None
    for c in _ready_clips():
        cid = c["id"]
        if cid in taken:
            continue
        last = history["clips"].get(cid)
        if last and rest_days and (day - date.fromisoformat(last)).days < rest_days:
            continue
        wins = media.windows(c, settings)
        if not wins:
            continue
        unused = sum(1 for st in wins if f"{cid}@{st}" not in history["windows"])
        rank = (last is not None, -unused, last or "", cid)
        if best is None or rank < best[0]:
            best = (rank, cid)
    return best[1] if best else None


def clip_windows(cid, n, settings, history, avoid=()):
    """n different pieces of one video: never used first, then least recently used.
    A short video repeats pieces, each with a new look."""
    c = next((x for x in _ready_clips() if x["id"] == cid), None)
    starts = media.windows(c, settings) if c else []
    keys = [(f"{cid}@{st}", cid, st) for st in starts]
    keys.sort(key=lambda w: (w[0] in avoid, history["windows"].get(w[0], {}).get("last", -1)))
    if not keys:
        return []
    return [keys[i % len(keys)] for i in range(n)]


def build_day(day: date, settings=None, now=None, out_dir=None, record=True):
    """Render one reel per posting time for `day` that doesn't have one yet.

    Posting times are grouped into sets of `reels_per_set` (3 by default). Each set
    is one hook and caption on different pieces of one video. Each new set takes
    the next hook in rotation and a video that hasn't been used for
    `video_rest_days` days. Times already gone (or within 10 minutes) are skipped.
    When the day already has reels, new ones are added after them (joining their
    set if it was started). Returns only the newly built reels.
    """
    s = settings or store.settings()
    tz = ZoneInfo(s["timezone"])
    now = now or datetime.now(tz)
    per = max(1, int(s.get("reels_per_set", 3)))
    existing = (plan(day) or []) if record else []
    have = {r["local_time"] for r in existing}
    all_times = sorted(s["times"])
    slots = [(t, i // per) for i, t in enumerate(all_times) if t not in have
             and datetime.fromisoformat(f"{day.isoformat()}T{t}").replace(tzinfo=tz) > now + timedelta(minutes=10)]
    if not slots:
        return []
    if not _ready_clips():
        raise RuntimeError("No ready footage. Upload clips on the setup page.")
    h = _history()
    h.setdefault("clips", {})
    # sets already started today keep their hook and video
    sets = {}
    for r in existing:
        if "set" in r and r.get("clip"):
            sets.setdefault(r["set"], {"hook": {"hook": r["hook"], "caption": r.get("caption", ""),
                                                "tag": r.get("tag", "")},
                                       "clip": r["clip"].split("@")[0], "used": set()})
            sets[r["set"]]["used"].add(r["clip"])
    taken = {v["clip"] for v in sets.values()}
    new_sets = sorted({k for _, k in slots} - set(sets))
    hooks = pick_hooks(len(new_sets), h)
    if new_sets and not hooks:
        raise RuntimeError("No hooks. Add hooks on the setup page.")
    skipped_sets = []
    for k, hk in zip(new_sets, hooks):
        cid = pick_clip(day, s, h, taken, rest=record)
        if cid is None:
            skipped_sets.append(k)
            continue
        taken.add(cid)
        sets[k] = {"hook": hk, "clip": cid, "used": set()}
    if skipped_sets and record:
        msg = (f"Not enough rested videos for {day:%a %b %-d}: {len(skipped_sets)} set(s) not built. "
               f"Each video rests {s.get('video_rest_days', 7)} days; upload more videos.")
        if not any(e.get("text") == msg for e in store.events(100)):
            store.event("warn", msg)
    tracks = media.music_tracks()
    out = out_dir or batch_dir(day)
    out.mkdir(parents=True, exist_ok=True)
    yt_cap = int(s.get("youtube_daily_cap", 3))
    yt_used = sum(1 for r in existing if (r.get("platforms") or {}).get("youtube", {}).get("status") != "off"
                  and "youtube" in (r.get("platforms") or {}))
    items = []
    n = max([r["n"] for r in existing] + [0])
    for k in sorted({k for _, k in slots}):
        if k not in sets:
            continue
        st = sets[k]
        times_k = [t for t, kk in slots if kk == k]
        wins = clip_windows(st["clip"], len(times_k), s, h, avoid=st["used"])
        for t, (key, cid, start) in zip(times_k, wins):
            n += 1
            h["counter"] += 1
            variant = pick_variant(key, h["counter"], h)
            music = tracks[(h["counter"]) % len(tracks)] if tracks else None
            moff = 0
            if music:
                mdur = media.duration(music)
                span = max(1, int(mdur - float(s["reel_seconds"]) - 1))
                moff = (h["counter"] * 17) % span
            fname = f"reel_{n:02d}.mp4"
            hk = st["hook"]
            render(cid, start, hk["hook"], music, moff, variant, out / fname, s["reel_seconds"])
            plats = {p: {"status": "pending"} for p, a in (s.get("accounts") or {}).items() if a.get("enabled")}
            if "youtube" in plats:
                if yt_used >= yt_cap:
                    plats["youtube"] = {"status": "off", "error": "over the daily YouTube cap"}
                else:
                    yt_used += 1
            items.append({"n": n, "file": fname, "thumb": f"reel_{n:02d}.jpg", "hook": hk["hook"],
                          "caption": hk.get("caption", ""), "tag": hk.get("tag", ""),
                          "local_date": day.isoformat(), "local_time": t, "clip": key, "set": k,
                          "variant": variant, "skipped": False, "media_url": None, "platforms": plats})
            if record:
                rec = h["windows"].setdefault(key, {"last": 0, "variants": []})
                rec["last"] = h["counter"]
                rec["variants"].append(variant)
                h["hooks"][hk["hook"]] = h["counter"]
                h["clips"][cid] = day.isoformat()
    if record:
        store.write_json("history.json", h)
        save_plan(day, sorted(existing + items, key=lambda r: r["local_time"]))
    return items


def build_previews(n=3):
    """Sample reels for the setup page. Does not touch the footage history."""
    out = store.DATA_DIR / "previews"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    s = dict(store.settings())
    s["times"] = ["23:58"] * n
    tz = ZoneInfo(s["timezone"])
    now = datetime.now(tz).replace(hour=0, minute=0)
    items = build_day(now.date(), s, now=now, out_dir=out, record=False)
    store.write_json("previews/plan.json", items)
    return items
