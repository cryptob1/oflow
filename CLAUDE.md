# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Oflow is a voice dictation system for Hyprland/Wayland. It records audio via global hotkey (Super+I), transcribes it using OpenAI Whisper, optionally cleans it with GPT-4o-mini, and types the result into the active window using wtype.

## Common Commands

```bash
# Start the server (runs in background)
make run

# Stop the server
make stop

# Run tests
make test
python tests/test_robustness.py

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

The system uses a LangGraph pipeline (Sandwich Architecture):

```
Audio Recording → Audio Validation → Whisper STT → GPT-4o-mini Cleanup → Storage → wtype Output
```

### Single-file structure
All core logic is in `oflow.py`:
- `AudioValidator` - validates audio before API calls (duration, amplitude checks)
- `AudioProcessor` - normalizes audio and converts to base64 WAV
- `WhisperAPI` - OpenAI Whisper transcription client
- `TextCleanupAgent` - GPT-4o-mini text cleanup (optional, controlled by ENABLE_CLEANUP)
- `StorageManager` - JSONL transcript storage and memory persistence
- `MemoryBuilder` - learns user patterns from transcript history (optional, controlled by ENABLE_MEMORY)
- `VoiceDictationServer` - Unix socket server receiving start/stop/toggle commands

### LangGraph nodes
The pipeline is defined in `create_transcription_graph()`:
1. `node_whisper` - transcribes audio
2. `node_cleanup` - cleans up text (if ENABLE_CLEANUP=true)
3. `node_storage` - saves transcript, triggers memory building

### IPC
- Unix socket at `/tmp/voice-dictation.sock`
- Commands: `start`, `stop`, `toggle`
- Hyprland binds Super+I press/release to start/stop commands

### Data storage
- Transcripts: `~/.oflow/transcripts.jsonl`
- Memories: `~/.oflow/memories.json`
- Settings: `~/.oflow/settings.json`

## Configuration

Environment variables in `.env`:
- `OPENAI_API_KEY` - required
- `DEBUG_MODE` - enable verbose logging
- `ENABLE_CLEANUP` - enable GPT-4o-mini text cleanup (default: true)
- `ENABLE_MEMORY` - enable learning from transcript history (default: false)

## Audio Constants

- Sample rate: 16kHz (Whisper requirement)
- Channels: mono
- Min duration: 0.3s
- Min amplitude: 0.01 (speech detection threshold)
- Normalization target: 0.95

## Release TODO

- [ ] Publish AUR package for easy installation on Arch Linux
- [ ] Add systemd user service for auto-start
