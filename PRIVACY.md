# Privacy policy: AI Token Tracker for Claude

_Last updated: 23 November 2026_

AI Token Tracker for Claude is a browser extension that shows your claude.ai usage inside claude.ai.

## What it reads

- Your usage limits from claude.ai (the same numbers as claude.ai → Settings → Usage).
- The chat you have open on claude.ai, to count its length in tokens.

It reads these from claude.ai with your existing login, only on claude.ai.

## What it stores

Your usage numbers, the open chat's token count and your settings are stored only in your browser
(`chrome.storage.local`). Nothing is stored anywhere else.

## What it sends

Nothing is sent to the developer or to any third party. There is no analytics or tracking.

If you installed the extension from a zip file (not from a browser store), it asks GitHub once a day for the
latest version number, so it can tell you about updates. This request contains no information about you or your use.

The one exception is optional: if you add your own Anthropic API key in the settings, the text of the
open chat is sent to Anthropic's token counting API (`api.anthropic.com`) to count tokens exactly.
Your key is stored only in your browser.

## Permissions

| Permission | Why |
|---|---|
| Access to claude.ai | To read your usage and the open chat, and to show the usage bars on the page |
| storage | To remember your settings and the last usage numbers |
| alarms | To refresh your usage every few minutes |
| notifications | To tell you when a limit resets |
| api.anthropic.com (optional) | Only asked for when you add an API key, for exact token counts |

## Contact

Questions: open an issue at https://github.com/Surajprem7/AI-Token-Tracker/issues
