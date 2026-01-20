# oflow

**Voice-to-text for Omarchy** — Like [Wispr Flow](https://wisprflow.ai), but open source and local-first.

Press a key, speak, press again — your words appear wherever you're typing.

## Features

- **Instant transcription** — Groq Whisper runs at 200x realtime (~0.5s latency)
- **Smart cleanup** — Auto-fixes grammar, removes filler words, formats text
- **Waybar integration** — Click status icon to open settings, visual feedback while recording
- **Audio feedback** — Configurable sounds for start/stop/error
- **Spoken punctuation** — Say "period" or "new line" to insert symbols
- **Privacy-first** — All data stored locally, no cloud backend
- **Open source** — Built with Python + Tauri

## Install

### Requirements

- **wtype** — Types text into your active window (Wayland)
- **webkit2gtk-4.1** — Web rendering engine for Tauri desktop app
- **jq** — JSON processor for Waybar config manipulation

```bash
sudo pacman -S wtype webkit2gtk-4.1 jq
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
3. Press **Super+D** to start recording, press again to stop and transcribe

That's it.

## How It Works

```
Press Super+D → Speak → Press Super+D → Text appears in active window
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
sudo pacman -S wtype webkit2gtk-4.1 jq

# 2. Clone and install
git clone https://github.com/CryptoB1/oflow.git
cd oflow
make install
```

Or using AppImage:

```bash
# 1. Install dependencies (required)
sudo pacman -S wtype webkit2gtk-4.1 jq

# 2. Download and run AppImage
curl -L -o oflow.AppImage "https://github.com/CryptoB1/oflow/releases/latest/download/oflow.AppImage"
chmod +x oflow.AppImage
./oflow.AppImage
```

The user needs a Groq API key from https://console.groq.com/keys (free tier available). They can paste it in Settings after launching the app. The hotkey is **Super+D** (toggle mode: press to start, press again to stop).

## Build from Source

### Dependencies

```bash
# Runtime
sudo pacman -S wtype

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
3. Configure **Super+D** hotkey (toggle mode)
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
- Hyprland hotkey binding (Super+D)
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

### Default: Toggle Mode

**Super+D** — Press to start recording, press again to stop and transcribe.

The hotkey is configured automatically during `make install` in `~/.config/hypr/bindings.conf`:

```ini
bind = SUPER, D, exec, ~/.local/bin/oflow-ctl toggle
```

### Alternative: Push-to-Talk Mode

If you prefer hold-to-record instead of toggle, edit `~/.config/hypr/bindings.conf`:

```ini
# Push-to-talk: hold to record, release to stop
bind = SUPER, D, exec, ~/.local/bin/oflow-ctl start
bindr = SUPER, D, exec, ~/.local/bin/oflow-ctl stop
```

Then reload: `hyprctl reload`

### Changing the Hotkey

Edit `~/.config/hypr/bindings.conf`, change `SUPER, D` to your preferred key (e.g., `SUPER, I`), then run `hyprctl reload`.

## Architecture

```
Audio Recording → Validation → Whisper STT → LLM Cleanup → wtype Output
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
