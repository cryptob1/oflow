# oflow

> **Local voice dictation for Hyprland/Wayland** - Push-to-talk transcription with smart cleanup

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

## Features

- **Push-to-Talk** - Hold Super+I to record, release to transcribe
- **Smart Cleanup** - GPT-4o-mini fixes grammar, removes filler words, formats text
- **Desktop UI** - Tauri app for settings, transcript history, and API key configuration
- **Privacy-First** - All data stored locally in `~/.oflow/`, only API calls to OpenAI
- **Memory System** - Optional learning of your speech patterns for better cleanup
- **Reliable** - Single-instance protection prevents duplicate backends

## Quick Start

```bash
git clone https://github.com/CryptoB1/oflow.git
cd oflow
./setup.sh    # Install dependencies and configure
make run      # Start the backend
```

Press **Super+I** → speak → release → text appears!

## How It Works

```
Hold Super+I → Record Audio → Release
                    ↓
            OpenAI Whisper (transcribe)
                    ↓
            GPT-4o-mini (cleanup)
                    ↓
            Type into active window
```

| You Say | You Get |
|---------|---------|
| "um so like my NAME is ADAM" | "My name is Adam" |
| "uh first buy milk and second call mom" | "First, buy milk. Second, call mom." |

## Requirements

- **OS**: Linux with Hyprland (Wayland)
- **Python**: 3.13+
- **Tools**: `wtype`, `libnotify`
- **API Key**: [OpenAI](https://platform.openai.com/api-keys)

## Installation

### Automatic

```bash
./setup.sh
```

### Manual

```bash
# Install system dependencies
sudo pacman -S python wtype libnotify

# Create virtual environment
uv venv && source .venv/bin/activate
uv pip install -e .

# Configure API key (or use the UI)
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# Setup Hyprland keybindings
cat >> ~/.config/hypr/bindings.conf << 'EOF'
bind = SUPER, I, exec, /path/to/oflow/.venv/bin/python /path/to/oflow/oflow.py start
bindr = SUPER, I, exec, /path/to/oflow/.venv/bin/python /path/to/oflow/oflow.py stop
EOF
hyprctl reload
```

## Usage

### Backend

```bash
# Start the voice dictation server
make run
# or
./oflow.py

# The backend runs as a single instance (PID lock at /tmp/oflow.pid)
```

### Desktop UI

```bash
cd oflow-ui
npm install
npm run tauri dev
```

The UI provides:
- **Dashboard** - Quick status overview
- **History** - Browse and copy past transcriptions
- **Settings** - Configure API key, cleanup, and memory options

### Voice Dictation

1. Press and hold **Super+I**
2. Speak your text
3. Release **Super+I**
4. Text types into active window

## Configuration

Settings are stored in `~/.oflow/settings.json`:

```json
{
  "enableCleanup": true,
  "enableMemory": false,
  "openaiApiKey": "sk-..."
}
```

- **enableCleanup** - Use GPT-4o-mini to clean up transcripts
- **enableMemory** - Learn your speech patterns over time
- **openaiApiKey** - Your OpenAI API key

You can also set `OPENAI_API_KEY` in `.env` or the environment.

## Project Structure

```
oflow/
├── oflow.py           # Python backend (LangGraph + Whisper + GPT-4o-mini)
├── oflow-ui/          # Tauri desktop app (React + TypeScript + Tailwind)
│   ├── src/           # React frontend
│   └── src-tauri/     # Rust backend
├── setup.sh           # Installation script
├── Makefile           # Build automation
└── docs/              # Documentation
```

## Data Storage

All data is stored locally in `~/.oflow/`:

- `settings.json` - User preferences
- `transcripts.jsonl` - Transcript history (raw + cleaned)
- `memories.json` - Learned speech patterns (if enabled)

## Troubleshooting

### Backend not starting

```bash
# Check if already running
cat /tmp/oflow.pid
ps aux | grep oflow

# Clean up and restart
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock
make run
```

### Super+I not working

```bash
# Verify Hyprland bindings
grep -i oflow ~/.config/hypr/bindings.conf

# Reload Hyprland
hyprctl reload

# Test manually
./oflow.py start   # Should show "oflow is listening"
./oflow.py stop    # Should transcribe and type
```

### Audio too quiet

```bash
pactl set-source-volume @DEFAULT_SOURCE@ 150%
```

## License

MIT - see [LICENSE](LICENSE)

---

**Made for the Omarchy community**
