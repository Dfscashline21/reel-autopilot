"""Per-platform caption text."""
import re

IG_MAX_HASHTAGS = 5
_TAG = r"(?<!\w)#\w+"


def _banned(settings):
    return [w.strip() for w in (settings.get("banned_words") or "").split(",") if w.strip()]


def _strip_banned(text, words):
    for w in words:
        text = re.sub(rf"\b{re.escape(w)}\b", "", text, flags=re.I)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _limit_tags(text, n):
    seen = 0

    def keep(m):
        nonlocal seen
        seen += 1
        return m.group(0) if seen <= n else ""
    return re.sub(r"[ \t]{2,}", " ", re.sub(_TAG, keep, text)).strip()


def base(reel, settings):
    """The caption as the person wrote it, plus link line, notice and hashtags."""
    text = (reel.get("caption") or "").strip()
    link = (settings.get("link") or "").strip()
    if link and "link in bio" not in text.lower():
        text = f"{text} Link in bio.".strip()
    notice = (settings.get("notice") or "").strip()
    if notice:
        text = f"{text}\n\n{notice}"
    tags = (settings.get("hashtags") or "").strip()
    if tags:
        text = f"{text}\n\n{tags}"
    return _strip_banned(text, _banned(settings))


def for_platform(platform, reel, settings):
    text = base(reel, settings)
    if platform in ("instagram", "facebook"):
        return _limit_tags(text, IG_MAX_HASHTAGS)
    if platform == "tiktok":
        # TikTok: no links, no notice line, plain tags only.
        t = (reel.get("caption") or "").strip()
        t = re.sub(r"https?://\S+", "", t)
        t = re.sub(_TAG, "", t)
        t = _strip_banned(t, _banned(settings))
        tags = (settings.get("tiktok_hashtags") or settings.get("hashtags") or "").strip()
        tags = " ".join(re.findall(_TAG, tags)[:4])
        return f"{t}\n\n{tags}".strip()
    if platform == "youtube":
        return _limit_tags(text, 4) + " #shorts"
    if platform == "twitter":
        return text[:280]
    return text


def youtube_title(reel):
    t = (reel.get("hook") or reel.get("caption") or "Short").strip()
    return t if len(t) <= 100 else t[:97].rsplit(" ", 1)[0] + "..."
