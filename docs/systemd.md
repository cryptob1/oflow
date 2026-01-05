# Systemd Service Setup for OmarchyFlow

This guide explains how to set up OmarchyFlow as a systemd user service for automatic startup.

## Installation

### 1. Create the service file

Create `~/.config/systemd/user/omarchyflow.service`:

```ini
[Unit]
Description=OmarchyFlow Voice Dictation Server
Documentation=https://github.com/CryptoB1/omarchyflow
After=graphical-session.target pulseaudio.service pipewire.service
Wants=graphical-session.target

[Service]
Type=simple
ExecStart=/path/to/omarchyflow/.venv/bin/python /path/to/omarchyflow/omarchyflow.py
Restart=on-failure
RestartSec=5
Environment=PATH=/usr/bin:/usr/local/bin
WorkingDirectory=/path/to/omarchyflow

# Resource limits
MemoryMax=512M
CPUQuota=50%

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ReadWritePaths=/tmp

[Install]
WantedBy=default.target
```

### 2. Customize the paths

Replace `/path/to/omarchyflow` with the actual installation path:

```bash
# Example: If installed in ~/code/omarchyflow
sed -i 's|/path/to/omarchyflow|/home/yourusername/code/omarchyflow|g' \
    ~/.config/systemd/user/omarchyflow.service
```

### 3. Enable and start the service

```bash
# Reload systemd daemon
systemctl --user daemon-reload

# Enable the service (auto-start on login)
systemctl --user enable omarchyflow.service

# Start the service now
systemctl --user start omarchyflow.service

# Check status
systemctl --user status omarchyflow.service
```

## Management Commands

```bash
# View logs
journalctl --user -u omarchyflow.service -f

# Restart service
systemctl --user restart omarchyflow.service

# Stop service
systemctl --user stop omarchyflow.service

# Disable auto-start
systemctl --user disable omarchyflow.service
```

## Troubleshooting

### Service fails to start

1. Check logs:
   ```bash
   journalctl --user -u omarchyflow.service -n 50
   ```

2. Verify Python environment:
   ```bash
   # Ensure the venv is activated in the service
   # Or use absolute path to Python
   ExecStart=/path/to/omarchyflow/.venv/bin/python /path/to/omarchyflow/omarchyflow.py
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

# The service creates it with 0o666, but verify
```

### Audio device not found

Ensure PulseAudio/PipeWire is running before the service:

```bash
# Check audio server
pactl info

# Restart audio if needed
systemctl --user restart pulseaudio.service
# or
systemctl --user restart pipewire.service
```

## Environment Variables

You can add environment variables to the service:

```ini
[Service]
Environment=OPENAI_API_KEY=sk-your-key
Environment=USE_OPENAI_DIRECT=true
Environment=DEBUG_MODE=true
```

Or use an environment file:

```ini
[Service]
EnvironmentFile=/path/to/omarchyflow/.env
```

## Alternative: Socket Activation

For on-demand startup (saves resources when not in use):

Create `~/.config/systemd/user/omarchyflow.socket`:

```ini
[Unit]
Description=OmarchyFlow Socket

[Socket]
ListenStream=/tmp/voice-dictation.sock
SocketMode=0666

[Install]
WantedBy=sockets.target
```

Then modify the service to be socket-activated:

```ini
[Unit]
Description=OmarchyFlow Voice Dictation Server
Requires=omarchyflow.socket

[Service]
Type=simple
ExecStart=/path/to/omarchyflow/.venv/bin/python /path/to/omarchyflow/omarchyflow.py
StandardInput=socket
```

Enable with:
```bash
systemctl --user enable omarchyflow.socket
systemctl --user start omarchyflow.socket
```
