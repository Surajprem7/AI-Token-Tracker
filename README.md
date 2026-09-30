# AI Token Tracker

A small, dependency-free Python script that shows how many tokens each **Claude Code** session used.

Claude Code saves a transcript of every session in `~/.claude/projects/<project>/<session-id>.jsonl`.
Every reply in those files includes the API `usage` numbers. The script adds them up, so it needs no API key and no network access.

## Usage

```bash
python3 token_tracker.py                    # latest session + list of the 10 most recent
python3 token_tracker.py --all              # every session, one row each, plus a grand total
python3 token_tracker.py -s latest          # expanded view: tokens per prompt in that session
python3 token_tracker.py -s d3da6ab5 --calls  # ...plus every single API call
python3 token_tracker.py --html report.html # expandable HTML report (click a session to open it)
python3 token_tracker.py --json             # JSON for your own scripts
```

For `-s` you can pass the start of a session ID, its number in the list (`-s 3`), or `latest`.

Other options: `-n 20` (how many recent sessions to show), `-p myproject` (only sessions from one project),
`--dir PATH` (scan a different `projects` folder). If `CLAUDE_CONFIG_DIR` is set, the script scans it too.

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

Sub-agent (Task/Agent) transcripts are counted in their parent session and shown as `[sub-agent]` rows.
