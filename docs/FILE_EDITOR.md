# The Files editor

> **Status: built and tested (2026-11-07).** What's tested and what isn't is at the end of this page.

Each instance gets a **Files** tab (next to Installation). It lets an admin read and edit the instance's config and
script files, and pack Arma 3 missions, without leaving HostSpawn.

## What it looks like

```
┌─ Instance 1 · Rust Server ─────────────────────────────────────────────────┐
│ Overview  Players  Console  [Files]  Settings  Tools                       │
├───────────────────────────┬────────────────────────────────────────────────┤
│ 🔍 Search files…          │ oxide/config/Kits.json            ● not saved │
│                           │ ───────────────────────────────────────────── │
│ ▾ oxide/config            │  1 │ {                                         │
│     Kits.json        ◀    │  2 │   "Starter kit": {                        │
│     Gather.json           │  3 │     "items": ["rock", "torch"],           │
│ ▾ oxide/plugins           │  4 │     "cooldown": 600                       │
│     Kits.cs               │  5 │   }                                       │
│ ▾ server/server/cfg       │  6 │ }                                         │
│     server.cfg     🔒     │                                                │
│     users.cfg             │ ───────────────────────────────────────────── │
│                           │ [Save]  [Undo changes]  [Versions (4)]         │
└───────────────────────────┴────────────────────────────────────────────────┘
```

- **Left:** the instance's files, in folders, with a search box.
- **Right:** the open file with line numbers. A dot means there are changes that aren't saved.
- **🔒 Read-only:** files HostSpawn writes itself at every start (Rust's `server.cfg`, Arma 3's and Reforger's
  generated config). Edits there would be overwritten, so these open read-only, with a **Change these in Settings**
  button.

## Browsing the server folder
The left side opens in the **game server's own folder**, like a file manager. For Arma 3 that's where `mpmissions`,
`@extdb3`, `@life_server`, `keys`, `battleye` and `addons` are. Click a folder to go in, **.. (up one folder)** to go
back, and the house button to return to the server folder. Every file is listed with its size. Text files (below)
open in the editor; anything else (game data, `.pbo` archives, programs) is listed but can't be opened here: use
**Show in folder** to open it on the computer. Typing in **Search files** switches to a list of every editable file.

## Which files it opens

| Kind | File types |
|---|---|
| Text and notes | `.txt`, `.md`, `.csv` |
| Arma 3 scripts and missions | `.sqf`, `.sqm`, `.hpp`, `.ext`, `.cfg` |
| Configs | `.json`, `.ini`, `.xml`, `.yaml` / `.yml`, `.toml`, `.conf`, `.properties` |
| Rust plugins (Oxide) | `.cs` |
| Other scripts | `.lua`, `.js`, `.py`, `.sh`, `.html`, `.css` |

Listed but not opened: game data (maps, models, `.pbo` archives) and files over 1 MB (logs are on the Console
tab). Never shown: HostSpawn's private folders and backups. Adding a file type is a one-line change in `backend/manager/files.py`
(`TEXT_EXT`).

## Editing and saving

1. Click a file on the left to open it.
2. Make your change and press **Save**. HostSpawn checks the file first:
   - **OK:** it's saved, the old version is kept, and you see "Saved. Applies at the next restart" with a
     **Restart now** button. Restart asks you to confirm, and players get the usual countdown.
   - **A mistake** (for example a missing comma in JSON): nothing is saved. You see the reason and the line, for
     example "Not valid JSON: Expecting ',' (line 4, column 7). Nothing was saved." Click it to jump to that line.
   - **The file changed while you were editing** (the game or another admin saved it): it won't overwrite their
     change. Reload the file, then make your change again.
3. Leaving with unsaved changes asks you first.

What's checked before saving:

| File | Check |
|---|---|
| `.json` | valid JSON |
| `.toml` | valid TOML |
| `.xml` | valid XML |
| `.cfg`, `.hpp`, `.ext`, `.sqm` | every `{` has a `}` (text in quotes is ignored) |
| `.sqf` | brackets `( [ {` closed, no quote or comment left open |

## Versions (undo after saving)

