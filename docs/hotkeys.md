# Keyboard Shortcuts for Oflow

Oflow uses a toggle hotkey configured via Hyprland bindings.

## Default Shortcut

**Super + D** (toggle mode: press to start recording, press again to stop and transcribe)

## How It Works

1. **Press Super+D** to start recording
2. **Speak** your message
3. **Press Super+D again** to stop recording and transcribe

The transcribed text will be automatically typed at your cursor position using `wtype`.

## Hyprland Configuration

The hotkey is configured during `make install`. It adds this to `~/.config/hypr/bindings.conf`:

```ini
# Oflow voice dictation (toggle: press Super+D to start/stop)
bind = SUPER, D, exec, ~/.local/bin/oflow-ctl toggle
```

The `oflow-ctl` script sends commands to the backend via Unix socket at `/tmp/voice-dictation.sock`.

## Changing the Hotkey

To use a different key:

1. Edit `~/.config/hypr/bindings.conf`
2. Change `SUPER, D` to your preferred key (e.g., `SUPER, I` or `CTRL SHIFT, SPACE`)
3. Reload Hyprland: `hyprctl reload`

### Example: Push-to-Talk Mode

If you prefer hold-to-record instead of toggle:

```ini
# Push-to-talk: hold to record, release to stop
bind = SUPER, I, exec, ~/.local/bin/oflow-ctl start
bindr = SUPER, I, exec, ~/.local/bin/oflow-ctl stop
```

## Troubleshooting

### Hotkey not working?

1. Check if oflow backend is running:
   ```bash
   python3 test_system.py
   ```

2. Reload Hyprland:
   ```bash
   hyprctl reload
   ```

3. Check if binding is in your config:
   ```bash
   grep oflow ~/.config/hypr/bindings.conf
   ```

### Backend not responding?

```bash
# Clean up stale files and restart
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock
~/.local/bin/oflow-toggle
```
