# Systemd Service Setup for Oflow

This guide explains how to set up Oflow as a systemd user service for automatic startup.

## Installation

### 1. Create the service file

Create `~/.config/systemd/user/oflow.service`:

```ini
[Unit]
Description=Oflow Voice Dictation Server
Documentation=https://github.com/CryptoB1/oflow
After=graphical-session.target pulseaudio.service pipewire.service
Wants=graphical-session.target

[Service]
Type=simple
ExecStart=/path/to/oflow/.venv/bin/python /path/to/oflow/oflow.py
Restart=on-failure
RestartSec=5
Environment=PATH=/usr/bin:/usr/local/bin
WorkingDirectory=/path/to/oflow

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

Replace `/path/to/oflow` with the actual installation path:

```bash
# Example: If installed in ~/code/oflow
sed -i 's|/path/to/oflow|/home/yourusername/code/oflow|g' \
    ~/.config/systemd/user/oflow.service
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

2. Verify Python environment:
   ```bash
   # Ensure the venv is activated in the service
   # Or use absolute path to Python
   ExecStart=/path/to/oflow/.venv/bin/python /path/to/oflow/oflow.py
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
EnvironmentFile=/path/to/oflow/.env
```

## Alternative: Socket Activation

For on-demand startup (saves resources when not in use):

Create `~/.config/systemd/user/oflow.socket`:

```ini
[Unit]
Description=Oflow Socket

[Socket]
ListenStream=/tmp/voice-dictation.sock
SocketMode=0666

[Install]
WantedBy=sockets.target
```

Then modify the service to be socket-activated:

```ini
[Unit]
Description=Oflow Voice Dictation Server
Requires=oflow.socket

[Service]
Type=simple
ExecStart=/path/to/oflow/.venv/bin/python /path/to/oflow/oflow.py
StandardInput=socket
```

Enable with:
```bash
systemctl --user enable oflow.socket
systemctl --user start oflow.socket
```
