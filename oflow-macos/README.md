# oflow-macos

Voice dictation for macOS. Press a hotkey, speak, press again — your words appear wherever you're typing.

Runs fully locally using whisper.cpp via `pywhispercpp` (no API key).

## Requirements

- macOS 10.15+ (Catalina or later)
- Python 3.11+
- `uv` (recommended) or `pip`

## Status

For now, **oflow-macos must be started from an interactive Terminal session** (leave that Terminal window open).
Running it as a LaunchAgent/background service is not supported yet due to macOS privacy permissions for global key capture.

## Installation

### Recommended (uses the Makefile)

```bash
cd oflow-macos
make install
```

### Manual (pip)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## macOS Permissions

Before first use, grant these permissions in **System Settings > Privacy & Security**:

1. **Accessibility** — Required for global hotkey and typing text into apps
   - Go to: Privacy & Security > Accessibility
   - Add and enable your Terminal app (Terminal.app, iTerm2, etc.)

2. **Input Monitoring** — Required for keyboard event capture
   - Go to: Privacy & Security > Input Monitoring
   - Add and enable your Terminal app

3. **Microphone** — Required for audio recording
   - macOS will prompt automatically on first recording

## Usage

### Start the app

```bash
make run
```

A `[MIC]` icon appears in the menu bar. The app runs in the background.

### Record and transcribe

- **Press Right Shift** to start recording
- Speak your text
- **Press Right Shift** again to stop
- Your transcribed text is typed into the active window

### CLI control

Send commands to a running instance:

```bash
oflow-macos toggle   # Toggle recording on/off
oflow-macos start    # Start recording
oflow-macos stop     # Stop recording
```

### Menu bar

Click the `[MIC]` icon in the menu bar for:

- **Toggle Recording** — Start/stop recording
- **Change Model...** — Switch Whisper model (downloads on first use)
- **Quit** — Exit oflow

### Status indicators

| Icon | State |
|------|-------|
| `[MIC]` | Ready (idle) |
| `[REC]` | Recording |
| `[...]` | Transcribing |

## Configuration

Settings are stored in `~/.oflow/settings.json`:

```json
{
  "whisperModel": "large-v3-turbo",
  "audioFeedbackTheme": "default",
  "audioFeedbackVolume": 0.3,
  "enableSpokenPunctuation": false,
  "wordReplacements": {}
}
```

### Settings

| Key | Default | Description |
|-----|---------|-------------|
| `whisperModel` | `"large-v3-turbo"` | Whisper model name (downloaded to `~/.oflow/models`) |
| `audioFeedbackTheme` | `"default"` | Sound theme: `default`, `subtle`, `mechanical`, `silent` |
| `audioFeedbackVolume` | `0.3` | Feedback volume (0.0 to 1.0) |
| `enableSpokenPunctuation` | `false` | Convert "period" to `.`, "comma" to `,`, etc. |
| `wordReplacements` | `{}` | Custom word replacements (`{"btw": "by the way"}`) |

### Transcripts

All transcriptions are saved to `~/.oflow/transcripts.jsonl` with timestamps.

## Troubleshooting

### "Accessibility permission not granted"

Add your Terminal to System Settings > Privacy & Security > Accessibility and restart oflow.

### Text goes to clipboard instead of being typed

This means Accessibility permission isn't granted. The app falls back to copying text to the clipboard — paste with Cmd+V.

### No audio / recording fails

Check that Microphone permission is granted for your Terminal app in System Settings > Privacy & Security > Microphone.

### Hotkey doesn't work

Ensure your Terminal app is enabled in both Accessibility and Input Monitoring, then restart oflow.
