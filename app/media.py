"""Source clips: probe, normalize to 1080x1920 SDR, cut into footage windows, thumbnails."""
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from . import store

W, H, FPS = 1080, 1920, 30
MAX_SPEED = 1.12
_jobs: "queue.Queue[str]" = queue.Queue()
_worker = None
_has_zscale = None


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True)
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def duration(path) -> float:
    try:
        return float(ffprobe(path).get("format", {}).get("duration") or 0)
    except ValueError:
        return 0.0


def window_seconds(settings=None) -> float:
    """Source seconds one reel can use. The grid is fixed so windows never overlap."""
    s = settings or store.settings()
    return round(float(s.get("reel_seconds", 13)) * MAX_SPEED + 0.5, 1)


def windows(clip, settings=None):
    """Footage windows for a clip: start seconds, non-overlapping, excluded ones dropped."""
    w = window_seconds(settings)
    n = int((clip.get("duration") or 0) // w)
    excluded = set(clip.get("excluded", []))
    return [round(i * w, 1) for i in range(n) if round(i * w, 1) not in excluded]


def all_windows(settings=None):
    """[(key, clip_id, start)] across ready clips."""
    out = []
    for c in store.clips():
        if c.get("status") == "ready":
            for st in windows(c, settings):
                out.append((f"{c['id']}@{st}", c["id"], st))
    return out


def _zscale():
    global _has_zscale
    if _has_zscale is None:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        _has_zscale = "zscale" in out and "tonemap" in out
    return _has_zscale


def _is_hdr(info):
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return s.get("color_transfer") in ("arib-std-b67", "smpte2084") or "10" in (s.get("pix_fmt") or "")
    return False


def normalize(src, dst):
    info = ffprobe(src)
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p"
    if _is_hdr(info) and _zscale():
        vf = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
              "zscale=t=bt709:m=bt709:r=tv," + vf)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-an", "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])


def thumbs(clip):
    """One small frame from the middle of every window, for the setup page."""
    w = window_seconds()
    src = store.DATA_DIR / "source" / "clips" / f"{clip['id']}.mp4"
    tdir = store.dir_("source", "thumbs")
    n = int((clip.get("duration") or 0) // w)
    for i in range(n):
        st = round(i * w, 1)
        out = tdir / f"{clip['id']}@{st}.jpg"
        if not out.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(st + w / 2), "-i", str(src),
                            "-frames:v", "1", "-vf", "scale=180:-2", str(out)], capture_output=True)


def _update_clip(cid, **changes):
    items = store.clips()
    for c in items:
        if c["id"] == cid:
            c.update(changes)
    store.save_clips(items)


def _run():
    while True:
        cid = _jobs.get()
        try:
            c = next((x for x in store.clips() if x["id"] == cid), None)
            if not c:
                continue
            raw = store.DATA_DIR / "source" / "raw" / c["raw"]
            dst = store.path("source", "clips", f"{cid}.mp4")
            _update_clip(cid, status="processing")
            normalize(raw, dst)
            d = duration(dst)
            _update_clip(cid, status="ready", duration=round(d, 2))
            thumbs({**c, "duration": d})
            raw.unlink(missing_ok=True)
            n = len(windows({**c, "duration": d}))
            store.event("clip", f"{c['name']} is ready: {d:.0f} seconds, {n} reel windows.")
        except Exception as e:  # noqa: BLE001
            _update_clip(cid, status="failed", error=str(e)[:300])
            store.event("error", f"Could not process {cid}: {str(e)[:200]}")
        finally:
            _jobs.task_done()


def start_worker():
    global _worker
    if _worker is None:
        _worker = threading.Thread(target=_run, daemon=True, name="clip-worker")
        _worker.start()
        # Re-queue anything a restart interrupted.
        for c in store.clips():
            if c.get("status") in ("queued", "processing") and (store.DATA_DIR / "source" / "raw" / c["raw"]).exists():
                _jobs.put(c["id"])


def add_clip(upload_path, original_name):
    """Register an uploaded file and queue it for processing. Returns the clip id."""
    items = store.clips()
    n = max([int(c["id"][4:]) for c in items] + [0]) + 1
    cid = f"clip{n:02d}"
    raw_name = f"{cid}{Path(original_name).suffix.lower() or '.mov'}"
    shutil.move(str(upload_path), store.path("source", "raw", raw_name))
    items.append({"id": cid, "name": original_name, "raw": raw_name, "status": "queued",
                  "duration": 0, "excluded": []})
    store.save_clips(items)
    _jobs.put(cid)
    return cid


def remove_clip(cid):
    store.save_clips([c for c in store.clips() if c["id"] != cid])
    (store.DATA_DIR / "source" / "clips" / f"{cid}.mp4").unlink(missing_ok=True)
    for t in (store.DATA_DIR / "source" / "thumbs").glob(f"{cid}@*.jpg"):
        t.unlink()


def music_tracks():
    d = store.dir_("source", "music")
    return sorted(p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac"))
