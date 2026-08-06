# Getting your Spotify data — a walkthrough

sonic-stats runs on your **Spotify Extended Streaming History** export — a
`.zip` file Spotify emails you after you request it. This isn't instant (it
can take up to a month for the full history), so this is usually the very
first thing to do, before you touch this repo at all.

This guide walks through requesting it, step by step.

---

## Two exports — request the right one

Spotify's privacy settings offer two separate downloads. They are **not**
the same thing:

| Export | What it contains | Typical wait |
|---|---|---|
| **Account data** | Your profile, playlists, and a *limited* recent listening history | ~5 days |
| **Extended streaming history** | Your **complete** play-by-play history since you created your account, in JSON | Up to **30 days** |

**sonic-stats needs Extended streaming history.** Account data alone won't
give you enough to work with — request Extended streaming history even if
you also grab Account data out of curiosity.

---

## Step by step

### 1. Log in and open your Account page
Go to [spotify.com](https://www.spotify.com) and log in. Click your profile
icon (top right) → **Account**.

`[SCREENSHOT: profile menu open, "Account" highlighted]`

### 2. Find Privacy settings
On the Account page, scroll to **Privacy settings**.

`[SCREENSHOT: Account page with Privacy settings section visible]`

### 3. Open "Download your data"
Click through to the data-download section. You'll see the two export
options described above.

`[SCREENSHOT: Download your data page, both export options visible]`

### 4. Request Extended streaming history
Click **Request** next to *Extended streaming history*. (Feel free to also
request *Account data* — they're independent and don't affect each other.)

`[SCREENSHOT: Extended streaming history row with Request button]`

### 5. Confirm via email
Spotify sends a confirmation email right away — this just verifies you're
the one who asked. Open it and click the confirmation link, or the request
never actually starts.

`[SCREENSHOT: Spotify confirmation email, "Confirm request" button/link]`

### 6. Wait
This is the part with no shortcut. Extended streaming history can take **up
to 30 days** — most people see it sooner, but don't build a workflow around
"any day now." There's nothing to check or click in the meantime.

### 7. Download the export
When it's ready, Spotify emails you a download link (valid for a limited
time — don't let it sit for weeks). Download the `.zip` and keep it
somewhere you can find it.

`[SCREENSHOT: "Your data is ready" email with download link/button]`

### 8. Load it into sonic-stats
Come back to the [User's Guide](USER_GUIDE.md#4-launch-and-load-your-history)
— on first launch, sonic-stats opens a file picker that takes the `.zip`
directly, no unzipping required.

---

## FAQ

**I never got the confirmation email — now what?**
Check spam/promotions first. If it's genuinely missing, just redo step 4;
requesting again is harmless.

**Can I request it again later (e.g. a year from now) to get an updated
history?**
Yes — there's no limit on re-requesting. sonic-stats also supports
incremental syncs via the Spotify API for staying current between exports
(see the User's Guide), so most people only need to do this full request
once.

**The download link expired before I clicked it.**
Submit a new request (steps 4–5) — Spotify doesn't reissue an expired link.

**Do I need a Spotify Developer account for this part?**
No — that's separate, and only needed later for enrichment/sync (see the
User's Guide's Prerequisites section). Requesting your data export just
needs your regular Spotify login.

---

*Screenshots above are placeholders pending capture from the live Spotify
site — see `gen_guide_screenshots.py` for how this repo's other guide
screenshots are generated.*
