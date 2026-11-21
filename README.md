# Reel Autopilot

Upload your B-roll and hooks once. Reel Autopilot turns them into short vertical reels every day and schedules
them to Instagram, TikTok, YouTube Shorts and Facebook through Blotato. Every reel uses footage it hasn't used
before, or changes the speed, crop and color when footage has to repeat.

## What you need

- A **Railway** account (railway.com). Hosting runs about $5–10 a month.
- A **Blotato** account on a paid plan (blotato.com), with your social accounts connected.
- 5–10 minutes of vertical B-roll of you, 20+ hooks, and optionally a few music tracks you have the rights to use.

Setting it up for a customer? Follow the [new customer runbook](docs/RUNBOOK.md).

## Set it up (about 15 minutes)

1. **Blotato:** sign up, open **Accounts**, connect Instagram, TikTok, YouTube and Facebook. Copy your API key
   from **Settings → API**.
2. **Deploy:** click **Deploy on Railway** below. Set `APP_PASSWORD` to a password you'll remember, then deploy.
   Railway attaches a disk at `/data` where your clips and reels live.

   [![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/7PMRjL)

3. **Open the app:** in Railway, open the service, go to **Settings → Networking → Generate Domain**, and open
   that address. Log in with your password.
4. **Follow the six setup steps:**
   1. Paste your Blotato key; the app finds your connected accounts. Untick any you don't want. Choose how
      Instagram reels go out: regular reels, or trial reels (shown to non-followers first) that Instagram shares
      with followers if they do well, or that you share yourself.
   2. Upload your clips. Big phone videos are fine; they convert in the background. Open **review** on each
      clip to untick any stretch with someone else in shot.
   3. Add hooks, one per line: `hook | caption | tag`.
   4. Add music (optional).
   5. Set your time zone, posting times, link in bio, hashtags, words to avoid and any required notice.
      Posting times are grouped into **sets** (3 reels per set by default): each set is one hook and caption on
      3 different pieces of one video. Each new set takes the next hook on your list, and a video isn't reused
      until it has rested 7 days. Both numbers can be changed.
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

- **Uploads:** there's no limit on clip length, but each upload must finish within Railway's 15-minute request
  limit, and clips selected together count as one upload. Upload large 4K files one or two at a time, or trim
  them to their best 30–60 seconds. Originals are deleted after conversion; the disk is 5 GB by default.
- **Trial reels** are only available to some Instagram accounts. If yours doesn't qualify, Instagram posts show
  as failed on Home; switch back to regular reels in Setup.
- **Adding a posting time** takes effect the same day if it's at least 10 minutes away.
- **How many videos you need:** sets a day × rest days. 2 sets a day with a 7-day rest needs 14 videos to post
  every day. With fewer, sets that have no rested video are skipped and Home shows a warning.

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

### The Railway template

The deploy button uses template `7PMRjL`: this repo, a volume at `/data`, a required `APP_PASSWORD`, and a public
domain on port 8080. To change it, edit the source project in Railway and regenerate the template.
