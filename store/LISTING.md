# Store listing kit

Everything needed to publish **AI Token Tracker for Claude** on the Chrome Web Store, Microsoft Edge Add-ons
and Firefox Add-ons. Copy the text from here; upload the files listed.

| What | File |
|---|---|
| Package for Chrome and Edge | `dist/AITokenTracker-Claude-<version>-chrome.zip` (run `./build.sh`, or take it from Releases) |
| Package for Firefox | `dist/AITokenTracker-Claude-<version>-firefox.zip` |
| Icon (128×128) | `icons/icon128.png` |
| Screenshots (1280×800) | `store/images/screenshot-1-chat.png` … `screenshot-4-settings.png` |
| Small promo tile (440×280) | `store/images/promo-small-440x280.png` |
| Marquee tile (1400×560, optional) | `store/images/promo-marquee-1400x560.png` |
| Privacy policy URL | `https://github.com/Surajprem7/AI-Token-Tracker/blob/main/PRIVACY.md` (use your default branch name if it isn't `main`) |
| Homepage / support URL | `https://github.com/Surajprem7/AI-Token-Tracker` |

---

## Texts (same for every store)

**Name** (max 75 characters)

> AI Token Tracker for Claude

**Short description / summary** (Chrome: max 132 characters)

> See your Claude usage limits, chat length and how many messages you have left, right inside claude.ai.

**Category:** Productivity (Chrome: *Productivity → Tools*; Edge: *Productivity*; Firefox: *Other* or *Tabs*… pick *Productivity* if offered)

**Language:** English (the extension itself has 10 languages)

**Full description**

> Know where you stand with your Claude limits without leaving the chat.
>
> AI Token Tracker adds a small "Usage" section to claude.ai's sidebar and one line under the message box:
>
> • Usage bars for your plan's limits: Session (5 hours), Weekly and per-model weekly limits, plus extra usage if you have it on, each with the time until it resets
> • A PEAK tag on weekdays 5–11 AM Pacific, when the session limit runs down faster, with the times in your own clock
> • A mark on the session bar for the weekly limit of the model you're using
> • The open chat's length in tokens, out of the model's context window (200K to 1M)
> • Whether the chat is still cached: a message sent while it's cached uses much less of your limit
> • Roughly how much of your session the next message will use, and about how many messages you have left, learned from your own use
> • How many times Claude has compacted (summarised) a long chat
> • The session line on Claude Code on the web too
>
> The toolbar icon shows your session % at a glance, and the popup has all the bars, the current chat and the settings. You can get a notification when a limit resets, choose which bars to show, switch between accounts (personal and team), and pick from 10 languages: English, Hindi, Spanish, German, French, Italian, Portuguese, Indonesian, Japanese and Korean.
>
> Private by design: it only runs on claude.ai, reads what the page already loads with your own login, and keeps everything in your browser. No tracking, no analytics, no servers. Optionally, add your own Anthropic API key for exact token counts.
>
> Usage across claude.ai, Claude Desktop and Claude Code counts towards the same limits, so the bars include all of it.
>
> This is an independent, open-source project. It is not affiliated with, endorsed by or sponsored by Anthropic. "Claude" is a trademark of Anthropic.
>
> Source code: https://github.com/Surajprem7/AI-Token-Tracker

---

## Chrome Web Store (one-time $5 developer fee)

1. Go to the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole), sign in with a Google account, pay the $5 registration fee and verify your email.
2. Click **New item** and upload `AITokenTracker-Claude-<version>-chrome.zip`.
3. **Store listing** tab: paste the name, summary and description above, set the category and language, upload the icon, the 4 screenshots and the small promo tile.
4. **Privacy** tab, use these answers:

   **Single purpose**
   > Shows the user's claude.ai usage limits and the open chat's token usage inside claude.ai and in the toolbar popup.

   **Permission justifications**

   | Permission | Justification |
   |---|---|
   | `storage` | Saves the user's settings and the latest usage numbers in the browser so the popup and badge can show them. |
   | `alarms` | Refreshes the usage numbers every few minutes and schedules the notification when a limit resets. |
   | `notifications` | Tells the user when one of their Claude limits has reset (can be turned off). |
   | Host permission `https://claude.ai/*` | Reads the user's own usage and the open chat from claude.ai, and shows the usage bars on the claude.ai page. |
   | Optional host permission `https://api.anthropic.com/*` | Only requested if the user adds their own Anthropic API key, to count tokens exactly with Anthropic's token counting API. |

   **Remote code:** No, I am not using remote code. (Copies installed from a GitHub zip fetch the latest version number from api.github.com once a day; the store version skips this, because the browser updates it.)

   **Data usage:** tick **Website content** (the open chat's text is read to count its length; it leaves the browser only if the user adds an API key, and then only to Anthropic's token counter). Tick **Authentication information** only if the reviewer asks about the optional API key. Then tick all three certifications (not sold, not used for unrelated purposes, not used for creditworthiness).

   **Privacy policy URL:** the PRIVACY.md link from the table above.

5. **Distribution:** Public, all regions. Click **Submit for review**. Reviews usually take a few days.

## Microsoft Edge Add-ons (free)

1. Register at [Partner Center → Microsoft Edge program](https://partner.microsoft.com/dashboard/microsoftedge/overview). You can sign in with your GitHub account. There is no fee.
2. **Create new extension** and upload the same `-chrome.zip`.
3. Fill in the listing with the same texts, icon, screenshots and promo tile. Privacy policy URL as above.
4. Submit. Edge reviews usually take a few business days.

## Firefox Add-ons (free)

1. Sign in at the [Firefox Add-on Developer Hub](https://addons.mozilla.org/developers/) and click **Submit a New Add-on** → **On this site**.
2. Upload `AITokenTracker-Claude-<version>-firefox.zip`.
3. **Source code:** Mozilla asks for source when a file is minified. `tokenizer.js` is a bundled build of the open-source *gpt-tokenizer* library, so answer **Yes** and upload a zip of this repository. Paste these build notes for the reviewer:

   > `tokenizer.js` is built from gpt-tokenizer 4.1.2 (MIT) with esbuild. To rebuild: `cd tokenizer-build && npm install && npm run build`. Every other file is unminified source as shipped. `./build.sh` makes the Firefox zip from them with `manifest_firefox.json`.

4. Fill in the listing with the same texts (summary max 250 characters), screenshots, and the privacy policy. License: **MIT License**.
5. Submit. Firefox reviews can take from a few hours to a couple of weeks.

---

## After publishing

- Add the store links to the README's Install section.
- For each new version: raise `version` in both manifests, push a `v<version>` tag (GitHub builds the zips), then upload the new zips in each dashboard.
