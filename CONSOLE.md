# JARVIS Command Console

A local personal assistant with a responsive web interface and Python tool backend.
The existing `jarvis_gui.py` desktop application remains available.

## Run

From PowerShell in this directory:

```powershell
.\start.ps1
```

Or, with dependencies installed:

```powershell
.venv\Scripts\python.exe server.py --open
```

Open http://localhost:4190. Use `--port 4191` if the port is occupied.
Start Ollama, then select an installed model in Settings. Multi-step requests require
an Ollama model with tool support. The chat API follows https://docs.ollama.com/api/chat.
No model download is performed automatically.

## Capabilities

- Local chat with context, user memory, up to eight tool calls, and inspectable results.
- Notes, user preferences, and dated reminders in SQLite.
- Filename search and reading PDF, Markdown, text, CSV, JSON, and source documents.
- Creation of new Markdown, text, and CSV reports without overwriting existing files.
- Windows Notepad, Calculator, Paint, and workspace Explorer launching.
- Browser and YouTube search opening. Search results are not read, and playback is not automated.
- Live CPU, RAM, disk and clock readings; arithmetic without Python eval.
- Browser text-to-speech with voice/rate controls and optional microphone transcription.
- Stop control prevents subsequent agent steps. An already-started model HTTP request may
  finish in the background, and completed tool actions are not undone.

Examples that also work without a model:

```text
system status
calculate (1500 + 750) * 2
save note: Pick up the documents on Friday
remind me in 10 minutes to stretch
find files report
read document report.md
open calculator
```

For document work, select the desired local folder in Settings. Model tools cannot
change that setting. Dotfiles, links escaping the workspace, and traversal are excluded.

## State and privacy

`data/jarvis.db` stores notes, reminders, chat, preferences, and settings. Known notes,
tasks, calendar titles and memories are imported once from the old JSON files. Original
files are not changed. Imported tasks without reliable timestamps remain undated.

The server binds only to loopback and requires a session token for changes. AI processing
uses local Ollama. Browser searches contact Google/YouTube. Browser microphone recognition
may use an online speech service; the interface asks before enabling it. Speech voices
depend on installed browser/system voices. There is no always-listening wake word.

Reminders are checked while the console is open. Overdue reminders appear on reopening.
Activity details are kept for the last 25 requests in the server session; conversation
and personal records persist across restarts. Back up the data directory with the server stopped.

This version does not automate arbitrary browser forms, send messages, make purchases,
delete computer files, or provide unrestricted shell execution.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use temporary databases and mocked model replies for deterministic multi-step and
cancellation checks. The browser test can be run from the existing Lanka One Playwright
installation; see `tests/browser.cjs`. Test screenshots contain temporary synthetic data.

UI assets: Lucide (ISC) and DM Sans (SIL OFL), with licenses in `web/assets/`.
