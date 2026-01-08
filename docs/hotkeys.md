# Setting up Hotkeys for Oflow

Oflow is designed to be triggered by a global hotkey, typically `Super+I`. Since it runs as a background service listening on a socket, you can bind any key to send the `toggle` command.

## Hyprland (Omarchy Default)

Add this line to your `~/.config/hypr/hyprland.conf`:

```ini
# Voice Dictation (Super + I)
bind = SUPER, I, exec, ~/voice-assistant/oflow.py toggle
```

### Explanation
- `bind`: Creates a key binding
- `SUPER`: The Windows/Command key
- `I`: The key to press
- `exec`: Execute a command
- `~/voice-assistant/oflow toggle`: The command that tells the running server to start/stop listening

## Other Window Managers

### i3 / Sway
In `~/.config/i3/config` or `~/.config/sway/config`:

```config
bindsym $mod+i exec ~/voice-assistant/oflow.py toggle
```

### GNOME
You can set a custom shortcut in Settings -> Keyboard -> View and Customize Shortcuts -> Custom Shortcuts.

- **Name**: Oflow Toggle
- **Command**: `/home/YOUR_USER/voice-assistant/oflow.py toggle`
- **Shortcut**: Super+I
