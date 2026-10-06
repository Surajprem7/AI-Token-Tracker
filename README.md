# AI Token Tracker

A small desktop app (plus a command-line tool) that shows how many tokens you spend with your AI tools, and roughly what they cost:
per session, per prompt, per AI tool, per engine (model), per app and per project.

It reads the log files the AI tools already save on your computer. It needs **no API keys and no account**.
It only reads those files and never changes them. (A few optional extras use the internet, see Privacy.)

## Which AIs it tracks

| AI tool | Where it reads from (Windows: the same paths under your user folder) | Setup |
|---|---|---|
| **Claude Code** (terminal, VS Code, JetBrains, Desktop, web, Agent SDK) | `~/.claude/projects/` | none |
| **OpenAI Codex CLI** (terminal, VS Code) | `~/.codex/sessions/` (or `$CODEX_HOME`) | none |
| **Google Gemini CLI** | `~/.gemini/tmp/*/chats/` | none |
| **Qwen Code** | `~/.qwen/projects/*/chats/` (or `$QWEN_HOME`) | none |
| **OpenCode** | `~/.local/share/opencode/opencode.db` (and older `storage/` files) | none |
| **Cline** app / CLI | `~/.cline/data/sessions/` | none |
| **Cline, Roo Code, Kilo Code** in VS Code, Cursor, Windsurf, VSCodium, Trae or Kiro | the editor's `User/globalStorage/<extension>/tasks/` folder (Windows `%APPDATA%\Code\…`, macOS `~/Library/Application Support/Code/…`, Linux `~/.config/Code/…`) | none |
| **Cursor** | your usage list from cursor.com, using Cursor's own saved login (Cursor keeps no token log on your computer) | be logged in to Cursor |
| **GitHub Copilot** CLI and Copilot app | `~/.copilot/session-store.db` | none |
| **Kiro** | Kiro's `globalStorage/kiro.kiroagent/dev_data/devdata.sqlite` | none |
| **Zed** agent | Zed's `threads/threads.db` | none |
| **Goose** | Goose's `sessions/sessions.db` | none |
| **Droid** (Factory) | `~/.factory/sessions/` | none |
| **Grok Build** | `~/.grok/sessions/` | none |
| **Kimi CLI / Kimi Code** | `~/.kimi/sessions/`, `~/.kimi-code/sessions/` | none |
| **CodeBuddy, WorkBuddy** | `~/.codebuddy/projects/`, `~/.workbuddy/projects/` | none |
| **Pi, oh-my-pi, OmO** | `~/.pi`, `~/.omp`, `~/.omo` `/agent/sessions/` | none |
| **MiniMax Code, Craft Agents, Hermes Agent, AnythingLLM** | their own session folders or databases | none |
| **LM Studio** (local models, cost 0) | `~/.lmstudio/server-logs/` | none |
| **Any other AI** (ChatGPT/OpenAI API, Anthropic API, Ollama, Grok, your own scripts…) | `~/.ai-token-tracker/usage/*.jsonl` or `*.csv` | log it yourself, see below |

