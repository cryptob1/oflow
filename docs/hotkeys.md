# Keyboard Shortcuts for Oflow

Oflow uses a configurable push-to-talk hotkey that works system-wide across all platforms.

## Default Shortcut

**Super + I** (hold to record, release to stop)

## Configuring the Shortcut

The keyboard shortcut can be configured directly in the Oflow app:

1. Open Oflow
2. Go to **Settings**
3. Find the **Keyboard Shortcut** section
4. Select your preferred shortcut from the dropdown

### Available Presets

| Shortcut | Description |
|----------|-------------|
| Super + I | Default - Uses Windows/Super/Cmd key |
| Ctrl + Shift + Space | Three-key combination |
| Alt + Space | Alternative quick access |
| F9 | Function key (no modifiers) |
| Ctrl + Shift + R | Alternative three-key combo |

## How Push-to-Talk Works

1. **Press** the shortcut key to start recording
2. **Speak** your message
3. **Release** the key to stop recording and process

The transcribed text will be automatically typed at your cursor position.

## Platform Support

The shortcut system uses Tauri's global-shortcut plugin, which provides consistent behavior across:

- **Linux** (X11 and Wayland, including Hyprland)
- **macOS** (uses Cmd key for Super)
- **Windows** (uses Win key for Super)

## Legacy: Hyprland Manual Bindings

If you're on Hyprland and prefer to use window manager bindings instead of the app's built-in shortcut, you can add this to `~/.config/hypr/bindings.conf`:

```ini
# Voice Dictation - Press to start, release to stop
bind = SUPER, I, exec, ~/.venv/bin/python ~/code/oflow/oflow.py start
bindr = SUPER, I, exec, ~/.venv/bin/python ~/code/oflow/oflow.py stop
```

**Note:** When using WM bindings, disable the in-app shortcut to avoid conflicts.

## Troubleshooting

### Shortcut not working?

1. Make sure Oflow is running (check system tray)
2. Try a different shortcut in Settings
3. Check if another app is using the same shortcut
4. On Linux, some shortcuts may be reserved by the desktop environment

### Shortcut conflicts

If your chosen shortcut conflicts with another application:

1. Change the Oflow shortcut in Settings
2. Or disable the conflicting shortcut in the other application
