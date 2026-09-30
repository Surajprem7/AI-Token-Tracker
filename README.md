# AI Token Tracker

A small desktop app (plus a command-line tool) that shows how many tokens you spend with your AI tools:
per session, per prompt, per AI tool, per engine (model), per app and per project.

It reads the log files the AI tools already save on your computer. It needs **no API keys, no login and no internet**.
It only reads those files. It never changes them and never sends anything anywhere.

## Which AIs it tracks

| AI tool | Where it reads from | Setup |
|---|---|---|
| **Claude Code** (terminal, VS Code, JetBrains, Desktop, web, Agent SDK) | `~/.claude/projects/` | none |
| **OpenAI Codex CLI** (terminal, VS Code) | `~/.codex/sessions/` (or `$CODEX_HOME`) | none |
| **Google Gemini CLI** | `~/.gemini/tmp/*/chats/` | none |
| **Any other AI** (ChatGPT/OpenAI API, Anthropic API, Ollama, Grok, your own scripts…) | `~/.ai-token-tracker/usage/*.jsonl` or `*.csv` | log it yourself, see below |

Chats on the ChatGPT, claude.ai or Gemini websites and mobile apps, and tools such as GitHub Copilot or Cursor, don't reveal token counts.
Nothing can track those automatically. If you know the numbers, you can still add them by hand.

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

## The app

**Dashboard tab**
- Totals for today, the last 7 days, the last 30 days and all time
- A chart of tokens per day for the last 14 days, coloured by engine (Opus, Sonnet, Haiku, GPT, Gemini, Llama…)
- Tables **by AI tool**, **by engine (model)**, **by app** and **by project**

**Sessions tab**
- A card with the latest session's totals (click any session to show that one instead)
- Every session from every AI in one list. Click ▸ to expand a session into its prompts, and a prompt into its individual API calls
- A filter box: type `codex`, `gemini`, a model name or a project

The app refreshes itself every 60 seconds.

## Install

### Windows: installer (easiest)
Download `AITokenTracker-Setup.exe` from the repo's **Releases** page (or from the *Build apps* workflow run under **Actions**) and run it.
- It installs for your user only, so you don't need admin rights.
- It adds a Start menu entry and, if you want, a desktop shortcut.
- To uninstall it, go to *Settings → Apps*.

Windows SmartScreen may say "Windows protected your PC" because the app isn't code-signed. Click *More info → Run anyway*.
There is also a portable `AITokenTracker.exe` if you'd rather not install anything.

### macOS / Linux
Download `AITokenTracker-macOS.zip` or `AITokenTracker-linux.tar.gz` from the same place.
On macOS the app is unsigned, so the first time you open it, right-click it and choose *Open*.

### Any computer with Python 3.9+ (pip)
```bash
pip install ai_token_tracker-1.1.0-py3-none-any.whl   # or: pip install .
ai-tokens-gui     # opens the app
ai-tokens         # command-line version
pip uninstall ai-token-tracker
```

## Command line

```bash
ai-tokens                     # latest session + the 10 most recent, from every AI
ai-tokens --stats             # totals per AI tool, engine, app and project
ai-tokens --all               # every session
ai-tokens -t codex            # only one AI tool
ai-tokens -s latest --calls   # one session: per prompt, plus every API call
ai-tokens --json              # JSON for your own scripts
ai-tokens --html report.html  # static HTML report
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

## Development

```bash
python -m unittest discover -s tests   # reader tests for every AI tool
```

To build the apps, open **Actions → Build apps → Run workflow** in GitHub, or push a tag such as `v1.1.0` to publish a Release.
That builds the Windows installer and portable `.exe`, the macOS app, the Linux binary and the Python wheel.
