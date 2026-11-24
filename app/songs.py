"""The person's song list for Instagram and TikTok, matched to each platform's music catalog.

songs.json: [{"line", "title", "artist", "link_id", "fav",
              "ig": {"id", "title", "artist", "kind"} | None, "ig_note",
              "tt": {"id", "title", "artist"} | None, "tt_note"}]
song_history.json: {"used": {"2026-11-23": {"ig": [ids used that day], "ig_sets": {set: id}, "tt": [...]}},
                    "tt_trending": [...], "tt_trending_at": epoch}
"""
import random
import re
import time
from datetime import date, timedelta

from . import store

_LINK = re.compile(r"instagram\.com/(?:reels?/)?audio/(\d+)", re.I)
_SPLIT = re.compile(r"\s+(?:—|–|-|by)\s+", re.I)
_FAV = {"fav", "favorite", "favourite", "\u2b50", "\u2b50\ufe0f"}
TRENDING_TTL = 6 * 3600


def _norm(s):
    s = (s or "").lower().replace("&", "and")
    s = re.sub(r"[\u2018\u2019'\"`]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def parse(text):
    """One song per line: "Title — Artist", "Title", or an Instagram audio link (optionally
    followed by "| Title — Artist"). A star at the start or "| fav" marks a favorite."""
    out, seen = [], set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        fav = False
        if line.startswith("\u2b50"):
            line, fav = line.lstrip("\u2b50\ufe0f ").strip(), True
        parts = [p.strip() for p in line.split("|")]
        rest = []
        for p in parts[1:]:
            if p.lower() in _FAV:
                fav = True
            elif p:
                rest.append(p)
        head = parts[0]
        link_id = None
        m = _LINK.search(head)
        if m:
            link_id = m.group(1)
            head = rest.pop(0) if rest else ""
        bits = _SPLIT.split(head, maxsplit=1) if head else []
        title = bits[0].strip() if bits else ""
        artist = bits[1].strip() if len(bits) > 1 else ""
        key = link_id or _norm(f"{title} {artist}")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"line": raw.strip(), "title": title, "artist": artist, "link_id": link_id, "fav": fav})
    return out


def format_list(items):
    lines = []
    for s in items:
        lines.append(("\u2b50 " if s.get("fav") else "") + s["line"].lstrip("\u2b50\ufe0f "))
    return "\n".join(lines)


def _score(want_title, want_artist, title, artist):
    t, a = _norm(title), _norm(artist)
    wt, wa = _norm(want_title), _norm(want_artist)
    if not wt or not t:
        return 0
    if t == wt:
        score = 3
    elif t.startswith(wt) or wt.startswith(t) or wt in t:
        score = 2
    else:
        return 0
    if wa:
        if wa in a or (a and a in wa):
            score += 2
        else:
            return 0
    return score


def match_instagram(song, client, account_id):
    """Find the song in Instagram's catalog. Returns (asset or None, note)."""
    title, artist, link_id = song.get("title"), song.get("artist"), song.get("link_id")
    if link_id and not title:
        # A bare link: Instagram's audio page id. It is used as is and checked when the reel posts.
        return {"id": link_id, "title": "", "artist": "", "kind": "link"}, "From your link (checked when it posts)"
    query = f"{title} {artist}".strip()
    best, best_score = None, 0
    for kind in ("music", "original_sound"):
        for a in client.search_instagram_audio(account_id, query, kind):
            aid = str(a.get("audioId") or "")
            if link_id and aid == link_id:
                return ({"id": aid, "title": a.get("title") or title, "kind": kind,
                         "artist": a.get("displayArtist") or a.get("igUsername") or ""}, "Found")
            s = _score(title, artist, a.get("title"), a.get("displayArtist") or a.get("igUsername") or "")
            if s > best_score:
                best, best_score = (a, kind), s
        if best and not link_id:
            break
    if link_id:
        return {"id": link_id, "title": title, "artist": artist, "kind": "link"}, \
            "Not in search results; using your link (checked when it posts)"
    if not best:
        return None, "Not available to apps on Instagram"
    a, kind = best
    return ({"id": str(a["audioId"]), "title": a.get("title") or "", "kind": kind,
             "artist": a.get("displayArtist") or a.get("igUsername") or "",
             "preview": a.get("onPlatformAudioPreviewLink") or a.get("downloadUrl") or ""}, "Found")


def _tt_asset(t):
    clip = t.get("clip") or {}
    return {"id": str(clip.get("id") or t.get("id")), "title": t.get("name") or "", "artist": t.get("artist") or ""}


