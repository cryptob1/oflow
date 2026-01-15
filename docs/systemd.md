# Systemd Service Setup for Oflow

This guide explains how to set up Oflow as a systemd user service for automatic startup.

> **Note**: If you installed via `make install`, autostart is already configured using a `.desktop` file in `~/.config/autostart/`. This guide is for those who prefer systemd-based management.

## Recommended: Use Autostart (Default)

The `make install` command creates `~/.config/autostart/oflow.desktop` which starts oflow on login. This is simpler and works well for most users.

## Alternative: Systemd Service

### 1. Create the service file

Create `~/.config/systemd/user/oflow.service`:

```ini
[Unit]
Description=Oflow Voice Dictation
Documentation=https://github.com/CryptoB1/oflow
After=graphical-session.target pipewire.service
Wants=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/oflow
Restart=on-failure
RestartSec=5
Environment=PATH=%h/.local/bin:/usr/bin:/usr/local/bin

# Resource limits
MemoryMax=512M
CPUQuota=50%

[Install]
WantedBy=default.target
```

### 2. Disable the desktop autostart

To avoid running two instances:

```bash
rm ~/.config/autostart/oflow.desktop
```

### 3. Enable and start the service

```bash
# Reload systemd daemon
systemctl --user daemon-reload

# Enable the service (auto-start on login)
systemctl --user enable oflow.service

# Start the service now
systemctl --user start oflow.service

# Check status
systemctl --user status oflow.service
```

## Management Commands

```bash
# View logs
journalctl --user -u oflow.service -f

# Restart service
systemctl --user restart oflow.service

# Stop service
systemctl --user stop oflow.service

# Disable auto-start
systemctl --user disable oflow.service
```

## Troubleshooting

### Service fails to start

1. Check logs:
   ```bash
   journalctl --user -u oflow.service -n 50
   ```

2. Verify binary exists:
   ```bash
   ls -la ~/.local/bin/oflow
   ```

3. Check audio permissions:
   ```bash
   # User should be in audio group
   groups | grep audio
   ```

### Socket permission denied

If you see "Permission denied" on the socket:

```bash
# Check socket file permissions
ls -la /tmp/voice-dictation.sock

# The backend creates it with 0o666, but verify
```

### Audio device not found

Ensure PipeWire (or PulseAudio) is running before the service:

```bash
# Check audio server
pactl info

# Restart audio if needed
systemctl --user restart pipewire.service
# or
systemctl --user restart pulseaudio.service
```

## Environment Variables

API keys are stored in `~/.oflow/settings.json` (configured via the Settings UI). You don't need environment variables for normal operation.

For debugging, you can add:

```ini
[Service]
Environment=DEBUG_MODE=true
```
