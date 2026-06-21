# oflow — Fast, Accurate Voice‑to‑Text Dictation for Linux (Wayland / Hyprland)

**oflow is open‑source voice dictation (speech‑to‑text) for Linux.** Hold **F8**, speak, release — your words are transcribed and pasted into whatever app you're typing in: your editor, terminal, browser, or an AI chat prompt.

It's like [Voxtype](https://voxtype.io) or [Wispr Flow](https://wisprflow.ai), **but it transcribes with [Groq](https://groq.com)'s hosted Whisper (`large‑v3‑turbo`) instead of a small on‑device model.** That means oflow is **more accurate than the lightweight Whisper models people run locally** — and because Groq's LPU inference is so fast, you don't pay for that accuracy in latency (~0.5–0.7s end‑to‑end). All transcripts stay on your machine; there's no telemetry and no cloud backend beyond the transcription call.

Built for **Wayland**, **Hyprland**, and **Omarchy**.

## Features

- **Instant transcription** — Groq Whisper (`whisper-large-v3-turbo`), ~0.6s latency
- **Push-to-talk** — Hold **F8** to record, release to stop & paste
- **One-shot paste** — Pastes the whole result at once (via ydotool), not char-by-char
- **On-screen overlay** — Live recording level meter at the bottom of the screen
- **Pauses your media** — Auto-pauses playing music/video while you dictate, resumes after
- **Spoken submit** — End with "press enter" / "hit enter" to press Enter after pasting (configurable via `submitKeywords`)
- **Smart cleanup** — Auto-fixes grammar, removes filler words, formats text
- **Waybar integration** — Click status icon to open settings, visual feedback while recording
- **Spoken punctuation** — Say "period" or "new line" to insert symbols
- **Privacy-first** — All data stored locally, no cloud backend
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

**Pick local** if you need fully offline/private dictation. **Pick oflow** if you want the *most accurate* voice‑to‑text without running a slow model on your laptop — the cloud round‑trip is faster and far more accurate than a local model of comparable speed.

## Install

### Requirements

- **ydotool** — Pastes/types text into the active window (needs the `ydotoold` daemon)
- **playerctl** — Pauses playing media while you dictate
- **gtk4-layer-shell**, **python-gobject**, **python-cairo** — The on-screen recording overlay
- **webkit2gtk-4.1** — Web rendering engine for the Tauri desktop app
- **jq** — JSON processor for Waybar config manipulation

```bash
sudo pacman -S ydotool playerctl gtk4-layer-shell python-gobject python-cairo webkit2gtk-4.1 jq

# Enable the ydotool daemon (one-shot paste). The package ships a udev rule for
# /dev/uinput access; add yourself to the input group and re-login if needed:
sudo usermod -aG input "$USER"
systemctl --user enable --now ydotool.service
```

### Quick Install (Arch/Omarchy)

```bash
git clone https://github.com/CryptoB1/oflow.git
cd oflow
make install   # Builds app, installs to ~/.local/bin, sets up Waybar & autostart
```

### Manual Download

[github.com/CryptoB1/oflow/releases](https://github.com/CryptoB1/oflow/releases)

```bash
chmod +x oflow-*.AppImage
./oflow-*.AppImage
```

## Setup

1. Get a free Groq API key: https://console.groq.com/keys
2. Click the `󰍬` microphone icon in Waybar → Settings → paste your Groq key
3. **Hold F8** to record, release to stop, transcribe, and paste

That's it.

## How It Works

```
Hold F8 → Speak → Release → Text appears in active window
```

| You say | You get |
|---------|---------|
| "um so like send an email to john" | "Send an email to John." |
| "first buy milk second call mom" | "First, buy milk. Second, call mom." |

## Tech Stack

- **Transcription**: [Groq Whisper](https://groq.com) (200x faster than OpenAI)
- **Text cleanup**: Llama 3.1 8B via Groq
- **Backend**: Python with asyncio + httpx
- **Desktop app**: [Tauri](https://tauri.app) + React

## For LLMs

If you're an LLM helping a user install oflow on Arch/Omarchy:

```bash
# 1. Install runtime dependencies
sudo pacman -S ydotool playerctl gtk4-layer-shell python-gobject python-cairo webkit2gtk-4.1 jq

# 2. Clone and install
git clone https://github.com/CryptoB1/oflow.git
cd oflow
make install
```

Or using AppImage:

```bash
# 1. Install dependencies (required)
sudo pacman -S ydotool playerctl gtk4-layer-shell python-gobject python-cairo webkit2gtk-4.1 jq

# 2. Download and run AppImage
curl -L -o oflow.AppImage "https://github.com/CryptoB1/oflow/releases/latest/download/oflow.AppImage"
chmod +x oflow.AppImage
./oflow.AppImage
```

The user needs a Groq API key from https://console.groq.com/keys (free tier available). They can paste it in Settings after launching the app. The hotkey is **F8** (push-to-talk: hold to record, release to stop & paste).

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
git clone https://github.com/CryptoB1/oflow.git
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