def match_tiktok(song, tracks):
    best, best_score = None, 0
    for t in tracks:
        s = _score(song.get("title"), song.get("artist"), t.get("name"), t.get("artist"))
        if s > best_score:
            best, best_score = t, s
    if not best:
        return None, "Not in TikTok's commercial music list right now"
    return _tt_asset(best), "Found"


def resolve(items, client, accounts):
    """Look every song up on the connected Instagram and TikTok accounts."""
    ig = (accounts.get("instagram") or {})
    tt = (accounts.get("tiktok") or {})
    tracks, tt_err = [], None
    if tt.get("via") == "zernio":
        try:
            tracks = client.tiktok_music(tt["id"])
            _save_trending(tracks)
        except Exception as e:  # noqa: BLE001
            tt_err = f"TikTok music list unavailable: {str(e)[:120]}"
    for s in items:
        if ig.get("via") == "zernio":
            try:
                s["ig"], s["ig_note"] = match_instagram(s, client, ig["id"])
            except Exception as e:  # noqa: BLE001
                s["ig"], s["ig_note"] = None, f"Search failed: {str(e)[:120]}"
            time.sleep(0.5)
        else:
            s["ig"], s["ig_note"] = None, "Instagram isn't connected through Zernio"
        if tt.get("via") == "zernio" and not tt_err:
            s["tt"], s["tt_note"] = match_tiktok(s, tracks) if s.get("title") else (None, "Needs a title to match")
        else:
            s["tt"], s["tt_note"] = None, tt_err or "TikTok isn't connected through Zernio"
    return items


def songs():
    return store.read_json("songs.json", [])


def save(items):
    store.write_json("songs.json", items)


def _hist():
    return store.read_json("song_history.json", {"used": {}})


def _save_trending(tracks):
    h = _hist()
    h["tt_trending"] = [_tt_asset(t) | {"rank": t.get("rank")} for t in tracks]
    h["tt_trending_at"] = time.time()
    store.write_json("song_history.json", h)


def pick(platform, settings, client=None, account=None, day=None, rng=random, set_key=None):
    """A song for one reel on "instagram" or "tiktok", or None for the video's own sound.

    Reels in the same set (same hook and video) share one song. Each set gets a random song
    that no other set has used on that platform that day (only once every song has been
    used that day can one repeat). A favorite is twice as likely to be picked. When nothing
    on the list is available on that platform and the fallback is on, a trending track is
    picked the same way instead."""
    key = "ig" if platform == "instagram" else "tt"
    h = _hist()
    pool = [s[key] for s in songs() if s.get(key)]
    favs = {s[key]["id"] for s in songs() if s.get(key) and s.get("fav")}
    if not pool and (settings or {}).get("song_fallback", "trending") == "trending" and client and account:
        pool = _trending(platform, h, client, account)
    if not pool:
        return None
    day = str(day or date.today())
    h = _hist()
    used = h.setdefault("used", {})
    for d in [d for d in used if d < str(date.fromisoformat(day) - timedelta(days=3))]:
        del used[d]                                      # keep a few days only
    today = used.setdefault(day, {}).setdefault(key, [])
    by_set = used[day].setdefault(key + "_sets", {})
    if set_key is not None and str(set_key) in by_set:
        same = next((a for a in pool if a["id"] == by_set[str(set_key)]), None)
        if same:
            return same                                  # the set keeps its song
    counts = {a["id"]: today.count(a["id"]) for a in pool}
    least = min(counts.values())
    choices = [a for a in pool if counts[a["id"]] == least]
    song = rng.choices(choices, weights=[2 if a["id"] in favs else 1 for a in choices])[0]
    today.append(song["id"])
    if set_key is not None:
        by_set[str(set_key)] = song["id"]
    store.write_json("song_history.json", h)
    return song


def _trending(platform, h, client, account):
    try:
        if platform == "tiktok":
            if time.time() - h.get("tt_trending_at", 0) > TRENDING_TTL:
                _save_trending(client.tiktok_music(account["id"]))
            items = _hist().get("tt_trending") or []
            items = sorted(items, key=lambda t: t.get("rank") or 999)[:10]
            return [{k: t[k] for k in ("id", "title", "artist")} for t in items]
        found = client.search_instagram_audio(account["id"], "", "music")[:10]
        return [{"id": str(a["audioId"]), "title": a.get("title") or "", "artist": a.get("displayArtist") or "",
                 "kind": "music"} for a in found if a.get("audioId")]
    except Exception:  # noqa: BLE001
        return []
