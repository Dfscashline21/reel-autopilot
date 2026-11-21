# Reel Autopilot: New Customer Runbook

_Last updated Nov 20, 2026. For whoever sets up the app for a customer._

## Overview

A new customer can be live in about an hour of hands-on time: 15 minutes for accounts, 5 minutes to deploy, 30 to 40 minutes in the setup wizard. After that the app builds and schedules their reels every day on its own.

**What the customer gets:** their own copy of Reel Autopilot, a private web app on Railway. It turns their B-roll and hooks into short vertical reels and schedules them through Blotato to Instagram, TikTok, YouTube Shorts and Facebook. Each reel uses footage the app hasn't used before. When fresh footage runs out, it reuses older footage with a new speed, crop, color or mirror.

**Who does what**

| Task | You (setting it up) | Customer |
| --- | --- | --- |
| Create Blotato and Railway accounts in the customer's name, with their payment method | Walk them through it on a call | Signs up and pays |
| Connect social accounts in Blotato | Guide on the call | Logs in to each platform (only they can) |
| Collect B-roll, hooks and music | Send the input checklist; write or edit hooks | Films B-roll, approves hooks, supplies music |
| Deploy and run the setup wizard | Yes, in your own Railway account, with their Blotato key | Nothing |
| Go-live checks and the first week's review | Yes | Nothing |
| Hand-off | Transfer the Railway project to them; share the app address | Needs a paid Railway plan; accepts the invite; sets their own password |
| Reply to comments, add footage every 1 to 2 weeks | Optional, if you offer ongoing support | Yes |

**Running costs for the customer**

| Item | Cost |
| --- | --- |
| Railway hosting | Usage-based, roughly $5 to $10 a month |
| Blotato | A paid plan with API access (check current pricing on blotato.com) |
| Music | Free if they use their own or royalty-free tracks |

**Links:** deploy button https://railway.com/deploy/7PMRjL · code and README https://github.com/Dfscashline21/reel-autopilot

## Before you start

Collect everything below before deploying. Missing footage or hooks is the usual reason a setup stalls.

### Accounts

- [ ] **Blotato** account on a paid plan, with Instagram (business or creator), TikTok, YouTube and Facebook connected under **Accounts**
- [ ] **Railway** account (railway.com) with a payment method
- [ ] Instagram link in bio pointing to the customer's offer page

### Inputs

