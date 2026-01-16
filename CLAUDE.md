# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Oflow is a voice dictation system for Hyprland/Wayland. It records audio via global hotkey (Super+D toggle mode), transcribes it using Groq Whisper (or OpenAI), optionally cleans it with Llama 3.1 (or GPT-4o-mini), and types the result into the active window using wtype.

## Common Commands

```bash
# Start the server (runs in background)
make run

# Stop the server
make stop

# Run tests
make test

# Format code
make format
ruff format .

# Lint code
make lint
ruff check .

# Install development dependencies
uv pip install -e .[dev]

# Run tests with coverage
pytest --cov=oflow tests/
```

## Architecture

Simple pipeline (no LangGraph):

```
Audio Recording → Audio Validation → Whisper STT → LLM Cleanup → wtype Output
```

### Single-file structure
All core logic is in `oflow.py`:
- `AudioValidator` - validates audio before API calls (duration, amplitude checks)
- `AudioProcessor` - normalizes audio and converts to WAV bytes
- `transcribe_audio()` - Whisper API call (Groq or OpenAI)
- `cleanup_text()` - LLM cleanup (Llama 3.1 or GPT-4o-mini)
- `StorageManager` - JSONL transcript storage
- `VoiceDictationServer` - Unix socket server receiving start/stop/toggle commands
- `WaybarState` - writes state to `$XDG_RUNTIME_DIR/oflow/state` for Waybar integration
- `AudioFeedback` - generates audio cues for recording start/stop/error
- `TextProcessor` - spoken punctuation and word replacements

### IPC
- Unix socket at `/tmp/voice-dictation.sock`
- Commands: `start`, `stop`, `toggle`
- Hyprland binds Super+D to toggle command (press to start, press again to stop)

### Data storage
- Transcripts: `~/.oflow/transcripts.jsonl`
- Settings: `~/.oflow/settings.json`
- Waybar state: `$XDG_RUNTIME_DIR/oflow/state` (JSON for Waybar custom module)

## Configuration

Environment variables in `.env`:
- `GROQ_API_KEY` - Groq API key (recommended)
- `OPENAI_API_KEY` - OpenAI API key (fallback)
- `DEBUG_MODE` - enable verbose logging

### Settings JSON

Settings in `~/.oflow/settings.json`:
```json
{
  "provider": "groq",
  "groqApiKey": "gsk_...",
  "enableCleanup": true,
  "audioFeedbackTheme": "default",
  "audioFeedbackVolume": 0.3,
  "iconTheme": "minimal",
  "enableSpokenPunctuation": false,
  "wordReplacements": {
    "oflow": "oflow"
  }
}
```

### Waybar Integration

Add to your Waybar config (`~/.config/waybar/config`):
```jsonc
"modules-right": ["custom/oflow", ...],

"custom/oflow": {
    "exec": "cat $XDG_RUNTIME_DIR/oflow/state 2>/dev/null || echo '{\"text\":\"○\",\"class\":\"idle\"}'",
    "return-type": "json",
    "interval": 1,
    "format": "{}",
    "tooltip": true,
    "on-click": "~/.local/bin/oflow-toggle"
}
```

### Spoken Punctuation

When `enableSpokenPunctuation` is true, say these words to insert symbols:
- "period", "comma", "colon", "semicolon"
- "question mark", "exclamation mark"
- "open paren", "close paren", "open bracket", "close bracket"
- "new line", "new paragraph"
- "hash", "at sign", "dollar sign", "percent"

## Audio Constants

- Sample rate: 16kHz (Whisper requirement)
- Channels: mono
- Min duration: 0.5s
- Min amplitude: 0.02 (speech detection threshold)
- Normalization target: 0.95