Every save keeps the text from before it. The last **20 versions of each file** are kept, with who saved them and
when. Open **Versions** to view one, and **Restore** to put it back. Restoring is itself a save, so it can be undone
too.

## Script assistance

- **Colours** for SQF, Arma configs, JSON, C#, Lua and the other types: keywords, text, numbers and comments stand
  out. (SQF and Arma configs use C-style colours, which fit them closely.)
- **Brackets and quotes close themselves**, the matching bracket lights up, and new lines keep their indent.
- **Find and replace:** Ctrl+F (the 🔍 button), with match case. **Go to line:** Ctrl+G. **Save:** Ctrl+S.
- **Word wrap** on or off, and bigger or smaller text.
- **Mistakes found as you type** (JSON, and SQF brackets and quotes): a red bar above the editor says what's wrong
  and on which line, with a **Go to line** button. Only things that are definitely wrong, so it doesn't nag.
- **Starter templates** (New file):
  - Arma 3: `init.sqf`, `initServer.sqf`, `description.ext` with the common settings explained,
    `onPlayerRespawn.sqf`.
  - Rust (Oxide): an empty plugin with the usual parts, and a config file example.

It doesn't write or fix scripts for you, and it can't run them: only the game can. After saving, restart and look at
the Console tab.

## Arma 3 missions and PBO files

The left side has a **Missions** section listing the folders in `mpmissions/`, and whether each is packed:

```
▾ Missions (mpmissions)
    Altis_Life.Altis    packed 14:02  [Pack to PBO]
    MyTraining.Stratis  not packed    [Pack to PBO]
```

1. Edit the mission's `.sqf`, `.sqm` and `.hpp` files in the editor.
2. Press **Pack to PBO**. HostSpawn checks the folder has a `mission.sqm`, then packs it into
   `MyTraining.Stratis.pbo` with its built-in packer. The previous `.pbo` is kept as `.pbo.bak`.
3. The mission is used at the next restart (if it's the mission chosen in Settings).

## Who can use it, and what keeps it safe

- **Admins only.** Moderators and viewers don't see the Files tab (or its Database view), and the engine refuses them too.
- **Only this instance's folder.** Nothing outside it, even through shortcuts (symlinks), and never HostSpawn's
  private folders or backups.
- **Passwords stay hidden.** A file holding a password HostSpawn manages won't open, and a password can't be saved
  into a file.
- **Text files up to 1 MB.**
- **Files are written all at once**, so a crash mid-save can't leave half a file. Every save is in the event log,
  with who saved it.

## For developers

- Engine: `backend/manager/files.py` (list, read, check, save, versions, mission packing). Versions are kept in
  `state/<instance>/file_versions/`.
- API (admins only):
  - `GET /api/files?server=`: files and missions.
  - `GET /api/file?server=&path=`: one file, with its versions.
  - `GET /api/file/version?server=&path=&id=`: one old version.
  - `POST /api/file {server, path, text, sha}`: save. `sha` is from when the file was opened, so a changed file is
    refused.
  - `POST /api/file/restore {server, path, id}`: put a version back.
  - `POST /api/file/pack {server, folder}`: pack an Arma 3 mission.
- App: `frontend/app/lib/screens/files_tab.dart`, using the `re_editor` and `re_highlight` packages.

## How it was tested

- `backend/tests/test_files.py` (14 tests): only text files listed (no game data, backups, private folders, huge or
  binary files); `../`, absolute paths, symlinks and private folders refused; a file holding a managed password won't
  open and a password can't be saved; bad JSON, SQF and braces refused with the line and nothing saved; a file that
  changed since it was opened isn't overwritten; versions kept (20), restore, permissions kept; generated files
  read-only; mission packing checked by reading the .pbo back, `.bak` kept, bad folder names refused; moderators get
  403, admins can list, open and save.
- `frontend/app/test/files_tab_test.dart` (4 tests): open a file, read-only files, Pack to PBO, a new file from a
  template, the as-you-type checks.
- A screenshot run against a sandbox (`docs/images/10-files.png`).
- **Not tested:** whether a packed mission loads in a real Arma 3 server, and plugins compiling in a real Rust server.
  Those need real game servers.