| Input | What good looks like | Common problem |
| --- | --- | --- |
| B-roll | 5 to 10 vertical phone clips, 5 to 10 minutes in total, of the customer doing their work; good light; nobody else in frame unless they've agreed | Too little footage (each 15 seconds makes about one reel) or horizontal video |
| Hooks | 20 to 40 short on-screen lines, one per line as `hook \| caption \| tag`, mixing personal goal, dare, comment-question and offer hooks | Long hooks (over about 8 words) or sales language |
| Captions | One short line per hook with a call to action | Too many hashtags (the app trims Instagram to 5) |
| Music | 3 to 5 tracks the customer has the rights to use, 2 minutes or longer | Popular songs they don't own; reels get muted |
| Schedule | Time zone, 3 to 5 posting times when their audience is online | Times too close to now on day one (reels need 10 minutes' lead) |
| Rules | Words to avoid, any required notice ("Not medical advice"), hashtags | Regulated topics on TikTok |

**Large files:** phone video is often 200 MB to 1 GB per clip. The upload page accepts it, but a slow connection can take a while. If an upload stalls, export at a lower quality or trim each clip to its best 30 to 60 seconds.

## Step-by-step setup

Follow these in order. Each step ends with a check; don't move on until it passes.

### 1. Blotato (customer, about 10 minutes)

1. Sign up at blotato.com and choose a paid plan.
2. Open **Accounts** and connect each platform. For Instagram, use a business or creator account. For Facebook, pick the Page to post to.
3. Open **Settings → API** and copy the API key.

**Check:** every platform shows as connected in Blotato's Accounts page.

### 2. Deploy in your own Railway account (about 5 minutes)

You build and test the app in **your** Railway account, then transfer the whole project to the customer at hand-off. You never need their Railway login.

1. Signed in to Railway as yourself, open https://railway.com/deploy/7PMRjL.
2. Fill in **APP\_PASSWORD** with a setup password you'll use. The customer replaces it after the transfer.
3. Click **Deploy**. The first build takes 3 to 5 minutes.
4. Rename the project after the customer (project → Settings → name), so it's easy to find and transfer later.
5. Open the service, then **Settings → Networking**, and copy the public address (ending in `.up.railway.app`). If none is shown, click **Generate Domain**.

**Check:** opening the address shows the Reel Autopilot login page, and `<address>/health` shows `{"ok":true}`.

**Hosting cost while it's yours:** Railway bills your account until the transfer, usually a few dollars for a setup week. Transfer soon after go-live, or include it in your setup fee.

### 3. Setup wizard (30 to 40 minutes)

Log in, then work down the Setup page. Each step turns green when done.

1. **Connect Blotato:** paste the key and click Connect. The app lists the connected accounts. Untick any platform they don't want; X is off by default. Under **Instagram reels**, choose how Instagram posts go out, then click **Save platforms**:
   - **Regular reels** (the default): shown to followers and shared to the feed.
   - **Trial reels, Instagram shares if they do well:** shown to non-followers first; Instagram shows each one to followers too when it performs.
   - **Trial reels, I share myself:** shown to non-followers only, until the customer shares a reel to followers in the Instagram app.

   Trial reels suit a new account testing hooks, or a customer who doesn't want every test reel on their profile grid. Instagram doesn't offer trial reels to every account; if theirs doesn't qualify, Instagram posts show **failed** on Home, so switch back to Regular reels. The setting covers Instagram only and applies to every Instagram reel not yet handed to Blotato.
2. **Upload B-roll:** choose all clips and click Upload. Each converts in the background (about 1 minute per minute of 4K video). Refresh until all show **Ready**. Open **review** on each clip and untick any stretch with someone else in shot or nothing happening.
3. **Hooks and captions:** paste one per line as `hook | caption | tag`, or upload a .txt or .json file. Click Save.
4. **Music:** upload the tracks. Optional: without music the reels are silent.
5. **Schedule and captions:** time zone, posting times, link in bio, hashtags, TikTok hashtags, words to avoid, required notice, YouTube daily limit (keep 3 or less), reel length (13 seconds is the default), days to build ahead.

   **Posting times: one reel each, grouped into sets.** Every time on the list gets its own reel, every day, posted to every platform that's ticked. The times are grouped in order into sets of 3 (the **Reels per set** setting). Each set is one hook and caption on 3 different pieces of one video.

   - Write them in 24-hour format in the customer's time zone, separated by commas, for example `07:00, 07:02, 07:04, 07:06, 07:08, 07:10`.
   - Example with those 6 times: 7:00–7:04 is Set 1 (hook 1 on 3 pieces of one video), 7:06–7:10 is Set 2 (hook 2 on 3 pieces of another video). The next day takes hooks 3 and 4, and so on, cycling back to the top of the hook list.
   - The app picks each set's video. A video isn't used again until it has rested 7 days (the **Days before a video can be used again** setting). Videos needed to post every day = sets a day × rest days: 2 sets a day needs 14 videos. With fewer, sets without a rested video are skipped, and Home shows "videos ready / needed".
   - Each video needs at least 3 pieces of about 15 seconds (45 seconds or more) so a set never repeats a piece.
   - YouTube gets reels only up to the YouTube daily limit (normally the earliest times); reels past the limit still go to the other platforms.
   - A time added today is built that same day if it's at least 10 minutes away; if it lands in a set that has already started, it uses that set's hook and video. Removing a time stops future reels at that slot; a reel already built for it still posts unless you Skip it on Home.
6. **Preview:** click **Make sample reels**, wait about a minute, refresh, and play all three. Check the text is readable, the face isn't covered and the music fits.

When everything is green, click **Start autopilot**.

### 4. Go-live checks (first day)

- [ ] Home shows **Running** and lists today's or tomorrow's reels with thumbnails.
- [ ] Within 6 hours of the first posting time, each reel shows **scheduled** next to each platform.
- [ ] In Blotato, **Posts → Scheduled** lists the same reels at the right local times.
- [ ] After the first time slot passes, the post is live on each platform and shows under **Published** in Blotato.
- [ ] The Activity feed on Home shows no red errors.

**Tip:** for a first test, set one or two posting times 30 to 60 minutes ahead, "Days to build ahead" to 0 and Instagram only. To keep the test away from the customer's followers, choose a trial reels option first. Check the post publishes, then add the real times and switch Instagram to the mode the customer wants.

## Running it

The app checks every 5 minutes. It keeps reels built for today plus the next 2 days, and hands each reel to Blotato 6 hours before it posts.

**How a reel moves through the app**

| Status on Home | Meaning | Can the customer still change it? |
| --- | --- | --- |
| pending | Built, waiting for its hand-off window | Yes: Skip it on Home |
| scheduled | In Blotato, will post at its time | Only in Blotato: Posts → Scheduled → Move to drafts |
| off | Platform turned off, or over the daily YouTube limit | Not applicable |
| failed | Blotato refused it; hover to see why | No retry; fix the cause so later reels post |
| missed | Its time passed before it could be scheduled (app paused or down) | No |
| skipped | Customer skipped it | No |

**Daily (5 minutes, customer)**

- Reply to comments on yesterday's reels.
- Glance at Home: no red lines in Activity, today's reels show **scheduled**.

**Weekly (20 minutes, customer or you)**

- Compare last week's reels in each platform's insights or Blotato Analytics: views, average watch time, comments, follows.
- On the Setup page, remove the 3 to 5 weakest hooks and add new ones. Add a winning hook again with a fresh caption so it runs on new footage.

**Footage refresh (every 1 to 2 weeks)**

- Home shows **days of fresh footage left**. When it drops below 3, film and upload new clips.
- Until then, the app reuses older footage with a different speed, crop, color or mirror, so no reel is an exact repeat.

**Changing settings:** edit them on the Setup page at any time. A posting time added today is built within 5 minutes, as long as it's at least 10 minutes away. Reels already built (up to 2 days ahead) keep their time and hook; Skip any you don't want. Hashtags, link, required notice, words to avoid and the Instagram reels mode apply to every reel not yet handed to Blotato.

## Troubleshooting

Start with the Activity feed on Home: most problems show there in red, with Blotato's own message. For anything about the app itself, open the service in Railway and read **Deployments → View logs**.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Login page says "No password is set" | `APP_PASSWORD` is missing or blank in Railway | Railway → service → Variables → set `APP_PASSWORD` → let it redeploy |
| Password not accepted | Wrong password | It's in Railway → service → Variables → `APP_PASSWORD` |
| Site doesn't load | Service crashed or redeploying | Railway → Deployments: wait for the green check, or click Redeploy and read the logs |
| Clip stays "Processing" | Long 4K or HDR video converting | Wait up to about 1 minute per minute of footage, then refresh |
| Clip shows "Failed" | Unusual file format or damaged file | Re-export as MP4 (H.264) from the phone or editor and upload again |
| Upload stalls or errors | Very large file or slow connection | Upload one clip at a time, or export at lower quality or trim to 30 to 60 seconds |
| "Blotato said: 401" | Wrong API key; a copied key often loses a trailing `=` | Copy the key again from Blotato Settings → API and paste it whole |
| No platforms listed after connecting | No social accounts connected in Blotato | Connect them in Blotato → Accounts, then click Refresh accounts |
| Start says "Still needed: …" | A setup step isn't finished | Complete the steps it names |
| Home shows nothing built | The app runs every 5 minutes, or a build failed | Press Run now; look for "Build failed" in Activity (usually no ready clips or no hooks) |
| YouTube shows failed: "maximum number of 10 posts" | Blotato's YouTube limit of about 10 posts a day per account, counting posts from other tools | Lower "Most reels a day to YouTube" in Setup |
| Instagram shows failed after turning on trial reels | Instagram hasn't made trial reels available to this account yet | Setup → Instagram reels → Regular reels → Save platforms; try trial reels again later |
| A reel shows missed | The app was paused or down when it was due for hand-off | Check Railway for a crash; nothing to do for the missed reel |
| Post published at the wrong hour | Wrong time zone in Setup | Fix the time zone; already-built reels keep their old time |
| Need to cancel a reel already in Blotato | Reels can't be pulled back once handed off | Blotato → Posts → Scheduled → Move to drafts |
| TikTok removed a post | TikTok flags regulated or sales-heavy topics without detail | Soften captions and words-to-avoid, or untick TikTok in Setup |
| "Footage is running low" | Most footage stretches already used | Upload new clips; until then reels reuse footage with a new look |
| Disk nearly full (Home) | Many large source clips | Remove clips no longer needed, or raise the volume size in Railway → service → Volume. Reel videos older than 7 days are deleted automatically |

## Pausing, handing off and shutting down

Reels already in Blotato always post unless they're moved to drafts in Blotato, so check Blotato whenever you stop the app.

| Goal | What to do |
| --- | --- |
| Pause for a few days | Home → **Pause**. Nothing new is built or handed off. Reels already in Blotato (up to 6 hours ahead) still post. **Resume** picks up again |
| Stop completely | Setup → **Stop autopilot**. Then in Blotato, move any remaining scheduled reels to drafts |
| Hand the app to the customer after a done-for-you setup | The customer needs a Railway account on an active Hobby or Pro plan. In Railway, open the project → Settings → Members, invite the customer's email (project members work on Hobby; workspace invites don't). Once they accept, click the three dots next to their name → Transfer Ownership. Give them the app address; then the customer changes `APP_PASSWORD` in Railway |
| Change the password | Railway → service → Variables → edit `APP_PASSWORD`; it redeploys in about a minute |
| Shut it down for good | Stop autopilot, clear Blotato's scheduled reels, then Railway → project → Settings → **Delete project**. This deletes the footage and history on the disk, so download anything worth keeping first |
| Update to the latest version | Railway → service → Settings → Source shows the GitHub repo; redeploy to pick up new code. Settings, clips and history on the disk are kept |

**Not yet tested:** that the app's disk (clips, settings, footage history) moves with the project in a transfer. Railway treats the volume as part of the project, so it should. Confirm it on the first customer: after the transfer, log in and check Home and Setup still show everything.
