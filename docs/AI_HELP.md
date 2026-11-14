# AI help: using your own AI assistant with HostSpawn

Lots of server owners use an AI assistant (Claude, ChatGPT, Copilot, Gemini...) to help change an Altis Life
mission, write a Rust plugin or work out why a server won't start. The AI does a much better job when it knows how
your server is set up. HostSpawn tells it, and **leaves out your passwords and your players' details**.

There are two ways. Both use the same guide.

## 1. Copy for AI (any AI chat)
1. Open the instance in the HostSpawn app.
2. Open the instance's **Tools** tab and press **Copy for AI** (admins only).
3. Paste into your AI chat and type your question at the end, for example *"How do I add a new car to the Altis
   Life car shop?"* or *"Why does my server keep crashing?"*.

What gets copied:
- The game, the server type (for example Altis Life), the mission, map and server-side mods.
- Where things are: the mission, configs, plugins, logs, and how missions are packed (Arma 3).
- Rules for the AI: which files hold passwords (don't open), which files HostSpawn rewrites at every start (change
  those in Settings), keep a backup, ask before deleting or stopping anything.
- How to restart and read the logs.
- A short list of the instance's folders.
- What the **Server Doctor** found just now.
- The newest 40 console log lines.

## 2. AGENTS.md (AI tools that work in a folder)
Some AI tools work directly on the files in a folder: Claude Code, OpenAI Codex, GitHub Copilot, Cursor and others.
They read a file called `AGENTS.md` (Claude Code reads `CLAUDE.md`) to learn the rules for that folder.

Every time an instance HostSpawn created starts, HostSpawn writes both into the instance's folder:
- `AGENTS.md`: the same guide as above, without the logs and Server Doctor findings (those go out of date).
- `CLAUDE.md`: one line that points Claude Code at `AGENTS.md`.

Open the instance's folder (the **Open folder** button on the Tools tab) in your AI tool and it will read them on its own.

**Your own notes:** put anything you want your AI to know in `AI_NOTES.md` in the instance's folder, for example
*"We changed the police pay in Config_Licenses.hpp"*. HostSpawn adds it to the guide every time (both ways).

**Your own AGENTS.md or CLAUDE.md:** if you write one yourself, HostSpawn never replaces it. It only updates files
it wrote itself (the first line says so). The Files tab shows HostSpawn's copies as read-only.

## What's kept out
- **Passwords, RCON passwords, Steam logins, tokens:** every value in HostSpawn's secrets file is replaced with
  `***` everywhere in the guide, including the log lines and your notes.
- **Files that hold a password** (for example extDB3's settings file with the database password) are listed under
  "don't open", but never read out.
- **Players' details** in the log lines: IP addresses become `<ip>`, SteamIDs `<steamid>`, BattlEye GUIDs `<guid>`.
- **Your computer's paths:** only paths inside the instance's folder are used.

## Good to know
- An AI tool that works **inside the folder** can still open files on its own. `AGENTS.md` tells it which files not
  to open, but it can't force it. If you want to be completely safe, use **Copy for AI** instead: you only paste
  what HostSpawn already cleaned.
- Always read what an AI suggests before you use it. Keep a copy of a file before changing it (the Files tab keeps
  the last 20 versions anyway), and restart the server afterwards to check it still starts.
- Servers you set up by hand (not created by HostSpawn) get a shorter guide: game, logs and Server Doctor only, and
  no `AGENTS.md` is written into their folders.

## For developers
- Engine: `backend/manager/aihelp.py`. `guide(adapter, doctor=...)` is the Copy for AI text, `write(adapter)` writes
  `AGENTS.md`/`CLAUDE.md` (called from `ManagedServer._start` after `write_configs()`; a failure never stops a start).
- API: `GET /api/v1/aihelp?server=<id>` → `{"text": ...}`, admins only (the guide lists the instance's files).
- App: `frontend/app/lib/screens/ai_help.dart` (the button), `Api.aiHelp`.
- Tests: `backend/tests/test_aihelp.py` (a fake Altis Life instance with fake passwords, IPs and SteamIDs in its files
  and logs; none of them may appear in the guide).
