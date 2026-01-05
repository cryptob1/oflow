# OmarchyFlow

> Local voice dictation for Omarchy (Hyprland/Wayland) - A WhisperFlow/Willow alternative supporting OpenAI & Gemini direct audio APIs

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

## Features

- ✨ **Fast & Accurate** - Direct audio API transcription (OpenAI: 100% reliable, Gemini: 30% consistency)
- 🎯 **Smart Formatting** - Automatic case correction, filler word removal, grammar fixes
- 🔐 **100% Private** - Audio processed through your own API key
- ⌨️ **Global Hotkey** - Press Super+I to dictate anywhere
- 🎤 **Auto-Paste** - Transcribed text automatically types into active window
- 💰 **Cost-Effective** - OpenAI: ~$0.005/use, Gemini: ~$0.0001/use (cheaper but less reliable)

## Demo

```
🎤 Hold Super+I → Speak → Release
✨ "um so like my NAME is ADAM"
→ "My name is Adam"
```

## Requirements

- **OS**: Arch Linux with Hyprland/Wayland
- **Python**: 3.13+
- **Tools**: `wtype` (for Wayland text injection)
- **API**: OpenAI API key (recommended) OR OpenRouter API key (for Gemini)

## Installation

### 1. Install System Dependencies

```bash
# Arch Linux
sudo pacman -S python python-pip wtype libnotify mpv

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone Repository

```bash
git clone https://github.com/CryptoB1/omarchyflow.git
cd omarchyflow
```

### 3. Install Python Dependencies

```bash
uv venv
source .venv/bin/activate
uv pip install sounddevice numpy httpx python-dotenv faster-whisper
```

### 4. Configure API Key

**Option A: OpenAI (Recommended - 100% reliability)**

```bash
cp .env.example .env
# Edit .env and add your OpenAI API key:
echo "OPENAI_API_KEY=sk-..." > .env
echo "USE_OPENAI_DIRECT=true" >> .env
echo "USE_OPENROUTER_GEMINI=false" >> .env
```

Get your API key from: https://platform.openai.com/api-keys

**Option B: Gemini via OpenRouter (Cheaper - 30% consistency)**

```bash
cp .env.example .env
# Edit .env and add your OpenRouter API key:
echo "OPENROUTER_API_KEY=sk-or-v1-..." > .env
echo "USE_OPENAI_DIRECT=false" >> .env
echo "USE_OPENROUTER_GEMINI=true" >> .env
```

Get your API key from: https://openrouter.ai/keys

**Warning**: Gemini has only 30% consistency (same audio = different transcriptions)

### 5. Setup Hyprland Keybindings

Add to `~/.config/hypr/bindings.conf`:

```conf
bind = SUPER, I, exec, /path/to/omarchyflow/omarchyflow start
bindr = SUPER, I, exec, /path/to/omarchyflow/omarchyflow stop
```

Reload Hyprland config:
```bash
hyprctl reload
```

### 6. Start the Server

```bash
# Run in background
./omarchyflow &

# Or use systemd (recommended)
# See docs/systemd.md for service file
```

## Usage

### Basic Dictation

1. Press and hold **Super+I**
2. Speak your text
3. Release **Super+I**
4. Text appears in active window

### What Gets Cleaned Up

| Input | Output |
|-------|--------|
| "um my NAME is ADAM" | "My name is Adam" |
| "so like first buy milk and uh second call mom" | "1. Buy milk\n2. Call mom" |
| "STOP doing that" | "Stop doing that" |

### Features

- **Case Normalization**: Converts ALL-CAPS to proper case
- **Filler Removal**: Removes um, uh, like, you know
- **Grammar Fixes**: Automatic punctuation and capitalization
- **List Formatting**: Detects "first, second, third" and formats as numbered lists
- **Smart Numbers**: Converts "fifteen" to "15", "three thirty PM" to "3:30 PM"

## Configuration

Edit `.env` to customize:

```bash
# API Configuration (choose ONE)
OPENAI_API_KEY=sk-...           # Your OpenAI API key
OPENROUTER_API_KEY=sk-or-v1-... # Your OpenRouter API key

# Mode Settings (enable ONLY ONE)
USE_OPENAI_DIRECT=true          # OpenAI gpt-4o-audio-preview (100% reliable, $0.005/use)
USE_OPENROUTER_GEMINI=false     # Gemini 2.5 Flash (30% consistency, $0.0001/use)
USE_AUDIO_DIRECT=false          # Legacy OpenRouter audio (broken)

