"""Small Zernio REST client (https://docs.zernio.com) for Instagram and TikTok.

Zernio can attach music to a post: a track from Instagram's licensed catalog on Reels,
and a track from TikTok's Commercial Music Library on TikTok videos.
"""
import os
import time

import httpx

from . import captions

API = "https://zernio.com/api/v1"
PLATFORMS = ["instagram", "tiktok"]


class ZernioError(Exception):
    pass


class Client:
    def __init__(self, api_key, http=None):
        self.key = (api_key or "").strip()
        self.http = http or httpx.Client(timeout=120)

    def _req(self, method, path, **kw):
        headers = {"Authorization": f"Bearer {self.key}"}
        for attempt in range(4):
            r = self.http.request(method, API + path, headers=headers, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code >= 400:
                try:
                    j = r.json()
                    msg = j.get("error") or j.get("message") or r.text
                    if isinstance(msg, dict):
                        msg = msg.get("message") or str(msg)
                except ValueError:
                    msg = r.text
                raise ZernioError(f"{r.status_code}: {str(msg)[:300]}")
            return r.json() if r.content else {}
        raise ZernioError("gave up after retries")

    def accounts(self):
        """{platform: {"id", "username", "via"}} for the first active Instagram and TikTok account."""
        out = {}
        for a in self._req("GET", "/accounts").get("accounts", []):
            p = a.get("platform")
            if p not in PLATFORMS or p in out or a.get("isActive") is False:
                continue
            out[p] = {"id": str(a.get("_id")), "username": a.get("username") or a.get("displayName") or "",
                      "via": "zernio"}
        return out

    def upload(self, file_path):
        """Upload a local video; returns the public URL to post from (kept 7 days, copied when posted)."""
        name = os.path.basename(file_path)
        j = self._req("POST", "/media/presign",
                      json={"filename": name, "contentType": "video/mp4", "size": os.path.getsize(file_path)})
        with open(file_path, "rb") as f:
            r = self.http.put(j["uploadUrl"], content=f.read(), headers={"Content-Type": "video/mp4"}, timeout=600)
        if r.status_code >= 400:
            raise ZernioError(f"upload failed {r.status_code}: {r.text[:200]}")
        return j["publicUrl"]

    # ── music ────────────────────────────────────────────────────────────────
    def search_instagram_audio(self, account_id, query, audio_type="music"):
        """Up to ~30 Instagram catalog tracks (audio_type "music" or "original_sound").
        No query returns Instagram's trending list."""
        params = {"audioType": audio_type}
        if query:
            params["q"] = query
        return self._req("GET", f"/accounts/{account_id}/instagram/audio", params=params).get("audio") or []

    def tiktok_music(self, account_id):
        """TikTok's Commercial Music Library list (trending, not searchable, not paged)."""
        return self._req("GET", f"/accounts/{account_id}/tiktok/commercial-music").get("tracks") or []

    # ── posts ────────────────────────────────────────────────────────────────
    def body(self, platform, account, reel, media_url, when_utc, settings, song=None):
        entry = {"platform": platform, "accountId": account["id"]}
        body = {"content": captions.for_platform(platform, reel, settings),
                "mediaItems": [{"type": "video", "url": media_url}],
                "scheduledFor": when_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "platforms": [entry]}
        if platform == "instagram":
            psd = {"thumbOffset": 1500}
            trial = (settings or {}).get("ig_trial", "off")
            if trial in ("MANUAL", "SS_PERFORMANCE"):
                psd["trialParams"] = {"graduationStrategy": trial}
            else:
                psd["shareToFeed"] = True
            if song:
                # The track plays on its own: the video's own sound is muted.
                psd["audioConfiguration"] = {"audioId": song["id"], "audioVolume": 100, "videoVolume": 0}
            entry["platformSpecificData"] = psd
        elif platform == "tiktok":
            tt = {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "allow_duet": True,
                  "allow_stitch": True, "content_preview_confirmed": True, "express_consent_given": True,
                  "video_made_with_ai": False, "video_cover_timestamp_ms": 1500}
            if song:
                tt["musicSoundInfo"] = {"musicSoundId": song["id"], "musicSoundVolume": 100}
                tt["videoOriginalSoundVolume"] = 0
            body["tiktokSettings"] = tt
        return body

    def schedule(self, platform, account, reel, media_url, when_utc, settings, song=None):
        """Schedule one reel on one platform; returns Zernio's post id."""
        j = self._req("POST", "/posts", json=self.body(platform, account, reel, media_url, when_utc, settings, song))
        pid = (j.get("post") or {}).get("_id") or j.get("_id")
        if not pid:
            raise ZernioError(str(j.get("error") or j.get("message") or j)[:300])
        return str(pid)

    def replace_media(self, post_id, new_url):
        """Swap the video on a scheduled post, keeping its time, caption and music."""
        self._req("PUT", f"/posts/{post_id}", json={"mediaItems": [{"type": "video", "url": new_url}]})
