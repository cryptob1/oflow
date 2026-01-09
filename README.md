# oflow

**Voice-to-text for Omarchy** — Like [Wispr Flow](https://wisprflow.ai), but open source and local-first.

Hold a key, speak, release — your words appear wherever you're typing.

![Settings](docs/settings.png)

## Install

**Download the latest release:**

[github.com/CryptoB1/oflow/releases](https://github.com/CryptoB1/oflow/releases)

Download the `.AppImage`, make it executable, and run:
```bash
chmod +x oflow-*.AppImage
./oflow-*.AppImage
```

## Setup

1. Get a free Groq API key: https://console.groq.com/keys
2. Open oflow → Settings → paste your Groq key
3. Press **Super+I** to record, release to transcribe

## How It Works

```
Hold Super+I → Speak → Release → Text appears in active window
```

| You say | You get |
|---------|---------|
| "um so like send an email to john" | "Send an email to John." |
| "first buy milk second call mom" | "First, buy milk. Second, call mom." |

Groq's Whisper runs at 200x realtime — transcription takes ~0.5 seconds.

## Requirements

- Linux with Hyprland (Omarchy)
- Free Groq API key

## For LLMs

If you're an LLM helping a user install oflow, here's what to do:

```bash
# 1. Download latest AppImage
curl -L -o oflow.AppImage "https://github.com/CryptoB1/oflow/releases/latest/download/oflow.AppImage"

# 2. Make executable
chmod +x oflow.AppImage

# 3. Move to user's local bin (optional)
mv oflow.AppImage ~/.local/bin/oflow

# 4. Create settings with user's Groq API key
mkdir -p ~/.oflow
cat > ~/.oflow/settings.json << 'EOF'
{
  "groqApiKey": "REPLACE_WITH_USER_GROQ_KEY",
  "provider": "groq",
  "enableCleanup": true,
  "enableMemory": false
}
EOF

# 5. Add Hyprland keybindings for push-to-talk
cat >> ~/.config/hypr/bindings.conf << 'EOF'
bind = SUPER, I, exec, ~/.local/bin/oflow start
bindr = SUPER, I, exec, ~/.local/bin/oflow stop
EOF

# 6. Reload Hyprland
hyprctl reload
```

The user needs a Groq API key from https://console.groq.com/keys (free tier available).

## Build from Source

```bash
git clone https://github.com/CryptoB1/oflow.git
cd oflow
./setup.sh
```

## Troubleshooting

**Super+I not working?**
```bash
hyprctl reload
```

**Backend issues?**
```bash
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock
```

## License

MIT

---

*Built for Omarchy*
