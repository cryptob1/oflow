# cortex — Wispr Flow–grade Voice Typing for Linux, Powered by Groq

**The most accurate voice‑to‑text dictation on Linux — and it's effectively free.**
Hold the **Copilot key**, speak, release: your words are transcribed and pasted into any app — your editor, terminal, browser, or an AI chat prompt.

<!--
  ⭐ DEMO GIF — this is the single biggest driver of GitHub stars. Record a ~5s clip
  (hold the Copilot key → overlay appears → speak → text pastes), save it to docs/demo.gif, and
  uncomment the line below:
  <p align="center"><img src="docs/demo.gif" alt="cortex — hold the Copilot key, speak, and your words paste into any app" width="640"></p>
-->

cortex is like [Voxtype](https://voxtype.io) or [Wispr Flow](https://wisprflow.ai), but instead of running a *small* Whisper model on your laptop, it transcribes with **[Groq](https://groq.com)'s hosted Whisper `large‑v3‑turbo`** — the *full*, accurate model, in ~0.5s, with **no GPU, no 3 GB of resident RAM, and no model setup**. Local dictation forces a trade between fast‑but‑inaccurate and accurate‑but‑slow; Groq breaks it.

> **Will a cloud API cost me anything?** Almost certainly not. Groq's free tier is **~2,000 transcriptions per day** — far more than anyone speaks. Past that it's just **$0.04 per hour of audio** (≈9× cheaper than OpenAI); a month of heavy daily use is a few cents. ([Groq pricing](https://groq.com/pricing))

Built for **Wayland**, **Hyprland**, and **Omarchy**. Open source, transcripts stored locally, no telemetry.

## Features

- **Instant transcription** — Groq Whisper (`whisper-large-v3-turbo`), ~0.6s latency
- **Push-to-talk** — Hold the **Copilot key** to record, release to stop & paste
- **One-shot paste** — Pastes the whole result at once (via ydotool), not char-by-char
- **🧠 Second brain** — capture notes (**Copilot+N**) & meetings (**Copilot+M**) into a plain-Markdown, Obsidian-compatible vault ([see below](#-second-brain))
- **🎯 Initiatives** — say *"start an initiative…"*; notes & meetings auto-link to your goals, with an AI status per goal
- **🌙 Dreams & 📖 Journal** — nightly, cortex consolidates your captures into your goals and reconstructs *what you worked on* each day from your dictations
- **⏰ Reminders** — say *"remind me to… Friday at 3"*; cortex notifies you when it's due
- **Ask your brain** — natural-language, cited search over your whole vault — local embeddings, no extra API key
- **On-screen overlay** — Live recording level meter at the bottom of the screen
- **Pauses your media** — Auto-pauses playing music/video while you dictate, resumes after
- **Voice commands** — Say "jarvis scratch that", "jarvis select all", "jarvis enter" and cortex presses the real keys ([see below](#-voice-commands))
- **Fast mode** — Skips AI cleanup on short dictations for instant output
- **Smart cleanup** — Auto-fixes grammar, removes filler words, formats text
- **Waybar integration** — Click status icon to open settings, visual feedback while recording
- **Spoken punctuation** — Say "period" or "new line" to insert symbols
- **Privacy-conscious** — Transcripts, notes & meetings stored locally in a plain-Markdown vault; no telemetry; audio is sent only to your transcription provider and never stored
- **Open source** — Built with Python + Tauri

## How cortex compares to local voice dictation (Voxtype, nerd‑dictation)

Most open‑source Linux dictation tools (Voxtype, nerd‑dictation, numen) run a **small Whisper model on your own CPU/GPU** to stay fast. cortex takes the opposite trade‑off: it sends your audio to **Groq's hosted Whisper `large‑v3‑turbo`**, then runs an LLM cleanup pass — so you get a *large, accurate* model at *low latency*.

| | **cortex** (Groq cloud) | **Local models** (Voxtype default, nerd‑dictation) |
|---|---|---|
| Speech model | Whisper **large‑v3‑turbo** (server‑side) | Small Whisper (tiny/base) on your hardware |
| **Accuracy** | Higher — full large model | Lower — small model chosen to stay fast |
| **Speed** | ~0.5–0.7 s (Groq LPU inference) | Fast for tiny models, slow for accurate ones |
| Grammar/filler cleanup | Yes — Llama 3.1 8B pass | Usually none |
| Works offline | No (needs internet for the transcription call) | Yes |
| Battery | Light (compute is off‑device) | Heavier per dictation |

**The verdict:** if you're ever offline, use a local tool. Otherwise there's no contest — cortex gives you a *larger, more accurate* model at *lower latency*, with *no GPU, no RAM cost, no model downloads, and no real bill*. You stop choosing between "fast" and "accurate."

## Install — one command (Arch / Omarchy)

```bash
curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
```

Installs everything — dependencies, the app, the **Copilot-key** hotkey, the recording overlay, the paste daemon, and autostart. Then paste a free [Groq API key](https://console.groq.com/keys) and hold the **Copilot key** to talk.

### …or have Claude Code (or any AI agent) install it for you

Open your terminal AI agent (Claude Code, Cursor, Aider, …) and paste this prompt **verbatim**:

```text
Install cortex (voice dictation for Linux) from https://github.com/cryptob1/oflow on
this Arch/Omarchy machine:
1. Run: curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
2. Make sure the ydotoold user service is enabled (systemctl --user enable --now ydotool.service).
3. Then ask me for my Groq API key (https://console.groq.com/keys), write it to
   ~/.cortex/settings.json as "groqApiKey", and restart cortex.
4. Tell me to hold the Copilot key to dictate.
```

That's the whole setup, hands‑off — the agent installs everything and wires in your key.

<details>
<summary><b>Manual install / full dependency list</b></summary>

```bash
# Dependencies
sudo pacman -S --needed git base-devel uv nodejs npm rust \
  webkit2gtk-4.1 jq ydotool playerctl gtk4-layer-shell python-gobject python-cairo

# Enable the ydotool paste daemon (ships a /dev/uinput udev rule; you may need to re-login)
sudo usermod -aG input "$USER"
systemctl --user enable --now ydotool.service

# Build & install
git clone https://github.com/cryptob1/oflow.git ~/code/cortex
cd ~/code/cortex && make install
```
</details>

## Setup (10 seconds)

1. Click the `󰍬` mic icon in Waybar (or the tray) → **Settings** → paste a free [Groq API key](https://console.groq.com/keys).
2. **Hold the Copilot key**, speak, release — your words paste wherever you're typing.

> **Tip:** end a dictation with **"press enter"** to submit — great for firing off AI prompts hands‑free.

## How It Works

```
Hold the Copilot key → Speak → Release → Text appears in active window
```

| You say | You get |
|---------|---------|
| "um so like send an email to john" | "Send an email to John." |
| "first buy milk second call mom" | "First, buy milk. Second, call mom." |

## 🎙️ Voice commands

Dictation is more than typing — say **"jarvis"** followed by a command and cortex presses the real keys, anywhere in a sentence:

| Say… | …and cortex does |
|------|-----------------|
| **"jarvis scratch that"** | deletes your last dictation — the magic undo |
| **"jarvis enter"** / **"jarvis send it"** | presses Enter (submit a chat or prompt) |
| **"jarvis new line"** / **"jarvis new paragraph"** | inserts a real line break |
| **"jarvis select all"** | Ctrl+A |
| **"jarvis undo"** / **"jarvis redo"** | Ctrl+Z / Ctrl+Shift+Z |
| **"jarvis delete word"** | deletes the previous word |
| **"jarvis tab"** / **"jarvis escape"** | Tab / Esc |

**Why the wake word?** Requiring **"jarvis"** first is what makes it safe: ordinary speech like *"select all the files"* stays literal text — only *"jarvis select all"* fires the command. So you never have to think about whether a phrase will be misread.

The wake word is configurable in **Settings → Spoken Commands** (default `jarvis` — a name Whisper transcribes reliably; pick any word it hears cleanly), and the recording overlay rotates a hint each time so the commands are easy to discover.

## 🧠 Second brain

Beyond dictation, cortex captures into a personal knowledge base — a plain-Markdown **vault** (default `~/brain`, or a subfolder of your existing [Obsidian](https://obsidian.md) vault). Every item is one Markdown file with a human title and YAML frontmatter, so it reads cleanly in Obsidian and syncs conflict-free. Then it **organizes and reflects on** what you capture.

### Capture — all by voice

Extra hotkeys ride on the Copilot key (which holds Super+Shift, so these are `Super+Shift+N` / `Super+Shift+M`); **initiatives** and **reminders** are just spoken *inside* a note:

| Gesture / phrase | Type | What happens |
|---|---|---|
| Hold **Copilot** | **Dictate** | transcribe → paste into the focused app *(unchanged)* |
| **Copilot + N** (toggle) | **Note** | hands-free note → `notes/` |
| **Copilot + M** (toggle) | **Meeting** | records **system audio + your mic**, transcribes (auto-chunked) + summarizes (key points / decisions / action items) → `meetings/` |
| Note: *"start an initiative to…"* | **Initiative** | a tracked goal (name + goals) → `initiatives/` |
| Note: *"remind me to… \<when\>"* | **Reminder** | task + due time (natural language resolved) → `reminders/`; fires a desktop notification when due |

### Ask your brain

The app's **Ask** tab (and the `cortex-brain` CLI) answer natural-language questions over your whole vault:

```bash
cortex-brain "what did we decide about onboarding?"
```

A **local RAG**: content is embedded on-device with [`fastembed`](https://github.com/qdrant/fastembed) (ONNX, no API key), the most relevant chunks are retrieved, and Groq synthesizes a **cited** answer. The index lives outside the vault (`~/.cache/cortex`, rebuildable) so it never bloats sync.

### Initiatives — goals your brain tracks

Every note and meeting is **automatically linked** to the initiatives it relates to (semantic match + an LLM check for precision). The **Initiatives** tab shows each goal with an on-demand **coach-style status** — momentum, recent activity (cited), open action items, next steps — synthesized from everything linked to it.

### Dreams & Journal — reflection, nightly

- **Dreams**: a nightly consolidation pass re-links captures, refreshes each initiative's status, flags stale goals, and suggests emergent initiatives from recurring themes — leaving a **dream journal** you read in the morning. Self-coordinating across machines (one runs per night).
- **Journal**: reconstructs *what you worked on* each day from your dictation stream (timestamps + the app each snippet went into) into a readable **Summary / Timeline / by-theme** log. The noisy stream stays local; only the synthesized journal lands in your vault.

### Sync across devices

The vault is just a folder of Markdown, so sync it with whatever you like — and cortex is built to be **multi-device-safe** (one file per capture, deduped reminders, self-coordinating dreams, index kept out of the vault):

- **Obsidian Sync** — put the vault inside your Obsidian vault (a `<vault>/cortex` subfolder); set **Search root** to the whole vault so Ask covers your existing notes too. Keep Obsidian running (autostart) for continuous sync. *Easiest if you already use Obsidian.*
- **Syncthing** — background, free, private P2P (great incl. Android).
- **Cloud drive / git** — a Dropbox/Drive folder, or a private git remote (enable **auto-push** in Settings).

Read and search everything on your phone with **Obsidian mobile**. New machine? See [Multi-machine setup](#multi-machine-setup).

## Multi-machine setup

cortex is **sync-agnostic**: it reads and writes a plain-Markdown folder, and everything is designed to be safe across machines — one file per capture (never collide), reminders deduped across devices, dreams self-coordinate to one run per night, and the search index lives *outside* the vault so it never syncs. Pick any sync tool for the vault folder; cortex does the rest.

**New machine — one command** (does the cortex side; the vault syncs via your chosen tool):

```bash
git clone https://github.com/cryptob1/oflow.git ~/code/cortex
bash ~/code/cortex/scripts/setup-machine.sh
```

It runs `make install`, then asks for this machine's synced-vault path and your API key and writes the config (`brainVaultPath = <vault>/cortex`, `brainReadRoot = <vault>`, `brainGit = false`). Then sync that vault folder:

- **Obsidian Sync** — open the vault in Obsidian, enable Sync, and autostart Obsidian (continuous while it runs).
- **Syncthing / cloud drive / git remote** — share the vault folder between machines.

Two devices point cortex at their *own* local copy of the same synced vault — captures made anywhere show up everywhere.

## Tech Stack

- **Transcription**: [Groq Whisper](https://groq.com) `large-v3-turbo` (or ElevenLabs Scribe / OpenAI / Deepgram) — ~300× realtime, ~$0.04/hr
- **Cleanup / summaries / initiatives / journal**: Llama (3.1 8B cleanup, 3.3 70B for summaries, status, journal) via Groq
- **Meeting audio**: PipeWire null-sink mixing system output + mic, captured with `pw-record`
- **Second brain**: plain-Markdown vault (Obsidian-compatible) + local [`fastembed`](https://github.com/qdrant/fastembed) embeddings for on-device semantic search & auto-linking (index kept outside the vault)
- **Sync**: none built-in — sync-agnostic (Obsidian Sync / Syncthing / cloud / git); multi-device-safe by design
- **Backend**: Python with asyncio + httpx
- **Desktop app**: [Tauri](https://tauri.app) + React (Dashboard · Ask · Initiatives · Dreams · Journal · Reminders · History · Notes · Meetings · Settings)

## For LLMs / AI coding agents

If you're an AI agent installing cortex on an Arch/Omarchy machine, just run the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
```

It installs all dependencies, builds the app, and sets up the Copilot-key hotkey, the overlay, the `ydotoold` paste daemon, and autostart. Afterward, tell the user to paste a free Groq API key (https://console.groq.com/keys) in **Settings** (tray/Waybar mic icon) and **hold the Copilot key** to dictate. Push‑to‑talk: hold the Copilot key to record, release to stop, transcribe, and paste. End a sentence with "press enter" to also submit.

## Build from Source

### Dependencies

```bash
# Runtime
sudo pacman -S ydotool playerctl gtk4-layer-shell python-gobject python-cairo

# Build tools (Tauri + Python)
sudo pacman -S webkit2gtk-4.1 base-devel curl wget file openssl appmenu-gtk-module gtk3 libappindicator-gtk3 librsvg libvips patchelf nodejs npm rust

# Python environment (uv is recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Build & Run

```bash
git clone https://github.com/cryptob1/oflow.git
cd cortex
make install  # Full install: build app, setup hotkey, Waybar & autostart
```

This will:
1. Build the Tauri app
2. Install `cortex` and `cortex-ctl` to `~/.local/bin/`
3. Configure **Copilot-key** push-to-talk hotkey
4. Add Waybar status indicator with colored icons (green/red/yellow)
5. Enable autostart on login
6. Launch cortex

### Development Mode

```bash
make dev      # Hot reload for frontend + backend
```

### Starting the Backend Manually

The Python backend handles audio recording and transcription:

```bash
# Setup Python environment (first time only)
make setup-backend

# Start backend
source .venv/bin/activate
python cortex.py &

# Check it's running
python test_system.py
```

### Make Targets

| Command | Description |
|---------|-------------|
| `make install` | Full install: build app, install to ~/.local/bin, configure Waybar, enable autostart |
| `make build` | Build release binary only |
| `make build-appimage` | Build self-contained AppImage with bundled Python backend |
| `make install-appimage` | Install AppImage to ~/.local/bin |
| `make dev` | Run in development mode with hot reload |
| `make run` | Start backend server only |
| `make stop` | Stop all cortex processes |
| `make uninstall` | Completely remove cortex (binaries, config, Waybar, hotkeys) |

## Uninstall

To completely remove cortex from your system:

```bash
cd cortex
make uninstall
```

This removes:
- All binaries from `~/.local/bin/` (cortex, cortex-ctl, cortex-toggle)
- Waybar module and CSS styling
- Hyprland hotkey binding (Copilot-key push-to-talk)
- Autostart entry
- Settings directory (`~/.cortex/`)
- Runtime files and sockets

## Waybar Integration

cortex displays a clickable microphone icon in the center of Waybar (next to the clock):
- `󰍬` idle (green) — click to open settings
- `󰍮` recording (red) — actively listening
- `󰦖` transcribing (yellow) — processing audio
- `󰍭` error (red with slash) — something went wrong

The icon, position, and CSS styling are automatically configured during `make install`.

## Configuration

Settings are stored in `~/.cortex/settings.json`:

```json
{
  "provider": "groq",                 // "groq", "elevenlabs", "openai", or "deepgram"
  "groqApiKey": "gsk_...",            // Your Groq API key (also used for cleanup/summaries/Ask)
  "openaiApiKey": "sk-...",           // Your OpenAI API key (if using openai provider)
  "enableCleanup": true,              // LLM grammar/punctuation cleanup
  "audioFeedbackTheme": "default",    // "default", "subtle", "mechanical", "silent"
  "audioFeedbackVolume": 0.3,         // 0.0 to 1.0
  "iconTheme": "nerd-font",           // "nerd-font", "emoji", "minimal", "text"
  "enableSpokenPunctuation": false,   // Say "period" → "."
  "wordReplacements": {},             // Custom word corrections {"cortex": "Cortex"}

  // Second brain (Settings → Second Brain)
  "brainVaultPath": "~/brain",        // Where cortex WRITES captures (e.g. "<vault>/cortex")
  "brainReadRoot": "",                // Where Ask/initiatives READ (whole vault); "" = brainVaultPath
  "brainGit": false                   // Git-commit each capture; leave off when a sync tool handles history
}
```

A few behaviors are tuned with environment variables (e.g. in `~/.cortex/.env`):

```bash
CORTEX_BRAIN_DIR=~/brain                 # overrides brainVaultPath (write root)
CORTEX_BRAIN_READ_DIR=~/Documents/work   # overrides brainReadRoot (search whole vault)
CORTEX_LINK_THRESHOLD=0.45               # embedding recall for initiative auto-linking (LLM verifies)
CORTEX_MAX_TRANSCRIBE_CONCURRENCY=12     # cap parallel chunk requests (long meetings)
CORTEX_MIC_WARMUP_MS=250                 # on-demand mic warm-up before capture
CORTEX_PERSISTENT_MIC=false              # keep the mic stream warm across dictations
```

## Hotkey Configuration

### Default: Push-to-Talk (Copilot key)

**Hold the Copilot key** to record, release to stop, transcribe, and paste. The
Copilot key emits `Super+Shift+F23` (Hyprland keycode `code:201`); Omarchy binds
that to its menu by default, so the installer unbinds it first.

The hotkey is configured automatically during `make install` in `~/.config/hypr/bindings.conf`:

```ini
unbind = SUPER SHIFT, code:201
bindd = SUPER SHIFT, code:201, Cortex dictation (hold to talk), exec, ~/.local/bin/cortex-ctl start
bindr = SUPER SHIFT, code:201, exec, ~/.local/bin/cortex-ctl stop
# Second-brain capture (Copilot holds Super+Shift, so these are Copilot+N / Copilot+M):
unbind = SUPER SHIFT, N
bind = SUPER SHIFT, N, exec, ~/.local/bin/cortex-ctl note      # toggle a hands-free note
unbind = SUPER SHIFT, M
bind = SUPER SHIFT, M, exec, ~/.local/bin/cortex-ctl meeting   # toggle a meeting recording
```

> cortex regenerates this block on start, so the note/meeting binds are added automatically for the Copilot hotkey — no manual editing needed.

On a keyboard without a Copilot key, override the hotkey at install time:

```bash
make setup-hotkey CORTEX_HOTKEY=", F8" CORTEX_HOTKEY_UNBIND= CORTEX_HOTKEY_LABEL=F8
```

### Alternative: Toggle Mode

If you prefer press-to-start / press-to-stop instead of hold, use a single bind:

```ini
bind = SUPER, D, exec, ~/.local/bin/cortex-ctl toggle
```

Then reload: `hyprctl reload`

### Changing the Hotkey

Edit `~/.config/hypr/bindings.conf`, change `SUPER, D` to your preferred key (e.g., `SUPER, I`), then run `hyprctl reload`.

## Architecture

```
Audio Recording → Validation → Whisper STT → LLM Cleanup → one-shot paste (ydotool)
```

- **Backend** (`cortex.py`) — Single Python file (~1200 lines) handling audio capture, transcription, and text output
- **Frontend** (`cortex-ui/`) — Tauri v2 app (Rust + React) for settings UI and system tray
- **IPC** — Unix socket at `/tmp/voice-dictation.sock` for start/stop/toggle commands

## Troubleshooting

**Run the test script to diagnose issues:**
```bash
python3 test_system.py
```

**Hotkey not working?**
```bash
hyprctl reload
```

**Backend not responding?**
```bash
rm -f /tmp/cortex.pid /tmp/voice-dictation.sock
~/.local/bin/cortex-toggle
```

**Check if binding is configured:**
```bash
grep cortex ~/.config/hypr/bindings.conf
```

**Enable debug logging:**
```bash
DEBUG_MODE=true python cortex.py
```

## License

MIT

---

*Built for Omarchy*
