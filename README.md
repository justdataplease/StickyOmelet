# StickyOmelet

A compact, always-on-top Google Keep client for Windows 11. It gives you a focused desktop list and editor while your notes stay synced with your existing Google Keep account.

### ⬇️ [Download StickyOmelet.exe](https://github.com/justdataplease/StickyOmelet/releases/latest/download/StickyOmelet.exe) &nbsp;·&nbsp; [Release notes (v5.0.0)](https://github.com/justdataplease/StickyOmelet/releases/latest) &nbsp;·&nbsp; [All releases](https://github.com/justdataplease/StickyOmelet/releases)

Single portable `.exe`. No installer, no Python, no account server — StickyOmelet talks directly to Google through the community [`gkeepapi`](https://github.com/kiwiz/gkeepapi) client.

> [!IMPORTANT]
> **Unofficial community project** — not affiliated with Google or endorsed by it. Google offers no supported consumer Keep API, so authentication or sync can break without notice if Google changes its private interfaces.

## Features

- Searchable cards for notes and checklists, with all / pinned / note / checklist filters
- Create, edit, pin, recolor, and trash; native checklist editing with active and completed sections
- Autosave ~900 ms after you stop typing; `Ctrl+S` syncs immediately; `F5` pulls changes from other devices
- Multi-step undo/redo for the note body, remembered across refreshes
- Dark and light themes, adjustable 9–22 pt editor text, auto `YYYY-MM-DD HH:MM:SS` titles for new notes
- Resizable, always-on-top window that collapses to a draggable dot bubble when minimized
- Optional **Start with Windows**; remembers last note, window/bubble position, theme, and font size
- Credentials stored with Windows DPAPI (scoped to your user), never as plaintext

## Install

**Requirements:** Windows 11 (10 untested), an internet connection, a Google account with Keep, and Chrome for the one-time connection.

1. **[Download `StickyOmelet.exe`](https://github.com/justdataplease/StickyOmelet/releases/latest/download/StickyOmelet.exe)** and move it to a permanent folder such as `C:\StickyOmelet\` (don't run it from Downloads or a temp folder).
2. Double-click to launch. If SmartScreen appears, choose **More info → Run anyway**.
3. On first launch, enter your Google email, pick **Connect securely through Chrome**, and sign in through the temporary Chrome window. Notes load when it closes.

An existing `gkeepapi` master token can also be entered manually — treat it like a password. Enable **Start with Windows** only after the EXE is in its permanent home.

Open a specific note from a terminal:

```powershell
StickyOmelet.exe --note "Shopping list"
```

## Upgrading from StickyDot

StickyOmelet is the same app under a new name. Replace `StickyDot.exe` with `StickyOmelet.exe` and launch it: your saved Google Keep connection, window and dot positions, theme, and **Start with Windows** choice are carried over automatically on first run.

## How sync and storage work

StickyOmelet sends operations straight between your computer and Google via `gkeepapi` — there's no intermediary server, and Google Keep stays the source of truth. Avoid editing the same note at the same time in another Keep client; the last sync wins.

Preferences and the encrypted token live at `%LOCALAPPDATA%\StickyOmelet\settings.json`. Note content is only held in memory. Disconnecting from inside the app removes the stored credential; your notes stay in Google Keep. The Chrome flow uses a throwaway browser profile that is deleted right after the token is captured.

Sync errors appear in the footer without blocking the dot or editor. Once your connection is back, press `Ctrl+S` to retry the current edit or `F5` to refresh. Keep the app open until important edits have synced.

## Keyboard shortcuts

| Shortcut | Action | | Shortcut | Action |
|---|---|---|---|---|
| `Ctrl+N` | New note | | `Ctrl+S` | Sync now |
| `Ctrl+Shift+N` | New checklist | | `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `Ctrl+L` | Toggle list / note | | `Ctrl++` / `Ctrl+-` | Text size |
| `Ctrl+F` | Focus search | | `Ctrl+0` | Reset text to 11 pt |
| `F5` | Refresh from Keep | | `Esc` | Back to list |

## Development

Python 3.12 for development and release builds.

```powershell
git clone <repository-url> && cd StickyOmelet
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt

python notes_widget.py   # run from source
.\run-tests.ps1          # offline test suite (no Google credentials needed)
.\build.ps1              # -> dist\StickyOmelet.exe
```

The test suite is fully offline: browser, token-exchange, and Keep calls are replaced with test doubles. GitHub Actions runs the same checks on `windows-latest` for every push and PR. **Never** put a personal Google token in tests, commits, screenshots, or CI variables.

Building leaves running copies of StickyOmelet open. If you are running `dist\StickyOmelet.exe`, close it after your notes have synced before rebuilding that file.

## Known limitations

- Not a supported public API — authentication and sync can break after any Google-side change.
- Chrome is required for the guided connection (not for normal use afterward).
- Rich formatting, drawings, images, reminders, collaborators, and archives aren't editable.
- Concurrent edits are not merged.

## Contributing

Issues and PRs welcome. Keep changes focused, add or update offline tests, run `.\run-tests.ps1` on Windows, and disclose any behavior that relies on an unofficial Google interface. Don't include credentials, personal notes, built EXEs, or local settings. For auth failures, include the error and your Windows/Chrome versions but redact emails, cookies, tokens, device IDs, and note content.

## Disclaimer

Use at your own risk. Back up important information and verify critical edits in Google Keep. Compatibility with Google's private interfaces is not guaranteed.

Created by [justdataplease.com](https://justdataplease.com).
