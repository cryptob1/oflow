#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Starting Oflow..."

# Setup Hyprland keybindings if not already configured
BINDINGS_FILE="$HOME/.config/hypr/bindings.conf"
if [ -f "$BINDINGS_FILE" ]; then
    if ! grep -q "oflow" "$BINDINGS_FILE"; then
        echo "Setting up Hyprland keybindings..."
        cat >> "$BINDINGS_FILE" << EOF

# Oflow voice dictation (push-to-talk: hold Super+I to record, release to stop)
bind = SUPER, I, exec, $SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/oflow.py start
bindr = SUPER, I, exec, $SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/oflow.py stop
EOF
        hyprctl reload 2>/dev/null || true
        echo "Keybindings configured: Super+I"
    fi
fi

# Clean up any existing processes
pkill -f "python.*oflow.py" 2>/dev/null || true
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock 2>/dev/null || true

# Start backend
echo "Starting backend..."
.venv/bin/python oflow.py &
BACKEND_PID=$!

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
    pkill -f "python.*oflow.py" 2>/dev/null || true
    rm -f /tmp/oflow.pid /tmp/voice-dictation.sock 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start frontend
echo "Starting frontend..."
cd oflow-ui && npm run tauri dev

# Cleanup when frontend exits
cleanup
