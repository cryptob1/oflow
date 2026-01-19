# Combined Installation Guide

## For Easy Installation (Recommended)

The combined binary includes both frontend and backend in a single package:

```bash
# Clone and install combined binary
git clone <repository-url>
cd oflow
make install-combined
```

This will:
1. Build a single binary with embedded Python backend
2. Install to `~/.local/bin/oflow`
3. Set up Waybar integration
4. Configure Super+D hotkey
5. Create autostart entry (starts hidden)

## How It Works

- **Single Binary**: The Tauri frontend bundles the Python backend as a resource
- **Auto-Start Backend**: When launched, the frontend automatically starts the embedded backend
- **Development Mode**: Falls back to development backend if embedded not available
- **No Separate Processes**: Users don't need to manually start the backend

## Development vs Production

- **Development**: Use `make run` to start backend separately, `make dev` for hot reload
- **Production**: Use `make install-combined` for single binary installation

## Troubleshooting

If the backend doesn't start automatically:
1. Check logs: `journalctl --user -u oflow`
2. The binary includes fallback to development backend
3. Manual start: `~/.local/bin/oflow`