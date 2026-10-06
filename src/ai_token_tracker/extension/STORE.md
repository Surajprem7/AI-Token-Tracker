# Publishing the extension (one-click install for everyone)

Publishing in the stores turns installation into one click ("Add to Chrome" / "Get").
It needs your own developer account; the files are ready.

| Store | Cost | Where |
|---|---|---|
| Chrome Web Store (Chrome, Brave, Opera) | one-time USD 5 registration | https://chrome.google.com/webstore/devconsole |
| Microsoft Edge Add-ons | free | https://partner.microsoft.com/dashboard/microsoftedge |
| Firefox Add-ons | free | https://addons.mozilla.org/developers/ |

Upload `AITokenTracker-BrowserExtension.zip` from the latest GitHub release. Reviews usually take a few days.

## Listing text

**Name:** AI Token Tracker

**Short description:** See how many tokens your Claude, ChatGPT and Gemini chats use, and your Claude 5-hour and weekly usage.

**Description:**
AI Token Tracker shows what your AI chats cost in tokens.

- Estimated tokens for every chat on claude.ai, chatgpt.com and gemini.google.com
- Your Claude plan usage: current 5-hour session and weekly limit, with reset timers
- Messages sent per ChatGPT/Gemini model in the last hours, with your own limits
- Works with the free AI Token Tracker desktop app (Windows, macOS, Linux), which also tracks
  Claude Code, Codex, Gemini CLI, Cursor, Copilot and many more

Token counts on the websites are estimates (about 4 characters per token). The extension only reads the chat
you have open and sends results only to the AI Token Tracker app on your own computer. No account, no tracking.

**Category:** Productivity

## Permission reasons (asked by the stores)

- **Access to claude.ai, chatgpt.com, gemini.google.com:** to read the open chat and estimate its tokens, and (claude.ai) your plan usage.
- **Access to 127.0.0.1 / localhost:** to send the estimates to the AI Token Tracker app on your own computer.
- **storage:** to remember your chats' estimates and the connection to the app.
- **alarms:** to retry sending once a minute when the app was closed.

**Remote code:** none. **Data sold or shared:** none.

## Privacy policy (paste into the store form)

AI Token Tracker reads the chat you have open on claude.ai, chatgpt.com or gemini.google.com to estimate its token use,
and reads your Claude plan usage from claude.ai. It stores these estimates (chat titles, your prompts' first 400 characters,
token counts and times) in your browser and sends them only to the AI Token Tracker app running on your own computer
(127.0.0.1). Nothing is sent to the developer or any other server. Uninstalling the extension deletes its data.
