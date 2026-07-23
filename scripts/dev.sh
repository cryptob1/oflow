#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Starting Cortex..."

# Setup Hyprland keybindings if not already configured
BINDINGS_FILE="$HOME/.config/hypr/bindings.conf"
if [ -f "$BINDINGS_FILE" ]; then
    if ! grep -q "cortex" "$BINDINGS_FILE"; then
        echo "Setting up Hyprland keybindings..."
        # Install cortex-ctl if not present
        if [ ! -f "$HOME/.local/bin/cortex-ctl" ]; then
            make -C "$SCRIPT_DIR" install-cortex-ctl
        fi
        cat >> "$BINDINGS_FILE" << EOF

# Cortex voice dictation (toggle: press Super+D to start/stop)
bind = SUPER, D, exec, ~/.local/bin/cortex-ctl toggle
EOF
        hyprctl reload 2>/dev/null || true
        echo "Keybindings configured: Super+D (toggle mode)"
    fi
fi

# Clean up any existing processes
pkill -f "python.*cortex.py" 2>/dev/null || true
rm -f /tmp/cortex.pid /tmp/voice-dictation.sock /tmp/cortex-backend.log 2>/dev/null || true

# Start backend with logs to file
echo "Starting backend..."
.venv/bin/python cortex.py > /tmp/cortex-backend.log 2>&1 &
BACKEND_PID=$!

# Tail logs in background
tail -f /tmp/cortex-backend.log 2>/dev/null | sed 's/^/[backend] /' &
TAIL_PID=$!

# Wait for socket to be ready (up to 10 seconds)
echo "Waiting for backend to be ready..."
for i in {1..20}; do
    if [ -S /tmp/voice-dictation.sock ]; then
        echo "Backend ready!"
        break
    fi
    sleep 0.5
done

if [ ! -S /tmp/voice-dictation.sock ]; then
    echo "Warning: Backend socket not found after 10 seconds"
fi

# Cleanup function
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $TAIL_PID 2>/dev/null || true
    pkill -f "python.*cortex.py" 2>/dev/null || true
    rm -f /tmp/cortex.pid /tmp/voice-dictation.sock /tmp/cortex-backend.log 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start frontend
echo "Starting frontend..."
cd cortex-ui && npm run tauri dev

# Cleanup when frontend exits
cleanup
