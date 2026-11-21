# Reel Autopilot

Upload your B-roll and hooks once. Reel Autopilot turns them into short vertical reels every day and schedules
them to Instagram, TikTok, YouTube Shorts and Facebook through Blotato. Every reel uses footage it hasn't used
before, or changes the speed, crop and color when footage has to repeat.

## What you need

- A **Railway** account (railway.com). Hosting runs about $5–10 a month.
- A **Blotato** account on a paid plan (blotato.com), with your social accounts connected.
- 5–10 minutes of vertical B-roll of you, 20+ hooks, and optionally a few music tracks you have the rights to use.

## Set it up (about 15 minutes)

1. **Blotato:** sign up, open **Accounts**, connect Instagram, TikTok, YouTube and Facebook. Copy your API key
   from **Settings → API**.
2. **Deploy:** click **Deploy on Railway** below. Set `APP_PASSWORD` to a password you'll remember, then deploy.
   Railway attaches a disk at `/data` where your clips and reels live.

   [![Deploy on Railway](https://railway.com/button.svg)](RAILWAY_TEMPLATE_URL)

3. **Open the app:** in Railway, open the service, go to **Settings → Networking → Generate Domain**, and open
   that address. Log in with your password.
4. **Follow the six setup steps:**
   1. Paste your Blotato key; the app finds your connected accounts. Untick any you don't want.
   2. Upload your clips. Big phone videos are fine; they convert in the background. Open **review** on each
      clip to untick any stretch with someone else in shot.
   3. Add hooks, one per line: `hook | caption | tag`.
   4. Add music (optional).
   5. Set your time zone, posting times, link in bio, hashtags, words to avoid and any required notice.
   6. Make sample reels, check them, then press **Start autopilot**.

## Day to day

- The **Home** page shows the next reels, which platforms they're scheduled on, and how many days of fresh
  footage are left.
- Reels are built 2 days ahead and handed to Blotato 6 hours before they post. Until then you can **Skip** one.
  After that, remove it in Blotato: **Posts → Scheduled → Move to drafts**.
- **Pause** stops new reels. Reels already in Blotato still post.
- Upload new clips when the footage counter gets low (about every 1–2 weeks at 5 reels a day).
- Reply to comments. That part stays human.

## Good to know

- **YouTube** accepts about 10 posts a day per account through Blotato, so the app sends at most 3 reels a day
  there by default (change it in step 5).
- **Instagram** rejects posts with more than 5 hashtags; the app trims extras.
- **TikTok** gets a plain caption: no links, no notice line, up to 4 hashtags.
- Use only music you have the rights to. Platforms mute or remove reels with unlicensed audio.

## Credits

The content approach (a real person in everyday footage, short hooks with a specific goal or stake, and
recreating what works) comes from the 21-Day Trial Reel Challenge by christinealeta × abby. This app automates
the production and posting; see their challenge for the method itself.

## For developers

```
pip install -r requirements.txt
DATA_DIR=./data APP_PASSWORD=pw COOKIE_SECURE=0 uvicorn app.main:app --reload
pytest -q tests   # needs ffmpeg
```

Code map: `app/main.py` (web pages), `app/media.py` (clip conversion and footage windows), `app/builder.py`
(reel rendering), `app/runner.py` (the 5-minute autopilot loop and scheduling), `app/blotato.py` (posting),
`app/captions.py` (per-platform captions), `app/store.py` (files on the `/data` disk).

### Publishing the Railway template (one time, for the owner of this repo)

In Railway: **Templates → New Template → from this GitHub repo**. Add a **volume** mounted at `/data`, add the
variable `APP_PASSWORD` (mark it as required), and set the health check path to `/health`. Publish, then replace
`RAILWAY_TEMPLATE_URL` above with the template's deploy link.
