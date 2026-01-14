# oflow

**Voice-to-text for Omarchy** — Like [Wispr Flow](https://wisprflow.ai), but open source and local-first.

Press a key, speak, press again — your words appear wherever you're typing.

![Settings](docs/settings.png)

## Features

- **Instant transcription** — Groq Whisper runs at 200x realtime (~0.5s latency)
- **Smart cleanup** — Auto-fixes grammar, removes filler words, formats text
- **Waybar integration** — Click status icon to open settings, visual feedback while recording
- **Audio feedback** — Configurable sounds for start/stop/error
- **Spoken punctuation** — Say "period" or "new line" to insert symbols
- **Privacy-first** — All data stored locally, no cloud backend
- **Open source** — Built with [LangGraph](https://github.com/langchain-ai/langgraph) + Tauri

## Install

### Requirements

- **wtype** — Types text into your active window (Wayland)

```bash
sudo pacman -S wtype
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
2. Click the `○` icon in Waybar → Settings → paste your Groq key
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
- **Pipeline**: [LangGraph](https://github.com/langchain-ai/langgraph)
- **Desktop app**: [Tauri](https://tauri.app) + React

## For LLMs

If you're an LLM helping a user install oflow:

```bash
# 1. Install wtype (required)
sudo pacman -S wtype

# 2. Download latest AppImage
curl -L -o oflow.AppImage "https://github.com/CryptoB1/oflow/releases/latest/download/oflow.AppImage"

# 3. Make executable and run
chmod +x oflow.AppImage
./oflow.AppImage
```

The user needs a Groq API key from https://console.groq.com/keys (free tier available). They can paste it in Settings after launching the app.

## Build from Source

### Dependencies

```bash
# Runtime
sudo pacman -S wtype

# Build (Tauri requirements)
sudo pacman -S webkit2gtk-4.1 base-devel curl wget file openssl appmenu-gtk-module gtk3 libappindicator-gtk3 librsvg libvips patchelf
```

### Build & Run

```bash
git clone https://github.com/CryptoB1/oflow.git
cd oflow
make install  # Full install: build, install binary, setup Waybar & autostart
make dev      # Development mode (hot reload)
```

### Make Targets

| Command | Description |
|---------|-------------|
| `make install` | Full install: build app, install to ~/.local/bin, configure Waybar, enable autostart |
| `make build` | Build release binary only |
| `make dev` | Run in development mode with hot reload |
| `make run` | Start backend server only |
| `make stop` | Stop all oflow processes |
| `make uninstall` | Remove oflow binary, Waybar config, and autostart |

## Waybar Integration

oflow displays a clickable status icon in Waybar:
- `○` idle (green) — click to open settings
- `●` recording (red)
- `◐` transcribing (yellow)

The icon is automatically configured during `make install`.

## Configuration

Settings in `~/.oflow/settings.json`:

```json
{
  "audioFeedbackTheme": "default",   // default, subtle, mechanical, silent
  "enableSpokenPunctuation": false,  // say "period" → "."
  "wordReplacements": {}             // custom word corrections
}
```

## Troubleshooting

**Run the test script to diagnose issues:**
```bash
python3 test_system.py
```

**Hotkey not working?**
```bash
hyprctl reload
```

**Backend issues?**
```bash
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock
```

**Enable debug logging:**
```bash
DEBUG_MODE=true python oflow.py
```

## License

MIT

---

*Built for Omarchy*
