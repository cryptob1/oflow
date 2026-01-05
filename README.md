# OmarchyFlow

> Local voice dictation for Omarchy (Hyprland/Wayland) - A WhisperFlow/Willow alternative supporting OpenAI & Gemini direct audio APIs

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

## Demo

```
🎤 Hold Super+I → Speak → Release
✨ "um so like my NAME is ADAM"
→ "My name is Adam"
```

## Quick Start

```bash
git clone https://github.com/CryptoB1/omarchyflow.git
cd omarchyflow
./setup.sh    # Installs everything, prompts for API key
make run      # Start the server
```

Then press **Super+I** to dictate!

## Features

- ✨ **Fast & Accurate** - Direct audio API transcription (OpenAI: 100% reliable)
- 🎯 **Smart Formatting** - Filler word removal, case correction, grammar fixes
- ⌨️ **Global Hotkey** - Press Super+I to dictate anywhere
- 🎤 **Auto-Paste** - Text automatically types into active window
- 💰 **Cost-Effective** - ~$0.005/use (or ~$0.0001 with Gemini)

## Requirements

- **OS**: Arch Linux with Hyprland/Wayland
- **Python**: 3.13+
- **API Key**: [OpenAI](https://platform.openai.com/api-keys) (recommended) or [OpenRouter](https://openrouter.ai/keys)

## Installation

### Automatic (Recommended)

```bash
./setup.sh
```

The setup script will:
1. Install system dependencies (wtype, libnotify)
2. Install uv package manager
3. Create Python virtual environment
4. Install all dependencies
5. Configure your API key
6. Set up Hyprland keybindings

### Manual

<details>
<summary>Click to expand manual installation steps</summary>

```bash
# System dependencies
sudo pacman -S python wtype libnotify

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python environment
uv venv
source .venv/bin/activate
uv pip install sounddevice numpy httpx python-dotenv faster-whisper

# Configure API key
cp .env.example .env
# Edit .env with your API key

# Hyprland keybindings - add to ~/.config/hypr/bindings.conf:
# bind = SUPER, I, exec, /path/to/.venv/bin/python /path/to/omarchyflow.py start
# bindr = SUPER, I, exec, /path/to/.venv/bin/python /path/to/omarchyflow.py stop
```

</details>

## Usage

### Start the Server

```bash
make run          # Foreground
make run &        # Background
```

Or use systemd for auto-start - see [docs/systemd.md](docs/systemd.md).

### Dictate

1. Press and hold **Super+I**
2. Speak your text
3. Release **Super+I**
4. Text appears in active window

### Make Commands

```bash
make help     # Show all commands
make run      # Start server
make test     # Run test suite
make status   # Check if server running
make clean    # Remove generated files
```

## Configuration

Edit `.env` to customize:

```bash
# Choose ONE backend:
USE_OPENAI_DIRECT=true      # OpenAI ($0.005/use, 100% reliable)
USE_OPENROUTER_GEMINI=false # Gemini ($0.0001/use, 30% consistency)

# API Keys:
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-v1-...

# Optional:
SAMPLE_RATE=16000           # Audio sample rate
DEBUG_MODE=false            # Verbose logging
```

## Text Processing

| Input | Output |
|-------|--------|
| "um my NAME is ADAM" | "My name is Adam" |
| "first buy milk second call mom" | "1. Buy milk\n2. Call mom" |
| "STOP doing that" | "Stop doing that" |

**Features:**
- Filler removal (um, uh, like, you know)
- Case normalization
- Punctuation & grammar fixes
- List detection & formatting

## Cost

| Model | Per Use | 1000/month | Reliability |
|-------|---------|------------|-------------|
| **OpenAI gpt-4o-audio** | $0.005 | $5.00 | 100% ✅ |
| **Gemini 2.5 Flash** | $0.0001 | $0.10 | 30% ⚠️ |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Server not running" | `make run &` |
| "No module found" | `source .venv/bin/activate` |
| Audio too quiet | `pactl set-source-volume @DEFAULT_SOURCE@ 200%` |
| wtype not found | `sudo pacman -S wtype` |

## License

MIT - see [LICENSE](LICENSE)

---

**Made with ❤️ for the Omarchy community**