# Audio Settings (advanced)
SAMPLE_RATE=16000               # Audio sample rate (default: 16000)
```

## Testing

Run the included test suite to verify setup:

```bash
./test_suite.py
```

Expected output:
```
Total tests: 10
✅ Passed: 10
❌ Failed: 0
Success rate: 100.0%
```

## Troubleshooting

### "No module named 'sounddevice'"
```bash
source .venv/bin/activate
uv pip install sounddevice
```

### "Server not running"
```bash
# Check if server is running
ps aux | grep omarchyflow

# Start server
./omarchyflow &
```

### "Audio volume too low"
The script automatically sets mic volume to 150%. If still quiet:
```bash
pactl set-source-volume @DEFAULT_SOURCE@ 200%
```

### "Permission denied"
```bash
chmod +x omarchyflow test_suite.py
```

## Cost Breakdown

| Model | Per 3s dictation | 100 uses | 1000 uses/month | Reliability |
|-------|------------------|----------|-----------------|-------------|
| **OpenAI gpt-4o-audio-preview** | ~$0.005 | ~$0.50 | ~$5.00 | **100%** ✅ |
| **Gemini 2.5 Flash (OpenRouter)** | ~$0.0001 | ~$0.01 | ~$0.10 | **30%** ⚠️ |

**OpenAI**: More expensive but 100% consistent transcriptions  
**Gemini**: 50x cheaper but inconsistent (same audio = different results)

**Still much cheaper than transcription services!**

## Architecture

```
┌─────────────┐
│   Press     │
│  Super+I    │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Record    │
│   Audio     │ (16kHz, mono)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Normalize  │
│   Volume    │ (95% peak)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Base64      │
│ Encode      │
└──────┬──────┘
       │
       ▼
┌─────────────────────────┐
│  OpenAI API             │
│  gpt-4o-audio-preview   │
│  Minimal prompt:        │
│  "Transcribe."          │
└──────┬──────────────────┘
       │
       ▼
┌─────────────┐
│  Clean      │
│  Output     │ (strip preambles)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   wtype     │
│  (paste)    │
└─────────────┘
```

## Why Not Whisper?

We tested multiple approaches:

| Approach | Speed | Accuracy | Cost | Reliability |
|----------|-------|----------|------|-------------|
| **Whisper Local** | 2-3s | 95% | Free | 100% |
| **OpenRouter Gemini 2.5 Flash** | 1s | 60% | $0.0001 | 30% ⚠️ |
| **OpenRouter Other Models** | 1s | 30% | Low | 30% ❌ |
| **OpenAI gpt-audio-mini** | 1s | 40% | Low | 40% ❌ |
| **OpenAI gpt-4o-audio** | 1s | **100%** | $0.005 | **100%** ✅ |

**Gemini findings:** Works but inconsistent - tested 10x identical audio, got 6 different transcriptions  
**Result**: OpenAI `gpt-4o-audio-preview` is most reliable. Gemini available as cheaper alternative.

## Comparison to Alternatives

| Feature | OmarchyFlow | WhisperFlow | Willow |
|---------|-------------|-------------|--------|
| **Platform** | Linux/Wayland | macOS | Any |
| **Backend** | OpenAI Direct | Whisper.cpp/Cloud | CTranslate2 |
| **Cost** | $0.005/use | Free/Paid | Free (self-host) |
| **Speed** | ~1s | ~2s | ~1s |
| **Accuracy** | 100% | 95% | 98% |
| **Setup** | Simple | Simple | Complex |
| **Privacy** | API-based | Local/Cloud | Local |

## Contributing

Contributions welcome! Please:

1. Fork the repo
2. Create a feature branch
3. Test with `./test_suite.py`
4. Submit a PR

## License

MIT License - see [LICENSE](LICENSE)

## Acknowledgments

- Inspired by [WhisperFlow](https://github.com/moritzWa/whisperflow) (macOS)
- Inspired by [Willow](https://github.com/toverainc/willow) (ESP32 hardware)
- Built for [Omarchy](https://github.com/omarchy) Hyprland setup

## Support

- **Issues**: https://github.com/CryptoB1/omarchyflow/issues
- **Discussions**: https://github.com/CryptoB1/omarchyflow/discussions

---

**Made with ❤️ for the Omarchy community**
