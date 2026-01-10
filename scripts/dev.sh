#!/usr/bin/env bash
set -e

echo "Starting Oflow..."

# Clean up any existing processes
pkill -f "python.*oflow.py" 2>/dev/null || true
rm -f /tmp/oflow.pid /tmp/voice-dictation.sock 2>/dev/null || true

# Start backend
echo "Starting backend..."
.venv/bin/python oflow.py &
BACKEND_PID=$!

# Wait for socket to be ready
sleep 2

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
