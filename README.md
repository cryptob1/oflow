# Oflow

> **Robust voice dictation for Hyprland/Wayland** - Built with LangChain architecture for reliability

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

## Features

- 🎯 **Production-Ready** - Built with LangChain async architecture for 100% reliability
- 🔊 **Smart Validation** - Audio validation prevents empty/silent recordings from wasting API calls
- 🔄 **Auto-Retry** - Exponential backoff retry logic handles transient failures
- ⚡ **Fast** - Async streaming enables sub-second response times
- 🎨 **Smart Formatting** - Automatic grammar correction, filler word removal, case normalization
- ⌨️ **Global Hotkey** - Press Super+I to dictate anywhere in Hyprland
- 🔐 **Privacy-First** - Audio processed through your own API key
- 💰 **Cost-Effective** - ~$0.005/use (OpenAI) or ~$0.0001/use (Gemini)

## Quick Start

```bash
git clone https://github.com/CryptoB1/oflow.git
cd oflow
./setup.sh    # Installs dependencies and configures API keys
make run      # Start the server
```

Press **Super+I** → speak → release → text appears!

## Demo

```
🎤 Hold Super+I → "um so like my NAME is ADAM"
✨ Auto-formatted → "My name is Adam"
```

## Installation

### Requirements

- **OS**: Arch Linux with Hyprland (Wayland)
- **Python**: 3.13+
- **Tools**: `wtype` (Wayland text injection), `libnotify` (notifications)
- **API Key**: [OpenAI](https://platform.openai.com/api-keys) (recommended) or [OpenRouter](https://openrouter.ai/keys) (for Gemini)

### Automatic Setup

```bash
./setup.sh
```

The setup script will:
1. Install system dependencies (`wtype`, `libnotify`)
2. Create Python virtual environment
3. Install Python dependencies
4. Configure API keys in `.env`
5. Setup Hyprland keybindings

### Manual Setup

```bash
# 1. Install system dependencies
sudo pacman -S python python-pip wtype libnotify

# 2. Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Create virtual environment and install dependencies
uv venv
source .venv/bin/activate
uv pip install sounddevice numpy httpx python-dotenv langchain-core

# 4. Configure API keys
cp .env.example .env
# Edit .env and add your API key

# 5. Setup Hyprland keybindings
echo 'bind = SUPER, I, exec, /path/to/oflow/oflow start' >> ~/.config/hypr/bindings.conf
echo 'bindr = SUPER, I, exec, /path/to/oflow/oflow stop' >> ~/.config/hypr/bindings.conf
hyprctl reload
```

## Configuration

Edit `.env` to configure your setup:

```bash
# Provider Selection (choose ONE)
USE_OPENAI_DIRECT=true          # OpenAI gpt-4o-audio-preview (100% reliable)
USE_OPENROUTER_GEMINI=false     # Gemini 2.5 Flash (30% consistency, 50x cheaper)

# API Keys
OPENAI_API_KEY=sk-...           # From https://platform.openai.com/api-keys
OPENROUTER_API_KEY=sk-or-v1-... # From https://openrouter.ai/keys (if using Gemini)

# Advanced (optional)
SAMPLE_RATE=16000               # Audio sample rate
DEBUG_MODE=false                # Enable debug logging
```

## Usage

### Starting the Server

```bash
# Method 1: Using Makefile
make run

# Method 2: Direct execution
./oflow

# Method 3: With systemd (auto-start on boot)
sudo cp oflow.service /etc/systemd/system/
sudo systemctl enable --now oflow
```

### Voice Dictation

1. **Press and hold** Super+I
2. **Speak** your text
3. **Release** Super+I
4. Text automatically types into active window

### What Gets Cleaned Up

| Input | Output |
|-------|--------|
| "um my NAME is ADAM" | "My name is Adam" |
| "so like first buy milk and uh second call mom" | "1. Buy milk\n2. Call mom" |
| "STOP doing that" | "Stop doing that" |

## Architecture

Oflow uses the **"Sandwich Architecture"** from [LangChain's voice agent guide](https://docs.langchain.com/oss/python/langchain/voice-agent):

```
Audio Recording
    ↓
Audio Validation ← NEW: Prevents empty/silent audio
    ↓
STT Stream (Async)
    ├─ Producer: Send audio chunks
    └─ Consumer: Receive transcripts
    ↓
Retry Logic (3x with backoff) ← NEW: Handles transient failures
    ↓
Event System ← NEW: Clear error visibility
    ↓
Text Output
```

**Key Improvements Over Traditional Approaches:**
- ✅ Audio validation before API calls (saves money, prevents errors)
- ✅ Async streaming (non-blocking, efficient)
- ✅ Retry logic with exponential backoff
- ✅ Event-driven error handling
- ✅ Comprehensive test coverage

See [`docs/architecture.md`](docs/architecture.md) for detailed architecture documentation.

## Provider Comparison

| Provider | Cost/use | Reliability | Latency | Recommendation |
|----------|----------|-------------|---------|----------------|
| **OpenAI gpt-4o-audio-preview** | $0.005 | 100% | ~1s | ✅ **Production** |
| **Gemini 2.5 Flash (OpenRouter)** | $0.0001 | 30% | ~1s | ⚠️ **Experimentation** |

**Note**: Gemini is 50x cheaper but inconsistent (same audio = different results). See [`docs/gemini-integration.md`](docs/gemini-integration.md) for details.

## Testing

Run the test suite to verify your installation:

```bash
# Run all tests
make test

# Or manually
python tests/test_robustness.py
```

Expected output:
```
============================================================
Oflow LangChain Robustness Tests
============================================================

✅ Empty audio correctly rejected
✅ Silent audio correctly rejected
✅ Valid audio accepted
✅ Audio normalized to 0.950
...

Results: 8 passed, 0 failed
============================================================
```

## Development

```bash
# Install development dependencies
uv pip install -e .[dev]

# Run tests with coverage
pytest --cov=oflow tests/

# Format code
ruff format .

# Lint code
ruff check .
```

## Troubleshooting

### Server Won't Start

```bash
# Check if server is running
ps aux | grep oflow

# View logs
journalctl -u oflow -f  # If using systemd
tail -f /tmp/oflow.log  # If run manually
```

### "Transcription Failed" Errors

1. **Check API key**: Verify `.env` has correct `OPENAI_API_KEY` or `OPENROUTER_API_KEY`
2. **Check credits**: Verify you have API credits at platform.openai.com or openrouter.ai
3. **Enable debug mode**: Set `DEBUG_MODE=true` in `.env` and check logs
4. **Test audio**: Run `python tests/test_robustness.py` to verify audio processing

### Audio Too Quiet

```bash
# Increase microphone volume
pactl set-source-volume @DEFAULT_SOURCE@ 200%
```

### Keybinding Not Working

```bash
# Verify Hyprland config was updated
grep -i "oflow" ~/.config/hypr/bindings.conf

# Reload Hyprland config
hyprctl reload

# Test manually
./oflow start
# Speak something
./oflow stop
```

## Project Structure

```
oflow/
├── docs/               # Documentation
│   ├── architecture.md    # LangChain architecture details
│   ├── gemini-integration.md  # Gemini provider documentation
│   └── systemd.md         # Systemd service setup
├── tests/              # Test suite
│   ├── test_robustness.py  # Comprehensive robustness tests
│   └── test_legacy.py      # Legacy Whisper tests
├── .github/
│   └── workflows/
│       └── ci.yml         # CI/CD pipeline
├── oflow         # Main executable
├── setup.sh            # Installation script
├── Makefile            # Build automation
├── pyproject.toml      # Python project metadata
├── .env.example        # Environment template
├── LICENSE             # MIT License
├── README.md           # This file
├── CHANGELOG.md        # Version history
├── CONTRIBUTING.md     # Contribution guidelines
└── CODE_OF_CONDUCT.md  # Community standards
```

## Contributing

Contributions are welcome! Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`make test`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Architecture inspired by [LangChain's voice agent guide](https://docs.langchain.com/oss/python/langchain/voice-agent)
- Similar to [WhisperFlow](https://github.com/moritzWa/whisperflow) (macOS) and [Willow](https://github.com/toverainc/willow) (hardware)
- Built for the [Omarchy](https://github.com/omarchy) Hyprland community

## Links

- **GitHub**: https://github.com/CryptoB1/oflow
- **Issues**: https://github.com/CryptoB1/oflow/issues
- **Discussions**: https://github.com/CryptoB1/oflow/discussions

---

**Made with ❤️ for the Omarchy community**
