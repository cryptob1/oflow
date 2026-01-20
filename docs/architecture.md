# Oflow Architecture

## Overview

Oflow is a voice dictation system for Hyprland/Wayland. It uses a simple pipeline:

```
Audio Recording → Validation → Whisper STT → LLM Cleanup → wtype Output
```

## Components

### Backend (`oflow.py`)

A single Python file (~1200 lines) that handles:

1. **Audio Recording** - Uses `sounddevice` to capture audio from the default microphone
2. **Audio Validation** - Checks duration and amplitude before sending to API
3. **Transcription** - Sends audio to Groq Whisper (or OpenAI) for speech-to-text
4. **Cleanup** - Optional LLM cleanup with Llama 3.1 (or GPT-4o-mini)
5. **Text Output** - Types result using `wtype` (Wayland) or `xdotool` (X11)

### IPC

- **Unix Socket**: `/tmp/voice-dictation.sock`
- **Commands**: `start`, `stop`, `toggle`
- **PID File**: `/tmp/oflow.pid` (prevents duplicate instances)

### Frontend (`oflow-ui/`)

A Tauri v2 app (Rust + React) providing:
- Settings UI (API keys, provider selection)
- Transcript history viewer
- System tray integration
- Single-instance enforcement
- Auto-starts backend if not running

### Waybar Integration

State file at `$XDG_RUNTIME_DIR/oflow/state` provides real-time status:
- `idle` (green) - Ready to record
- `recording` (red) - Currently recording
- `transcribing` (yellow) - Processing audio
- `error` (red) - Something went wrong

The oflow icon appears in Waybar center (next to clock) and is configured automatically by `make install`.

## Data Flow

```
1. User presses Super+D
   ↓
2. Hyprland runs: ~/.local/bin/oflow-ctl toggle
   ↓
3. oflow-ctl sends "toggle" to Unix socket
   ↓
4. Backend starts recording audio
   ↓
5. User presses Super+D again
   ↓
6. Backend stops recording, processes audio:
   a. Validate (duration, amplitude)
   b. Normalize audio
   c. Send to Whisper API → raw text
   d. Send to LLM API → cleaned text (optional)
   e. Filter hallucinations
   f. Apply text processing (spoken punctuation, replacements)
   ↓
7. Backend types result with wtype
   ↓
8. Backend saves transcript to ~/.oflow/transcripts.jsonl
```

## Configuration

### Settings File (`~/.oflow/settings.json`)

```json
{
  "provider": "groq",
  "groqApiKey": "gsk_...",
  "openaiApiKey": "sk-...",
  "enableCleanup": true,
  "audioFeedbackTheme": "default",
  "audioFeedbackVolume": 0.3,
  "iconTheme": "minimal",
  "enableSpokenPunctuation": false,
  "wordReplacements": {}
}
```

### Environment Variables

- `GROQ_API_KEY` - Groq API key (fallback if not in settings.json)
- `OPENAI_API_KEY` - OpenAI API key (fallback)
- `DEBUG_MODE` - Enable verbose logging

## Why This Architecture?

1. **Simple** - Single Python file, no complex frameworks
2. **Fast** - Direct httpx calls, no LangChain overhead
3. **Reliable** - Hallucination filtering, audio validation
4. **Lightweight** - Minimal dependencies (sounddevice, numpy, httpx)
