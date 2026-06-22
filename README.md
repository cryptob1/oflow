# oflow — Wispr Flow–grade Voice Typing for Linux, Powered by Groq

**The most accurate voice‑to‑text dictation on Linux — and it's effectively free.**
Hold **F8**, speak, release: your words are transcribed and pasted into any app — your editor, terminal, browser, or an AI chat prompt.

<!--
  ⭐ DEMO GIF — this is the single biggest driver of GitHub stars. Record a ~5s clip
  (hold F8 → overlay appears → speak → text pastes), save it to docs/demo.gif, and
  uncomment the line below:
  <p align="center"><img src="docs/demo.gif" alt="oflow — hold F8, speak, and your words paste into any app" width="640"></p>
-->

oflow is like [Voxtype](https://voxtype.io) or [Wispr Flow](https://wisprflow.ai), but instead of running a *small* Whisper model on your laptop, it transcribes with **[Groq](https://groq.com)'s hosted Whisper `large‑v3‑turbo`** — the *full*, accurate model, in ~0.5s, with **no GPU, no 3 GB of resident RAM, and no model setup**. Local dictation forces a trade between fast‑but‑inaccurate and accurate‑but‑slow; Groq breaks it.

> **Will a cloud API cost me anything?** Almost certainly not. Groq's free tier is **~2,000 transcriptions per day** — far more than anyone speaks. Past that it's just **$0.04 per hour of audio** (≈9× cheaper than OpenAI); a month of heavy daily use is a few cents. ([Groq pricing](https://groq.com/pricing))

Built for **Wayland**, **Hyprland**, and **Omarchy**. Open source, transcripts stored locally, no telemetry.

## Features

- **Instant transcription** — Groq Whisper (`whisper-large-v3-turbo`), ~0.6s latency
- **Push-to-talk** — Hold **F8** to record, release to stop & paste
- **One-shot paste** — Pastes the whole result at once (via ydotool), not char-by-char
- **On-screen overlay** — Live recording level meter at the bottom of the screen
- **Pauses your media** — Auto-pauses playing music/video while you dictate, resumes after
- **Voice commands** — Say "oflow scratch that", "oflow select all", "oflow enter" and oflow presses the real keys ([see below](#-voice-commands))
- **Fast mode** — Skips AI cleanup on short dictations for instant output
- **Smart cleanup** — Auto-fixes grammar, removes filler words, formats text
- **Waybar integration** — Click status icon to open settings, visual feedback while recording
- **Spoken punctuation** — Say "period" or "new line" to insert symbols
- **Privacy-conscious** — Transcripts stored locally, no telemetry; audio is sent only to Groq for the ~0.5s transcription and never stored
- **Open source** — Built with Python + Tauri

## How oflow compares to local voice dictation (Voxtype, nerd‑dictation)

Most open‑source Linux dictation tools (Voxtype, nerd‑dictation, numen) run a **small Whisper model on your own CPU/GPU** to stay fast. oflow takes the opposite trade‑off: it sends your audio to **Groq's hosted Whisper `large‑v3‑turbo`**, then runs an LLM cleanup pass — so you get a *large, accurate* model at *low latency*.

| | **oflow** (Groq cloud) | **Local models** (Voxtype default, nerd‑dictation) |
|---|---|---|
| Speech model | Whisper **large‑v3‑turbo** (server‑side) | Small Whisper (tiny/base) on your hardware |
| **Accuracy** | Higher — full large model | Lower — small model chosen to stay fast |
| **Speed** | ~0.5–0.7 s (Groq LPU inference) | Fast for tiny models, slow for accurate ones |
| Grammar/filler cleanup | Yes — Llama 3.1 8B pass | Usually none |
| Works offline | No (needs internet for the transcription call) | Yes |
| Battery | Light (compute is off‑device) | Heavier per dictation |

**The verdict:** if you're ever offline, use a local tool. Otherwise there's no contest — oflow gives you a *larger, more accurate* model at *lower latency*, with *no GPU, no RAM cost, no model downloads, and no real bill*. You stop choosing between "fast" and "accurate."

## Install — one command (Arch / Omarchy)

```bash
curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
```

Installs everything — dependencies, the app, the **F8** hotkey, the recording overlay, the paste daemon, and autostart. Then paste a free [Groq API key](https://console.groq.com/keys) and hold **F8** to talk.

### …or have Claude Code (or any AI agent) install it for you

Open your terminal AI agent (Claude Code, Cursor, Aider, …) and paste this prompt **verbatim**:

```text
Install oflow (voice dictation for Linux) from https://github.com/cryptob1/oflow on
this Arch/Omarchy machine:
1. Run: curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
2. Make sure the ydotoold user service is enabled (systemctl --user enable --now ydotool.service).
3. Then ask me for my Groq API key (https://console.groq.com/keys), write it to
   ~/.oflow/settings.json as "groqApiKey", and restart oflow.
4. Tell me to hold F8 to dictate.
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
git clone https://github.com/cryptob1/oflow.git ~/code/oflow
cd ~/code/oflow && make install
```
</details>

## Setup (10 seconds)

1. Click the `󰍬` mic icon in Waybar (or the tray) → **Settings** → paste a free [Groq API key](https://console.groq.com/keys).
2. **Hold F8**, speak, release — your words paste wherever you're typing.

> **Tip:** end a dictation with **"press enter"** to submit — great for firing off AI prompts hands‑free.

## How It Works

```
Hold F8 → Speak → Release → Text appears in active window
```

| You say | You get |
|---------|---------|
| "um so like send an email to john" | "Send an email to John." |
| "first buy milk second call mom" | "First, buy milk. Second, call mom." |

## 🎙️ Voice commands

Dictation is more than typing — say **"oflow"** followed by a command and oflow presses the real keys, anywhere in a sentence:

| Say… | …and oflow does |
|------|-----------------|
| **"oflow scratch that"** | deletes your last dictation — the magic undo |
| **"oflow enter"** / **"oflow send it"** | presses Enter (submit a chat or prompt) |
| **"oflow new line"** / **"oflow new paragraph"** | inserts a real line break |
| **"oflow select all"** | Ctrl+A |
| **"oflow undo"** / **"oflow redo"** | Ctrl+Z / Ctrl+Shift+Z |
| **"oflow delete word"** | deletes the previous word |
| **"oflow tab"** / **"oflow escape"** | Tab / Esc |

**Why the wake word?** Requiring **"oflow"** first is what makes it safe: ordinary speech like *"select all the files"* stays literal text — only *"oflow select all"* fires the command. So you never have to think about whether a phrase will be misread.

The wake word is configurable in **Settings → Spoken Commands** (default `oflow`), and the recording overlay rotates a hint each time so the commands are easy to discover.

## Tech Stack

- **Transcription**: [Groq Whisper](https://groq.com) `large-v3-turbo` — ~300× realtime, ~$0.04/hr
- **Text cleanup**: Llama 3.1 8B via Groq
- **Backend**: Python with asyncio + httpx
- **Desktop app**: [Tauri](https://tauri.app) + React

## For LLMs / AI coding agents

If you're an AI agent installing oflow on an Arch/Omarchy machine, just run the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
```

It installs all dependencies, builds the app, and sets up the F8 hotkey, the overlay, the `ydotoold` paste daemon, and autostart. Afterward, tell the user to paste a free Groq API key (https://console.groq.com/keys) in **Settings** (tray/Waybar mic icon) and **hold F8** to dictate. Push‑to‑talk: hold F8 to record, release to stop, transcribe, and paste. End a sentence with "press enter" to also submit.

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
cd oflow
make install  # Full install: build app, setup hotkey, Waybar & autostart
```

This will:
1. Build the Tauri app
2. Install `oflow` and `oflow-ctl` to `~/.local/bin/`
3. Configure **F8** push-to-talk hotkey
4. Add Waybar status indicator with colored icons (green/red/yellow)
5. Enable autostart on login
6. Launch oflow

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
python oflow.py &

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
| `make stop` | Stop all oflow processes |
| `make uninstall` | Completely remove oflow (binaries, config, Waybar, hotkeys) |

## Uninstall

To completely remove oflow from your system:

```bash
cd oflow
make uninstall
```

This removes:
- All binaries from `~/.local/bin/` (oflow, oflow-ctl, oflow-toggle)
- Waybar module and CSS styling
- Hyprland hotkey binding (F8 push-to-talk)
- Autostart entry
- Settings directory (`~/.oflow/`)
- Runtime files and sockets

## Waybar Integration

oflow displays a clickable microphone icon in the center of Waybar (next to the clock):
- `󰍬` idle (green) — click to open settings
- `󰍮` recording (red) — actively listening
- `󰦖` transcribing (yellow) — processing audio
- `󰍭` error (red with slash) — something went wrong

The icon, position, and CSS styling are automatically configured during `make install`.

## Configuration

Settings are stored in `~/.oflow/settings.json`:

```json
{
  "provider": "groq",                 // "groq" (recommended) or "openai"
  "groqApiKey": "gsk_...",            // Your Groq API key
  "openaiApiKey": "sk-...",           // Your OpenAI API key (if using openai provider)
  "enableCleanup": true,              // LLM grammar/punctuation cleanup
  "audioFeedbackTheme": "default",    // "default", "subtle", "mechanical", "silent"
  "audioFeedbackVolume": 0.3,         // 0.0 to 1.0
  "iconTheme": "nerd-font",           // "nerd-font", "emoji", "minimal", "text"
  "enableSpokenPunctuation": false,   // Say "period" → "."
  "wordReplacements": {}              // Custom word corrections {"oflow": "Oflow"}
}
```

## Hotkey Configuration

### Default: Push-to-Talk (F8)

**Hold F8** to record, release to stop, transcribe, and paste.

The hotkey is configured automatically during `make install` in `~/.config/hypr/bindings.conf`:

```ini
bind  = , F8, exec, ~/.local/bin/oflow-ctl start
bindr = , F8, exec, ~/.local/bin/oflow-ctl stop
```

### Alternative: Toggle Mode

If you prefer press-to-start / press-to-stop instead of hold, use a single bind:

```ini
bind = SUPER, D, exec, ~/.local/bin/oflow-ctl toggle
```

Then reload: `hyprctl reload`

### Changing the Hotkey

Edit `~/.config/hypr/bindings.conf`, change `SUPER, D` to your preferred key (e.g., `SUPER, I`), then run `hyprctl reload`.

## Architecture

```
Audio Recording → Validation → Whisper STT → LLM Cleanup → one-shot paste (ydotool)
```

- **Backend** (`oflow.py`) — Single Python file (~1200 lines) handling audio capture, transcription, and text output
- **Frontend** (`oflow-ui/`) — Tauri v2 app (Rust + React) for settings UI and system tray
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
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock
~/.local/bin/oflow-toggle
```

**Check if binding is configured:**
```bash
grep oflow ~/.config/hypr/bindings.conf
```

**Enable debug logging:**
```bash
DEBUG_MODE=true python oflow.py
```

## License

MIT

---

*Built for Omarchy*
