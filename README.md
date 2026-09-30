# Claude Token Tracker

A small desktop app (plus a command-line tool) that shows how many tokens you spend in **Claude Code**:
per session, per prompt, per engine (model) and per Claude app.

It reads the transcripts Claude Code already saves on your computer (`~/.claude/projects/…/*.jsonl`).
Every reply in them records its exact token counts, so the app needs **no API key, no login and no internet**.
It only reads those files. It never changes them and never sends anything anywhere.

## The app

**Dashboard tab**
- Totals for today, the last 7 days, the last 30 days and all time
- A chart of tokens per day for the last 14 days, coloured by engine (Opus / Sonnet / Haiku / Fable)
- Tables **by engine (model)**, **by AI app** (terminal, VS Code, JetBrains, Desktop, web, Agent SDK) and **by project**

**Sessions tab**
- A card with the latest session's totals (click any session to show that one instead)
- Every session in a list. Click ▸ to expand a session into its prompts, and a prompt into its individual API calls
- A filter box, and a *Folder…* button if your transcripts are somewhere unusual

The app refreshes itself every 60 seconds.

## Install

### Windows: installer (easiest)
Download `ClaudeTokenTracker-Setup.exe` from the repo's **Releases** page (or from the *Build apps* workflow run under **Actions**) and run it.
- It installs for your user only, so you don't need admin rights.
- It adds a Start menu entry and, if you want, a desktop shortcut.
- To uninstall it, go to *Settings → Apps*.

Windows SmartScreen may say "Windows protected your PC" because the app isn't code-signed. Click *More info → Run anyway*.
There is also a portable `ClaudeTokenTracker.exe` if you'd rather not install anything.

### macOS / Linux
Download `ClaudeTokenTracker-macOS.zip` or `ClaudeTokenTracker-linux.tar.gz` from the same place.
On macOS the app is unsigned, so the first time you open it, right-click it and choose *Open*.

### Any computer with Python 3.9+ (pip)
```bash
pip install claude_token_tracker-1.0.0-py3-none-any.whl   # or: pip install .
claude-tokens-gui     # opens the app
claude-tokens         # command-line version
pip uninstall claude-token-tracker
```

## Command line

```bash
claude-tokens                     # latest session + the 10 most recent
claude-tokens --stats             # totals per engine, AI app and project
claude-tokens --all               # every session
claude-tokens -s latest --calls   # one session: per prompt, plus every API call
claude-tokens --json              # JSON for your own scripts
claude-tokens --html report.html  # static HTML report
```

Other options: `-n 20` (how many recent sessions), `-p myproject` (one project only), `--dir PATH` (another `projects` folder).
If `CLAUDE_CONFIG_DIR` is set, that folder is scanned too.

## What the numbers mean

| Column      | Meaning |
|-------------|---------|
| Input       | New prompt tokens that were not in the cache |
| Cache write | Context written to the prompt cache (billed a bit above normal input) |
| Cache read  | Context read back from the cache on later calls (billed at a small fraction of input) |
| Output      | Tokens Claude generated, including thinking |
| Total       | The sum of all four |

Cache reads are usually the largest number because the whole conversation is re-read on every call.
They are also the cheapest kind of token, so for cost, **Output** and **Input + Cache write** tell you more.
Sub-agent transcripts are counted in their parent session.

**Limits:** only Claude Code (terminal, IDE, desktop, web and Agent SDK) writes these transcripts.
Chats on claude.ai or in the Claude mobile app, and other AI tools, aren't included.

## Building the apps yourself

In GitHub, open **Actions → Build apps → Run workflow**, or push a tag such as `v1.0.0` to publish a Release.
That builds the Windows installer and portable `.exe`, the macOS app, the Linux binary and the Python wheel.
Locally: `pip install pyinstaller . && pyinstaller --onefile --windowed --name ClaudeTokenTracker packaging/app.py`.
