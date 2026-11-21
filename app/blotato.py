"""Small Blotato REST client (https://help.blotato.com/api)."""
import time

import httpx

from . import captions

API = "https://backend.blotato.com/v2"
PLATFORMS = ["instagram", "tiktok", "youtube", "facebook", "twitter"]
NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube",
         "facebook": "Facebook", "twitter": "X"}


class BlotatoError(Exception):
    pass


class Client:
    def __init__(self, api_key, http=None):
        self.key = (api_key or "").strip()
        self.http = http or httpx.Client(timeout=120)

    def _req(self, method, path, **kw):
        headers = {"blotato-api-key": self.key}
        for attempt in range(4):
            r = self.http.request(method, API + path, headers=headers, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text
                except ValueError:
                    msg = r.text
                raise BlotatoError(f"{r.status_code}: {str(msg)[:300]}")
            return r.json() if r.content else {}
        raise BlotatoError("gave up after retries")

    def me(self):
        return self._req("GET", "/users/me")

    def accounts(self):
        """{platform: {"id", "username", "page_id"}} for every connected account."""
        out = {}
        for a in self._req("GET", "/users/me/accounts").get("items", []):
            p = a.get("platform")
            if p not in PLATFORMS or p in out:
                continue
            item = {"id": str(a.get("id")), "username": a.get("username") or a.get("fullname") or ""}
            if p == "facebook":
                subs = self._req("GET", f"/users/me/accounts/{a['id']}/subaccounts").get("items", [])
                if subs:
                    item["page_id"] = str(subs[0]["id"])
                    item["username"] = item["username"] or subs[0].get("name", "")
            out[p] = item
        return out

    def upload(self, file_path):
        """Upload a local file; returns the public URL Blotato will post from."""
        name = file_path.rsplit("/", 1)[-1]
        j = self._req("POST", "/media/uploads", json={"filename": name})
        with open(file_path, "rb") as f:
            r = self.http.put(j["presignedUrl"], content=f.read(),
                              headers={"Content-Type": "video/mp4"}, timeout=600)
        if r.status_code >= 400:
            raise BlotatoError(f"upload failed {r.status_code}: {r.text[:200]}")
        return j["publicUrl"]

    def schedule(self, platform, account, reel, media_url, when_utc, settings):
        target = {"targetType": platform}
        if platform == "instagram":
            trial = (settings or {}).get("ig_trial", "off")
            if trial in ("MANUAL", "SS_PERFORMANCE"):
                # Trial reels go to non-followers first, so they aren't shared to the feed.
                target.update({"mediaType": "reel", "trial": {"graduationStrategy": trial}})
            else:
                target.update({"mediaType": "reel", "shareToFeed": True})
        elif platform == "tiktok":
            target.update({"privacyLevel": "PUBLIC_TO_EVERYONE", "disabledComments": False,
                           "disabledDuet": False, "disabledStitch": False,
                           "isBrandedContent": False, "isYourBrand": False,
                           "isAiGenerated": False})
        elif platform == "youtube":
            target.update({"title": captions.youtube_title(reel), "privacyStatus": "public",
                           "shouldNotifySubscribers": False, "isMadeForKids": False,
                           "containsSyntheticMedia": False})
        elif platform == "facebook":
            target.update({"pageId": account.get("page_id"), "mediaType": "reel"})
        body = {"post": {"accountId": account["id"],
                         "content": {"text": captions.for_platform(platform, reel, settings),
                                     "mediaUrls": [media_url], "platform": platform},
                         "target": target},
                "scheduledTime": when_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        j = self._req("POST", "/posts", json=body)
        if not j.get("postSubmissionId"):
            raise BlotatoError(str(j.get("message") or j)[:300])
        return j["postSubmissionId"]