Roo Code and Kilo Code don't log the model on every request. The tracker looks it up in the task's history, or uses the cost the extension recorded.
Goose, Droid, Craft Agents and Hermes save only totals per session, and Kiro and LM Studio save no session at all (they're grouped by day).
`ai-tokens --sources` (or the Sources page) shows which of these it found on your computer.

Chats on the ChatGPT, claude.ai or Gemini websites don't reveal token counts; the browser extension (below) estimates them. Phone apps can't be tracked.
The **Plan limits** card still shows how much of each subscription they used. If you know the numbers, you can also add them by hand.

### Chats on the Claude, ChatGPT and Gemini websites (browser extension)

The websites don't show token counts, so the **AI Token Tracker browser extension** estimates them from the text of each chat
(the whole chat so far as input, each reply as output, about 4 characters per token) and sends them to the app.
Its popup also shows your Claude **Session (5h)** and **Weekly** usage, the tokens of each chat, and how many messages you sent
per ChatGPT/Gemini model in the last 3 hours. Those sites don't show their limits, so you can enter your plan's limit per model
(*Set limits*) and see a bar and when your oldest message stops counting (idea from lugia19's ChatGPT-Counter userscript).

1. Download `AITokenTracker-BrowserExtension.zip` from the Releases page and unzip it.
2. Chrome or Edge: open `chrome://extensions` (`edge://extensions`), turn on **Developer mode**, click **Load unpacked** and choose the folder.
   (Firefox: `about:debugging` → This Firefox → Load Temporary Add-on → pick `manifest.json`; it stays until Firefox restarts.)
3. In the app, open **Sources** and click **Connect browser extension**. A page opens in your browser and the extension connects by itself.
   (Or copy the connection code from the same card and paste it in the extension's popup.)

To make installing one click ("Add to Chrome"), the extension can be published in the Chrome, Edge and Firefox stores; see `browser-extension/STORE.md`.

It reads only the chat you have open, using the site's own data and your existing login, and sends results only to the app on your computer.
If the app is closed, chats wait in the extension and are sent later. Gemini is read from the page itself, so its numbers are rougher.

### Adding any other AI

By hand:
```bash
ai-tokens --add ChatGPT gpt-5 1200 350 --prompt "draft email"   # TOOL MODEL INPUT OUTPUT
```

From your own Python code (works with any provider's response object):
```python
from ai_token_tracker import log_usage

r = client.messages.create(...)          # Anthropic
log_usage("My app", r.model, r.usage.input_tokens, r.usage.output_tokens)

r = client.responses.create(...)         # OpenAI
log_usage("My app", r.model, r.usage.input_tokens, r.usage.output_tokens)
```

Or drop a CSV into `~/.ai-token-tracker/usage/`. It needs these columns:
`timestamp, tool, model, input_tokens, output_tokens`. These are optional: `session, project, prompt, cache_read_tokens, cache_write_tokens`.
If `tool` is missing, the file name is used as the tool name.

## The dashboard

`ai-tokens-gui` (or the installed **AI Token Tracker** app) opens a dashboard in its own window.
It uses the Edge WebView2 window built into Windows 10/11 and a WebKit window on macOS. Anywhere else it opens in your browser.

**Overview**
- Period switcher (Today, 7, 30 and 90 days, All time) and on/off chips for each AI tool
- The token total for the period, its **estimated cost**, and the change versus the previous period
- A share bar showing each AI tool's portion, with its tokens, cost and sessions
- Tiles for sessions, prompts, API calls, output, cache reads, active days and your **daily streak**
- A usage chart by day (or by hour or week), split by AI tool or by model, showing tokens or cost, with hover details
- "When you work": tokens by hour of day
- A 12-month **activity heatmap**
- Top models with their share and cost
- Tables by day, project, model, AI tool or app, sortable, with **CSV export**

- Drag the cards (or use the arrow keys on their handle) to arrange them; pick a **custom date range**; enlarge the usage chart

**Sessions**: cost per session and per prompt, cache hit rate, git commits, active time and repeated prompts at the top.
Search across prompts, projects and models, and sort by newest, most tokens or highest cost.
Each session shows badges (commits, costly, repeated prompts), its cache savings, the **command to continue it** (Claude Code, Codex, Grok)
and the commits made while it was active. Click a prompt to see every API call.

**Insights**
- *What the tokens produced*: git commits made during AI sessions, and the AI cost per commit, by project
- *Context size*: CLAUDE.md, AGENTS.md, GEMINI.md and rules files that are added to every message, with estimated tokens
- *Skills*: which Claude Code skills you use, how often, and what the replies that ran them cost
- *Cache savings*: how much prompt caching saved, by model

**Sources**: shows which AI tools were found and where, the price list, updates, the widget and *start with the computer*.

Press **Ctrl+K** (⌘K on a Mac) to find any session, prompt, project, model or page. Costs can be shown in **15 currencies**.
It has light and dark themes (following your system, or switched by hand), works on narrow windows, and refreshes itself every minute.

## Widget and tray icon

The widget button (top right) opens a small window that **stays on top** of your other windows: today's tokens and cost,
your latest session and your plan limits. On Windows there is also a **tray icon** near the clock (open the dashboard, show the widget, quit);
with it, closing the dashboard keeps the app running in the tray. Turn on *Show the widget when the computer starts* on the Sources page
to have it appear when you log in. Opening the app again while it runs just brings it to the front.

## Plan limits and outages

The first page shows how much of each **subscription's limits** you've used, for every AI that's logged in on your computer:
**Claude** (5-hour window, this week), **ChatGPT/Codex**, **Gemini**, **Cursor**, **GitHub Copilot** and **Kimi**.
These are the same numbers each company shows on its own usage page, and they cover everything on that account
(for Claude: cloud sessions, claude.ai and the phone app too). They're shares of your plan, not exact tokens.

Each tool's own saved login is read (never stored by us) and sent only to that company. An expired login is never refreshed by us;
open the tool once and it refreshes itself. These are the endpoints the tools themselves use; they aren't published, so they may need an update if a company changes them.

When Claude, OpenAI, Cursor or GitHub report an incident on their public status page, a banner says so, so a missing number isn't mistaken for a tracker problem.

## Cost estimates

Every API call gets an estimated cost at pay-as-you-go **API list prices**, including cache-read and cache-write prices.
Claude's 1-hour cache writes cost more, and the tracker prices them separately.
- A price list for about 300 models is bundled, so costs work offline.
- `ai-tokens --update-prices` (or the button on the Sources page) downloads the latest public price list from
  [LiteLLM](https://github.com/BerriAI/litellm). It only downloads when you ask (the other network request is the update check, see Updates).
- You can set your own prices in `~/.ai-token-tracker/prices.json` (USD per 1M tokens):
  `{"my-model": {"input": 1.0, "output": 4.0, "cache_read": 0.1, "cache_write": 1.25}}`
- Models without a known price are counted as tokens and flagged as unpriced, never guessed.

If you're on a subscription (Claude Max, ChatGPT Pro…), you aren't billed per token. The figure then shows what the same usage would cost on the API.

## Install

### Windows: installer (easiest)
Download `AITokenTracker-Setup.exe` from the repo's **Releases** page (or from the *Build apps* workflow run under **Actions**) and run it.
- It installs for your user only, so you don't need admin rights.
- It adds a Start menu entry and, if you want, a desktop shortcut.
- To uninstall it, go to *Settings → Apps*.

Windows SmartScreen may say "Windows protected your PC" because the app isn't code-signed. Click *More info → Run anyway*.
There is also a portable `AITokenTracker.exe` if you'd rather not install anything.

### macOS / Linux
Download `AITokenTracker-macOS-AppleSilicon.zip` (Macs with an M1 or later chip) or `AITokenTracker-linux.tar.gz` from the same place.
On an Intel Mac, use the pip install below.
On macOS the app is unsigned, so the first time you open it, right-click it and choose *Open*.

### Any computer with Python 3.9+ (pip): Windows, macOS or Linux
This is the quickest way to try it before the installers are built.

**Windows**
1. Install Python from [python.org](https://www.python.org/downloads/). Keep the "py launcher" option ticked.
2. Open **PowerShell** in the folder where you saved the `.whl` file and run:
   ```powershell
   py -m pip install --user "ai_token_tracker-3.2.0-py3-none-any.whl[app]"
   py -m ai_token_tracker            # opens the dashboard window
   py -m ai_token_tracker --stats    # command-line summary
   ```
   If the `[app]` part fails to install, run the same command without `[app]`. The dashboard then opens in your browser instead.

**macOS**
```bash
python3 -m pip install --user "ai_token_tracker-3.2.0-py3-none-any.whl[app]"
python3 -m ai_token_tracker            # opens the dashboard window
```
If macOS doesn't have Python yet, `python3` offers to install the Command Line Tools, or you can use [python.org](https://www.python.org/downloads/).

To uninstall: `py -m pip uninstall ai-token-tracker` (Windows) or `python3 -m pip uninstall ai-token-tracker` (macOS/Linux).

## Command line

```bash
ai-tokens                     # latest session + the 10 most recent, from every AI
ai-tokens --stats             # totals per AI tool, engine, app and project
ai-tokens --all               # every session
ai-tokens -t codex            # only one AI tool
ai-tokens -s latest --calls   # one session: per prompt, plus every API call
ai-tokens --json              # JSON for your own scripts
ai-tokens --serve             # dashboard in your browser (add --port 7690 --no-open for a fixed address)
ai-tokens --sources           # which AI tools were found, and where
ai-tokens --update-prices     # refresh the price list used for cost estimates
```

Other options: `-n 20` (how many recent sessions), `-p myproject` (one project only), `--dir PATH` (another Claude Code `projects` folder).

## What the numbers mean

| Column      | Meaning |
|-------------|---------|
| Input       | New prompt tokens that were not in the cache |
| Cache write | Context written to the prompt cache |
| Cache read  | Context read back from the cache on later calls (billed at a small fraction of input) |
| Output      | Tokens the AI generated, including thinking/reasoning |
| Total       | The sum of all four |

The tools report tokens in slightly different ways. OpenAI and Gemini count cached tokens inside "input", for example.
The tracker converts everything to the four columns above, so numbers from different AIs can be compared.
Cache reads are usually the largest number because the whole conversation is re-read on every call.
They are also the cheapest kind of token, so for cost, **Output** and **Input + Cache write** tell you more.

## Updates

Installed copies keep themselves up to date. Every time the app opens (and every 6 hours while it stays open), it asks GitHub for the latest release.
When a newer version exists, it installs it automatically and restarts:
- **Windows** (installed with `AITokenTracker-Setup.exe`): runs the new installer silently.
- **Mac app**: swaps in the new app and reopens it.
- **pip installs**: upgrades with pip.
- **Portable `.exe` and the Linux app**: show a bar with a download link instead.

Every download is checked against the SHA-256 checksum GitHub publishes for it. The check fetches only the latest version number; nothing about your usage is sent.
To ship an update to everyone, bump `__version__` in `src/ai_token_tracker/__init__.py`. Then open **Actions → Build apps → Run workflow**, tick **Publish a release**, and run it (or push a tag such as `v2.4.0`). The workflow builds and publishes the release, and installed apps pick it up.

## Privacy and security

- The tracker only **reads** log files. It never changes them, and it has no account, telemetry or cloud sync.
- Its network requests: the update check to GitHub each time the app opens (the latest version number; nothing about your usage);
  plan limits from each AI company whose tool is logged in here (each login goes only to its own company); Cursor's usage list from cursor.com (if Cursor is logged in);
  public status pages; exchange rates (only if you pick a currency other than dollars); and, when you ask, the price-list download.
- Git commits are read with git on your computer: only your own commits' time, short hash and title.
- The dashboard is served on `127.0.0.1` only. Each launch gets a random access token, and requests from other websites are refused.
- Prompt text appears only in your own dashboard, clipped to a short preview.

## Development

```bash
python -m unittest discover -s tests   # reader tests for every AI tool
```

## Credits

- Interface ideas came from studying [TokenTracker](https://github.com/xiufengsun/TokenTracker) (MIT):
  a period switcher with a big total, a share bar, a heatmap and trend charts, a bundled price list, plan limits for several AIs,
  status alerts, session insights, skills and context-size views, and a widget. File formats of the AI tools were learned partly
  from its documentation comments. No code or assets were copied. Everything here was written from scratch.
- Model prices come from [LiteLLM](https://github.com/BerriAI/litellm)'s public price list (MIT).

To build the apps, open **Actions → Build apps → Run workflow** in GitHub, or push a tag such as `v2.3.0` to publish a Release.
That builds the Windows installer and portable `.exe`, the macOS app, the Linux binary and the Python wheel.
